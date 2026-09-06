import pytest
import httpx
from api import app
from storage import storage
from models import ProxyItem


@pytest.fixture(autouse=True)
async def setup_test_db():
    await storage.init_db()
    # Insert test dummy proxy
    item = ProxyItem(
        ip="123.45.67.89",
        port=1080,
        protocol="socks5",
        username="testuser",
        password="testpwd",
        country="PH",
        latency=150,
        score=100,
        is_active=True
    )
    await storage.upsert_proxy(item)


@pytest.mark.asyncio
async def test_get_stats():
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/v1/stats")
        assert response.status_code == 200
        data = response.json()
        assert "total_proxies" in data
        assert "active_proxies" in data
        assert "by_country" in data


@pytest.mark.asyncio
async def test_get_single_proxy():
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/v1/proxy?country=PH")
        assert response.status_code == 200
        data = response.json()
        assert data["ip"] == "123.45.67.89"
        assert data["port"] == 1080
        assert data["protocol"] == "socks5"
        assert data["username"] == "testuser"
        assert data["password"] == "testpwd"
        assert data["country"] == "PH"


@pytest.mark.asyncio
async def test_get_single_proxy_not_found():
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/v1/proxy?country=ZZ")
        assert response.status_code == 404


@pytest.mark.asyncio
async def test_list_proxies():
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/v1/proxies?country=PH")
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
        assert len(data) >= 1
        assert data[0]["country"] == "PH"


@pytest.mark.asyncio
async def test_import_proxies_text():
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        raw_text = "socks5://newuser:newpwd@111.222.33.44:9999\n55.66.77.88:8080"
        response = await client.post("/api/v1/import", data={"text": raw_text})
        assert response.status_code == 200
        res = response.json()
        assert res["imported"] >= 2


@pytest.mark.asyncio
async def test_web_dashboard():
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/")
        assert response.status_code == 200
        assert "text/html" in response.headers["content-type"]
        assert "多渠道动态 IP 代理池" in response.text


@pytest.mark.asyncio
async def test_get_proxy_purity_filter():
    clean_item = ProxyItem(
        ip="200.200.200.200",
        port=1080,
        protocol="socks5",
        country="PH",
        ip_type="residential",
        clean_level="A",
        google_clean=True,
        is_active=True
    )
    await storage.upsert_proxy(clean_item)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/v1/proxy?country=PH&clean_only=true")
        assert response.status_code == 200
        data = response.json()
        assert data["clean_level"] in ("A", "B")
        assert data["ip"] == "200.200.200.200"
        assert data["ip_type"] == "residential"
