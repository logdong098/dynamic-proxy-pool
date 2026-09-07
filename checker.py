import time
import asyncio
import logging
from typing import Optional, Tuple, Dict, Any
import httpx

from models import ProxyItem
from config import settings
from storage import storage

logger = logging.getLogger("checker")


class ProxyChecker:
    def __init__(
        self,
        target_url: str = None,
        backup_url: str = None,
        timeout: float = None,
        concurrency: int = None
    ):
        self.target_url = target_url or settings.CHECK_TARGET_URL
        self.backup_url = backup_url or settings.BACKUP_TARGET_URL
        self.timeout = timeout or settings.CHECK_TIMEOUT
        self.semaphore = asyncio.Semaphore(concurrency or settings.CHECK_CONCURRENCY)
        self.fast_check: Optional[bool] = None

    def set_fast_check(self, enabled: bool) -> None:
        """Override fast mode for the current scheduler cycle."""
        self.fast_check = enabled

    async def _check_google_clean(self, proxy_url: str) -> bool:
        """Test whether proxy can access Google without captcha challenge."""
        try:
            transport = httpx.AsyncHTTPTransport(proxy=proxy_url)
            async with httpx.AsyncClient(
                transport=transport,
                timeout=4.0,
                headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
            ) as client:
                resp = await client.get("https://www.google.com/generate_204")
                return resp.status_code in (200, 204)
        except Exception:
            return False

    async def _resolve_ip_type(self, ip: str) -> Tuple[str, bool, int]:
        """
        Query IP intelligence to determine if IP is residential, datacenter, or mobile.
        Returns: (ip_type, is_known_proxy, fraud_base_score)
        """
        try:
            url = f"http://ip-api.com/json/{ip}?fields=status,mobile,proxy,hosting"
            async with httpx.AsyncClient(timeout=4.0) as client:
                resp = await client.get(url)
                if resp.status_code == 200:
                    data = resp.json()
                    is_hosting = data.get("hosting", False)
                    is_mobile = data.get("mobile", False)
                    is_proxy = data.get("proxy", False)

                    if is_hosting:
                        return "datacenter", is_proxy, 45
                    elif is_mobile:
                        return "mobile", is_proxy, 15
                    else:
                        # Non-hosting, non-mobile ISP IP is residential broadband
                        return "residential", is_proxy, 10
        except Exception:
            pass
        return "unknown", False, 30

    async def check_proxy(self, proxy: ProxyItem) -> Tuple[bool, Optional[int], Optional[str], Optional[str], str, int, bool, str]:
        """
        Validates proxy connectivity, measures latency, resolves country & anonymity,
        and performs IP purity & risk level evaluation.
        Returns:
            (is_alive, latency_ms, country, anonymity, ip_type, fraud_score, google_clean, clean_level)
        """
        proxy_url = proxy.to_url()
        async with self.semaphore:
            start_time = time.perf_counter()
            is_alive = False
            latency_ms = None
            country = None
            egress_ip = None
            anonymity = "anonymous"

            try:
                # 1. Primary check: Cloudflare trace
                transport = httpx.AsyncHTTPTransport(proxy=proxy_url)
                async with httpx.AsyncClient(
                    transport=transport,
                    timeout=self.timeout,
                    headers={"User-Agent": "Mozilla/5.0 (compatible; ProxyChecker/1.0)"}
                ) as client:
                    resp = await client.get(self.target_url)
                    if resp.status_code == 200:
                        is_alive = True
                        latency_ms = int((time.perf_counter() - start_time) * 1000)

                        for line in resp.text.splitlines():
                            if line.startswith("loc="):
                                country = line.split("=", 1)[1].strip().upper()
                            elif line.startswith("ip="):
                                egress_ip = line.split("=", 1)[1].strip()

                        if egress_ip and egress_ip == proxy.ip:
                            anonymity = "elite"

            except Exception:
                # Primary failed, attempt fallback check
                try:
                    start_time = time.perf_counter()
                    transport = httpx.AsyncHTTPTransport(proxy=proxy_url)
                    async with httpx.AsyncClient(
                        transport=transport,
                        timeout=self.timeout / 2,
                        headers={"User-Agent": "Mozilla/5.0"}
                    ) as client:
                        resp = await client.get(self.backup_url)
                        if resp.status_code == 200:
                            is_alive = True
                            latency_ms = int((time.perf_counter() - start_time) * 1000)
                            anonymity = "anonymous"
                except Exception:
                    pass

            if not is_alive:
                return False, None, None, None, "unknown", 0, False, "D"

            # 2. Optional IP purity/risk evaluation. Fast mode deliberately
            # skips these extra external requests during the initial purge;
            # reachability above remains mandatory for a proxy to be alive.
            fast_check = settings.FAST_CHECK if self.fast_check is None else self.fast_check
            if fast_check:
                return True, latency_ms, country, anonymity, "unknown", 0, False, "C"

            test_ip = egress_ip or proxy.ip
            ip_type, is_known_proxy, base_score = await self._resolve_ip_type(test_ip)
            google_clean = await self._check_google_clean(proxy_url)

            # Calculate composite fraud score
            score = base_score
            if is_known_proxy:
                score += 20
            if google_clean:
                score = max(0, score - 15)
            else:
                score += 20
            fraud_score = max(0, min(100, score))

            # Determine cleanliness level: A (Ultra), B (Good), C (Datacenter), D (Risk)
            if ip_type in ("residential", "mobile") and google_clean and fraud_score <= 25:
                clean_level = "A"
            elif google_clean or fraud_score <= 45:
                clean_level = "B"
            elif ip_type == "datacenter" and fraud_score <= 75:
                clean_level = "C"
            else:
                clean_level = "D"

            return True, latency_ms, country, anonymity, ip_type, fraud_score, google_clean, clean_level

    async def validate_and_update(self, proxy: ProxyItem) -> bool:
        """Runs check on single proxy and updates database with full metrics."""
        try:
            is_alive, latency, country, anonymity, ip_type, fraud_score, google_clean, clean_level = await asyncio.wait_for(
                self.check_proxy(proxy), timeout=self.timeout + 2
            )
        except asyncio.TimeoutError:
            logger.warning("Proxy check timed out: %s:%s", proxy.ip, proxy.port)
            is_alive, latency, country, anonymity, ip_type, fraud_score, google_clean, clean_level = (
                False, None, None, None, "unknown", 0, False, "D"
            )
        proxy_id = proxy.id
        if not proxy_id:
            proxy_id = await storage.get_id_by_endpoint(proxy.ip, proxy.port, proxy.protocol, proxy.username)
        if proxy_id:
            await storage.update_check_result(
                proxy_id=proxy_id,
                is_alive=is_alive,
                latency=latency,
                country=country,
                anonymity=anonymity,
                ip_type=ip_type,
                fraud_score=fraud_score,
                google_clean=google_clean,
                clean_level=clean_level,
                max_fail_count=settings.MAX_FAIL_COUNT
            )
            if not is_alive:
                await storage.delete_endpoint(proxy.ip, proxy.port, reason="connectivity_or_auth_failed")
        return is_alive

    async def check_batch(self, proxies: list[ProxyItem]) -> dict:
        """Run concurrent checks over a list of proxies."""
        if not proxies:
            return {"total": 0, "alive": 0, "dead": 0}

        tasks = [self.validate_and_update(p) for p in proxies]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        alive_count = sum(1 for r in results if r is True)
        dead_count = sum(1 for r in results if r is False or isinstance(r, Exception))

        return {
            "total": len(proxies),
            "alive": alive_count,
            "dead": dead_count
        }


checker = ProxyChecker()
