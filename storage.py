import os
import asyncio
import aiosqlite
from datetime import datetime
from typing import List, Optional, Tuple, Dict, Any

from models import ProxyItem, ProxyQuery, StatsResponse
from config import settings


class ProxyStorage:
    def __init__(self, db_path: str = None):
        self.db_path = db_path or settings.DB_PATH
        self._write_lock = asyncio.Lock()
        os.makedirs(os.path.dirname(os.path.abspath(self.db_path)), exist_ok=True)

    async def init_db(self):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("PRAGMA journal_mode=WAL;")
            await db.execute("""
                CREATE TABLE IF NOT EXISTS proxies (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ip TEXT NOT NULL,
                    port INTEGER NOT NULL,
                    protocol TEXT NOT NULL,
                    username TEXT DEFAULT NULL,
                    password TEXT DEFAULT NULL,
                    country TEXT NOT NULL DEFAULT 'UNKNOWN',
                    country_name TEXT DEFAULT NULL,
                    latency INTEGER DEFAULT NULL,
                    anonymity TEXT DEFAULT 'unknown',
                    score INTEGER DEFAULT 100,
                    fail_count INTEGER DEFAULT 0,
                    is_active INTEGER DEFAULT 1,
                    source TEXT DEFAULT 'manual',
                    ip_type TEXT DEFAULT 'unknown',
                    fraud_score INTEGER DEFAULT 0,
                    google_clean INTEGER DEFAULT 0,
                    clean_level TEXT DEFAULT 'C',
                    last_checked TIMESTAMP DEFAULT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
            """)

            # Automatic column migrations for existing SQLite tables
            async with db.execute("PRAGMA table_info(proxies)") as cur:
                existing_cols = [row[1] for row in await cur.fetchall()]
                if "ip_type" not in existing_cols:
                    await db.execute("ALTER TABLE proxies ADD COLUMN ip_type TEXT DEFAULT 'unknown';")
                if "fraud_score" not in existing_cols:
                    await db.execute("ALTER TABLE proxies ADD COLUMN fraud_score INTEGER DEFAULT 0;")
                if "google_clean" not in existing_cols:
                    await db.execute("ALTER TABLE proxies ADD COLUMN google_clean INTEGER DEFAULT 0;")
                if "clean_level" not in existing_cols:
                    await db.execute("ALTER TABLE proxies ADD COLUMN clean_level TEXT DEFAULT 'C';")

            # Unique index & query indexes
            await db.execute("""
                CREATE UNIQUE INDEX IF NOT EXISTS idx_proxies_unique
                ON proxies(protocol, ip, port, coalesce(username, ''));
            """)
            await db.execute("CREATE INDEX IF NOT EXISTS idx_proxies_country ON proxies(country);")
            await db.execute("CREATE INDEX IF NOT EXISTS idx_proxies_protocol ON proxies(protocol);")
            await db.execute("CREATE INDEX IF NOT EXISTS idx_proxies_active ON proxies(is_active);")
            await db.execute("CREATE INDEX IF NOT EXISTS idx_proxies_clean_level ON proxies(clean_level);")
            await db.execute("CREATE INDEX IF NOT EXISTS idx_proxies_ip_type ON proxies(ip_type);")
            await db.commit()

    def _row_to_item(self, row: Any) -> ProxyItem:
        return ProxyItem(
            id=row[0],
            ip=row[1],
            port=row[2],
            protocol=row[3],
            username=row[4],
            password=row[5],
            country=row[6],
            country_name=row[7],
            latency=row[8],
            anonymity=row[9],
            score=row[10],
            fail_count=row[11],
            is_active=bool(row[12]),
            source=row[13],
            ip_type=row[14] or "unknown",
            fraud_score=row[15] or 0,
            google_clean=bool(row[16]),
            clean_level=row[17] or "C",
            last_checked=datetime.fromisoformat(row[18]) if row[18] else None,
            created_at=datetime.fromisoformat(row[19]) if row[19] else None,
            updated_at=datetime.fromisoformat(row[20]) if row[20] else None,
        )

    async def upsert_proxy(self, proxy: ProxyItem) -> bool:
        sql = """
            INSERT INTO proxies (
                ip, port, protocol, username, password, country, country_name,
                latency, anonymity, score, fail_count, is_active, source,
                ip_type, fraud_score, google_clean, clean_level,
                last_checked, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'), datetime('now'))
            ON CONFLICT(protocol, ip, port, coalesce(username, '')) DO UPDATE SET
                password = coalesce(excluded.password, proxies.password),
                country = CASE WHEN proxies.country = 'UNKNOWN' AND excluded.country != 'UNKNOWN' THEN excluded.country ELSE proxies.country END,
                country_name = coalesce(excluded.country_name, proxies.country_name),
                ip_type = CASE WHEN proxies.ip_type = 'unknown' AND excluded.ip_type != 'unknown' THEN excluded.ip_type ELSE proxies.ip_type END,
                clean_level = CASE WHEN excluded.clean_level != 'C' THEN excluded.clean_level ELSE proxies.clean_level END,
                source = excluded.source,
                updated_at = datetime('now');
        """
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(sql, (
                proxy.ip, proxy.port, proxy.protocol.lower(),
                proxy.username, proxy.password,
                proxy.country.upper() if proxy.country else "UNKNOWN",
                proxy.country_name,
                proxy.latency,
                proxy.anonymity or "unknown",
                proxy.score,
                proxy.fail_count,
                1 if proxy.is_active else 0,
                proxy.source,
                proxy.ip_type or "unknown",
                proxy.fraud_score or 0,
                1 if proxy.google_clean else 0,
                proxy.clean_level or "C",
                proxy.last_checked.isoformat() if proxy.last_checked else None
            ))
            await db.commit()
            return True

    async def upsert_many(self, proxies: List[ProxyItem]) -> int:
        if not proxies:
            return 0
        count = 0
        async with aiosqlite.connect(self.db_path) as db:
            sql = """
                INSERT INTO proxies (
                    ip, port, protocol, username, password, country, country_name,
                    latency, anonymity, score, fail_count, is_active, source,
                    ip_type, fraud_score, google_clean, clean_level,
                    last_checked, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'), datetime('now'))
                ON CONFLICT(protocol, ip, port, coalesce(username, '')) DO UPDATE SET
                    password = coalesce(excluded.password, proxies.password),
                    country = CASE WHEN proxies.country = 'UNKNOWN' AND excluded.country != 'UNKNOWN' THEN excluded.country ELSE proxies.country END,
                    ip_type = CASE WHEN proxies.ip_type = 'unknown' AND excluded.ip_type != 'unknown' THEN excluded.ip_type ELSE proxies.ip_type END,
                    source = excluded.source,
                    updated_at = datetime('now');
            """
            for p in proxies:
                await db.execute(sql, (
                    p.ip, p.port, p.protocol.lower(),
                    p.username, p.password,
                    p.country.upper() if p.country else "UNKNOWN",
                    p.country_name,
                    p.latency,
                    p.anonymity or "unknown",
                    p.score,
                    p.fail_count,
                    1 if p.is_active else 0,
                    p.source,
                    p.ip_type or "unknown",
                    p.fraud_score or 0,
                    1 if p.google_clean else 0,
                    p.clean_level or "C",
                    p.last_checked.isoformat() if p.last_checked else None
                ))
                count += 1
            await db.commit()
        return count

    async def get_proxy(
        self,
        country: Optional[str] = None,
        protocol: Optional[str] = None,
        min_score: int = 50,
        clean_only: bool = False,
        ip_type: Optional[str] = None
    ) -> Optional[ProxyItem]:
        """Fetch one best active proxy with optional purity filtering."""
        conditions = ["is_active = 1", "score >= ?"]
        params: List[Any] = [min_score]

        if country and country.upper() != "ALL":
            conditions.append("country = ?")
            params.append(country.upper())
        if protocol and protocol.lower() != "all":
            conditions.append("protocol = ?")
            params.append(protocol.lower())
        if clean_only:
            conditions.append("clean_level IN ('A', 'B')")
        if ip_type and ip_type.lower() != "all":
            conditions.append("ip_type = ?")
            params.append(ip_type.lower())

        where_clause = " AND ".join(conditions)
        sql = f"""
            SELECT id, ip, port, protocol, username, password, country, country_name,
                   latency, anonymity, score, fail_count, is_active, source,
                   ip_type, fraud_score, google_clean, clean_level,
                   last_checked, created_at, updated_at
            FROM proxies
            WHERE {where_clause}
            ORDER BY
                CASE clean_level WHEN 'A' THEN 1 WHEN 'B' THEN 2 WHEN 'C' THEN 3 ELSE 4 END ASC,
                score DESC,
                coalesce(latency, 99999) ASC
            LIMIT 1;
        """
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(sql, params) as cursor:
                row = await cursor.fetchone()
                if row:
                    return self._row_to_item(row)
        return None

    async def list_proxies(self, query: ProxyQuery) -> List[ProxyItem]:
        conditions = []
        params: List[Any] = []

        if query.is_active is not None:
            conditions.append("is_active = ?")
            params.append(1 if query.is_active else 0)

        if query.country and query.country.upper() != "ALL":
            conditions.append("country = ?")
            params.append(query.country.upper())

        if query.protocol and query.protocol.lower() != "all":
            conditions.append("protocol = ?")
            params.append(query.protocol.lower())

        if query.min_score is not None:
            conditions.append("score >= ?")
            params.append(query.min_score)

        if query.max_latency is not None:
            conditions.append("latency <= ?")
            params.append(query.max_latency)

        if query.clean_only:
            conditions.append("clean_level IN ('A', 'B')")

        if query.ip_type and query.ip_type.lower() != "all":
            conditions.append("ip_type = ?")
            params.append(query.ip_type.lower())

        where_clause = " WHERE " + " AND ".join(conditions) if conditions else ""
        sql = f"""
            SELECT id, ip, port, protocol, username, password, country, country_name,
                   latency, anonymity, score, fail_count, is_active, source,
                   ip_type, fraud_score, google_clean, clean_level,
                   last_checked, created_at, updated_at
            FROM proxies
            {where_clause}
            ORDER BY is_active DESC, score DESC, coalesce(latency, 99999) ASC
            LIMIT ? OFFSET ?;
        """
        params.extend([query.limit, query.offset])

        results = []
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(sql, params) as cursor:
                async for row in cursor:
                    results.append(self._row_to_item(row))
        return results

    async def count_unchecked(self) -> int:
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute("SELECT COUNT(*) FROM proxies WHERE last_checked IS NULL") as cursor:
                row = await cursor.fetchone()
                return int(row[0] or 0)

    async def get_proxies_for_check(self, limit: int = 100) -> List[ProxyItem]:
        sql = """
            SELECT id, ip, port, protocol, username, password, country, country_name,
                   latency, anonymity, score, fail_count, is_active, source,
                   ip_type, fraud_score, google_clean, clean_level,
                   last_checked, created_at, updated_at
            FROM proxies
            ORDER BY
                CASE WHEN last_checked IS NULL THEN 0 ELSE 1 END,
                last_checked ASC
            LIMIT ?;
        """
        results = []
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(sql, (limit,)) as cursor:
                async for row in cursor:
                    results.append(self._row_to_item(row))
        return results

    async def update_check_result(
        self,
        proxy_id: int,
        is_alive: bool,
        latency: Optional[int] = None,
        country: Optional[str] = None,
        anonymity: Optional[str] = None,
        ip_type: Optional[str] = None,
        fraud_score: Optional[int] = None,
        google_clean: Optional[bool] = None,
        clean_level: Optional[str] = None,
        max_fail_count: int = 3
    ):
        async with self._write_lock:
            async with aiosqlite.connect(self.db_path) as db:
                await db.execute("PRAGMA busy_timeout=10000;")
                if is_alive:
                    sql = """
                        UPDATE proxies SET
                            is_active = 1,
                            fail_count = 0,
                            latency = coalesce(?, latency),
                            score = min(100, score + 10),
                            country = CASE WHEN (country = 'UNKNOWN' OR country IS NULL) AND ? IS NOT NULL THEN ? ELSE country END,
                            anonymity = coalesce(?, anonymity),
                            ip_type = coalesce(?, ip_type),
                            fraud_score = coalesce(?, fraud_score),
                            google_clean = CASE WHEN ? IS NOT NULL THEN ? ELSE google_clean END,
                            clean_level = coalesce(?, clean_level),
                            last_checked = datetime('now'),
                            updated_at = datetime('now')
                        WHERE id = ?;
                    """
                    g_val = 1 if google_clean is True else (0 if google_clean is False else None)
                    await db.execute(sql, (
                        latency, country, country, anonymity,
                        ip_type, fraud_score, g_val, g_val, clean_level, proxy_id
                    ))
                else:
                    sql = f"""
                        UPDATE proxies SET
                            fail_count = fail_count + 1,
                            score = max(0, score - 30),
                            is_active = CASE WHEN fail_count + 1 >= {max_fail_count} THEN 0 ELSE is_active END,
                            last_checked = datetime('now'),
                            updated_at = datetime('now')
                        WHERE id = ?;
                    """
                    await db.execute(sql, (proxy_id,))
                await db.commit()

    async def prune_dead(self, max_fail_count: int = 5) -> int:
        sql = "DELETE FROM proxies WHERE fail_count >= ? OR score <= 0;"
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(sql, (max_fail_count,))
            deleted = cursor.rowcount
            await db.commit()
            return deleted

    async def get_stats(self) -> StatsResponse:
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute("""
                SELECT
                    count(*),
                    sum(case when is_active = 1 then 1 else 0 end),
                    avg(case when is_active = 1 then latency end),
                    sum(case when is_active = 1 and clean_level IN ('A', 'B') then 1 else 0 end),
                    sum(case when is_active = 1 and ip_type = 'residential' then 1 else 0 end)
                FROM proxies;
            """) as cur:
                row = await cur.fetchone()
                total = row[0] or 0
                active = row[1] or 0
                avg_latency = round(row[2], 1) if row and row[2] is not None else None
                clean_count = row[3] or 0
                residential_count = row[4] or 0

            by_country: Dict[str, int] = {}
            async with db.execute("SELECT country, count(*) FROM proxies WHERE is_active = 1 GROUP BY country ORDER BY count(*) DESC;") as cur:
                async for c_row in cur:
                    by_country[c_row[0]] = c_row[1]

            by_protocol: Dict[str, int] = {}
            async with db.execute("SELECT protocol, count(*) FROM proxies WHERE is_active = 1 GROUP BY protocol ORDER BY count(*) DESC;") as cur:
                async for p_row in cur:
                    by_protocol[p_row[0]] = p_row[1]

            by_clean_level: Dict[str, int] = {}
            async with db.execute("SELECT clean_level, count(*) FROM proxies WHERE is_active = 1 GROUP BY clean_level ORDER BY clean_level ASC;") as cur:
                async for l_row in cur:
                    by_clean_level[l_row[0]] = l_row[1]

        return StatsResponse(
            total_proxies=total,
            active_proxies=active,
            clean_proxies=clean_count,
            residential_proxies=residential_count,
            by_country=by_country,
            by_protocol=by_protocol,
            by_clean_level=by_clean_level,
            avg_latency_ms=avg_latency
        )


storage = ProxyStorage()
