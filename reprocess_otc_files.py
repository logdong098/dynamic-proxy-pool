#!/usr/bin/env python3
"""Re-process downloaded OTC text attachments with the current extractor."""
from __future__ import annotations

import asyncio
from pathlib import Path

from config import settings
from extractor import extract_proxies_from_text
from storage import storage

TEXT_SUFFIXES = {".txt", ".csv", ".json", ".yaml", ".yml", ".log"}


def decode(data: bytes) -> str:
    for enc in ("utf-8-sig", "gb18030", "big5", "latin-1"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            pass
    return data.decode("utf-8", errors="replace")


async def main() -> None:
    root = Path(settings.DATA_DIR) / "otc_files"
    all_items = []
    report = []
    for path in sorted(root.iterdir()):
        if not path.is_file() or path.suffix.lower() not in TEXT_SUFFIXES:
            continue
        text = decode(path.read_bytes())
        source = f"tg_file:老王技术交流分享群:{path.name.split('-', 1)[-1]}"
        items = extract_proxies_from_text(text, source=source)
        report.append((path.name, len(text.splitlines()), len(items)))
        all_items.extend(items)

    unique = {}
    for item in all_items:
        key = (item.protocol, item.ip, item.port, item.username or "")
        unique[key] = item
    items = list(unique.values())
    upserted = await storage.upsert_many(items)
    print("FILES")
    for name, lines, count in report:
        print(f"{name}\tlines={lines}\tparsed={count}")
    print(f"SUMMARY text_files={len(report)} parsed_total={len(all_items)} unique={len(items)} upserted={upserted}")


asyncio.run(main())
