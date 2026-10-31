#!/usr/bin/env python3
"""Deletion-tombstone journal export and restore replay.

A backup of an old point in time can still contain records that were later
purged. Restore therefore replays a durable tombstone journal before the
restored database is exposed. A journal entry must contain the exact deleted
rows (in safe child-before-parent order), NULL foreign-key detaches, and
private files; an entry without that detail is rejected as
TOMBSTONE_INCOMPLETE instead of reviving data.

The maintenance purge appends ``purgedRows``, ``purgedDetaches``,
``purgedFiles`` and the anonymous-ledger bucket to ``TrashEntry.data`` in the
same transaction that removes the object. See docs/BACKUP-RESTORE.md for the
coordination contract.

The journal is data, never SQL: table names and detach columns come from the
explicit allow-lists below, and identifiers are escaped as literals. A
separate AUTHORITY record lets restore fail closed when it cannot prove that
the current staff/account state is newer than the restored snapshot.
"""
from __future__ import annotations

import argparse
import errno
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import time
from contextlib import contextmanager
from typing import NoReturn


# Explicit whitelist, never caller-supplied SQL. Keep in sync with
# backend/app/storage_coordination.py and backend/app/models.py.
ALLOWED_TABLES = {
    "principals", "customer_accounts", "staff", "auth_sessions",
    "one_time_tokens", "order_access_grants", "config_sections", "assets",
    "upload_sessions", "preparations", "artifacts", "quotes",
    "delivery_locations", "delivery_slots", "orders", "order_revisions",
    "order_files", "print_tasks", "print_attempts", "binding_tasks",
    "packing_tasks", "cash_receipts", "payment_records", "receipt_adjustments",
    "delivery_batches", "delivery_assignments", "delivery_proofs",
    "business_exceptions", "batch_plans", "after_sales", "refund_records",
    "promotion_campaigns", "coupon_grants", "promotion_events", "referrals",
    "mail_templates", "email_outbox", "export_jobs", "trash_entries",
    "audit_logs", "anonymous_ledger", "deletion_requests",
    "idempotency_records", "jobs", "wage_policies", "wage_entries",
    "wage_settlements", "wage_settlement_items", "supplementary_charges",
    "supplementary_movements", "supplementary_disputes",
}

# The exact nullable FK columns that may be detached during replay. This is
# deliberately narrow: a tombstone may not NULL arbitrary columns.
DETACH_COLUMNS = {
    "preparations": {"source_grant_id"},
    "order_access_grants": {"source_grant_id"},
    "wage_entries": {"order_id", "assignment_id"},
    "promotion_events": {"order_id"},
    "coupon_grants": {"reserved_order_id", "used_order_id"},
    "referrals": {"qualified_order_id"},
}

# Child-before-parent delete order. Grouping by table (rather than emitting
# journal order) avoids FK failures when a purge captured rows in an order that
# is safe for the live transaction but not for a replay from another snapshot.
# Source grants are additionally NULLed before their self-referential DELETE.
DELETE_ORDER = (
    "wage_settlement_items", "print_attempts", "wage_entries",
    "supplementary_movements", "supplementary_disputes", "refund_records",
    "receipt_adjustments", "promotion_events", "print_tasks",
    "payment_records", "delivery_proofs", "binding_tasks",
    "supplementary_charges", "referrals", "packing_tasks", "order_revisions",
    "order_files", "order_access_grants", "delivery_assignments",
    "coupon_grants", "cash_receipts", "business_exceptions", "after_sales",
    "upload_sessions", "orders", "artifacts", "wage_settlements",
    "wage_policies", "quotes", "one_time_tokens", "export_jobs",
    "delivery_batches", "batch_plans", "auth_sessions", "assets", "staff",
    "preparations", "deletion_requests", "customer_accounts", "trash_entries",
    "promotion_campaigns", "principals", "mail_templates", "jobs",
    "idempotency_records", "email_outbox", "delivery_slots",
    "delivery_locations", "config_sections", "audit_logs", "anonymous_ledger",
)

