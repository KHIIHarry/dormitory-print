"""Production packaging, same-origin proxy and service-isolation checks.

These are deployment acceptance checks, not a substitute for a real server
drill.  They run the real Compose config renderer and the real backup scripts;
anything requiring a remote backup target or real TLS certificate is reported
as unavailable instead of being faked.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
DEPLOY = ROOT / "deploy"
COMPOSE = DEPLOY / "compose.yaml"
DOCKERFILE = BACKEND / "Dockerfile"
DOCKERIGNORE = BACKEND / ".dockerignore"
ENV_EXAMPLE = BACKEND / ".env.example"
DEPLOY_ENV_EXAMPLE = DEPLOY / ".env.example"
NGINX_TEMPLATE = DEPLOY / "nginx" / "templates" / "default.conf.template"
NGINX_TLS_TEMPLATE = DEPLOY / "nginx" / "templates-tls" / "default.conf.template"
BACKUP = DEPLOY / "backup"

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


def _compose_config(extra_env=None, drop_env=()):
    env = os.environ.copy()
    env.update(COMPOSE_TEST_ENV)
    if extra_env:
        env.update(extra_env)
    for key in drop_env:
        env.pop(key, None)
    return subprocess.run(
        ["docker", "compose", "-f", str(COMPOSE), "config", "--format", "json"],
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


def _mount(service, target):
    for volume in service.get("volumes", []) or []:
        if volume.get("target") == target:
            return volume
    return None


def _networks(service):
    networks = service.get("networks") or {}
    return set(networks)


def _text(path):
    assert path.exists(), f"missing required file: {path}"
    return path.read_text(encoding="utf-8")


def test_dockerfile_is_rootless_and_has_real_office_pdf_heif_font_stack():
    text = _text(DOCKERFILE)
    lowered = text.lower()
    assert "from python:3.12" in lowered
    assert "libreoffice-writer" in lowered
    assert "libreoffice-calc" in lowered
    assert "libreoffice-impress" in lowered
    assert "ghostscript" in lowered
    assert "fonts-noto-cjk" in lowered
    assert "libheif" in lowered
    assert "pip install" in lowered and "requirements.txt" in lowered
    assert "--no-cache-dir" in lowered
    users = [line.split(None, 1)[1].strip() for line in text.splitlines()
             if line.strip().lower().startswith("user ")]
    assert users, "Dockerfile must switch to a non-root runtime user"
    assert users[-1] not in ("root", "0", "0:0")
    assert "healthcheck" in lowered
    assert "app.main:create_app" in text and "--factory" in text
    assert "DORMPRINT_SECRET_KEY" not in text
    assert "password" not in lowered or "dormprint" in lowered  # no literal secrets

    ignore = _text(DOCKERIGNORE)
    for pattern in (".venv", ".data", ".env", "tests", "__pycache__", ".git"):
        assert pattern in ignore, f".dockerignore must exclude {pattern}"


def test_env_examples_are_placeholders_only_and_https(monkeypatch):
    for path in (ENV_EXAMPLE, DEPLOY_ENV_EXAMPLE):
        text = _text(path)
        lines = [line.strip() for line in text.splitlines()
                 if line.strip() and not line.strip().startswith("#")]
        for line in lines:
            key = line.split("=", 1)[0]
            assert key.startswith(("DORMPRINT_", "POSTGRES_", "REDIS_", "RESTIC_",
                                   "AWS_", "BACKUP_", "PG")), (path, key)
        assert "DORMPRINT_SECRET_KEY=" in text
        assert "DORMPRINT_PUBLIC_ORIGIN=https://" in text
        assert "DORMPRINT_ENVIRONMENT=production" in text
        # No shipped default administrator password or fake seed data.
        assert "INITIAL_ADMIN_PASSWORD" not in text
        assert "admin123" not in text.lower()
        assert "changeme" not in text.lower()
        assert "DORMPRINT_MAIL_ALLOWED_HOSTS" in text
    backend_env = _text(ENV_EXAMPLE)
    assert "DORMPRINT_ALLOW_INSECURE_SMTP=false" in backend_env
    assert "DORMPRINT_ALLOW_INTERNAL_SMTP=false" in backend_env
    assert "DORMPRINT_STORAGE_ROOT=" in backend_env


def test_compose_has_required_services_and_never_publishes_data_services(compose):
    services = compose["services"]
    required = {"proxy", "api", "file-worker", "business-worker", "beat",
                "postgres", "redis", "backup"}
    assert required <= set(services)
    for name in ("postgres", "redis", "api", "file-worker", "business-worker",
                 "beat", "backup"):
        assert not services[name].get("ports"), f"{name} must not publish ports"
    assert services["proxy"].get("ports"), "proxy is the only public entry point"
    published = json.dumps(services["proxy"].get("ports"))
    assert "127.0.0.1" in published, "plaintext proxy must bind loopback until TLS is configured"
    assert "postgres" in services["postgres"]["image"]
    assert ":16" in services["postgres"]["image"]


def test_compose_worker_isolation_and_office_sandbox(compose):
    services = compose["services"]
    files = services["file-worker"]
    business = services["business-worker"]
    api = services["api"]

    assert _networks(files) == {"internal"}
    assert {"internal", "egress"} <= _networks(business)
    assert {"internal", "egress"} <= _networks(api)
    assert _networks(services["postgres"]) == {"internal"}
    assert _networks(services["redis"]) == {"internal"}
    assert _networks(services["beat"]) == {"internal"}

    command = files.get("command")
    command_text = " ".join(command) if isinstance(command, list) else str(command)
    assert "-Q" in command_text and "files" in command_text
    assert "app.jobs:celery_app" in command_text
    assert "-Q" in command_text and "business" not in command_text
    assert files.get("read_only") is True
    assert files.get("user") and not str(files["user"]).startswith("0")
    assert files.get("cap_drop") == ["ALL"]
    assert files.get("tmpfs")
    assert files.get("pids_limit")
    assert files.get("mem_limit")
    assert files.get("cpus")
    assert int(str(files.get("pids_limit"))) <= 1024

    business_text = " ".join(business["command"]) if isinstance(business["command"], list) else str(business["command"])
    assert "-Q" in business_text and "business" in business_text
    assert "files" not in business_text
    assert business.get("read_only") is True
    assert "DORMPRINT_MAIL_ALLOWED_HOSTS" in business.get("environment", {})

    api_text = " ".join(api["command"]) if isinstance(api["command"], list) else str(api["command"])
    assert "app.main:create_app" in api_text and "--factory" in api_text
    assert "celery" not in api_text.lower()
    assert "DORMPRINT_MAIL_ALLOWED_HOSTS" in api.get("environment", {})
    assert services["beat"]["command"]
    beat_text = " ".join(services["beat"]["command"]) if isinstance(services["beat"]["command"], list) else str(services["beat"]["command"])
    assert "beat" in beat_text and "app.jobs:celery_app" in beat_text


def test_private_storage_volume_is_shared_only_with_services_that_need_it(compose):
    services = compose["services"]
    for name in ("api", "file-worker", "business-worker", "backup"):
        volume = _mount(services[name], "/var/lib/dormprint")
        if volume is None:
            # Backup may mount a deliberately read-only copy at a different path.
            volume = _mount(services[name], "/storage")
        assert volume, f"{name} needs the private file volume"
    for name in ("proxy", "postgres", "redis", "beat", "volume-init"):
        assert _mount(services[name], "/var/lib/dormprint") is None
        assert _mount(services[name], "/storage") is None
    assert _mount(services["proxy"], "/var/lib/dormprint") is None
    proxy_mounts = json.dumps(services["proxy"].get("volumes"))
    assert "dist" in proxy_mounts
    assert "storage" not in proxy_mounts.lower()


def test_compose_fails_closed_without_production_secret_and_origin():
    for key in ("DORMPRINT_SECRET_KEY", "DORMPRINT_PUBLIC_ORIGIN", "POSTGRES_PASSWORD"):
        result = _compose_config(drop_env=(key,))
        assert result.returncode != 0, f"compose must refuse to render without {key}"


def test_compose_has_one_shot_migration_and_admin_init_without_password_defaults():
    services = _compose_config().returncode
    assert services == 0
    text = _text(COMPOSE)
    assert "python -m app.cli migrate" in text or "app.cli\", \"migrate" in text or "app.cli,migrate" in text
    assert "init-admin" in text
    assert "doctor" in text
    assert "INITIAL_ADMIN_PASSWORD" not in text
    assert "seed" not in text.lower()
    assert "demo" not in text.lower()


def test_nginx_same_origin_spa_private_api_and_binary_range():
    template = _text(NGINX_TEMPLATE)
    assert "location /api/" in template
    assert "proxy_pass http://api:8000" in template
    assert 'Cache-Control "no-store"' in template
    assert "client_max_body_size" in template
    assert "${DORMPRINT_UPLOAD_LIMIT_MB}m" in template
    assert "Range $http_range" in template
    assert "If-Range $http_if_range" in template
    for spa in ("/u/", "/manage/", "/courier/"):
        assert f"location {spa}" in template
        assert "try_files $uri" in template and "/index.html" in template
    assert "root /usr/share/nginx/html" in template
    assert "alias" not in template.lower()
    assert "/var/lib/dormprint" not in template
    assert "proxy_buffering off" in template

    tls = _text(NGINX_TLS_TEMPLATE)
    assert "listen 443 ssl" in tls
    assert "ssl_certificate " in tls
    assert "Strict-Transport-Security" in tls
    assert "TLSv1.2" in tls and "TLSv1.3" in tls
    assert "return 301 https://" in tls


def test_nginx_main_config_runs_unprivileged_and_writable_dirs_are_tmpfs():
    main = _text(DEPLOY / "nginx" / "nginx.conf")
    assert "pid /tmp/nginx.pid" in main
    assert "client_body_temp_path /tmp/" in main
    assert "server_tokens off" in main
    assert "include /etc/nginx/conf.d/*.conf" in main
    compose = json.loads(_compose_config().stdout)
    proxy = compose["services"]["proxy"]
    assert proxy.get("read_only") is True
    assert proxy.get("user") and not str(proxy["user"]).startswith("0")


def test_backup_service_schedules_real_hourly_and_daily_jobs(compose):
    backup = compose["services"]["backup"]
    command = " ".join(backup["command"]) if isinstance(backup["command"], list) else str(backup["command"])
    assert "backup" in command.lower()
    env = backup.get("environment", {})
    assert str(env.get("BACKUP_DAILY_UTC_HOUR", "2")) == "2"
    assert "DORMPRINT_SECRET_KEY" not in env, "backup must not hold the application master key"
    assert _mount(backup, "/state"), "backup needs a persistent state/cache volume"
    assert backup.get("read_only") is True


def test_backup_scripts_are_runnable_and_tombstone_restore_is_explicit():
    for name in ("backup.sh", "restore.sh", "backup-scheduler.sh", "check_capacity.sh"):
        path = BACKUP / name
        assert path.exists(), f"missing backup script {name}"
        result = subprocess.run(["bash", "-n", str(path)], capture_output=True, text=True)
        assert result.returncode == 0, (name, result.stderr)
    for name in ("verify_manifest.py", "apply_tombstones.py", "post_restore.sql"):
        assert (BACKUP / name).exists(), f"missing restore component {name}"
    post = _text(BACKUP / "post_restore.sql")
    assert "auth_sessions" in post and "revoked_at" in post
    assert "order_access_grants" in post and "revoked_at" in post
    assert "one_time_tokens" in post
    assert "session_version" in post
    assert "idempotency_records" in post
    restore = _text(BACKUP / "restore.sh")
    assert "post_restore.sql" in restore
    assert "apply_tombstones.py" in restore
    assert "verify_manifest.py" in restore
    assert "DORMPRINT_SECRET_KEY" in restore
    backup = _text(BACKUP / "backup.sh")
    assert "pg_dump" in backup
    assert "restic backup" in backup
    assert "manifest" in backup.lower()
    assert "--group-by host --tag dormprint" in backup
    assert "--keep-hourly 168" in backup
    assert "--keep-daily 30" in backup
    assert "BACKUP_UNAVAILABLE" in backup
    assert "/storage" in backup
    # A database-only backup is not acceptable.
    assert "artifacts" in backup or "normalized" in backup


def test_unconfigured_remote_backup_fails_honestly(tmp_path):
    env = os.environ.copy()
    env.update({
        "RESTIC_REPOSITORY": "",
        "RESTIC_PASSWORD": "",
        "RESTIC_PASSWORD_FILE": "",
        "PGHOST": "127.0.0.1",
        "PGDATABASE": "dormprint",
        "PGUSER": "dormprint",
        "PGPASSWORD": "local-test-only",
        "DORMPRINT_STORAGE_ROOT": str(tmp_path / "storage"),
        "BACKUP_STATE_DIR": str(tmp_path / "state"),
    })
    (tmp_path / "storage").mkdir()
    result = subprocess.run(
        ["bash", str(BACKUP / "backup.sh"), "--mode", "hourly"],
        env=env, text=True, capture_output=True, timeout=30,
    )
    output = result.stdout + result.stderr
    assert result.returncode != 0
    assert "BACKUP_UNAVAILABLE" in output
    assert "BACKUP_OK" not in output
    assert not (tmp_path / "state" / "last-success.json").exists()


def test_capacity_alert_is_a_real_command(tmp_path):
    script = BACKUP / "check_capacity.sh"
    env = os.environ.copy()
    env.update({
        "DORMPRINT_CAPACITY_ALERT_PERCENT": "0",
        "DORMPRINT_STORAGE_ROOT": str(tmp_path),
    })
    result = subprocess.run(["bash", str(script)], env=env, text=True, capture_output=True)
    assert result.returncode != 0
    assert "CAPACITY_ALERT" in result.stdout + result.stderr
    env["DORMPRINT_CAPACITY_ALERT_PERCENT"] = "100"
    result = subprocess.run(["bash", str(script)], env=env, text=True, capture_output=True)
    assert result.returncode == 0
    assert "CAPACITY_OK" in result.stdout


def test_deployment_docs_do_not_claim_unprovided_infrastructure():
    for name in ("DEPLOYMENT.md", "BACKUP-RESTORE.md"):
        text = _text(ROOT / "docs" / name)
        assert "DORMPRINT_SECRET_KEY" in text
        assert "https://" in text
    deployment = _text(ROOT / "docs" / "DEPLOYMENT.md")
    assert "RPO" in deployment and "RTO" in deployment
    assert "未提供" in deployment or "not provided" in deployment.lower()
    backup_doc = _text(ROOT / "docs" / "BACKUP-RESTORE.md")
    assert "restic" in backup_doc.lower()
    assert "tombstone" in backup_doc.lower() or "墓碑" in backup_doc
    assert "4" in backup_doc and "小时" in backup_doc or "hour" in backup_doc.lower()
