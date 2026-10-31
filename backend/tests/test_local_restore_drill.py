"""Bounded local backup/restore drill against the dedicated drill PostgreSQL.

Only ``dormprint-drill-pg`` (127.0.0.1:55433) and the already-built
``dormprint-backup:prod`` image are used.  Docker named volumes keep the
restic data, state and private storage isolated and disposable; the deployment
scripts are bind-mounted read-only from ``deploy/backup`` so the latest code is
exercised.  No test PostgreSQL (55432), ``date-*``/``userdate-*`` container,
production database name or repository secret is touched.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import timedelta
from pathlib import Path
from urllib.parse import quote

import pytest

pytest.importorskip("fitz")
pytest.importorskip("sqlalchemy")

import fitz
from sqlalchemy import create_engine, delete, select, text
from sqlalchemy.orm import Session

from app.models import (
    Account,
    AnonymousLedger,
    Artifact,
    Asset,
    AuthSession,
    DeliverySlot,
    Location,
    Order,
    Principal,
    Preparation,
    Quote,
    Staff,
    TrashEntry,
    utcnow,
)

REPO = Path(__file__).resolve().parents[2]
BACKUP_SCRIPTS = REPO / "deploy" / "backup"
BACKUP_IMAGE = os.environ.get("DORMPRINT_DRILL_BACKUP_IMAGE", "dormprint-backup:prod")
DRILL_CONTAINER = os.environ.get("DORMPRINT_DRILL_PG_CONTAINER", "dormprint-drill-pg")
DRILL_PORT = os.environ.get("DORMPRINT_DRILL_PG_PORT", "55433")
DRILL_HOST = os.environ.get("DORMPRINT_DRILL_PG_HOST", "127.0.0.1")
DRILL_DOCKER_HOST = "host.docker.internal"


def _run(command: list[str], *, env: dict[str, str] | None = None,
         timeout: int = 420, check: bool = True) -> subprocess.CompletedProcess:
    result = subprocess.run(command, env=env, capture_output=True, text=True, timeout=timeout)
    if check and result.returncode != 0:
        raise AssertionError(
            "command failed: " + " ".join(command[:3]) +
            f" (rc={result.returncode})\nstdout:\n{result.stdout[-4000:]}\nstderr:\n{result.stderr[-4000:]}"
        )
    return result


def _docker_env(container: str) -> dict[str, str]:
    result = _run(["docker", "inspect", container, "--format", "{{json .Config.Env}}"])
    pairs = json.loads(result.stdout)
    return dict(item.split("=", 1) for item in pairs if "=" in item)


def _credentials() -> tuple[str, str]:
    env = _docker_env(DRILL_CONTAINER)
    return env.get("POSTGRES_USER", "dormprint"), env["POSTGRES_PASSWORD"]


def _psql(database: str, sql: str) -> subprocess.CompletedProcess:
    return _run([
        "docker", "exec", DRILL_CONTAINER,
        "psql", "-U", "dormprint", "-d", database, "-v", "ON_ERROR_STOP=1", "-Atc", sql,
    ])


def _create_database(name: str) -> None:
    assert re.fullmatch(r"dormprint_(drill|restore)_[0-9a-f]{10}", name)
    _psql("postgres", f'CREATE DATABASE "{name}"')


def _drop_database(name: str) -> None:
    assert re.fullmatch(r"dormprint_(drill|restore)_[0-9a-f]{10}", name)
    _psql("postgres", f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


def _host_url(database: str, password: str) -> str:
    return "postgresql+psycopg://dormprint:" + quote(password, safe="") + \
           f"@{DRILL_HOST}:{DRILL_PORT}/{database}"


def _make_pdf(label: str) -> bytes:
    document = fitz.open()
    page = document.new_page()
    page.insert_text((72, 72), f"DormPrint local restore drill: {label}")
    page.draw_rect(fitz.Rect(60, 90, 520, 140), color=(0.2, 0.2, 0.2))
    payload = document.tobytes()
    document.close()
    return payload


def _seed(engine, storage: Path) -> dict:
    now = utcnow()
    payloads = {"purge": _make_pdf("purge-me"), "keep": _make_pdf("keep-me")}
    records: dict = {}
    with Session(engine) as session:
        principal = Principal()
        session.add(principal)
        session.flush()
        account = Account(
            principal_id=principal.id,
            email="drill-admin@example.test",
            password_hash="not-a-real-password",
            status="ACTIVE",
            verified_at=now,
            display_name="Drill Admin",
        )
        session.add(account)
        session.flush()
        principal.account_id = account.id
        staff = Staff(
            account_id=account.id,
            is_super_admin=True,
            username="drill-admin",
            name="Drill Admin",
            role="ADMIN",
            enabled=True,
            status="ACTIVE",
            capabilities=["*"],
            must_change_password=False,
            password_hash="",
        )
        session.add(staff)
        session.flush()
        auth = AuthSession(
            kind="CUSTOMER",
            token_hash=hashlib.sha256(secrets.token_bytes(32)).hexdigest(),
            principal_id=principal.id,
            account_id=account.id,
            session_version=account.session_version,
            expires_at=now + timedelta(days=1),
        )
        session.add(auth)
        location = Location(name="Drill Building", x=0.2, y=0.3)
        session.add(location)
        session.flush()
        slot = DeliverySlot(
            date=now.date().isoformat(),
            name="Drill Slot",
            starts_at=now + timedelta(hours=1),
            ends_at=now + timedelta(hours=2),
            capacity=2,
        )
        session.add(slot)
        session.flush()

        for kind, payload in payloads.items():
            key = f"final/{kind}-{secrets.token_hex(6)}.pdf"
            path = storage / key
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(payload)
            digest = hashlib.sha256(payload).hexdigest()
            asset = Asset(
                principal_id=principal.id,
                purpose="FINAL_PDF",
                name=f"{kind}.pdf",
                mime="application/pdf",
                size=len(payload),
                sha256=digest,
                storage_key=key,
                status="READY",
                pages=1,
            )
            session.add(asset)
            session.flush()
            preparation = Preparation(
                principal_id=principal.id,
                checkout_account_id=account.id,
                status="READY",
                content_revision=1,
                input={},
                data={},
                expires_at=now + timedelta(days=1),
            )
            session.add(preparation)
            session.flush()
            artifact = Artifact(
                preparation_id=preparation.id,
                asset_id=asset.id,
                client_file_id=f"file-{kind}",
                sha256=digest,
                pages=1,
                sheets=1,
                settings={},
                page_map=[],
            )
            session.add(artifact)
            session.flush()
            quote = Quote(
                preparation_id=preparation.id,
                checkout_account_id=account.id,
                price_version=1,
                expires_at=now + timedelta(days=1),
                subtotal_cents=0,
                discount_cents=0,
                total_cents=0,
                data={},
            )
            session.add(quote)
            session.flush()
            records[kind] = {
                "assetId": asset.id,
                "artifactId": artifact.id,
                "preparationId": preparation.id,
                "quoteId": quote.id,
                "storageKey": key,
                "sha256": digest,
                "size": len(payload),
            }

        order = Order(
            principal_id=principal.id,
            checkout_account_id=account.id,
            pickup_code="DRILL01",
            status="CANCELLED",
            preparation_id=records["keep"]["preparationId"],
            quote_id=records["keep"]["quoteId"],
            total_cents=0,
            cash_received_cents=0,
            refunded_cents=0,
            nickname="Drill",
            phone="13800000000",
            location_id=location.id,
            requested_slot_id=slot.id,
            close_at=now + timedelta(days=1),
            data={"drill": True},
        )
        session.add(order)
        ledger = AnonymousLedger(bucket="ANONYMOUS", totals={"paidCents": 1234, "refundCents": 0})
        session.add(ledger)
        session.commit()
        records.update({
            "accountId": account.id,
            "staffId": staff.id,
            "authId": auth.id,
            "orderId": order.id,
            "ledgerId": ledger.id,
        })
    return records


def _volume(name: str) -> str:
    return f"dormprint-drill-{name}"


def _create_volume(name: str) -> None:
    _run(["docker", "volume", "create", name])


def _remove_volume(name: str) -> None:
    _run(["docker", "volume", "rm", "-f", name], check=False)


def _copy_seed_to_volume(source: Path, volume: str) -> None:
    # Use a disposable helper container plus `docker cp` instead of asking
    # restic to read the host bind mount directly.  Docker Desktop's file
    # sharing can make pread() fail on bind-mounted files; copying the bytes
    # into a named volume keeps the synthetic fixture path real without relying
    # on that host mount during the backup.
    name = f"{volume}-copy"
    _run([
        "docker", "create", "--name", name, "--user", "0",
        "-v", f"{volume}:/storage", "--entrypoint", "true", BACKUP_IMAGE,
    ])
    try:
        _run(["docker", "cp", f"{source}/.", f"{name}:/storage/"])
    finally:
        _run(["docker", "rm", "-f", name], check=False)


def _cat_volume(volume: str, relative: str) -> str:
    assert re.fullmatch(r"[A-Za-z0-9_./-]+", relative)
    result = _run([
        "docker", "run", "--rm", "--user", "0",
        "-v", f"{volume}:/data", "--entrypoint", "cat", BACKUP_IMAGE, f"/data/{relative}",
    ])
    return result.stdout


def _volume_exists(volume: str, relative: str) -> bool:
    assert re.fullmatch(r"[A-Za-z0-9_./-]+", relative)
    result = _run([
        "docker", "run", "--rm", "--user", "0",
        "-v", f"{volume}:/storage", "--entrypoint", "test", BACKUP_IMAGE,
        "-e", f"/storage/{relative}",
    ], check=False)
    return result.returncode == 0


def _volume_sha(volume: str, relative: str) -> str:
    assert re.fullmatch(r"[A-Za-z0-9_./-]+", relative)
    result = _run([
        "docker", "run", "--rm", "--user", "0",
        "-v", f"{volume}:/storage", "--entrypoint", "sha256sum", BACKUP_IMAGE,
        f"/storage/{relative}",
    ])
    return result.stdout.split()[0]


def _container_env(database: str, password: str, restic_password: str,
                   secret_key: str) -> dict[str, str]:
    assert DRILL_PORT != "55432", "drill must never use the shared test PostgreSQL port"
    assert DRILL_CONTAINER == "dormprint-drill-pg", "only the dedicated drill container is allowed"
    env = os.environ.copy()
    env.update({
        "PGHOST": DRILL_DOCKER_HOST,
        "PGPORT": DRILL_PORT,
        "PGDATABASE": database,
        "PGUSER": "dormprint",
        "PGPASSWORD": password,
        "RESTIC_REPOSITORY": "/repository",
        "RESTIC_PASSWORD": restic_password,
        "DORMPRINT_SECRET_KEY": secret_key,
        "DORMPRINT_ENVIRONMENT": "test",
        "HOME": "/tmp",
        "RESTIC_CACHE_DIR": "/state/restic-cache",
    })
    return env


def _backup(*args: str, state: str, storage: str, repository: str,
            database: str, password: str, restic_password: str,
            secret_key: str) -> subprocess.CompletedProcess:
    env = _container_env(database, password, restic_password, secret_key)
    env.update({"DORMPRINT_STORAGE_ROOT": "/storage", "BACKUP_HOSTNAME": "dormprint-drill"})
    command = [
        "docker", "run", "--rm", "--user", "0",
        "--name", f"dormprint-drill-backup-{secrets.token_hex(4)}",
        "-v", f"{BACKUP_SCRIPTS}:/opt/dormprint:ro",
        "-v", f"{state}:/state",
        "-v", f"{storage}:/storage",
        "-v", f"{repository}:/repository",
        "-e", "PGHOST", "-e", "PGPORT", "-e", "PGDATABASE", "-e", "PGUSER", "-e", "PGPASSWORD",
        "-e", "RESTIC_REPOSITORY", "-e", "RESTIC_PASSWORD", "-e", "DORMPRINT_SECRET_KEY",
        "-e", "BACKUP_STATE_DIR", "-e", "DORMPRINT_STORAGE_ROOT", "-e", "RESTIC_CACHE_DIR",
        "-e", "BACKUP_HOSTNAME", "-e", "DORMPRINT_ENVIRONMENT", "-e", "HOME",
        "--entrypoint", "/bin/bash", BACKUP_IMAGE,
        "/opt/dormprint/backup.sh", *args,
    ]
    return _run(command, env=env, timeout=420)


def _restore(*args: str, state: str, repository: str, target_storage: str,
             external: str, database: str, password: str,
             restic_password: str, secret_key: str) -> subprocess.CompletedProcess:
    env = _container_env(database, password, restic_password, secret_key)
    env["DORMPRINT_STORAGE_ROOT"] = "/primary-storage"
    command = [
        "docker", "run", "--rm", "--user", "0",
        "--name", f"dormprint-drill-restore-{secrets.token_hex(4)}",
        "-v", f"{BACKUP_SCRIPTS}:/opt/dormprint:ro",
        "-v", f"{state}:/state",
        "-v", f"{repository}:/repository",
        "-v", f"{target_storage}:/restored-storage",
        "-v", f"{external}:/external:ro",
        "-e", "PGHOST", "-e", "PGPORT", "-e", "PGDATABASE", "-e", "PGUSER", "-e", "PGPASSWORD",
        "-e", "RESTIC_REPOSITORY", "-e", "RESTIC_PASSWORD", "-e", "DORMPRINT_SECRET_KEY",
        "-e", "BACKUP_STATE_DIR", "-e", "DORMPRINT_STORAGE_ROOT", "-e", "RESTIC_CACHE_DIR",
        "-e", "DORMPRINT_ENVIRONMENT", "-e", "HOME",
        "--entrypoint", "/bin/bash", BACKUP_IMAGE,
        "/opt/dormprint/restore.sh", *args,
    ]
    return _run(command, env=env, timeout=420)


def _restic(*args: str, repository: str, restic_password: str,
            check: bool = True) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env["RESTIC_PASSWORD"] = restic_password
    command = [
        "docker", "run", "--rm", "--user", "0",
        "-v", f"{repository}:/repository",
        "-e", "RESTIC_REPOSITORY=/repository", "-e", "RESTIC_PASSWORD",
        "--entrypoint", "restic", BACKUP_IMAGE, *args,
    ]
    return _run(command, env=env, timeout=180, check=check)


def test_local_restore_drill_real_backup_restore_and_bootstrap():
    if shutil.which("docker") is None:
        pytest.skip("docker is not available")
    _run(["docker", "inspect", DRILL_CONTAINER])
    if not BACKUP_SCRIPTS.is_dir():
        pytest.skip("deploy/backup scripts are not available")

    _user, password = _credentials()
    token = secrets.token_hex(5)
    primary_db = f"dormprint_drill_{token}"
    restore_db = f"dormprint_restore_{token}"
    restic_password = secrets.token_urlsafe(32)
    secret_key = secrets.token_urlsafe(48)
    names = {
        "state": _volume(f"{token}-state"),
        "storage": _volume(f"{token}-storage"),
        "repository": _volume(f"{token}-repository"),
        "restored": _volume(f"{token}-restored"),
        "external": _volume(f"{token}-external"),
    }
    root = Path(tempfile.mkdtemp(prefix="dormprint-restore-drill-"))
    seed_storage = root / "seed-storage"
    seed_storage.mkdir()
    evidence: dict = {
        "drillContainer": DRILL_CONTAINER,
        "primaryDatabase": primary_db,
        "restoreDatabase": restore_db,
        "host": DRILL_HOST,
        "port": DRILL_PORT,
        "storageMode": "isolated disposable Docker named volumes",
        "timingsMs": {},
        "snapshots": {},
    }
    engine = None
    restore_engine = None
    started = time.monotonic()
    try:
        for volume in names.values():
            _create_volume(volume)
        _create_database(primary_db)
        engine = create_engine(_host_url(primary_db, password))
        from app.models import Base
        Base.metadata.create_all(engine)
        records = _seed(engine, seed_storage)
        _copy_seed_to_volume(seed_storage, names["storage"])
        evidence["seed"] = {
            "assets": 2, "artifacts": 2, "account": 1, "staff": 1,
            "authSessions": 1, "cancelledOrders": 1, "anonymousLedger": 1,
            "purgeSha256": records["purge"]["sha256"],
            "keepSha256": records["keep"]["sha256"],
        }

        hourly_started = time.monotonic()
        hourly = _backup("--init", "--mode", "hourly",
                         state=names["state"], storage=names["storage"],
                         repository=names["repository"], database=primary_db,
                         password=password, restic_password=restic_password,
                         secret_key=secret_key)
        evidence["timingsMs"]["hourlyBackup"] = round((time.monotonic() - hourly_started) * 1000)
        assert "BACKUP_OK" in hourly.stdout, hourly.stdout + hourly.stderr
        hourly_success = json.loads(_cat_volume(names["state"], "last-success.json"))
        assert hourly_success["mode"] == "hourly"
        hourly_snapshot = hourly_success["snapshot"]
        evidence["snapshots"]["hourly"] = hourly_snapshot
        evidence["snapshots"]["hourlyFiles"] = hourly_success["files"]
        evidence["snapshots"]["hourlyObjects"] = hourly_success["referencedObjects"]
        assert hourly_success["files"] >= 2
        assert hourly_success["referencedObjects"] == 4

        daily_started = time.monotonic()
        daily = _backup("--mode", "daily",
                        state=names["state"], storage=names["storage"],
                        repository=names["repository"], database=primary_db,
                        password=password, restic_password=restic_password,
                        secret_key=secret_key)
        evidence["timingsMs"]["dailyBackup"] = round((time.monotonic() - daily_started) * 1000)
        assert "BACKUP_OK" in daily.stdout, daily.stdout + daily.stderr
        daily_success = json.loads(_cat_volume(names["state"], "last-success.json"))
        assert daily_success["mode"] == "daily"
        evidence["snapshots"]["daily"] = daily_success["snapshot"]
        assert daily_success["snapshot"]

        snapshots = _restic("snapshots", "--json", repository=names["repository"],
                            restic_password=restic_password)
        snapshot_rows = json.loads(snapshots.stdout)
        evidence["snapshots"]["count"] = len(snapshot_rows)
        evidence["snapshots"]["rows"] = [
            {"id": row.get("short_id"), "time": row.get("time"), "tags": row.get("tags")}
            for row in snapshot_rows
        ]
        daily_rows = [row for row in snapshot_rows if "daily" in (row.get("tags") or [])]
        assert len(daily_rows) == 1
        daily_snapshot_full = daily_rows[0]["id"]
        daily_snapshot = daily_rows[0]["short_id"]
        assert daily_snapshot_full != hourly_snapshot
        assert daily_success["snapshot"] == daily_snapshot_full
        evidence["snapshots"]["selectedDaily"] = daily_snapshot
        evidence["snapshots"]["selectedDailyFull"] = daily_snapshot_full
        evidence["snapshots"]["dailyTagMatchesSelected"] = "daily" in daily_rows[0]["tags"]
        evidence["snapshots"]["dailyPointerMatchesRow"] = True
        assert len(snapshot_rows) >= 2
        listed = _restic("ls", daily_snapshot, repository=names["repository"],
                         restic_password=restic_password)
        for required in ("db.dump", "files.manifest", "immutable.tsv", "tombstones.jsonl",
                         "final/keep-"):
            assert required in listed.stdout, f"missing {required} in restic listing"
        wrong = _restic("snapshots", repository=names["repository"],
                        restic_password="wrong-password", check=False)
        assert wrong.returncode != 0, "restic repository did not reject a wrong password"
        evidence["resticEncryptionCheck"] = "wrong password rejected"

        mutation_started = time.monotonic()
        with Session(engine) as session:
            purge_artifact = session.get(Artifact, records["purge"]["artifactId"])
            purge_asset = session.get(Asset, records["purge"]["assetId"])
            assert purge_artifact is not None and purge_asset is not None
            session.execute(delete(Artifact).where(Artifact.id == purge_artifact.id))
            session.execute(delete(Asset).where(Asset.id == purge_asset.id))
            stamp = utcnow().isoformat()
            session.add(TrashEntry(
                target_type="ASSET",
                target_id=purge_asset.id,
                actor_id="drill",
                reason="local restore drill purge",
                status="PURGED",
                purge_at=utcnow() + timedelta(days=1),
                data={
                    "purgedAt": stamp,
                    "updatedAt": stamp,
                    "purgedRows": [
                        {"table": "artifacts", "id": purge_artifact.id},
                        {"table": "assets", "id": purge_asset.id},
                    ],
                    "purgedFiles": [records["purge"]["storageKey"]],
                },
            ))
            session.commit()
        (seed_storage / records["purge"]["storageKey"]).unlink()
        evidence["timingsMs"]["mutation"] = round((time.monotonic() - mutation_started) * 1000)

        export = _run([
            "docker", "run", "--rm", "--user", "0",
            "-v", f"{BACKUP_SCRIPTS}:/opt/dormprint:ro",
            "-v", f"{names['external']}:/external",
            "-e", "PGHOST", "-e", "PGPORT", "-e", "PGDATABASE", "-e", "PGUSER", "-e", "PGPASSWORD",
            "-e", "DORMPRINT_STORAGE_ROOT",
            "--entrypoint", "python3", BACKUP_IMAGE,
            "/opt/dormprint/apply_tombstones.py", "export",
            "--output", "/external/tombstones.jsonl",
        ], env={**os.environ, "PGHOST": DRILL_DOCKER_HOST, "PGPORT": DRILL_PORT,
                "PGDATABASE": primary_db, "PGUSER": "dormprint", "PGPASSWORD": password,
                "DORMPRINT_STORAGE_ROOT": "/storage"}, timeout=120)
        assert "TOMBSTONE_EXPORT_OK" in export.stdout, export.stdout + export.stderr
        entries = [json.loads(line) for line in _cat_volume(
            names["external"], "tombstones.jsonl").splitlines() if line]
        evidence["externalJournalEntries"] = len(entries)
        assert any(item.get("kind") == "AUTHORITY" for item in entries)
        assert any(item.get("targetType") == "ASSET" for item in entries)

        restore_started = time.monotonic()
        restored = _restore(
            "--snapshot", daily_snapshot,
            "--target-database", restore_db,
            "--target-storage", "/restored-storage",
            "--journal", "/external/tombstones.jsonl",
            state=names["state"], repository=names["repository"],
            target_storage=names["restored"], external=names["external"],
            database=primary_db, password=password,
            restic_password=restic_password, secret_key=secret_key,
        )
        evidence["timingsMs"]["restore"] = round((time.monotonic() - restore_started) * 1000)
        assert "RESTORE_OK" in restored.stdout, restored.stdout + restored.stderr
        evidence["snapshots"]["restoredFrom"] = daily_snapshot

        restore_engine = create_engine(_host_url(restore_db, password))
        with Session(restore_engine) as session:
            assert session.scalar(select(Artifact.id).where(
                Artifact.id == records["purge"]["artifactId"])) is None
            assert session.scalar(select(Asset.id).where(
                Asset.id == records["purge"]["assetId"])) is None
            keep_asset = session.get(Asset, records["keep"]["assetId"])
            keep_artifact = session.get(Artifact, records["keep"]["artifactId"])
            assert keep_asset is not None and keep_artifact is not None
            assert keep_asset.sha256 == records["keep"]["sha256"]
            assert keep_artifact.sha256 == records["keep"]["sha256"]
            assert _volume_exists(names["restored"], keep_asset.storage_key)
            assert _volume_sha(names["restored"], keep_asset.storage_key) == records["keep"]["sha256"]
            assert not _volume_exists(names["restored"], records["purge"]["storageKey"])
            account = session.get(Account, records["accountId"])
            staff = session.get(Staff, records["staffId"])
            auth = session.get(AuthSession, records["authId"])
            assert account is not None and account.session_version == 2
            assert staff is not None
            assert staff.enabled is False
            assert staff.status == "DISABLED"
            assert staff.capabilities == []
            assert staff.is_super_admin is True
            assert auth is not None and auth.revoked_at is not None
            ledger = session.get(AnonymousLedger, records["ledgerId"])
            assert ledger is not None and ledger.totals["paidCents"] == 1234
            latest = session.scalar(text(
                "SELECT latest FROM _dormprint_restore_authority "
                "WHERE kind='AUTHORITY' AND id='__authority__'"))
            assert latest is True
            audit = session.scalar(text(
                "SELECT count(*) FROM audit_logs WHERE action='RESTORE_SECURITY_RESET'"))
            assert audit == 1
            evidence["restoreChecks"] = {
                "purgedArtifactAbsent": True,
                "purgedFileAbsent": True,
                "keptPdfShaMatches": True,
                "accountSessionVersion": account.session_version,
                "staffDisabled": True,
                "authRevoked": True,
                "anonymousLedgerRetained": True,
                "authorityLatest": bool(latest),
                "securityAudit": audit,
            }

        bootstrap_started = time.monotonic()
        bootstrap_password = "BootstrapDrillPass-42!"
        bootstrap = subprocess.run(
            [sys.executable, "-m", "app.cli", "init-admin",
             "--username", "bootstrap-admin",
             "--email", "bootstrap-admin@example.test",
             "--password-stdin"],
            cwd=str(REPO / "backend"),
            env={**os.environ,
                 "DORMPRINT_DATABASE_URL": _host_url(restore_db, password),
                 "DORMPRINT_ENVIRONMENT": "test",
                 "DORMPRINT_SECRET_KEY": secret_key,
                 "DORMPRINT_STORAGE_ROOT": str(seed_storage),
                 "PYTHONPATH": str(REPO / "backend")},
            input=bootstrap_password + "\n",
            capture_output=True, text=True, timeout=120,
        )
        evidence["timingsMs"]["bootstrap"] = round((time.monotonic() - bootstrap_started) * 1000)
        assert bootstrap.returncode == 0, (
            "init-admin bootstrap failed\nstdout:\n" + bootstrap.stdout[-4000:] +
            "\nstderr:\n" + bootstrap.stderr[-4000:]
        )
        with Session(restore_engine) as session:
            bootstrap_staff = session.scalar(select(Staff).where(Staff.username == "bootstrap-admin"))
            assert bootstrap_staff is not None
            assert bootstrap_staff.enabled is True
            assert bootstrap_staff.status == "ACTIVE"
            assert bootstrap_staff.is_super_admin is True
            bootstrap_account = session.get(Account, bootstrap_staff.account_id)
            assert bootstrap_account is not None
            assert bootstrap_account.email == "bootstrap-admin@example.test"
            assert bootstrap_account.verified_at is not None
            seed_staff_after_bootstrap = session.get(Staff, records["staffId"])
            assert seed_staff_after_bootstrap is not None
            assert seed_staff_after_bootstrap.enabled is False
            assert seed_staff_after_bootstrap.status == "DISABLED"
            evidence["bootstrap"] = {
                "username": bootstrap_staff.username,
                "superAdmin": True,
                "enabled": True,
                "accountEmail": bootstrap_account.email,
                "staffCount": len(session.scalars(select(Staff)).all()),
                "seedSuperAdminStillDisabled": True,
            }

        evidence["elapsedMs"] = round((time.monotonic() - started) * 1000)
        evidence["result"] = "PASS"
        print("DRILL_EVIDENCE_JSON=" + json.dumps(evidence, ensure_ascii=False, sort_keys=True))
    finally:
        if restore_engine is not None:
            restore_engine.dispose()
        if engine is not None:
            engine.dispose()
        for database in (primary_db, restore_db):
            try:
                _drop_database(database)
            except Exception:
                pass
        for volume in names.values():
            _remove_volume(volume)
        shutil.rmtree(root, ignore_errors=True)
