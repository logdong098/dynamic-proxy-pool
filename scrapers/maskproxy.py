import asyncio
import logging
from typing import List
import httpx

from models import ProxyItem
from config import settings
from extractor import extract_proxies_from_html

logger = logging.getLogger("scraper.maskproxy")


class MaskProxyScraper:
    BASE_URL = "https://maskproxy.io/free/"

    def __init__(self):
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9,zh-CN;q=0.8,zh;q=0.7",
        }

    async def fetch_country(self, client: httpx.AsyncClient, country: str) -> List[ProxyItem]:
        """Fetch free proxies for a specific country."""
        url = f"{self.BASE_URL}?country={country}&protocol=&anonymous=&limit=50"
        try:
            resp = await client.get(url, timeout=12.0)
            if resp.status_code == 200:
                proxies = extract_proxies_from_html(resp.text, source=f"maskproxy.io:{country}")
                for p in proxies:
                    if p.country == "UNKNOWN" or not p.country:
                        p.country = country.upper()
                logger.info(f"MaskProxy: successfully scraped {len(proxies)} proxies for country {country}")
                return proxies
            else:
                logger.warning(f"MaskProxy: returned status {resp.status_code} for {country}")
        except Exception as e:
            logger.error(f"MaskProxy: error scraping {country}: {e}")
        return []

    async def fetch_all(self, countries: List[str] = None) -> List[ProxyItem]:
        """Fetch proxies for all configured countries."""
        target_countries = countries or settings.target_country_list[:6]  # query top countries
        results: List[ProxyItem] = []

        async with httpx.AsyncClient(headers=self.headers, follow_redirects=True) as client:
            # Query global/general page first
            try:
                resp = await client.get(f"{self.BASE_URL}?limit=50", timeout=12.0)
                if resp.status_code == 200:
                    general_proxies = extract_proxies_from_html(resp.text, source="maskproxy.io:general")
                    results.extend(general_proxies)
            except Exception as e:
                logger.error(f"MaskProxy general fetch error: {e}")

            # Query each target country
            tasks = [self.fetch_country(client, country) for country in target_countries]
            country_results = await asyncio.gather(*tasks, return_exceptions=True)
            for res in country_results:
                if isinstance(res, list):
                    results.extend(res)

        # Deduplicate
        unique: List[ProxyItem] = []
        seen = set()
        for p in results:
            key = (p.protocol, p.ip, p.port, p.username or "")
            if key not in seen:
                seen.add(key)
                unique.append(p)

        logger.info(f"MaskProxy: Total scraped unique proxies: {len(unique)}")
        return unique


maskproxy_scraper = MaskProxyScraper()
