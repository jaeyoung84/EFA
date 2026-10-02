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
    assert conf.count("proxy_pass http://127.0.0.1:8801;") == 4
    assert "proxy_set_header X-Forwarded-For $remote_addr;" in conf  # 클라이언트가 보낸 XFF는 버린다
    assert "return 301 https://efa-quant.com$request_uri;" in conf  # http·www는 기본 주소로
    www_block = conf[conf.index("server_name www.efa-quant.com;"):]
    assert www_block.index("return 301 https://efa-quant.com$request_uri;") < www_block.index("}")


def test_nginx_drops_unknown_host_names():
    conf = (ROOT / "infra/nginx/efa-quant.conf").read_text()
    assert re.search(r"listen 80 default_server;\s*server_name _;\s*return 444;", conf)
    assert re.search(r"listen 443 ssl default_server;\s*server_name _;\s*ssl_reject_handshake on;", conf)
    assert conf.count("default_server") == 2 and "server_tokens off;" in conf


def test_backend_container_has_a_memory_cap():
    compose = (ROOT / "docker-compose.prod.yml").read_text()
    backend = compose[compose.index("  backend:"):compose.index("  mongo:")]
    assert re.search(r"^\s*mem_limit: 1g$", backend, re.M) and "restart: unless-stopped" in backend


@pytest.mark.asyncio
async def test_content_security_policy_blocks_inline_scripts(client):
    csp = (await client.get("/")).headers["content-security-policy"]
    directives = dict((part.strip().split(" ", 1) + [""])[:2] for part in csp.split(";"))
    assert directives["script-src"] == "'self' https://cdn.jsdelivr.net/npm/apexcharts@3.54.0/ https://static.cloudflareinsights.com"
    assert "unsafe-inline" not in directives["script-src"] and "unsafe-eval" not in csp
    assert directives["frame-ancestors"] == "'none'" and directives["object-src"] == "'none'"
    assert directives["connect-src"] == "'self' https://cloudflareinsights.com"
    assert "camera=()" in (await client.get("/api/health")).headers["permissions-policy"]
    assert "content-security-policy" not in (await client.get("/docs")).headers


def test_frontend_has_no_inline_handlers_or_scripts():
    root = ROOT / "app/frontend"
    for path in [root / "index.html", root / "privacy.html", *sorted((root / "js").rglob("*.js"))]:
        text = path.read_text()
        assert not re.search(r"\son(click|load|error|change|submit|input|mouseover)\s*=", text), path
        assert "javascript:" not in text, path
        if path.suffix == ".html":
            assert not re.search(r"<script(?![^>]*\bsrc=)[^>]*>", text), path


def test_nginx_rate_limits_login_kis_and_api():
    conf = (ROOT / "infra/nginx/efa-quant.conf").read_text()
    assert "limit_req_zone $binary_remote_addr zone=efa_login:10m rate=10r/m;" in conf
    assert "limit_req_zone $binary_remote_addr zone=efa_kis:10m rate=3r/s;" in conf
    assert "limit_req_zone $binary_remote_addr zone=efa_api:10m rate=20r/s;" in conf
    assert "limit_req_status 429;" in conf
    login = conf[conf.index("location ~ ^/api/auth/(login|signup|naver/)"):]
    assert login.index("limit_req zone=efa_login") < login.index("}")
    kis = conf[conf.index("location ~ ^/api/(market/live-quotes|market/stock-risk|portfolio/valuation)$"):]
    assert kis.index("limit_req zone=efa_kis") < kis.index("}")
    assert re.search(r"location /api/ \{\s*limit_req zone=efa_api burst=120 nodelay;", conf)
    assert re.search(r"location / \{\s*proxy_pass http://127.0.0.1:8801;\s*\}", conf)   # 화면 파일은 제한 없음
