import re
import json
import csv
import io
import base64
from typing import List, Optional
from urllib.parse import urlparse
from bs4 import BeautifulSoup
from models import ProxyItem


IPV4_REGEX = r"(?:(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\.){3}(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)"
PORT_REGEX = r"(?:[1-9][0-9]{0,3}|[1-5][0-9]{4}|6[0-4][0-9]{3}|65[0-4][0-9]{2}|655[0-2][0-9]|6553[0-5])"

# Pattern 1: URI format: protocol://[user:pass@]ip:port
URI_PATTERN = re.compile(
    rf"^(?P<protocol>https?|socks[45])://(?:(?P<user>[^:@\s]+):(?P<pass>[^@\s]+)@)?(?P<ip>{IPV4_REGEX}):(?P<port>{PORT_REGEX})",
    re.IGNORECASE
)

# Pattern 2: IP:PORT:USER:PASS
IP_PORT_USER_PASS_PATTERN = re.compile(
    rf"^(?P<ip>{IPV4_REGEX})[:\s]+(?P<port>{PORT_REGEX})[:\s]+(?P<user>[^:\s]+)[:\s]+(?P<pass>[^\s]+)$"
)

# Pattern 3: USER:PASS@IP:PORT
USER_PASS_IP_PORT_PATTERN = re.compile(
    rf"^(?P<user>[^:@\s]+):(?P<pass>[^@\s]+)@(?P<ip>{IPV4_REGEX})[:\s]+(?P<port>{PORT_REGEX})$"
)

# Pattern 4: IP:PORT (optional protocol at end or beginning)
IP_PORT_PATTERN = re.compile(
    rf"(?P<ip>{IPV4_REGEX})[:\s]+(?P<port>{PORT_REGEX})"
)


def parse_shadowsocks_url(url: str, source: str = "text") -> Optional[ProxyItem]:
    """Parse shadowsocks ss:// URL (both SIP002 and legacy formats)."""
    if not url.lower().startswith("ss://"):
        return None
    try:
        # Strip hash tag and query parameters
        main_part = url[5:].split("#")[0].split("?")[0].strip()
        if "@" in main_part:
            # SIP002 format: ss://base64(method:password)@ip:port
            userinfo_b64, server_part = main_part.split("@", 1)
            if ":" in server_part:
                ip, port_str = server_part.rsplit(":", 1)
                pad = len(userinfo_b64) % 4
                if pad:
                    userinfo_b64 += "=" * (4 - pad)
                decoded_userinfo = base64.urlsafe_b64decode(userinfo_b64.encode()).decode("utf-8", errors="ignore")
                method, password = decoded_userinfo.split(":", 1)
                return ProxyItem(
                    ip=ip,
                    port=int(port_str),
                    protocol="socks5",
                    username=method,
                    password=password,
                    source=source
                )
        else:
            # Legacy format: ss://base64(method:password@ip:port)
            pad = len(main_part) % 4
            if pad:
                main_part += "=" * (4 - pad)
            decoded = base64.urlsafe_b64decode(main_part.encode()).decode("utf-8", errors="ignore")
            if "@" in decoded and ":" in decoded:
                userinfo, server_part = decoded.split("@", 1)
                method, password = userinfo.split(":", 1)
                ip, port_str = server_part.rsplit(":", 1)
                return ProxyItem(
                    ip=ip,
                    port=int(port_str),
                    protocol="socks5",
                    username=method,
                    password=password,
                    source=source
                )
    except Exception:
        pass
    return None


def parse_proxy_line(line: str, default_protocol: str = "http", source: str = "text") -> Optional[ProxyItem]:
    """Parse a single text line into a ProxyItem."""
    line = line.strip()
    if not line or line.startswith("#") or line.startswith("//"):
        return None

    # Check shadowsocks format
    if line.lower().startswith("ss://"):
        ss_item = parse_shadowsocks_url(line, source=source)
        if ss_item:
            return ss_item

    # Check URI format
    uri_match = URI_PATTERN.match(line)
    if uri_match:
        data = uri_match.groupdict()
        return ProxyItem(
            ip=data["ip"],
            port=int(data["port"]),
            protocol=data["protocol"].lower(),
            username=data.get("user"),
            password=data.get("pass"),
            source=source
        )

    # Check IP:PORT:USER:PASS
    match = IP_PORT_USER_PASS_PATTERN.match(line)
    if match:
        data = match.groupdict()
        return ProxyItem(
            ip=data["ip"],
            port=int(data["port"]),
            protocol=default_protocol.lower(),
            username=data["user"],
            password=data["pass"],
            source=source
        )

    # Check USER:PASS@IP:PORT
    match = USER_PASS_IP_PORT_PATTERN.match(line)
    if match:
        data = match.groupdict()
        return ProxyItem(
            ip=data["ip"],
            port=int(data["port"]),
            protocol=default_protocol.lower(),
            username=data["user"],
            password=data["pass"],
            source=source
        )

    # Check simple IP:PORT with potential protocol keywords in the line
    match = IP_PORT_PATTERN.search(line)
    if match:
        ip = match.group("ip")
        port = int(match.group("port"))
        protocol = default_protocol.lower()

        lower_line = line.lower()
        if "socks5" in lower_line:
            protocol = "socks5"
        elif "socks4" in lower_line:
            protocol = "socks4"
        elif "https" in lower_line:
            protocol = "https"
        elif "http" in lower_line:
            protocol = "http"

        # Check for potential 2-letter country code tag (e.g. [PH], US, etc.)
        country = "UNKNOWN"
        country_match = re.search(r"\b([A-Z]{2})\b", line)
        if country_match:
            candidate = country_match.group(1)
            # Filter out common false positives
            if candidate not in ["IP", "OK", "NO", "ON", "TO", "ID", "BY", "MS"]:
                country = candidate

        return ProxyItem(
            ip=ip,
            port=port,
            protocol=protocol,
            country=country,
            source=source
        )

    return None


