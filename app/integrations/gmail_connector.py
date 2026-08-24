"""Gmail connector — read-only polling, deterministic rules first (Step 7).

The real Gmail client is isolated behind the GmailClient protocol and only
imported when GMAIL_ENABLED=true, so the core service and CI never depend on
Google libraries or live OAuth. Duplicate protection is server-side: the API
is idempotent on (gmail, message_id).
"""
import logging
import os
from typing import Protocol

log = logging.getLogger("gamegate.gmail")

ORDER_PROBLEM_KEYWORDS = (
    "order", "refund", "not arrived", "delayed", "missing", "chargeback", "complaint",
)
NEWSLETTER_MARKERS = ("unsubscribe", "newsletter", "no-reply", "noreply")


class GmailClient(Protocol):
    def list_new_messages(self) -> list[dict]:
        """Return recent messages as dicts:
        {id, sender, subject, snippet, received_at (ISO 8601)}"""
        ...


def classify_email(sender: str, subject: str, snippet: str) -> str:
    """The connector's deterministic first pass — no AI, no VIP list. VIP
    senders are applied server-side from the DB settings (single source of
    truth), so the connector never carries its own VIP list (M14)."""
    sender_lower = sender.lower()
    text = f"{subject} {snippet}".lower()
    if any(keyword in text for keyword in ORDER_PROBLEM_KEYWORDS):
        return "actionable"
    if any(marker in sender_lower or marker in text for marker in NEWSLETTER_MARKERS):
        return "ignore"
    return "informational"


def normalize_email(message: dict) -> dict:
    priority = classify_email(
        message["sender"], message["subject"], message.get("snippet", "")
    )
    return {
        "source": "gmail",
        "external_id": message["id"],
        "sender": message["sender"],
        "title": message["subject"],
        "content": message.get("snippet", "")[:2000],  # safe snippet, never full body
        "received_at": message["received_at"],
        "priority": priority,
        "requires_action": priority in ("urgent", "actionable"),
        "metadata": {},
    }


class GmailPoller:
    def __init__(self, gmail: GmailClient, api):
        self.gmail = gmail
        self.api = api

    def poll_once(self) -> int:
        """Fetch → normalize → ingest. Returns how many posts succeeded.
        Failures are logged and retried implicitly next cycle (idempotent API)."""
        try:
            messages = self.gmail.list_new_messages()
        except Exception:
            log.exception("Gmail fetch failed; will retry next cycle")
            return 0
        ingested = 0
        for message in messages:
            if self.api.post_event(normalize_email(message)):
                ingested += 1
        return ingested


def build_real_client() -> GmailClient:
    """Real Gmail API client. Requires GMAIL_ENABLED=true plus OAuth files
    (credentials.json / token.json — both gitignored). See docs/GMAIL_SETUP.md."""
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build

    scopes = ["https://www.googleapis.com/auth/gmail.readonly"]
    token_path = os.environ.get("GMAIL_TOKEN_PATH", "token.json")
    creds = Credentials.from_authorized_user_file(token_path, scopes)
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())

    service = build("gmail", "v1", credentials=creds)

    class RealGmailClient:
        def list_new_messages(self) -> list[dict]:
            refs, page_token = [], None
            # Paginate: a backlog beyond one page must not starve older messages.
            # The 200 hard cap keeps a poll bounded — request only the remaining
            # room each page and slice, so the final page can't push the total
            # past 200 (review MINOR: a 150+100 page could otherwise reach 250).
            while len(refs) < 200:
                listing = (
                    service.users()
                    .messages()
                    .list(
                        userId="me", q="is:unread newer_than:1d",
                        maxResults=min(100, 200 - len(refs)), pageToken=page_token,
                    )
                    .execute()
                )
                refs.extend(listing.get("messages", []))
                page_token = listing.get("nextPageToken")
                if not page_token:
                    break
            refs = refs[:200]  # belt-and-suspenders: never exceed the cap
            results = []
            for ref in refs:
                msg = (
                    service.users()
                    .messages()
                    .get(userId="me", id=ref["id"], format="metadata")
                    .execute()
                )
                headers = {
                    h["name"].lower(): h["value"]
                    for h in msg.get("payload", {}).get("headers", [])
                }
                from datetime import UTC, datetime

                # internalDate is epoch millis — always parseable, unlike the
                # RFC-2822 Date header.
                received = datetime.fromtimestamp(
                    int(msg.get("internalDate", 0)) / 1000, tz=UTC
                ).isoformat()
                results.append(
                    {
                        "id": msg["id"],
                        "sender": headers.get("from", "unknown"),
                        "subject": headers.get("subject", "(no subject)"),
                        "snippet": msg.get("snippet", ""),
                        "received_at": received,
                    }
                )
            return results

    return RealGmailClient()
