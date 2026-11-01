"""Client-IP trust boundary checks for the Nginx -> API proxy hop.

The login-device screen records the IP the API sees.  A client-supplied
``X-Forwarded-For`` must never become that value, so these tests pin down:

* the Nginx templates overwrite (never append) the forwarding headers with the
  TCP peer address;
* the API trusts forwarded headers only from the proxy's fixed ``internal``
  network address, not from ``*`` or a published port;
* the local stack uses a distinct private subnet so it cannot collide with the
  production stack;
* a real Nginx render drops forged headers before they reach the upstream.

The Docker test is an isolated config drill (two throwaway containers on a
throwaway network); it is skipped when Docker or the pinned images are not
available instead of being faked.
"""
from __future__ import annotations

import asyncio
import ipaddress
import json
import os
import re
import shutil
import subprocess
import time
import uuid
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
DEPLOY = ROOT / "deploy"
COMPOSE = DEPLOY / "compose.yaml"
DOCKERFILE = BACKEND / "Dockerfile"
NGINX_TEMPLATE = DEPLOY / "nginx" / "templates" / "default.conf.template"
NGINX_TLS_TEMPLATE = DEPLOY / "nginx" / "templates-tls" / "default.conf.template"
NGINX_MAIN = DEPLOY / "nginx" / "nginx.conf"
LOCAL_SH = DEPLOY / "local.sh"
DEPLOYMENT_DOC = ROOT / "docs" / "DEPLOYMENT.md"

UPSTREAM_IMAGE = "nginx:1.27-alpine"
PROXY_IMAGE = "nginxinc/nginx-unprivileged:1.27-alpine"

COMPOSE_TEST_ENV = {
    "POSTGRES_PASSWORD": "test-only-db-password",
    "REDIS_PASSWORD": "test-only-redis-password",
    "DORMPRINT_SECRET_KEY": "test-only-" + "s" * 48,
    "DORMPRINT_PUBLIC_ORIGIN": "https://print.invalid",
    "DORMPRINT_PUBLIC_HOST": "print.invalid",
    "DORMPRINT_MAIL_ALLOWED_HOSTS": "smtp.invalid",
    "RESTIC_REPOSITORY": "",
    "RESTIC_PASSWORD": "",
}

TRUST_VARS = ("DORMPRINT_INTERNAL_SUBNET", "DORMPRINT_PROXY_IP")


def _clean_env(extra=None):
    env = os.environ.copy()
    env.update(COMPOSE_TEST_ENV)
    for key in TRUST_VARS:
        env.pop(key, None)
    if extra:
        env.update(extra)
    return env


def _compose_config(config_file=COMPOSE, extra_env=None):
    env = _clean_env(extra_env)
    return subprocess.run(
        ["docker", "compose", "-f", str(config_file), "config", "--format", "json"],
        cwd=str(DEPLOY),
        env=env,
        text=True,
        capture_output=True,
        timeout=60,
    )


@pytest.fixture(scope="module")
def compose():
    result = _compose_config()
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def _command_option(command, option):
    for index, token in enumerate(command):
        if token == option:
            return command[index + 1]
    return None


def test_nginx_templates_overwrite_forwarded_headers_with_peer_address():
    for template in (NGINX_TEMPLATE, NGINX_TLS_TEMPLATE):
        text = template.read_text()
        assert "proxy_set_header X-Forwarded-For $remote_addr;" in text, template
        assert "proxy_set_header X-Real-IP $remote_addr;" in text, template
        # Appending would forward a client-controlled chain; nginx variables
        # starting with $http_ would echo arbitrary request headers.
        assert "$proxy_add_x_forwarded_for" not in text, template
        assert "$http_x_forwarded_for" not in text, template
        assert "$http_x_real_ip" not in text, template
        assert text.count("proxy_set_header X-Forwarded-For") == 1, template


def test_compose_api_trusts_only_the_proxy_hop(compose):
    api = compose["services"]["api"]
    command = api["command"]
    assert "--proxy-headers" in command
    trusted = _command_option(command, "--forwarded-allow-ips")
    assert trusted, "api must keep explicit forwarded-allow-ips"
    assert trusted not in {"*", "0.0.0.0/0", "::/0"}

    trusted_ip = ipaddress.ip_address(trusted)
    proxy_networks = compose["services"]["proxy"]["networks"]
    proxy_ip = ipaddress.ip_address(proxy_networks["internal"]["ipv4_address"])
    assert trusted_ip == proxy_ip

    internal = compose["networks"]["internal"]
    assert internal.get("internal") is True
    subnet = ipaddress.ip_network(internal["ipam"]["config"][0]["subnet"])
    assert trusted_ip in subnet

    # Only the proxy may be reachable from outside the private network.
    assert not api.get("ports")
    proxy_ports = compose["services"]["proxy"].get("ports") or []
    assert proxy_ports, "proxy must publish the loopback entry"
    for published in proxy_ports:
        assert published.get("host_ip") in {"127.0.0.1", "::1"}, published


