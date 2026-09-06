import logging
from typing import List, Optional
import httpx

from models import ProxyItem
from config import settings

logger = logging.getLogger("scraper.geonode")


class GeonodeScraper:
    BASE_URL = "https://proxylist.geonode.com/api/proxy-list"

    async def fetch_page(
        self,
        client: httpx.AsyncClient,
        country: Optional[str] = None,
        limit: int = 100,
        page: int = 1
    ) -> List[ProxyItem]:
        params = {
            "limit": limit,
            "page": page,
            "sort_by": "lastChecked",
            "sort_type": "desc"
        }
        if country and country.upper() != "ALL":
            params["country"] = country.upper()

        proxies: List[ProxyItem] = []
        try:
            resp = await client.get(self.BASE_URL, params=params, timeout=15.0)
            if resp.status_code == 200:
                data = resp.json().get("data", [])
                for item in data:
                    ip = item.get("ip")
                    port = item.get("port")
                    if not ip or not port:
                        continue

                    protocols = item.get("protocols", ["http"])
                    protocol = protocols[0].lower() if protocols else "http"
                    cntry = item.get("country", "UNKNOWN").upper()
                    city = item.get("city")
                    latency = int(item.get("latency", 0)) or None
                    anonymity = item.get("anonymityLevel", "unknown")

                    proxies.append(ProxyItem(
                        ip=str(ip).strip(),
                        port=int(port),
                        protocol=protocol,
                        country=cntry,
                        country_name=city,
                        latency=latency,
                        anonymity=anonymity,
                        source=f"geonode:{cntry}"
                    ))
                logger.info(f"Geonode: fetched {len(proxies)} proxies (country: {country or 'all'}, page: {page})")
            else:
                logger.warning(f"Geonode API failed with status {resp.status_code}")
        except Exception as e:
            logger.error(f"Geonode API request error: {e}")

        return proxies

    async def fetch_all(self, countries: List[str] = None) -> List[ProxyItem]:
        """Fetch general and target country proxies from Geonode."""
        results: List[ProxyItem] = []
        target_countries = countries or settings.target_country_list[:5]

        headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko)"
        }

        async with httpx.AsyncClient(headers=headers, follow_redirects=True) as client:
            # 1. Fetch general high-quality page (100 proxies)
            general = await self.fetch_page(client, limit=100, page=1)
            results.extend(general)

            # 2. Fetch specific target countries (e.g. PH, US, HK)
            for c in target_countries:
                c_proxies = await self.fetch_page(client, country=c, limit=50, page=1)
                results.extend(c_proxies)

        # Deduplicate
        unique: List[ProxyItem] = []
        seen = set()
        for p in results:
            key = (p.protocol, p.ip, p.port, p.username or "")
            if key not in seen:
                seen.add(key)
                unique.append(p)

        logger.info(f"Geonode: Total scraped unique proxies: {len(unique)}")
        return unique


geonode_scraper = GeonodeScraper()
