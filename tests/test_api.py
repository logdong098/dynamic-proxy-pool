import pytest
import httpx
from api import app
from storage import storage
from models import ProxyItem, ProxyQuery


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
        latency=10,
        score=100,
        is_active=True,
        clean_level="C"
    )
    await storage.upsert_proxy(item)
    p = await storage.get_by_id(1)
    if p:
        await storage.update_check_result(
            proxy_id=p.id,
            is_alive=True,
            latency=10,
            country="PH",
            clean_level="C",
            ip_type="datacenter"
        )


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
        assert data["country"] == "PH"
        assert data["protocol"] == "socks5"
        assert data["is_active"] is True
        assert "ip" in data


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
        assert "实时测活" in response.text
        assert "快捷出池" in response.text
        assert "toast-container" in response.text
        assert "stat-active" in response.text
        assert "stat-clean" in response.text


@pytest.mark.asyncio
async def test_get_proxy_purity_filter():
    clean_item = ProxyItem(
        ip="200.200.200.200",
        port=1080,
        protocol="socks5",
        country="PH",
        latency=5,
        score=100,
        ip_type="residential",
        clean_level="A",
        google_clean=True,
        is_active=True
    )
    await storage.upsert_proxy(clean_item)
    p = await storage.get_proxy(country="PH", ip_type="residential")
    if p:
        await storage.update_check_result(
            proxy_id=p.id,
            is_alive=True,
            latency=5,
            country="PH",
            clean_level="A",
            ip_type="residential"
        )

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/v1/proxy?country=PH&clean_only=true")
        assert response.status_code == 200
        data = response.json()
        assert data["clean_level"] in ("A", "B")
        assert data["ip"] == "200.200.200.200"
        assert data["ip_type"] == "residential"


@pytest.mark.asyncio
async def test_realtime_check_proxy(monkeypatch):
    from checker import checker

    test_item = ProxyItem(
        ip="199.199.199.199",
        port=8888,
        protocol="socks5",
        country="TC",
        score=90
    )
    await storage.upsert_proxy(test_item)
    proxies = await storage.list_proxies(ProxyQuery(country="TC", is_active=None))
    assert len(proxies) > 0
    proxy_id = proxies[0].id

    async def mock_check(proxy):
        return (True, 120, "TC", "anonymous", "residential", 10, True, "A")

    monkeypatch.setattr(checker, "check_proxy", mock_check)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(f"/api/v1/proxy/{proxy_id}/check")
        assert resp.status_code == 200
        data = resp.json()
        assert data["is_alive"] is True
        assert data["latency"] == 120
        assert data["clean_level"] == "A"
        assert data["ip_type"] == "residential"

        resp404 = await client.post("/api/v1/proxy/99999999/check")
        assert resp404.status_code == 404


@pytest.mark.asyncio
async def test_realtime_check_batch(monkeypatch):
    from checker import checker

    test_item1 = ProxyItem(ip="198.198.198.1", port=8081, protocol="http", country="TB")
    test_item2 = ProxyItem(ip="198.198.198.2", port=8082, protocol="http", country="TB")
    await storage.upsert_many([test_item1, test_item2])
    proxies = await storage.list_proxies(ProxyQuery(country="TB", is_active=None))
    assert len(proxies) == 2
    ids = [p.id for p in proxies]

    async def mock_check(proxy):
        return (True, 95, "TB", "anonymous", "residential", 5, True, "A")

    monkeypatch.setattr(checker, "check_proxy", mock_check)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/api/v1/check-batch", json={"proxy_ids": ids})
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 2
        assert data["alive"] == 2


