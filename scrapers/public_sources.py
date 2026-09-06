import asyncio
import logging
from typing import List
import httpx

from models import ProxyItem
from extractor import extract_proxies_from_text

logger = logging.getLogger("scraper.public")

PUBLIC_SOURCES = [
    {
        "url": "https://raw.githubusercontent.com/TheSpeedX/SOCKS-List/master/http.txt",
        "protocol": "http",
        "name": "thespeedx_http"
    },
    {
        "url": "https://raw.githubusercontent.com/TheSpeedX/SOCKS-List/master/socks5.txt",
        "protocol": "socks5",
        "name": "thespeedx_socks5"
    },
    {
        "url": "https://raw.githubusercontent.com/monosans/proxy-list/main/proxies/all.txt",
        "protocol": "http",
        "name": "monosans_all"
    },
    {
        "url": "https://raw.githubusercontent.com/hookzof/socks5_list/master/proxy.txt",
        "protocol": "socks5",
        "name": "hookzof_socks5"
    }
]


class PublicSourcesScraper:
    def __init__(self):
        self.sources = PUBLIC_SOURCES

    async def fetch_source(self, client: httpx.AsyncClient, source_info: dict) -> List[ProxyItem]:
        url = source_info["url"]
        protocol = source_info["protocol"]
        name = source_info["name"]
        try:
            resp = await client.get(url, timeout=15.0)
            if resp.status_code == 200:
                proxies = extract_proxies_from_text(
                    resp.text,
                    default_protocol=protocol,
                    source=f"public:{name}"
                )
                logger.info(f"Public source '{name}': fetched {len(proxies)} proxies")
                # Limit to 300 per source per batch to prevent overwhelming queue
                return proxies[:300]
            else:
                logger.warning(f"Public source '{name}' failed with status {resp.status_code}")
        except Exception as e:
            logger.error(f"Public source '{name}' fetch error: {e}")
        return []

    async def fetch_all(self) -> List[ProxyItem]:
        results: List[ProxyItem] = []
        async with httpx.AsyncClient(follow_redirects=True) as client:
            tasks = [self.fetch_source(client, src) for src in self.sources]
            gathered = await asyncio.gather(*tasks, return_exceptions=True)
            for res in gathered:
                if isinstance(res, list):
                    results.extend(res)

        unique: List[ProxyItem] = []
        seen = set()
        for p in results:
            key = (p.protocol, p.ip, p.port, p.username or "")
            if key not in seen:
                seen.add(key)
                unique.append(p)

        logger.info(f"PublicSources: Total unique proxies scraped: {len(unique)}")
        return unique


public_scraper = PublicSourcesScraper()