IDENTIFIER = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")
BUCKET = re.compile(r"^[A-Za-z0-9:_-]{1,64}$")
TOTAL_KEY = re.compile(r"^[A-Za-z0-9_]{1,40}$")
FORBIDDEN_KEYS = {
    "email", "phone", "name", "nickname", "address", "password", "token",
    "payload", "display_name", "customer_name", "recipient", "sql",
}
STAFF_FACT_KEYS = {
    "id", "enabled", "status", "role", "isSuperAdmin", "capabilities",
    "sessionVersion",
}
ACCOUNT_FACT_KEYS = {"id", "status", "sessionVersion", "deletedAt"}

if set(DELETE_ORDER) != ALLOWED_TABLES or len(DELETE_ORDER) != len(ALLOWED_TABLES):
    raise RuntimeError("DELETE_ORDER must contain every allowed table exactly once")


def fail(message: str, code: int = 2) -> "NoReturn":
    print(message, file=sys.stderr)
    raise SystemExit(code)


def safe_relative(key: str) -> bool:
    if not key or key.startswith("/") or "\\" in key:
        return False
    parts = Path(key).parts
    return bool(parts) and ".." not in parts and parts[0] != ".coordination"


def _lock_timeout(timeout=None) -> float:
    raw = timeout
    if raw is None:
        raw = os.environ.get("DORMPRINT_MAINTENANCE_LOCK_TIMEOUT_SECONDS")
        if raw is None or raw == "":
            raw = os.environ.get("BACKUP_LOCK_TIMEOUT_SECONDS", "300")
    try:
        value = float(raw)
    except (TypeError, ValueError):
        fail("TOMBSTONE_APPLY_FAILED: maintenance lock timeout is invalid", 4)
    if not math.isfinite(value):
        fail("TOMBSTONE_APPLY_FAILED: maintenance lock timeout is invalid", 4)
    return min(max(value, 0.0), 3600.0)


@contextmanager
def maintenance_lock(storage: Path, timeout=None):
    """Hold the same flock as backup.sh while replaying files/rows.

    Backup takes ``<storage>/.coordination/maintenance.lock`` for the whole
    pg_dump + private-file snapshot. A restore replay must not unlink a file
    while that snapshot is reading it, so apply takes the identical lock (same
    path, same fcntl.flock semantics) and fails rather than proceeding
    locklessly.
    """

    root = Path(storage).expanduser()
    if not root.is_absolute():
        root = Path.cwd() / root
    try:
        root = root.resolve()
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
    except OSError as exc:
        fail(f"TOMBSTONE_APPLY_FAILED: storage root unavailable ({exc.__class__.__name__})", 4)

    coordination = root / ".coordination"
    if coordination.is_symlink() or (coordination.exists() and not coordination.is_dir()):
        fail("TOMBSTONE_APPLY_FAILED: unsafe coordination path", 4)
    try:
        coordination.mkdir(mode=0o700, exist_ok=True)
        if coordination.resolve().parent != root:
            fail("TOMBSTONE_APPLY_FAILED: coordination path escapes storage root", 4)
    except OSError as exc:
        fail(f"TOMBSTONE_APPLY_FAILED: coordination path unavailable ({exc.__class__.__name__})", 4)

    lock_path = coordination / "maintenance.lock"
    if lock_path.is_symlink():
        fail("TOMBSTONE_APPLY_FAILED: maintenance lock is a symlink", 4)
    flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        try:
            fd = os.open(lock_path, flags, 0o600)
        except PermissionError:
            fd = os.open(
                lock_path,
                os.O_RDONLY | os.O_CREAT | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
                0o600,
            )
    except OSError as exc:
        fail(
            f"TOMBSTONE_APPLY_FAILED: cannot open maintenance lock "
            f"({exc.__class__.__name__})",
            4,
        )

    acquired = False
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            fail("TOMBSTONE_APPLY_FAILED: maintenance lock is not a regular file", 4)
        try:
            if info.st_uid == os.geteuid():
                os.fchmod(fd, 0o600)
        except OSError:
            pass
        wait_for = _lock_timeout(timeout)
        deadline = time.monotonic() + wait_for
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired = True
                break
            except OSError as exc:
                if exc.errno not in (errno.EACCES, errno.EAGAIN):
                    fail(
                        f"TOMBSTONE_APPLY_FAILED: maintenance lock error "
                        f"({exc.__class__.__name__})",
                        4,
                    )
                if time.monotonic() >= deadline:
                    fail("TOMBSTONE_APPLY_FAILED: maintenance lock busy", 4)
                time.sleep(min(0.05, max(0.005, deadline - time.monotonic())))
        yield
    finally:
        if acquired:
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
            except OSError:
                pass
        os.close(fd)


