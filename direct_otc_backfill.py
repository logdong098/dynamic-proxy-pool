#!/usr/bin/env python3
"""Download known OTC attachments by message ID and extract proxy data."""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from telethon import TelegramClient

from checker import checker
from config import settings
from extractor import extract_proxies_from_text
from storage import storage

CHAT_TITLE = "老王技术交流分享群"
# IDs obtained from the Telegram attachment search result.
FILE_IDS = [
    809243, 746446, 746444, 536089, 532428, 526338, 492702,
    486927, 486926, 479514, 446373, 437736, 428827, 428583,
    428577, 393661, 437736,
]
OUT_DIR = Path(settings.DATA_DIR) / "otc_files"

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("direct-otc")


def decode_bytes(data: bytes) -> tuple[str, str]:
    for encoding in ("utf-8-sig", "gb18030", "big5", "latin-1"):
        try:
            return data.decode(encoding), encoding
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace"), "utf-8-replace"


async def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    client = TelegramClient(settings.TG_SESSION_PATH, settings.TG_API_ID, settings.TG_API_HASH)
    await client.connect()
    try:
        if not await client.is_user_authorized():
            raise RuntimeError("UserBot session is not authorized")
        entity = None
        async for dialog in client.iter_dialogs():
            title = (getattr(dialog.entity, "title", None) or "").strip()
            if title == CHAT_TITLE:
                entity = dialog.entity
                break
        if entity is None:
            raise RuntimeError(f"Chat not found: {CHAT_TITLE}")

        messages = await client.get_messages(entity, ids=FILE_IDS)
        all_proxies = []
        downloaded = 0
        text_files = 0
        skipped_binary = 0
        for message in messages:
            if message is None or not getattr(message, "file", None):
                logger.warning("Message not found or has no file: %s", message)
                continue
            name = (getattr(message.file, "name", None) or f"message-{message.id}").strip()
            safe_name = name.replace("/", "_").replace("\\", "_")
            target = OUT_DIR / f"{message.id}-{safe_name}"
            data = await message.download_media(bytes)
            if not data:
                logger.warning("Empty download: id=%s name=%s", message.id, name)
                continue
            target.write_bytes(data)
            downloaded += 1
            logger.info("Downloaded id=%s name=%s bytes=%d", message.id, name, len(data))
            # Never execute binary attachments. Only parse text-like files.
            lower = name.casefold()
            if lower.endswith((".txt", ".csv", ".json", ".yaml", ".yml", ".log")):
                text_files += 1
                text, encoding = decode_bytes(data)
                logger.info("Analyzing id=%s encoding=%s lines=%d", message.id, encoding, len(text.splitlines()))
                found = extract_proxies_from_text(
                    text, source=f"tg_file:{CHAT_TITLE}:{name}"
                )
                logger.info("Parsed id=%s name=%s proxies=%d", message.id, name, len(found))
                all_proxies.extend(found)
            else:
                skipped_binary += 1

        unique = {}
        for proxy in all_proxies:
            key = (proxy.protocol, proxy.ip, proxy.port, proxy.username or "")
            unique[key] = proxy
        proxies = list(unique.values())
        upserted = await storage.upsert_many(proxies)
        checked = await checker.check_batch(proxies) if proxies else {"alive": 0, "dead": 0}
        logger.info(
            "OTC_RESULT downloaded=%d text_files=%d binary_skipped=%d parsed=%d unique=%d "
            "upserted=%d alive=%s dead=%s output_dir=%s",
            downloaded, text_files, skipped_binary, len(all_proxies), len(proxies),
            upserted, checked.get("alive", 0), checked.get("dead", 0), OUT_DIR,
        )
    finally:
        await client.disconnect()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