def extract_proxies_from_text(text: str, default_protocol: str = "http", source: str = "text") -> List[ProxyItem]:
    """Extract all valid proxies from multiline raw text."""
    if not text:
        return []

    proxies: List[ProxyItem] = []
    seen = set()

    # Try parsing as JSON first
    trimmed = text.strip()
    if (trimmed.startswith("[") and trimmed.endswith("]")) or (trimmed.startswith("{") and trimmed.endswith("}")):
        try:
            data = json.loads(trimmed)
            if isinstance(data, dict):
                data = data.get("data", []) or data.get("proxies", []) or [data]
            if isinstance(data, list):
                for item in data:
                    if isinstance(item, dict) and "ip" in item and "port" in item:
                        p = ProxyItem(
                            ip=str(item["ip"]).strip(),
                            port=int(item["port"]),
                            protocol=item.get("protocol", default_protocol).lower(),
                            username=item.get("username") or item.get("user"),
                            password=item.get("password") or item.get("pwd") or item.get("pass"),
                            country=item.get("country", "UNKNOWN").upper(),
                            source=source
                        )
                        key = (p.protocol, p.ip, p.port, p.username or "")
                        if key not in seen:
                            seen.add(key)
                            proxies.append(p)
                if proxies:
                    return proxies
        except Exception:
            pass

    # Line-by-line parsing
    for line in text.splitlines():
        item = parse_proxy_line(line, default_protocol=default_protocol, source=source)
        if item:
            key = (item.protocol, item.ip, item.port, item.username or "")
            if key not in seen:
                seen.add(key)
                proxies.append(item)

    return proxies


def extract_proxies_from_file(file_path: str, default_protocol: str = "http", source: str = "file") -> List[ProxyItem]:
    """Read a local file and extract all proxy entries."""
    try:
        with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
            content = f.read()
        return extract_proxies_from_text(content, default_protocol=default_protocol, source=source or f"file:{file_path}")
    except Exception:
        return []


def extract_proxies_from_html(html_content: str, source: str = "html") -> List[ProxyItem]:
    """Parse HTML tables containing proxies (specifically handles maskproxy.io and similar sites)."""
    proxies: List[ProxyItem] = []
    seen = set()
    soup = BeautifulSoup(html_content, "lxml")

    # Find table rows
    rows = soup.find_all("tr")
    for row in rows:
        cols = row.find_all(["td", "th"])
        if len(cols) < 2:
            continue
        text_cols = [c.get_text(strip=True) for c in cols]

        # Check first column for IP
        ip_match = re.search(IPV4_REGEX, text_cols[0])
        if not ip_match:
            continue
        ip = ip_match.group(0)

        # Check second column for Port
        port_match = re.search(PORT_REGEX, text_cols[1])
        if not port_match:
            continue
        port = int(port_match.group(0))

        # Defaults
        country = "UNKNOWN"
        protocol = "http"
        anonymity = "unknown"
        latency = None

        # Analyze subsequent columns
        full_row_text = " ".join(text_cols).lower()
        if "socks5" in full_row_text:
            protocol = "socks5"
        elif "socks4" in full_row_text:
            protocol = "socks4"
        elif "https" in full_row_text:
            protocol = "https"
        elif "http" in full_row_text:
            protocol = "http"

        for col_text in text_cols[2:]:
            # 2-letter uppercase country code
            if len(col_text) == 2 and col_text.isupper():
                country = col_text
            # Latency (e.g. 5773 ms)
            lat_match = re.search(r"(\d+)\s*ms", col_text)
            if lat_match:
                try:
                    latency = int(lat_match.group(1))
                except ValueError:
                    pass
            # Anonymity
            if col_text.lower() in ["transparent", "anonymous", "elite"]:
                anonymity = col_text.lower()

        p = ProxyItem(
            ip=ip,
            port=port,
            protocol=protocol,
            country=country,
            anonymity=anonymity,
            latency=latency,
            source=source
        )
        key = (p.protocol, p.ip, p.port, p.username or "")
        if key not in seen:
            seen.add(key)
            proxies.append(p)

    return proxies
