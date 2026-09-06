import asyncio
import logging
from config import settings
from storage import storage
from checker import checker
from scrapers.maskproxy import maskproxy_scraper
from scrapers.public_sources import public_scraper
from scrapers.geonode import geonode_scraper

logger = logging.getLogger("scheduler")


class TaskScheduler:
    def __init__(self):
        self.is_running = False
        self._tasks = []
        self._check_lock = asyncio.Lock()

    async def scrape_job(self):
        """Periodically scrape websites and public sources."""
        logger.info("Scheduler: Starting scraping cycle...")
        all_new = []

        # 1. Scrape MaskProxy
        try:
            mp_proxies = await maskproxy_scraper.fetch_all()
            all_new.extend(mp_proxies)
        except Exception as e:
            logger.error(f"Scheduler scrape MaskProxy error: {e}")

        # 2. Scrape Geonode API
        try:
            gn_proxies = await geonode_scraper.fetch_all()
            all_new.extend(gn_proxies)
        except Exception as e:
            logger.error(f"Scheduler scrape Geonode error: {e}")

        # 3. Scrape Public Sources
        try:
            pub_proxies = await public_scraper.fetch_all()
            all_new.extend(pub_proxies)
        except Exception as e:
            logger.error(f"Scheduler scrape public sources error: {e}")

        if all_new:
            inserted = await storage.upsert_many(all_new)
            logger.info(f"Scheduler: Inserted/Updated {inserted} candidate proxies into storage.")
            # Trigger immediate check for a batch of fresh candidates
            asyncio.create_task(checker.check_batch(all_new[:50]))

    async def check_job(self):
        """Health-check one bounded batch without overlapping SQLite writes."""
        if self._check_lock.locked():
            logger.warning("Scheduler: Previous health check still running; skipping overlap.")
            return
        async with self._check_lock:
            logger.info("Scheduler: Starting proxy health check cycle...")
            try:
                unchecked = await storage.count_unchecked()
                fast_mode = unchecked > settings.FAST_CHECK_THRESHOLD
                checker.set_fast_check(fast_mode)
                logger.info(
                    "Scheduler: unchecked=%d threshold=%d mode=%s",
                    unchecked,
                    settings.FAST_CHECK_THRESHOLD,
                    "FAST" if fast_mode else "NORMAL",
                )
                proxies_to_check = await storage.get_proxies_for_check(limit=settings.CHECK_BATCH_SIZE)
                if proxies_to_check:
                    logger.info(f"Scheduler: Health checking {len(proxies_to_check)} proxies...")
                    res = await checker.check_batch(proxies_to_check)
                    logger.info(f"Scheduler: Health check completed: {res['alive']} alive, {res['dead']} dead.")
                else:
                    logger.info("Scheduler: No proxies currently pending health check.")

                pruned = await storage.prune_dead(max_fail_count=settings.MAX_FAIL_COUNT + 2)
                if pruned > 0:
                    logger.info(f"Scheduler: Pruned {pruned} continuously failing proxies.")
            except Exception as e:
                logger.error(f"Scheduler health check error: {e}")

    async def _scrape_loop(self):
        # Run once immediately on startup
        await self.scrape_job()
        while self.is_running:
            await asyncio.sleep(settings.SCRAPE_INTERVAL_MINUTES * 60)
            if self.is_running:
                await self.scrape_job()

    async def _check_loop(self):
        # Small delay after startup before check cycle
        await asyncio.sleep(5)
        while self.is_running:
            await self.check_job()
            await asyncio.sleep(settings.CHECK_INTERVAL_MINUTES * 60)

    async def start(self):
        if self.is_running:
            return
        self.is_running = True
        logger.info("Scheduler started.")
        self._tasks.append(asyncio.create_task(self._scrape_loop()))
        self._tasks.append(asyncio.create_task(self._check_loop()))

    async def stop(self):
        self.is_running = False
        for t in self._tasks:
            t.cancel()
        logger.info("Scheduler stopped.")


scheduler = TaskScheduler()