def sql_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def sql_json(value) -> str:
    return sql_literal(json.dumps(value, ensure_ascii=False, sort_keys=True)) + "::jsonb"


def stable_id(prefix: str, value: str) -> str:
    return prefix + hashlib.sha256(value.encode("utf-8")).hexdigest()[:24]


def _parse_time(value):
    if not isinstance(value, str) or not value:
        return None
    text = value.strip().replace("Z", "+00:00")
    try:
        from datetime import datetime

        parsed = datetime.fromisoformat(text)
        if parsed.tzinfo is None:
            from datetime import timezone

            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed
    except ValueError:
        return None


def _freshness(entry: dict):
    return (
        _parse_time(entry.get("updatedAt"))
        or _parse_time(entry.get("purgedAt"))
        or _parse_time(entry.get("generatedAt"))
    )


def _reject_pii(value, path: str = "entry") -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            normalized = str(key).lower().replace("-", "_")
            if (
                normalized in FORBIDDEN_KEYS
                or normalized.endswith("_name")
                or normalized.endswith("_email")
                or normalized.endswith("_phone")
            ):
                fail(f"TOMBSTONE_INVALID {path}.{key}: PII/unsafe field is not allowed")
            _reject_pii(child, f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _reject_pii(child, f"{path}[{index}]")


def load_journal(path: Path) -> list[dict]:
    if not path.exists():
        return []
    entries: list[dict] = []
    for line_number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not raw.strip():
            continue
        try:
            entry = json.loads(raw)
        except json.JSONDecodeError as error:
            fail(f"TOMBSTONE_INVALID line={line_number}: {error}")
        if not isinstance(entry, dict) or entry.get("version") not in (1, 2):
            fail(f"TOMBSTONE_INVALID line={line_number}: unsupported entry")
        entries.append(entry)
    return entries


def _validate_authority(entry: dict) -> dict:
    facts = entry.get("facts")
    if not isinstance(facts, dict):
        fail(f"TOMBSTONE_INVALID trashId={entry.get('trashId')} authority facts must be an object")
    staff = facts.get("staff") or []
    accounts = facts.get("accounts") or []
    if not isinstance(staff, list) or not isinstance(accounts, list):
        fail("TOMBSTONE_INVALID authority facts staff/accounts must be lists")
    for fact in staff:
        if not isinstance(fact, dict) or set(fact) - STAFF_FACT_KEYS:
            fail("TOMBSTONE_INVALID authority staff fact has unknown fields")
        if not isinstance(fact.get("id"), str) or not fact["id"] or len(fact["id"]) > 64:
            fail("TOMBSTONE_INVALID authority staff id")
        if not isinstance(fact.get("enabled"), bool):
            fail("TOMBSTONE_INVALID authority staff enabled")
        if not isinstance(fact.get("isSuperAdmin"), bool):
            fail("TOMBSTONE_INVALID authority staff isSuperAdmin")
        if fact.get("role") not in ("ADMIN", "COURIER"):
            fail("TOMBSTONE_INVALID authority staff role")
        if not isinstance(fact.get("status"), str) or len(fact["status"]) > 24:
            fail("TOMBSTONE_INVALID authority staff status")
        capabilities = fact.get("capabilities")
        if not isinstance(capabilities, list) or any(
            not isinstance(item, str) or len(item) > 80 for item in capabilities
        ):
            fail("TOMBSTONE_INVALID authority staff capabilities")
        if not isinstance(fact.get("sessionVersion"), int) or fact["sessionVersion"] < 0:
            fail("TOMBSTONE_INVALID authority staff sessionVersion")
    for fact in accounts:
        if not isinstance(fact, dict) or set(fact) - ACCOUNT_FACT_KEYS:
            fail("TOMBSTONE_INVALID authority account fact has unknown fields")
        if not isinstance(fact.get("id"), str) or not fact["id"] or len(fact["id"]) > 64:
            fail("TOMBSTONE_INVALID authority account id")
        if not isinstance(fact.get("status"), str) or len(fact["status"]) > 24:
            fail("TOMBSTONE_INVALID authority account status")
        if not isinstance(fact.get("sessionVersion"), int) or fact["sessionVersion"] < 0:
            fail("TOMBSTONE_INVALID authority account sessionVersion")
        if fact.get("deletedAt") is not None and not isinstance(fact["deletedAt"], str):
            fail("TOMBSTONE_INVALID authority account deletedAt")
    return {
        "trashId": "__authority__",
        "targetType": "AUTHORITY",
        "targetId": "__authority__",
        "kind": "AUTHORITY",
        "generatedAt": entry.get("generatedAt") or entry.get("updatedAt"),
        "facts": {"staff": staff, "accounts": accounts},
    }


def _validate_ledger(value, trash_id: str):
    if value is None:
        return None
    if not isinstance(value, dict):
        fail(f"TOMBSTONE_INVALID trashId={trash_id} ledger must be an object")
    bucket = value.get("bucket") or f"trash:{trash_id}"
    if not isinstance(bucket, str) or not BUCKET.match(bucket):
        fail(f"TOMBSTONE_INVALID trashId={trash_id} ledger bucket")
    totals = value.get("totals") or {}
    if not isinstance(totals, dict):
        fail(f"TOMBSTONE_INVALID trashId={trash_id} ledger totals must be an object")
    for key, amount in totals.items():
        if not isinstance(key, str) or not TOTAL_KEY.match(key):
            fail(f"TOMBSTONE_INVALID trashId={trash_id} ledger key={key!r}")
        if isinstance(amount, bool) or amount is None:
            continue
        if not isinstance(amount, (int, float)):
            fail(f"TOMBSTONE_INVALID trashId={trash_id} ledger value for {key} is not numeric")
    return {"bucket": bucket, "totals": totals}


def validate_entries(entries: list[dict]) -> tuple[list[dict], list[dict], list[dict]]:
    """Return ``(incomplete, deletion_plans, authority_entries)``."""

    _reject_pii(entries)
    incomplete: list[dict] = []
    plans: list[dict] = []
    authorities: list[dict] = []
    for entry in entries:
        if entry.get("kind") == "AUTHORITY" or entry.get("targetType") == "AUTHORITY":
            authorities.append(_validate_authority(entry))
            continue

        trash_id = str(entry.get("trashId") or "")
        target_type = str(entry.get("targetType") or "")
        target_id = str(entry.get("targetId") or "")
        rows = entry.get("rows") or []
        files = entry.get("files") or []
        detaches = entry.get("detaches") or []
        if not trash_id or not target_type or not target_id:
            fail(f"TOMBSTONE_INVALID trashId={trash_id!r} missing target identity")
        if len(trash_id) > 64 or len(target_type) > 48 or len(target_id) > 64:
            fail(f"TOMBSTONE_INVALID trashId={trash_id} target identity too long")
        if not isinstance(rows, list) or not isinstance(files, list) or not isinstance(detaches, list):
            fail(f"TOMBSTONE_INVALID trashId={trash_id} rows/files/detaches must be lists")
        if not rows:
            incomplete.append(entry)
            continue
        cleaned_rows: list[dict] = []
        for row in rows:
            if not isinstance(row, dict):
                fail(f"TOMBSTONE_INVALID trashId={trash_id} row is not an object")
            table = str(row.get("table") or "")
            identifier = str(row.get("id") or "")
            if not IDENTIFIER.match(table) or table not in ALLOWED_TABLES:
                fail(f"TOMBSTONE_INVALID trashId={trash_id} table={table!r}")
            if not identifier or len(identifier) > 128:
                fail(f"TOMBSTONE_INVALID trashId={trash_id} id={identifier!r}")
            cleaned_rows.append({"table": table, "id": identifier})
        cleaned_files = []
        for key in files:
            if not isinstance(key, str) or not safe_relative(key):
                fail(f"TOMBSTONE_INVALID trashId={trash_id} file={key!r}")
            cleaned_files.append(key)
        cleaned_detaches = []
        for detach in detaches:
            if not isinstance(detach, dict):
                fail(f"TOMBSTONE_INVALID trashId={trash_id} detach is not an object")
            table = str(detach.get("table") or "")
            column = str(detach.get("column") or "")
            identifier = str(detach.get("id") or "")
            if table not in DETACH_COLUMNS or column not in DETACH_COLUMNS[table]:
                fail(f"TOMBSTONE_INVALID trashId={trash_id} detach={table}.{column}")
            if not IDENTIFIER.match(table) or not IDENTIFIER.match(column):
                fail(f"TOMBSTONE_INVALID trashId={trash_id} detach identifier")
            if not identifier or len(identifier) > 128:
                fail(f"TOMBSTONE_INVALID trashId={trash_id} detach id={identifier!r}")
            cleaned_detaches.append({"table": table, "column": column, "id": identifier})
        ledger = _validate_ledger(entry.get("ledger"), trash_id)
        plans.append({
            "trashId": trash_id,
            "targetType": target_type,
            "targetId": target_id,
            "purgedAt": entry.get("purgedAt"),
            "updatedAt": entry.get("updatedAt") or entry.get("purgedAt"),
            "rows": cleaned_rows,
            "files": cleaned_files,
            "detaches": cleaned_detaches,
            "ledger": ledger,
        })
    return incomplete, plans, authorities


def choose_authority(authorities: list[dict]) -> tuple[dict, bool]:
    """Pick the freshest authority manifest and decide whether to overlay it.

    ``restore.sh`` concatenates the external journal first and the snapshot
    journal second. Two authority records therefore mean the external journal
    has one; it is only allowed to overlay when it is newer than the snapshot.
    With no external authority record the restored snapshot is the only known
    state, so post_restore must fail closed.
    """

    if not authorities:
        return {"staff": [], "accounts": []}, False
    if len(authorities) == 1:
        return authorities[0]["facts"], False
    first, last = authorities[0], authorities[-1]
    first_time = _freshness(first)
    last_time = _freshness(last)
    if first_time is not None and (last_time is None or first_time > last_time):
        return first["facts"], True
    return last["facts"], False


def _grouped_rows(plans: list[dict]) -> dict[str, list[str]]:
    grouped: dict[str, list[str]] = {table: [] for table in DELETE_ORDER}
    seen: set[tuple[str, str]] = set()
    for plan in plans:
        for row in plan["rows"]:
            key = (row["table"], row["id"])
            if key not in seen:
                seen.add(key)
                grouped[row["table"]].append(row["id"])
    return grouped


def _grouped_detaches(plans: list[dict]) -> dict[str, dict[str, list[str]]]:
    grouped: dict[str, dict[str, list[str]]] = {}
    seen: set[tuple[str, str, str]] = set()
    for plan in plans:
        for detach in plan["detaches"]:
            key = (detach["table"], detach["column"], detach["id"])
            if key in seen:
                continue
            seen.add(key)
            grouped.setdefault(detach["table"], {}).setdefault(detach["column"], []).append(
                detach["id"]
            )
    return grouped


def print_replay(plans: list[dict], authorities: list[dict]) -> None:
    detaches = _grouped_detaches(plans)
    grouped = _grouped_rows(plans)
    for table in DELETE_ORDER:
        for column in sorted(detaches.get(table, {})):
            for identifier in detaches[table][column]:
                print(f"UPDATE {table} SET {column} = NULL WHERE id = {sql_literal(identifier)};")
    for table in DELETE_ORDER:
        identifiers = grouped.get(table) or []
        if identifiers:
            print(
                f"DELETE FROM {table} WHERE id IN "
                f"({', '.join(sql_literal(identifier) for identifier in identifiers)});"
            )
    for plan in plans:
        for key in plan["files"]:
            print(f"-- remove-file {key}")
        if plan["ledger"]:
            print(f"-- preserve-ledger {plan['ledger']['bucket']}")
    facts, latest = choose_authority(authorities)
    print(f"-- restore-authority staff={len(facts['staff'])} accounts={len(facts['accounts'])} latest={str(latest).lower()}")


def execute_replay(
    plans: list[dict],
    authorities: list[dict],
    database: str,
    storage: Path,
    psql: str,
) -> int:
    detaches = _grouped_detaches(plans)
    grouped = _grouped_rows(plans)
    statements: list[str] = ["BEGIN;"]
    statements.append(
        "CREATE TABLE IF NOT EXISTS _dormprint_restore_authority ("
        "kind text NOT NULL, id text NOT NULL, facts jsonb NOT NULL, "
        "latest boolean NOT NULL DEFAULT false, PRIMARY KEY (kind, id));"
    )

    for table in DELETE_ORDER:
        for column in sorted(detaches.get(table, {})):
            identifiers = detaches[table][column]
            statements.append(
                f"UPDATE {table} SET {column} = NULL WHERE id IN "
                f"({', '.join(sql_literal(identifier) for identifier in identifiers)});"
            )

    for table in DELETE_ORDER:
        identifiers = grouped.get(table) or []
        if identifiers:
            statements.append(
                f"DELETE FROM {table} WHERE id IN "
                f"({', '.join(sql_literal(identifier) for identifier in identifiers)});"
            )

    files: list[str] = []
    for plan in plans:
        files.extend(plan["files"])
        ledger = plan["ledger"]
        if ledger:
            bucket = ledger["bucket"]
            ledger_id = stable_id("ledger-", bucket)
            audit_id = stable_id("audit-", bucket)
            statements.append(
                "INSERT INTO anonymous_ledger "
                "(id, bucket, totals, version, created_at, updated_at) VALUES ("
                f"{sql_literal(ledger_id)}, {sql_literal(bucket)}, "
                f"{sql_json(ledger['totals'])}, 1, now(), now()) "
                "ON CONFLICT (bucket) DO UPDATE SET "
                "totals = anonymous_ledger.totals || EXCLUDED.totals, "
                "updated_at = EXCLUDED.updated_at;"
            )
            statements.append(
                "INSERT INTO audit_logs "
                "(id, actor_id, action, target_type, target_id, reason, request_id, "
                "data, version, created_at, updated_at) VALUES ("
                f"{sql_literal(audit_id)}, 'restore', 'RESTORE_LEDGER_PRESERVED', "
                f"'ANONYMOUS_LEDGER', {sql_literal(bucket[:64])}, "
                "'tombstone replay preserved anonymous totals', '', "
                f"{sql_json({'bucket': bucket})}, 1, now(), now()) "
                "ON CONFLICT (id) DO NOTHING;"
            )

        data = {
            "purgedAt": plan["purgedAt"],
            "purgedFiles": plan["files"],
            "tombstoneReplay": True,
            "rowCount": len(plan["rows"]),
            "fileCount": len(plan["files"]),
        }
        if ledger:
            data["ledgerBucket"] = ledger["bucket"]
        statements.append(
            "INSERT INTO trash_entries "
            "(id, target_type, target_id, actor_id, reason, status, purge_at, data, "
            "version, created_at, updated_at) VALUES ("
            f"{sql_literal(plan['trashId'])}, {sql_literal(plan['targetType'])}, "
            f"{sql_literal(plan['targetId'])}, 'restore', 'tombstone replay', "
            f"'PURGED', now(), {sql_json(data)}, 1, now(), now()) "
            "ON CONFLICT (id) DO UPDATE SET status = 'PURGED', "
            "purge_at = EXCLUDED.purge_at, data = EXCLUDED.data, "
            "updated_at = EXCLUDED.updated_at;"
        )

    facts, latest = choose_authority(authorities)
    statements.append(
        "INSERT INTO _dormprint_restore_authority (kind, id, facts, latest) VALUES "
        f"('AUTHORITY', '__authority__', {sql_json(facts)}, {str(latest).lower()}) "
        "ON CONFLICT (kind, id) DO UPDATE SET facts = EXCLUDED.facts, "
        "latest = EXCLUDED.latest;"
    )
    statements.append("COMMIT;")

    result = subprocess.run(
        [psql, "-X", "--no-password", "-v", "ON_ERROR_STOP=1", "-d", database],
        input="\n".join(statements) + "\n",
        text=True,
        capture_output=True,
    )
    if result.returncode != 0:
        print(result.stderr, file=sys.stderr)
        fail("TOMBSTONE_APPLY_FAILED: database row replay failed", 4)

    root = storage.resolve()
    unique_files = list(dict.fromkeys(files))
    for key in unique_files:
        path = (storage / key).resolve()
        if root != path and root not in path.parents:
            fail(f"TOMBSTONE_INVALID file={key!r}", 2)
        try:
            path.unlink()
        except FileNotFoundError:
            continue
        except OSError as error:
            fail(f"TOMBSTONE_APPLY_FAILED file={key} error={error}", 4)
    return len(unique_files)


def psql_query(psql: str, database: str, query: str) -> str:
    result = subprocess.run(
        [psql, "-X", "--no-password", "-d", database, "-A", "-t", "-c", query],
        text=True,
        capture_output=True,
    )
    if result.returncode != 0:
        print(result.stderr, file=sys.stderr)
        fail("BACKUP_TOMBSTONE_EXPORT_FAILED", 4)
    return result.stdout


def _merge_entries(groups: list[list[dict]]) -> list[dict]:
    merged: dict[str, dict] = {}
    for group in groups:
        for entry in group:
            key = str(entry.get("trashId") or "")
            if not key:
                continue
            old = merged.get(key)
            old_time = _freshness(old) if old else None
            new_time = _freshness(entry)
            if old is None or new_time is None or old_time is None or new_time >= old_time:
                merged[key] = entry
    return list(merged.values())


def export_journal(args) -> int:
    database = args.database or os.environ.get("PGDATABASE", "")
    if not database:
        fail("BACKUP_TOMBSTONE_EXPORT_FAILED: PGDATABASE is not configured", 4)
    psql = args.psql
    query = """
        SELECT COALESCE(json_agg(json_build_object(
            'version', 2,
            'trashId', te.id,
            'targetType', te.target_type,
            'targetId', te.target_id,
            'purgedAt', COALESCE(te.data->>'purgedAt', te.updated_at::text),
            'updatedAt', COALESCE(te.data->>'updatedAt', te.updated_at::text),
            'rows', COALESCE(te.data->'purgedRows', '[]'::jsonb),
            'files', COALESCE(te.data->'purgedFiles', '[]'::jsonb),
            'detaches', COALESCE(te.data->'purgedDetaches', '[]'::jsonb),
            'ledger', CASE WHEN te.data ? 'ledgerTotals' THEN json_build_object(
                'bucket', COALESCE(te.data->>'ledgerBucket', 'trash:' || te.id),
                'totals', te.data->'ledgerTotals')
                ELSE NULL END
        ) ORDER BY te.updated_at), '[]'::json)
          FROM trash_entries te
         WHERE te.status = 'PURGED'
            OR (
                te.status = 'PURGE_PENDING'
                AND jsonb_typeof(te.data->'purgedRows') = 'array'
                AND jsonb_array_length(te.data->'purgedRows') > 0
                AND NULLIF(te.data->>'purgedAt', '') IS NOT NULL
            )
    """
    rows = json.loads(psql_query(psql, database, query) or "[]")

    authority_query = """
        SELECT json_build_object(
            'version', 2,
            'kind', 'AUTHORITY',
            'trashId', '__authority__',
            'targetType', 'AUTHORITY',
            'targetId', '__authority__',
            'generatedAt', to_char(now() at time zone 'utc', 'YYYY-MM-DD"T"HH24:MI:SS"Z"'),
            'facts', json_build_object(
                'staff', COALESCE((
                    SELECT json_agg(json_build_object(
                        'id', s.id,
                        'enabled', s.enabled,
                        'status', s.status,
                        'role', s.role,
                        'isSuperAdmin', s.is_super_admin,
                        'capabilities', s.capabilities,
                        'sessionVersion', s.session_version
                    ) ORDER BY s.id) FROM staff s), '[]'::json),
                'accounts', COALESCE((
                    SELECT json_agg(json_build_object(
                        'id', a.id,
                        'status', a.status,
                        'sessionVersion', a.session_version,
                        'deletedAt', a.deleted_at
                    ) ORDER BY a.id) FROM customer_accounts a), '[]'::json)
            )
        )
    """
    authority = json.loads(psql_query(psql, database, authority_query) or "{}")
    if not authority:
        fail("BACKUP_TOMBSTONE_EXPORT_FAILED: authority manifest query returned no row", 4)

    current = list(rows) + [authority]
    incomplete, _, _ = validate_entries(current)
    if incomplete:
        ids = ", ".join(str(entry.get("trashId") or "?") for entry in incomplete)
        fail(
            f"TOMBSTONE_INCOMPLETE count={len(incomplete)} trashIds={ids}; "
            "refusing to omit a PURGED tombstone with no actual rows",
            3,
        )
    cumulative_entries: list[dict] = []
    cumulative = Path(args.cumulative) if args.cumulative else None
    if cumulative and cumulative.exists():
        cumulative_entries = load_journal(cumulative)

    merged = _merge_entries([cumulative_entries, current])
    ordered = sorted(
        merged,
        key=lambda item: (
            1 if item.get("kind") == "AUTHORITY" else 0,
            str(item.get("purgedAt") or item.get("generatedAt") or ""),
        ),
    )
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        "".join(json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n" for entry in ordered),
        encoding="utf-8",
    )
    output.chmod(0o600)
    if cumulative:
        cumulative.parent.mkdir(parents=True, exist_ok=True)
        cumulative.write_text(output.read_text(encoding="utf-8"), encoding="utf-8")
        cumulative.chmod(0o600)
    print(f"TOMBSTONE_EXPORT_OK entries={len(ordered)} authority={len([a for a in ordered if a.get('kind') == 'AUTHORITY'])}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", nargs="?", default="apply", choices=("apply", "export"))
    parser.add_argument("--journal", type=Path)
    parser.add_argument("--storage", type=Path, default=Path(os.environ.get("DORMPRINT_STORAGE_ROOT", "/storage")))
    parser.add_argument("--database", default=None)
    parser.add_argument("--psql", default="psql")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--cumulative", type=Path)
    args = parser.parse_args()

    if args.command == "export":
        if not args.output:
            fail("usage: apply_tombstones.py export --output FILE [--cumulative FILE]", 2)
        return export_journal(args)

    if not args.journal:
        fail("usage: apply_tombstones.py --journal FILE [--dry-run] [--database NAME]", 2)
    entries = load_journal(args.journal)
    incomplete, plans, authorities = validate_entries(entries)
    if incomplete:
        for entry in incomplete:
            print(
                f"TOMBSTONE_INCOMPLETE trashId={entry.get('trashId')} "
                f"target={entry.get('targetType')}:{entry.get('targetId')} "
                "missing exact purgedRows/purgedFiles",
                file=sys.stderr,
            )
        fail(f"TOMBSTONE_INCOMPLETE count={len(incomplete)}; restore must not expose revived data", 3)

    if args.dry_run:
        print_replay(plans, authorities)
        row_count = sum(len(plan["rows"]) for plan in plans)
        file_count = sum(len(plan["files"]) for plan in plans)
        facts, latest = choose_authority(authorities)
        print(
            f"TOMBSTONE_OK entries={len(plans)} rows={row_count} files={file_count} "
            f"authority={len(facts['staff'])}/{len(facts['accounts'])} latest={str(latest).lower()} dry-run=true"
        )
        return 0

    database = args.database or os.environ.get("PGDATABASE", "")
    if not database:
        fail("TOMBSTONE_APPLY_FAILED: --database or PGDATABASE is required", 2)
    # The database replay and the private-file unlink are one maintenance
    # operation. Hold the exact backup flock until both finish.
    with maintenance_lock(args.storage):
        deleted_files = execute_replay(plans, authorities, database, args.storage, args.psql)
    row_count = sum(len(plan["rows"]) for plan in plans)
    print(f"TOMBSTONE_OK entries={len(plans)} rows={row_count} files={deleted_files}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
