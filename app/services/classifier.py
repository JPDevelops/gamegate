"""AI classifier with a deterministic fallback (Step 10).

NON-NEGOTIABLE (runbook): if the LLM is unavailable, slow, or returns
garbage, GameGate keeps working. The provider sits behind an interface, its
output is schema-validated like any untrusted API response, and every failure
path lands on DeterministicClassifier. Tests never make live LLM calls.
"""
import json
import logging
import os
from typing import Protocol

import httpx
from pydantic import BaseModel, Field, ValidationError

from app.models.event import Event, EventPriority

log = logging.getLogger("gamegate.classifier")


class Classification(BaseModel):
    category: str = Field(pattern="^(action_required|fyi|ignore)$")
    urgency: int = Field(ge=1, le=10)
    summary: str
    suggested_action: str
    reason: str


class Classifier(Protocol):
    def classify(self, event: Event) -> Classification: ...


class ClassifierError(Exception):
    pass


class DeterministicClassifier:
    """Rule-based mapping from the priority the connectors already assigned."""

    def classify(self, event: Event) -> Classification:
        if event.priority == EventPriority.URGENT:
            return Classification(
                category="action_required", urgency=9,
                summary=f"Urgent from {event.sender}: {event.title}",
                suggested_action="Handle as soon as the session ends (or immediately).",
                reason="Connector rules marked this urgent.",
            )
        if event.priority == EventPriority.ACTIONABLE or event.requires_action:
            return Classification(
                category="action_required", urgency=6,
                summary=f"{event.sender}: {event.title}",
                suggested_action="Review and respond in the next digest window.",
                reason="Marked actionable by deterministic rules.",
            )
        if event.priority == EventPriority.IGNORE:
            return Classification(
                category="ignore", urgency=1,
                summary=event.title,
                suggested_action="None.",
                reason="Matched ignore rules (e.g. newsletter).",
            )
        return Classification(
            category="fyi", urgency=3,
            summary=f"{event.sender}: {event.title}",
            suggested_action="Read when convenient.",
            reason="No urgency signals found.",
        )


PROMPT = """You are GameGate's message classifier. Reply with ONLY a JSON object:
{"category": "action_required"|"fyi"|"ignore", "urgency": 1-10,
 "summary": "<one sentence>", "suggested_action": "<one sentence>",
 "reason": "<one sentence>"}"""


class OpenAIClassifier:
    """LLM-backed classifier. Sends title/sender/snippet only — never secrets,
    never full message bodies beyond the stored safe snippet."""

    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        timeout: float = 15.0,
        client: httpx.Client | None = None,
    ) -> None:
        self.api_key = api_key or os.environ.get("OPENAI_API_KEY", "")
        self.model = model or os.environ.get("CLASSIFIER_MODEL", "gpt-4o-mini")
        self.client = client or httpx.Client(timeout=timeout)

    def classify(self, event: Event) -> Classification:
        try:
            response = self.client.post(
                "https://api.openai.com/v1/chat/completions",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json={
                    "model": self.model,
                    "messages": [
                        {"role": "system", "content": PROMPT},
                        {
                            "role": "user",
                            "content": json.dumps(
                                {
                                    "source": event.source.value,
                                    "sender": event.sender,
                                    "title": event.title,
                                    "snippet": event.content[:500],
                                }
                            ),
                        },
                    ],
                },
            )
            response.raise_for_status()
            raw = response.json()["choices"][0]["message"]["content"]
            return Classification.model_validate_json(raw)
        except (httpx.HTTPError, ValidationError, KeyError, json.JSONDecodeError) as exc:
            raise ClassifierError(f"LLM classification failed: {exc}") from exc


class SafeClassifier:
    """Primary (LLM) with deterministic fallback. This class never raises."""

    def __init__(self, primary: Classifier | None, fallback: Classifier) -> None:
        self.primary = primary
        self.fallback = fallback

    def classify(self, event: Event) -> Classification:
        if self.primary is not None:
            try:
                return self.primary.classify(event)
            except Exception as exc:  # noqa: BLE001 — any primary failure must fall back, never crash
                log.warning("Classifier fell back to deterministic rules: %s", exc)
        return self.fallback.classify(event)


_classifier_cache: dict[bool, "SafeClassifier"] = {}


def reset_classifier_cache() -> None:
    """Drop the cached classifier so a changed key / enabled flag (set via the
    dashboard, which updates os.environ) is picked up on the next build."""
    _classifier_cache.clear()


def build_classifier() -> SafeClassifier:
    """Reuse the classifier (and thus its httpx.Client) instead of building a new
    one — and leaking its connection pool — on every request (M1). Keyed on the
    enabled flag so a dashboard toggle is still reflected. httpx.Client is safe to
    share across the threadpool."""
    enabled = (
        os.environ.get("CLASSIFIER_ENABLED", "").lower() == "true"
        and bool(os.environ.get("OPENAI_API_KEY", "").strip())
    )
    if enabled not in _classifier_cache:
        primary = OpenAIClassifier() if enabled else None
        _classifier_cache[enabled] = SafeClassifier(primary, DeterministicClassifier())
    return _classifier_cache[enabled]
