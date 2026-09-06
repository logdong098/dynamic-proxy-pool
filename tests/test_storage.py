import pytest
import pytest_asyncio
import os
import aiosqlite
from models import ProxyItem, ProxyQuery
from storage import ProxyStorage


@pytest_asyncio.fixture
async def temp_storage(tmp_path):
    db_file = tmp_path / "test_proxies.db"
    store = ProxyStorage(db_path=str(db_file))
    await store.init_db()
    return store


@pytest.mark.asyncio
async def test_storage_upsert_and_get(temp_storage):
    store = temp_storage
    proxy = ProxyItem(
        ip="103.152.112.5",
        port=1080,
        protocol="socks5",
        username="user1",
        password="pwd1",
        country="PH",
        latency=190,
        score=100,
        is_active=True
    )
    await store.upsert_proxy(proxy)

    # Fetch by country
    fetched = await store.get_proxy(country="PH")
    assert fetched is not None
    assert fetched.ip == "103.152.112.5"
    assert fetched.port == 1080
    assert fetched.username == "user1"
    assert fetched.country == "PH"

    # Query for nonexistent country
    none_found = await store.get_proxy(country="XX")
    assert none_found is None


@pytest.mark.asyncio
async def test_storage_list_and_filter(temp_storage):
    store = temp_storage
    items = [
        ProxyItem(ip="1.1.1.1", port=80, protocol="http", country="US", latency=100, score=90, is_active=True),
        ProxyItem(ip="2.2.2.2", port=443, protocol="https", country="US", latency=500, score=80, is_active=True),
        ProxyItem(ip="3.3.3.3", port=1080, protocol="socks5", country="PH", latency=200, score=95, is_active=True),
        ProxyItem(ip="4.4.4.4", port=8080, protocol="http", country="PH", latency=800, score=30, is_active=False),
    ]
    await store.upsert_many(items)

    # Filter by country US
    res_us = await store.list_proxies(ProxyQuery(country="US", is_active=True))
    assert len(res_us) == 2

    # Filter by country PH and active
    res_ph = await store.list_proxies(ProxyQuery(country="PH", is_active=True))
    assert len(res_ph) == 1
    assert res_ph[0].ip == "3.3.3.3"

    # Filter with min_score
    res_high_score = await store.list_proxies(ProxyQuery(min_score=90))
    assert len(res_high_score) == 2


@pytest.mark.asyncio
async def test_storage_check_result_update(temp_storage):
    store = temp_storage
    proxy = ProxyItem(
        ip="8.8.8.8",
        port=53,
        protocol="http",
        country="UNKNOWN",
        score=50,
        is_active=False
    )
    await store.upsert_proxy(proxy)
    proxies = await store.list_proxies(ProxyQuery(is_active=None))
    assert len(proxies) > 0
    p_id = proxies[0].id

    # Simulate alive check result with country detected as PH
    await store.update_check_result(
        proxy_id=p_id,
        is_alive=True,
        latency=150,
        country="PH",
        anonymity="elite"
    )

    updated = await store.get_proxy(country="PH", min_score=50)
    assert updated is not None
    assert updated.country == "PH"
    assert updated.latency == 150
    assert updated.is_active is True
    assert updated.score == 60
    assert updated.anonymity == "elite"


@pytest.mark.asyncio
async def test_storage_prune_dead(temp_storage):
    store = temp_storage
    dead_proxy = ProxyItem(
        ip="9.9.9.9",
        port=80,
        protocol="http",
        score=0,
        fail_count=5,
        is_active=False
    )
    await store.upsert_proxy(dead_proxy)

    pruned = await store.prune_dead(max_fail_count=3)
    assert pruned == 1


@pytest.mark.asyncio
async def test_storage_stats(temp_storage):
    store = temp_storage
    items = [
        ProxyItem(ip="1.1.1.1", port=80, protocol="http", country="US", latency=100, is_active=True),
        ProxyItem(ip="2.2.2.2", port=1080, protocol="socks5", country="PH", latency=200, is_active=True),
    ]
    await store.upsert_many(items)

    stats = await store.get_stats()
    assert stats.total_proxies == 2
    assert stats.active_proxies == 2
    assert stats.by_country.get("US") == 1
    assert stats.by_country.get("PH") == 1
    assert stats.by_protocol.get("socks5") == 1
    assert stats.avg_latency_ms == 150.0


@pytest.mark.asyncio
async def test_storage_purity_filtering(temp_storage):
    store = temp_storage
    items = [
        ProxyItem(
            ip="11.11.11.11", port=1080, protocol="socks5", country="PH",
            ip_type="residential", google_clean=True, clean_level="A", is_active=True
        ),
        ProxyItem(
            ip="22.22.22.22", port=8080, protocol="http", country="PH",
            ip_type="datacenter", google_clean=False, clean_level="C", is_active=True
        ),
    ]
    await store.upsert_many(items)

    # clean_only should return the residential Level A proxy
    clean_proxy = await store.get_proxy(country="PH", clean_only=True)
    assert clean_proxy is not None
    assert clean_proxy.ip == "11.11.11.11"
    assert clean_proxy.clean_level == "A"
    assert clean_proxy.ip_type == "residential"

    # ip_type filter for datacenter
    dc_proxy = await store.get_proxy(country="PH", ip_type="datacenter")
    assert dc_proxy is not None
    assert dc_proxy.ip == "22.22.22.22"
    assert dc_proxy.ip_type == "datacenter"
