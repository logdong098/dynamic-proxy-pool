#!/usr/bin/env python3
"""Backfill proxy files/messages from one Telegram chat using UserBot."""
from __future__ import annotations

import argparse
import asyncio
import logging
from pathlib import Path

from telethon import TelegramClient

from checker import checker
from config import settings
from extractor import extract_proxies_from_text
from storage import storage

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("backfill")


def decode_bytes(data: bytes) -> str:
    for encoding in ("utf-8", "gb18030", "latin-1"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="ignore")


async def resolve_chat(client: TelegramClient, requested: str):
    requested_norm = requested.lstrip("@").strip().casefold()
    async for dialog in client.iter_dialogs():
        entity = dialog.entity
        title = (getattr(entity, "title", None) or "").strip()
        username = (getattr(entity, "username", None) or "").strip()
        chat_id = str(getattr(entity, "id", ""))
        values = {title.casefold(), username.casefold(), chat_id}
        if requested_norm in values:
            return entity, title or username or chat_id
    raise RuntimeError(f"Telegram chat not found: {requested}")


async def run(args) -> int:
    session_path = settings.TG_SESSION_PATH
    if not Path(session_path).exists():
        raise RuntimeError(f"UserBot session does not exist: {session_path}")

    client = TelegramClient(session_path, settings.TG_API_ID, settings.TG_API_HASH)
    await client.connect()
    try:
        if not await client.is_user_authorized():
            raise RuntimeError("UserBot session is not authorized")

        entity, chat_label = await resolve_chat(client, args.chat)
        logger.info("Resolved chat: %s", chat_label)

        all_proxies = []
        matching_files = 0
        scanned = 0
        async for message in client.iter_messages(entity, search="otc", limit=None):
            scanned += 1
            filename = (getattr(message.file, "name", None) or "").strip()
            if filename and args.filename_contains.casefold() in filename.casefold():
                matching_files += 1
                logger.info("Downloading matching file: %s", filename)
                data = await message.download_media(bytes)
                if data:
                    all_proxies.extend(
                        extract_proxies_from_text(
                            decode_bytes(data),
                            source=f"tg_file:{chat_label}:{filename}",
                        )
                    )

        unique = {}
        for proxy in all_proxies:
            key = (proxy.protocol, proxy.ip, proxy.port, proxy.username or "")
            unique[key] = proxy
        proxies = list(unique.values())

        inserted = await storage.upsert_many(proxies)
        checked = await checker.check_batch(proxies) if proxies else {"alive": 0, "dead": 0}
        logger.info(
            "BACKFILL_RESULT chat=%s scanned=%d filename_contains=%s "
            "matching_files=%d extracted=%d unique=%d upserted=%d alive=%s dead=%s",
            chat_label,
            scanned,
            args.filename_contains,
            matching_files,
            len(all_proxies),
            len(proxies),
            inserted,
            checked.get("alive", 0),
            checked.get("dead", 0),
        )
    finally:
        await client.disconnect()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--chat", default="老王技术交流分享群")
    parser.add_argument("--limit", type=int, default=0, help="0 means all history")
    parser.add_argument("--filename-contains", default="otc")
    args = parser.parse_args()
    if args.limit <= 0:
        args.limit = None
    return asyncio.run(run(args))


if __name__ == "__main__":
    raise SystemExit(main())
