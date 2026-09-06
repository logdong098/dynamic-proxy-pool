#!/usr/bin/env python3
"""One-shot full purge: keep only proxies that really pass connectivity."""
from __future__ import annotations

import asyncio
import sqlite3

from checker import checker
from config import settings
from models import ProxyItem
from storage import storage


def row_to_proxy(row):
    return ProxyItem(
        id=row[0], ip=row[1], port=row[2], protocol=row[3], username=row[4], password=row[5],
        country=row[6], country_name=row[7], latency=row[8], anonymity=row[9], score=row[10],
        fail_count=row[11], is_active=bool(row[12]), source=row[13], ip_type=row[14] or "unknown",
        fraud_score=row[15] or 0, google_clean=bool(row[16]), clean_level=row[17] or "C",
    )


async def main():
    # Full purge uses normal mode so surviving proxies also get purity metrics.
    checker.set_fast_check(False)
    conn = sqlite3.connect(settings.DB_PATH)
    rows = conn.execute(
        "SELECT id,ip,port,protocol,username,password,country,country_name,latency,anonymity,score,fail_count,is_active,source,ip_type,fraud_score,google_clean,clean_level FROM proxies"
    ).fetchall()
    conn.close()
    proxies = [row_to_proxy(row) for row in rows]
    kept = 0
    removed = 0
    batch_size = 100
    for start in range(0, len(proxies), batch_size):
        batch = proxies[start:start + batch_size]
        result = await checker.check_batch(batch)
        # The checker updates metrics; remove only entries that failed this run.
        conn = sqlite3.connect(settings.DB_PATH)
        for p in batch:
            # A failed check increments fail_count; a successful check resets it.
            current = conn.execute("SELECT fail_count FROM proxies WHERE id=?", (p.id,)).fetchone()
            failed = current is None or current[0] > p.fail_count
            if failed:
                conn.execute("DELETE FROM proxies WHERE id=?", (p.id,))
                removed += 1
            else:
                kept += 1
        conn.commit()
        conn.close()
        print(f"batch={start//batch_size+1} total={len(batch)} alive={result['alive']} dead={result['dead']} kept={kept} removed={removed}", flush=True)
    print(f"PURGE_RESULT checked={len(proxies)} kept={kept} removed={removed}", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
