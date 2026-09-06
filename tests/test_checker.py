import pytest
from unittest.mock import AsyncMock, patch
from checker import ProxyChecker
from models import ProxyItem


@pytest.mark.asyncio
async def test_checker_parse_cloudflare_trace_and_purity():
    trace_text = """fl=123f45
h=cloudflare.com
ip=103.152.112.5
ts=1725600000
visit_scheme=https
uag=Mozilla/5.0
colo=MNL
sliver=none
http=http/2
loc=PH
tls=TLSv1.3
sni=plaintext
warp=off
gateway=off
rbi=off
kex=X25519
"""
    checker = ProxyChecker()
    proxy = ProxyItem(
        ip="103.152.112.5",
        port=1080,
        protocol="socks5",
        username="usr",
        password="pwd"
    )

    mock_resp = AsyncMock()
    mock_resp.status_code = 200
    mock_resp.text = trace_text

    with patch("httpx.AsyncClient.get", return_value=mock_resp), \
         patch.object(checker, "_resolve_ip_type", return_value=("residential", False, 10)), \
         patch.object(checker, "_check_google_clean", return_value=True):
        is_alive, latency, country, anonymity, ip_type, fraud_score, google_clean, clean_level = await checker.check_proxy(proxy)
        assert is_alive is True
        assert country == "PH"
        assert anonymity == "elite"
        assert latency is not None
        assert ip_type == "residential"
        assert google_clean is True
        assert clean_level == "A"


@pytest.mark.asyncio
async def test_checker_failure_handling():
    checker = ProxyChecker(timeout=0.1)
    proxy = ProxyItem(
        ip="192.0.2.1",
        port=9999,
        protocol="http"
    )
    is_alive, latency, country, anonymity, ip_type, fraud_score, google_clean, clean_level = await checker.check_proxy(proxy)
    assert is_alive is False
    assert latency is None
    assert country is None
    assert clean_level == "D"
