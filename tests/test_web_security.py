"""도메인(efa-quant.com) 전환 후 웹 보안 설정 회귀 테스트.

CORS는 허용 도메인만, 모든 응답에 보안 헤더, backend 포트는 서버 내부에서만,
실제 접속자 IP는 Cloudflare 대역에서 온 값만 인정하는지 설정 파일까지 확인한다.
"""
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.asyncio
async def test_cors_allows_only_the_site_domain(client):
    allowed = await client.get("/api/health", headers={"Origin": "https://efa-quant.com"})
    assert allowed.headers.get("access-control-allow-origin") == "https://efa-quant.com"
    assert "access-control-allow-credentials" not in allowed.headers
    other = await client.get("/api/health", headers={"Origin": "https://evil.example"})
    assert "access-control-allow-origin" not in other.headers
    preflight = await client.options("/api/health", headers={
        "Origin": "https://evil.example", "Access-Control-Request-Method": "POST"})
    assert preflight.status_code == 400


@pytest.mark.asyncio
async def test_security_headers_on_api_and_page(client):
    for path in ("/api/health", "/"):
        response = await client.get(path)
        assert response.headers["strict-transport-security"].startswith("max-age=31536000")
        assert response.headers["x-content-type-options"] == "nosniff"
        assert response.headers["x-frame-options"] == "DENY"
        assert response.headers["referrer-policy"] == "strict-origin-when-cross-origin"


def test_backend_port_is_bound_to_localhost_only():
    compose = (ROOT / "docker-compose.prod.yml").read_text()
    ports = re.findall(r'^\s*-\s*"([^"]*:8000)"', compose, re.M)
    assert ports == ["127.0.0.1:8801:8000"]


def test_uvicorn_trusts_forwarded_headers_only_from_docker_network():
    dockerfile = (ROOT / "Dockerfile").read_text()
    assert '"--proxy-headers", "--forwarded-allow-ips", "127.0.0.1,172.16.0.0/12"' in dockerfile


def test_nginx_takes_client_ip_only_from_cloudflare_and_proxies_locally():
    conf = (ROOT / "infra/nginx/efa-quant.conf").read_text()
    ranges = re.findall(r"^set_real_ip_from (\S+);", conf, re.M)
    assert len(ranges) >= 15 and "0.0.0.0/0" not in ranges and "::/0" not in ranges
    assert "real_ip_header CF-Connecting-IP;" in conf
    assert "proxy_pass http://127.0.0.1:8801;" in conf
    assert "proxy_set_header X-Forwarded-For $remote_addr;" in conf  # 클라이언트가 보낸 XFF는 버린다
    assert "return 301 https://efa-quant.com$request_uri;" in conf  # http·www는 기본 주소로
    www_block = conf[conf.index("server_name www.efa-quant.com;"):]
    assert www_block.index("return 301 https://efa-quant.com$request_uri;") < www_block.index("}")