@pytest.mark.asyncio
async def test_list_proxies_filter_clean_level_and_type():
    item_res_a = ProxyItem(ip="197.1.1.1", port=80, protocol="http", country="TT", clean_level="A", ip_type="residential", score=100)
    item_dc_c = ProxyItem(ip="197.1.1.2", port=80, protocol="http", country="TT", clean_level="C", ip_type="datacenter", score=100)
    await storage.upsert_many([item_res_a, item_dc_c])

    # Mark active and update fields
    for ip, lvl, itype in [("197.1.1.1", "A", "residential"), ("197.1.1.2", "C", "datacenter")]:
        p = (await storage.list_proxies(ProxyQuery(country="TT", is_active=None)))
        matching = [x for x in p if x.ip == ip]
        if matching:
            await storage.update_check_result(
                proxy_id=matching[0].id,
                is_alive=True,
                latency=100,
                country="TT",
                clean_level=lvl,
                ip_type=itype
            )

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        # Filter clean_level=A
        resp_a = await client.get("/api/v1/proxies?country=TT&clean_level=A")
        assert resp_a.status_code == 200
        data_a = resp_a.json()
        assert len(data_a) == 1
        assert data_a[0]["clean_level"] == "A"
        assert data_a[0]["ip_type"] == "residential"

        # Filter ip_type=datacenter
        resp_dc = await client.get("/api/v1/proxies?country=TT&ip_type=datacenter")
        assert resp_dc.status_code == 200
        data_dc = resp_dc.json()
        assert len(data_dc) == 1
        assert data_dc[0]["clean_level"] == "C"
        assert data_dc[0]["ip_type"] == "datacenter"


@pytest.mark.asyncio
async def test_import_various_formats_and_visibility():
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        # Test comma, pipe, and at formats
        mixed_text = (
            "180.1.1.1:8080:u1:p1\n"
            "180.1.1.2,8080,u2,p2\n"
            "180.1.1.3|8080|u3|p3\n"
            "180.1.1.4:8080@u4:p4\n"
            "\"180.1.1.5\", 8080"
        )
        resp = await client.post("/api/v1/import", data={"text": mixed_text})
        assert resp.status_code == 200
        data = resp.json()
        assert data["imported"] == 5

        # Check that imported proxies are active in storage
        p_id = await storage.get_id_by_endpoint("180.1.1.1", 8080, "http", "u1")
        assert p_id is not None
        p_item = await storage.get_by_id(p_id)
        assert p_item is not None
        assert p_item.is_active is True


@pytest.mark.asyncio
async def test_import_file_upload():
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        file_bytes = b"181.1.1.1:8080\n181.1.1.2:9090"
        files = {"file": ("test_proxies.txt", file_bytes, "text/plain")}
        resp = await client.post("/api/v1/import", files=files)
        assert resp.status_code == 200
        assert resp.json()["imported"] == 2


@pytest.mark.asyncio
async def test_checker_resolves_proxy_id_without_id(monkeypatch):
    from checker import checker

    item = ProxyItem(
        ip="182.1.1.1",
        port=8080,
        protocol="http",
        country="US"
    )
    await storage.upsert_proxy(item)

    # Item with id=None
    item_no_id = ProxyItem(ip="182.1.1.1", port=8080, protocol="http")
    assert item_no_id.id is None

    async def mock_check(p):
        return (True, 88, "US", "anonymous", "residential", 15, True, "A")

    monkeypatch.setattr(checker, "check_proxy", mock_check)
    res = await checker.validate_and_update(item_no_id)
    assert res is True

    # Verify that database was updated with metrics
    db_item = await storage.get_proxy(country="US")
    assert db_item is not None
    assert db_item.ip == "182.1.1.1"
    assert db_item.latency == 88
    assert db_item.clean_level == "A"
    assert db_item.ip_type == "residential"


@pytest.mark.asyncio
async def test_dashboard_elements():
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/")
        assert resp.status_code == 200
        # Title text
        assert "快捷提取代理" in resp.text
        assert "可用代理节点列表" in resp.text
        # Fallback copy function for HTTP
        assert "fallbackCopy" in resp.text
        # Refresh function call
        assert "manualRefresh(true)" in resp.text