def test_local_stack_uses_a_distinct_private_network_and_trusted_proxy(tmp_path):
    script = LOCAL_SH.read_text()
    lines = [
        line
        for line in script.splitlines()
        if line.startswith(("export DORMPRINT_INTERNAL_SUBNET=", "export DORMPRINT_PROXY_IP="))
    ]
    assert len(lines) == 2, lines
    result = subprocess.run(
        [
            "bash",
            "-c",
            "\n".join(lines) + '\nprintf "%s %s" "$DORMPRINT_INTERNAL_SUBNET" "$DORMPRINT_PROXY_IP"',
        ],
        env={key: value for key, value in os.environ.items() if key not in TRUST_VARS},
        text=True,
        capture_output=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    local_subnet, local_proxy = result.stdout.split()
    local_network = ipaddress.ip_network(local_subnet)
    local_ip = ipaddress.ip_address(local_proxy)
    assert local_ip in local_network
    # Production defaults are 172.30.0.0/24 / 172.30.0.2; a second stack on
    # the same host must not share the subnet or the fixed address.
    assert local_network != ipaddress.ip_network("172.30.0.0/24")
    assert local_ip != ipaddress.ip_address("172.30.0.2")

    env_file = tmp_path / "local.env"
    rendered = subprocess.run(
        ["bash", str(LOCAL_SH), "config", "--format", "json"],
        cwd=str(DEPLOY),
        env=_clean_env({"DORMPRINT_LOCAL_ENV_FILE": str(env_file)}),
        text=True,
        capture_output=True,
        timeout=90,
    )
    assert rendered.returncode == 0, rendered.stderr
    config = json.loads(rendered.stdout)
    command = config["services"]["api"]["command"]
    assert _command_option(command, "--forwarded-allow-ips") == local_proxy
    assert config["services"]["proxy"]["networks"]["internal"]["ipv4_address"] == local_proxy
    assert config["networks"]["internal"]["ipam"]["config"][0]["subnet"] == local_subnet


def test_dockerfile_direct_run_never_trusts_all_proxies():
    text = DOCKERFILE.read_text()
    match = re.search(r'"--forwarded-allow-ips",\s*"([^"]+)"', text)
    assert match, "Dockerfile CMD must set forwarded-allow-ips explicitly"
    assert match.group(1) == "127.0.0.1"
    assert '--forwarded-allow-ips", "*"' not in text


def test_deployment_docs_explain_trusted_proxy_and_unknown_hop():
    text = DEPLOYMENT_DOC.read_text()
    assert "DORMPRINT_PROXY_IP" in text
    assert "set_real_ip_from" in text
    assert "0.0.0.0/0" in text
    assert "未记录" in text


def test_uvicorn_trusts_forwarded_for_only_from_the_proxy_hop():
    from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

    def forwarded_client(trusted, peer, forwarded):
        seen = {}

        async def app(scope, receive, send):
            seen["client"] = scope["client"]

        middleware = ProxyHeadersMiddleware(app, trusted_hosts=trusted)
        scope = {
            "type": "http",
            "http_version": "1.1",
            "method": "GET",
            "scheme": "http",
            "path": "/",
            "raw_path": b"/",
            "query_string": b"",
            "headers": [(b"x-forwarded-for", forwarded.encode("ascii"))],
            "client": (peer, 4321),
            "server": ("api", 8000),
        }
        asyncio.run(middleware(scope, None, None))
        return seen["client"][0]

    # The proxy's own address is trusted, so the single address Nginx writes
    # is accepted as the client.
    assert forwarded_client("172.30.0.2", "172.30.0.2", "203.0.113.9") == "203.0.113.9"
    # Any other peer cannot inject a client address.
    assert forwarded_client("172.30.0.2", "198.51.100.5", "203.0.113.9") == "198.51.100.5"
    # Even if a forged chain reaches uvicorn, a restricted trust list walks
    # from the right and ignores the spoofed leftmost entry.
    assert (
        forwarded_client("172.30.0.2", "172.30.0.2", "6.6.6.6, 203.0.113.9")
        == "203.0.113.9"
    )
    # IPv6 clients are passed through as literals; uvicorn's parser must not
    # split on ':' or drop the address.
    assert forwarded_client("172.30.0.2", "172.30.0.2", "2001:db8::9") == "2001:db8::9"


def _docker_ready() -> bool:
    docker = shutil.which("docker")
    if not docker:
        return False
    version = subprocess.run(
        [docker, "version", "--format", "{{.Server.Version}}"],
        text=True,
        capture_output=True,
        timeout=30,
    )
    if version.returncode != 0:
        return False
    for image in (UPSTREAM_IMAGE, PROXY_IMAGE):
        inspected = subprocess.run(
            [docker, "image", "inspect", image],
            text=True,
            capture_output=True,
            timeout=30,
        )
        if inspected.returncode != 0:
            return False
    return True


@pytest.mark.skipif(not _docker_ready(), reason="docker or pinned proxy images unavailable")
def test_real_nginx_drops_forged_forwarded_headers(tmp_path):
    docker = shutil.which("docker")
    suffix = uuid.uuid4().hex[:8]
    network = f"dormprint-proxytest-{suffix}"
    upstream = f"dormprint-upstream-{suffix}"
    proxy = f"dormprint-edge-{suffix}"
    upstream_conf = tmp_path / "upstream.conf"
    upstream_conf.write_text(
        "server {\n"
        "    listen 8000;\n"
        "    default_type text/plain;\n"
        "    location / {\n"
        '        return 200 "xff=[$http_x_forwarded_for] realip=[$http_x_real_ip]\\n";\n'
        "    }\n"
        "}\n"
    )

    def run(*args, timeout=60, check=True):
        result = subprocess.run(
            [docker, *args],
            text=True,
            capture_output=True,
            timeout=timeout,
        )
        if check and result.returncode != 0:
            raise AssertionError(f"docker {' '.join(args)} failed: {result.stderr}")
        return result

    try:
        run("network", "create", network)
        run(
            "run", "--detach", "--rm", "--name", upstream,
            "--network", network, "--network-alias", "api",
            "-v", f"{upstream_conf}:/etc/nginx/conf.d/default.conf:ro",
            UPSTREAM_IMAGE,
        )
        upstream_ip = run(
            "inspect", "-f",
            "{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}",
            upstream,
        ).stdout.strip()
        assert upstream_ip, "upstream container has no address"

        run(
            "run", "--detach", "--rm", "--name", proxy,
            "--network", network, "--network-alias", "proxy",
            "-e", "DORMPRINT_UPLOAD_LIMIT_MB=220",
            "-e", "DORMPRINT_PUBLIC_HOST=probe.invalid",
            "-v", f"{NGINX_MAIN}:/etc/nginx/nginx.conf:ro",
            "-v", f"{DEPLOY / 'nginx' / 'templates'}:/etc/nginx/templates:ro",
            PROXY_IMAGE,
        )

        deadline = time.monotonic() + 30
        while True:
            ready = run(
                "exec", upstream, "wget", "-q", "-O-", "http://proxy:8080/healthz",
                check=False,
            )
            if ready.returncode == 0 and "ok" in ready.stdout:
                break
            if time.monotonic() > deadline:
                logs = run("logs", "--tail", "20", proxy, check=False).stderr
                raise AssertionError(f"proxy did not become ready: {logs}")
            time.sleep(0.3)

        body = run(
            "exec", upstream, "wget", "-qO-",
            "--header=X-Forwarded-For: 198.51.100.66, 203.0.113.77",
            "--header=X-Real-IP: 198.51.100.66",
            "http://proxy:8080/api/v1/health/live",
        ).stdout
        match = re.search(r"xff=\[([^\]]*)\] realip=\[([^\]]*)\]", body)
        assert match, body
        forwarded, real_ip = match.group(1), match.group(2)
        assert forwarded == upstream_ip, body
        assert real_ip == upstream_ip, body
        assert "," not in forwarded, "forged chain must not be appended"
        assert "198.51.100.66" not in body
        assert "203.0.113.77" not in body
    finally:
        run("rm", "-f", proxy, check=False)
        run("rm", "-f", upstream, check=False)
        run("network", "rm", network, check=False)
