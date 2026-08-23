"""Discord gateway wiring. Thin by design — logic lives in discord_connector.

Run: python -m app.integrations.discord_bot
Env: DISCORD_BOT_TOKEN, GAMEGATE_DISCORD_CHANNEL_ID (delivery channel),
     GAMEGATE_API_URL (default http://127.0.0.1:8000), GAMEGATE_API_TOKEN
Commands in the test server: !status  !digest
"""
import asyncio
import logging
import os

import discord

from app.integrations.discord_connector import (
    DeliveryPump,
    GameGateApi,
    format_status_reply,
    normalize_message,
)

log = logging.getLogger("gamegate.discord.bot")

PUMP_INTERVAL_SECONDS = 15


def build_client() -> discord.Client:
    intents = discord.Intents.default()
    intents.message_content = True
    client = discord.Client(intents=intents)

    api = GameGateApi(
        os.environ.get("GAMEGATE_API_URL", "http://127.0.0.1:8000"),
        os.environ.get("GAMEGATE_API_TOKEN", ""),
    )
    delivery_channel_id = int(os.environ.get("GAMEGATE_DISCORD_CHANNEL_ID", "0"))
    allowed_guild_id = int(os.environ.get("GAMEGATE_DISCORD_GUILD_ID", "0"))
    owner_id = int(os.environ.get("GAMEGATE_OWNER_DISCORD_ID", "0"))
    no_mentions = discord.AllowedMentions.none()
    pump_started = {"done": False}

    @client.event
    async def on_ready():
        log.info("Connected as %s", client.user)
        # Delivery is the desktop app's job (PO decision 2026-08-23); this pump
        # only runs if explicitly re-enabled. on_ready fires again after
        # reconnects — one pump only (Vega audit #5).
        deliver = os.environ.get("GAMEGATE_DISCORD_DELIVERY", "false").lower() == "true"
        if deliver and not pump_started["done"]:
            pump_started["done"] = True
            client.loop.create_task(pump_loop())

    async def pump_loop():
        channel = client.get_channel(delivery_channel_id)
        pump = DeliveryPump(api, lambda text: _send_sync(channel, text))
        while True:
            try:
                await asyncio.to_thread(pump.run_once)
            except Exception:
                log.exception("Delivery pump cycle failed; continuing")
            await asyncio.sleep(PUMP_INTERVAL_SECONDS)

    def _send_sync(channel, text: str) -> bool:
        if channel is None:
            return False
        future = asyncio.run_coroutine_threadsafe(
            channel.send(text, allowed_mentions=no_mentions), client.loop
        )
        try:
            future.result(timeout=15)
            return True
        except Exception:
            log.exception("Failed to send to Discord")
            return False

    @client.event
    async def on_message(message: discord.Message):
        if message.author.bot:
            return
        # Only the configured guild is trusted — no other server's users can
        # query digests or feed events into GameGate (Nebula audit #4).
        if message.guild is None or (
            allowed_guild_id and message.guild.id != allowed_guild_id
        ):
            return
        text = message.content.strip()
        if text == "!status":
            await message.channel.send(
                format_status_reply(api.get_status()), allowed_mentions=no_mentions
            )
            return
        if text == "!digest":
            preview = api.get_digest_preview()
            await message.channel.send(
                preview.get("text", "Nothing queued.") if preview else "API unreachable.",
                allowed_mentions=no_mentions,
            )
            return
        # The owner's own messages are never ingested (issue #34) — you should
        # not be interrupted by yourself. Commands above still work.
        if owner_id and message.author.id == owner_id:
            return
        payload = normalize_message(
            str(message.id),
            str(message.author),
            getattr(message.channel, "name", "DM"),
            message.content,
            message.created_at.isoformat(),
            is_dm=False,
        )
        await asyncio.to_thread(api.post_event, payload)

    return client


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    client = build_client()
    client.run(os.environ["DISCORD_BOT_TOKEN"])


if __name__ == "__main__":
    main()
