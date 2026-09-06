import pytest
from extractor import (
    parse_proxy_line,
    extract_proxies_from_text,
    extract_proxies_from_html
)


def test_parse_uri_format():
    line = "socks5://otc_user:secret123@103.152.112.5:1080"
    item = parse_proxy_line(line)
    assert item is not None
    assert item.ip == "103.152.112.5"
    assert item.port == 1080
    assert item.protocol == "socks5"
    assert item.username == "otc_user"
    assert item.password == "secret123"
    assert item.to_url() == "socks5://otc_user:secret123@103.152.112.5:1080"


def test_parse_ip_port_user_pass():
    line = "45.76.102.33:8080:admin:pass999"
    item = parse_proxy_line(line, default_protocol="http")
    assert item is not None
    assert item.ip == "45.76.102.33"
    assert item.port == 8080
    assert item.protocol == "http"
    assert item.username == "admin"
    assert item.password == "pass999"
    assert item.to_url() == "http://admin:pass999@45.76.102.33:8080"


def test_parse_user_pass_ip_port():
    line = "vip_buyer:token888@198.51.100.22:1085"
    item = parse_proxy_line(line, default_protocol="socks5")
    assert item is not None
    assert item.ip == "198.51.100.22"
    assert item.port == 1085
    assert item.protocol == "socks5"
    assert item.username == "vip_buyer"
    assert item.password == "token888"


def test_parse_simple_ip_port():
    line = "128.199.200.10:3128"
    item = parse_proxy_line(line)
    assert item is not None
    assert item.ip == "128.199.200.10"
    assert item.port == 3128
    assert item.protocol == "http"
    assert item.username is None


def test_extract_proxies_from_text():
    sample_text = """
    # Proxy list from TG channel
    socks5://user1:pwd1@1.1.1.1:1080
    2.2.2.2:8080:user2:pwd2
    3.3.3.3:3128
    invalid_line_with_no_proxy
    4.4.4.4:8888 SOCKS5 [PH]
    """
    proxies = extract_proxies_from_text(sample_text)
    assert len(proxies) == 4
    ips = [p.ip for p in proxies]
    assert "1.1.1.1" in ips
    assert "2.2.2.2" in ips
    assert "3.3.3.3" in ips
    assert "4.4.4.4" in ips


def test_extract_proxies_from_json():
    json_text = """
    [
        {"ip": "5.5.5.5", "port": 8080, "protocol": "http", "country": "US"},
        {"ip": "6.6.6.6", "port": 1080, "protocol": "socks5", "username": "u", "password": "p", "country": "PH"}
    ]
    """
    proxies = extract_proxies_from_text(json_text)
    assert len(proxies) == 2
    assert proxies[0].country == "US"
    assert proxies[1].country == "PH"
    assert proxies[1].username == "u"


def test_extract_proxies_from_html():
    html = """
    <table>
        <tr>
            <th>IP</th><th>Port</th><th>Country</th><th>Protocol</th><th>Latency</th>
        </tr>
        <tr>
            <td><code>8.220.136.174</code></td>
            <td>1080</td>
            <td>PH</td>
            <td><span>http</span></td>
            <td>1200 ms</td>
        </tr>
        <tr>
            <td><code>149.129.100.5</code></td>
            <td>8080</td>
            <td>US</td>
            <td><span>socks5</span></td>
            <td>450 ms</td>
        </tr>
    </table>
    """
    proxies = extract_proxies_from_html(html, source="test_html")
    assert len(proxies) == 2
    assert proxies[0].ip == "8.220.136.174"
    assert proxies[0].port == 1080
    assert proxies[0].country == "PH"
    assert proxies[0].protocol == "http"
    assert proxies[0].latency == 1200

    assert proxies[1].ip == "149.129.100.5"
    assert proxies[1].port == 8080
    assert proxies[1].country == "US"
    assert proxies[1].protocol == "socks5"
    assert proxies[1].latency == 450


def test_parse_shadowsocks_url():
    # SIP002 format
    url1 = "ss://YWVzLTI1Ni1nY206bXlwYXNzd29yZA==@198.51.100.99:8388#MyTestNode"
    item1 = parse_proxy_line(url1)
    assert item1 is not None
    assert item1.ip == "198.51.100.99"
    assert item1.port == 8388
    assert item1.username == "aes-256-gcm"
    assert item1.password == "mypassword"
    assert item1.protocol == "socks5"

    # Legacy format
    url2 = "ss://YWVzLTI1Ni1nY206bXlwYXNzd29yZEAxOTguNTEuMTAwLjk5OjgzODg=#LegacyNode"
    item2 = parse_proxy_line(url2)
    assert item2 is not None
    assert item2.ip == "198.51.100.99"
    assert item2.port == 8388
    assert item2.username == "aes-256-gcm"
    assert item2.password == "mypassword"
