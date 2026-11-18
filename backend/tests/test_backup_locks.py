"""Exercise restic repository-lock recovery without touching a real repository.

The backup scripts run in the production image, which has restic, flock and
GNU coreutils.  These tests put deterministic fakes first on PATH so the
control flow, exit codes and cleanup can be checked on any developer machine.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
import textwrap

import pytest

BACKUP = Path(__file__).resolve().parents[2] / "deploy" / "backup"


FAKE_RESTIC = r"""#!/usr/bin/env python3
import json
import os
from pathlib import Path
import sys
import time

state = Path(os.environ["FAKE_RESTIC_STATE"])
args = sys.argv[1:]
with (state / "calls.jsonl").open("a", encoding="utf-8") as handle:
    handle.write(json.dumps(args) + "\n")

locks = state / "locks"
command = args[0] if args else ""


def lock_ids():
    return [line for line in locks.read_text(encoding="utf-8").splitlines()
            if line.strip()]


if command == "list" and len(args) > 1 and args[1] == "locks":
    for lock_id in lock_ids():
        print(lock_id)
    raise SystemExit(0)

if command == "unlock":
    rc = int(os.environ.get("FAKE_UNLOCK_RC", "0"))
    if rc:
        print("fake unlock failed", file=sys.stderr)
        raise SystemExit(rc)
    if os.environ.get("FAKE_UNLOCK_REMOVES") == "1":
        locks.write_text("", encoding="utf-8")
    raise SystemExit(0)

if command == "cat":
    raise SystemExit(0)

if command == "backup":
    sleep_for = float(os.environ.get("FAKE_BACKUP_SLEEP", "0"))
    if sleep_for:
        time.sleep(sleep_for)
    retry = "--retry-lock" in args
    rc = int(os.environ.get("FAKE_RETRY_RC" if retry else "FAKE_OP_RC", "0"))
    output = os.environ.get("FAKE_RETRY_STDOUT" if retry else "FAKE_OP_STDOUT", "")
    if output:
        print(output)
    if rc:
        print("fake backup failure", file=sys.stderr)
    raise SystemExit(rc)

if command == "forget":
    retry = "--retry-lock" in args
    if retry:
        rc = int(os.environ.get("FAKE_FORGET_RETRY_RC",
                                os.environ.get("FAKE_RETRY_RC", "0")))
    else:
        rc = int(os.environ.get("FAKE_FORGET_OP_RC",
                                os.environ.get("FAKE_FORGET_RC", "0")))
    output = os.environ.get("FAKE_FORGET_STDOUT", "")
    if output:
        print(output)
    if rc:
        print("fake forget failure", file=sys.stderr)
    raise SystemExit(rc)

raise SystemExit(0)
"""


FAKE_PG_DUMP = r"""#!/usr/bin/env python3
import os
from pathlib import Path
import sys
import time

output = None
for arg in sys.argv[1:]:
    if arg.startswith("--file="):
        output = arg.split("=", 1)[1]
if not output:
    raise SystemExit(2)
Path(output).write_bytes(b"PGDMP fake dump\n")
marker = os.environ.get("FAKE_PG_DUMP_MARKER")
if marker:
    Path(marker).write_text("started", encoding="utf-8")
time.sleep(float(os.environ.get("FAKE_PG_DUMP_SLEEP", "0")))
"""


FAKE_PSQL = r"""#!/usr/bin/env python3
import os
import sys

args = sys.argv[1:]
query = ""
if "-c" in args:
    query = args[args.index("-c") + 1]
if "SELECT 'ASSET'" in query:
    print("ASSET\tasset-1\tartifacts/a.pdf\t" + os.environ["FAKE_ASSET_SHA"])
elif "AUTHORITY" in query:
    print('{"version":2,"kind":"AUTHORITY","trashId":"__authority__",'
          '"targetType":"AUTHORITY","targetId":"__authority__",'
          '"generatedAt":"2026-11-18T00:00:00Z",'
          '"facts":{"staff":[],"accounts":[]}}')
"""


FAKE_FLOCK = "#!/bin/sh\nexit 0\n"


def _write_exec(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(0o755)


def _setup(tmp_path: Path, *, locks: str = "") -> dict:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _write_exec(bin_dir / "restic", FAKE_RESTIC)
    _write_exec(bin_dir / "pg_dump", FAKE_PG_DUMP)
    _write_exec(bin_dir / "psql", FAKE_PSQL)
    _write_exec(bin_dir / "flock", FAKE_FLOCK)

    storage = tmp_path / "storage"
    (storage / "artifacts").mkdir(parents=True)
    pdf = storage / "artifacts" / "a.pdf"
    pdf.write_bytes(b"%PDF-1.4 backup lock test\n")
    sha = hashlib.sha256(pdf.read_bytes()).hexdigest()

    state = tmp_path / "state"
    state.mkdir()
    fake_state = tmp_path / "fake-restic"
    fake_state.mkdir()
    (fake_state / "locks").write_text(locks, encoding="utf-8")
    (fake_state / "calls.jsonl").touch()

    env = os.environ.copy()
    env.update({
        "PATH": f"{bin_dir}{os.pathsep}{env.get('PATH', '')}",
        "DORMPRINT_STORAGE_ROOT": str(storage),
        "BACKUP_STATE_DIR": str(state),
        "RESTIC_REPOSITORY": "fake-repository",
        "RESTIC_PASSWORD": "fake-password",
        "RESTIC_CACHE_DIR": str(state / "restic-cache"),
        "PGHOST": "127.0.0.1",
        "PGDATABASE": "dormprint",
        "PGUSER": "dormprint",
        "PGPASSWORD": "fake-password",
        "BACKUP_RESTIC_LOCK_RETRY_SECONDS": "1",
        "FAKE_RESTIC_STATE": str(fake_state),
        "FAKE_OP_RC": "0",
        "FAKE_RETRY_RC": "0",
        "FAKE_FORGET_RC": "0",
        "FAKE_UNLOCK_RC": "0",
        "FAKE_UNLOCK_REMOVES": "0",
        "FAKE_ASSET_SHA": sha,
        "BACKUP_LIB": str(BACKUP / "lib.sh"),
    })
    return {
        "env": env,
        "storage": storage,
        "state": state,
        "fake_state": fake_state,
        "locks": fake_state / "locks",
        "calls": fake_state / "calls.jsonl",
        "lib": BACKUP / "lib.sh",
        "marker": tmp_path / "pg-dump-started",
    }


def _run_lib(env: dict, snippet: str) -> subprocess.CompletedProcess:
    script = 'source "$BACKUP_LIB"\n' + snippet
    return subprocess.run(["bash", "-c", script], env=env, text=True,
                          capture_output=True, timeout=30)


def _run_backup(env: dict) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", str(BACKUP / "backup.sh"), "--mode", "daily"],
        env=env, text=True, capture_output=True, timeout=30,
    )


def _calls(env: dict) -> list[list[str]]:
    lines = env["calls"].read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines if line.strip()]


def test_unlock_preserves_active_lock_and_never_uses_remove_all(tmp_path):
    setup = _setup(tmp_path, locks="active-lock\n")
    result = _run_lib(setup["env"], "restic_unlock_stale startup")
    assert result.returncode == 0, result.stderr
    assert "RESTIC_LOCKS context=startup before=1" in result.stderr
    assert "RESTIC_UNLOCK context=startup removed=0 remaining=1" in result.stderr
    assert setup["locks"].read_text(encoding="utf-8").strip() == "active-lock"
    assert ["unlock"] in _calls(setup)
    assert not any("--remove-all" in call for call in _calls(setup))


def test_unlock_removes_only_stale_locks(tmp_path):
    setup = _setup(tmp_path, locks="stale-lock\n")
    setup["env"]["FAKE_UNLOCK_REMOVES"] = "1"
    result = _run_lib(setup["env"], "restic_unlock_stale startup")
    assert result.returncode == 0, result.stderr
    assert "RESTIC_UNLOCK context=startup removed=1 remaining=0" in result.stderr
    assert setup["locks"].read_text(encoding="utf-8").strip() == ""


def test_lock_contention_cleans_stale_lock_then_retries_with_retry_lock(tmp_path):
    setup = _setup(tmp_path, locks="stale-lock\n")
    env = setup["env"]
    env.update({"FAKE_OP_RC": "11", "FAKE_UNLOCK_REMOVES": "1"})
    result = _run_lib(
        env,
        'rc=0; run_restic_with_lock_recovery backup "$FAKE_RESTIC_STATE/op.log" '
        'backup --stdin || rc=$?; printf "RC=%s\\n" "$rc"; cat "$FAKE_RESTIC_STATE/op.log"',
    )
    assert result.returncode == 0, result.stderr
    assert "RC=0" in result.stdout
    assert "RESTIC_LOCK_RECOVERED operation=backup retry_lock=1s" in result.stderr
    calls = _calls(setup)
    assert calls[0] == ["backup", "--stdin"]
    assert calls[-1] == ["backup", "--stdin", "--retry-lock", "1s"]
    assert ["unlock"] in calls


def test_active_lock_timeout_returns_11_and_preserves_lock(tmp_path):
    setup = _setup(tmp_path, locks="active-lock\n")
    env = setup["env"]
    env.update({"FAKE_FORGET_OP_RC": "11", "FAKE_FORGET_RETRY_RC": "11"})
    result = _run_lib(
        env,
        'rc=0; run_restic_with_lock_recovery forget "$FAKE_RESTIC_STATE/op.log" '
        'forget --prune || rc=$?; printf "RC=%s\\n" "$rc"',
    )
    assert result.returncode == 0, result.stderr
    assert "RC=11" in result.stdout
    assert "RESTIC_LOCK_TIMEOUT operation=forget" in result.stderr
    assert setup["locks"].read_text(encoding="utf-8").strip() == "active-lock"


def test_non_lock_failure_is_not_retried(tmp_path):
    setup = _setup(tmp_path)
    env = setup["env"]
    env.update({"FAKE_OP_RC": "4", "FAKE_OP_STDOUT": "fatal upload error"})
    result = _run_lib(
        env,
        'rc=0; run_restic_with_lock_recovery backup "$FAKE_RESTIC_STATE/op.log" '
        'backup --stdin || rc=$?; printf "RC=%s\\n" "$rc"',
    )
    assert "RC=4" in result.stdout
    assert "RESTIC_LOCK_CONTENTION" not in result.stderr
    assert ["unlock"] not in _calls(setup)
    assert len([c for c in _calls(setup) if c[0] == "backup"]) == 1


def test_unlock_failure_preserves_original_exit_status(tmp_path):
    setup = _setup(tmp_path, locks="active-lock\n")
    env = setup["env"]
    env.update({"FAKE_OP_RC": "11", "FAKE_UNLOCK_RC": "1"})
    result = _run_lib(
        env,
        'rc=0; run_restic_with_lock_recovery backup "$FAKE_RESTIC_STATE/op.log" '
        'backup --stdin || rc=$?; printf "RC=%s\\n" "$rc"',
    )
    assert "RC=11" in result.stdout
    assert "RESTIC_LOCK_RECOVERY_FAILED operation=backup" in result.stderr
    assert len([c for c in _calls(setup) if c[0] == "backup"]) == 1


def test_retry_non_lock_failure_returns_that_failure(tmp_path):
    setup = _setup(tmp_path, locks="stale-lock\n")
    env = setup["env"]
    env.update({"FAKE_OP_RC": "11", "FAKE_UNLOCK_REMOVES": "1",
                "FAKE_RETRY_RC": "4", "FAKE_RETRY_STDOUT": "retry failed"})
    result = _run_lib(
        env,
        'rc=0; run_restic_with_lock_recovery backup "$FAKE_RESTIC_STATE/op.log" '
        'backup --stdin || rc=$?; printf "RC=%s\\n" "$rc"; '
        'cat "$FAKE_RESTIC_STATE/op.log"',
    )
    assert "RC=4" in result.stdout
    assert "RESTIC_LOCK_RECOVERED" not in result.stderr
    assert "RESTIC_LOCK_TIMEOUT" not in result.stderr
    assert "retry failed" in result.stdout


def test_invalid_retry_seconds_fails_before_any_repository_work(tmp_path):
    env = _setup(tmp_path)["env"]
    env["BACKUP_RESTIC_LOCK_RETRY_SECONDS"] = "not-a-number"
    result = _run_lib(env, "restic_lock_retry_seconds")
    assert result.returncode == 2
    assert "invalid BACKUP_RESTIC_LOCK_RETRY_SECONDS" in result.stderr


def test_retry_seconds_accepts_zero_and_rejects_above_max(tmp_path):
    setup = _setup(tmp_path)
    env = setup["env"]
    env["BACKUP_RESTIC_LOCK_RETRY_SECONDS"] = "0"
    result = _run_lib(env, "restic_lock_retry_seconds")
    assert result.returncode == 0
    assert result.stdout == "0"

    env["BACKUP_RESTIC_LOCK_RETRY_SECONDS"] = "3601"
    result = _run_lib(env, "restic_lock_retry_seconds")
    assert result.returncode == 2
    assert "must be between 0 and 3600" in result.stderr


def test_startup_unlock_failure_does_not_block_backup(tmp_path):
    setup = _setup(tmp_path, locks="active-lock\n")
    env = setup["env"]
    env.update({
        "FAKE_UNLOCK_RC": "1",
        "FAKE_OP_STDOUT": '{"message_type":"summary","snapshot_id":"abc12345"}',
    })
    result = _run_backup(env)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "RESTIC_UNLOCK_FAILED context=startup" in result.stderr
    assert "RESTIC_LOCK_RECOVERY_SKIPPED context=startup" in result.stderr
    assert "BACKUP_OK snapshot=abc12345 mode=daily" in result.stdout
    assert setup["locks"].read_text(encoding="utf-8").strip() == "active-lock"


def test_successful_daily_backup_cleans_stale_lock_before_backup(tmp_path):
    setup = _setup(tmp_path, locks="stale-lock\n")
    env = setup["env"]
    env["FAKE_UNLOCK_REMOVES"] = "1"
    env["FAKE_OP_STDOUT"] = '{"message_type":"summary","snapshot_id":"abc12345"}'
    result = _run_backup(env)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "BACKUP_OK snapshot=abc12345 mode=daily" in result.stdout
    assert "RESTIC_UNLOCK context=startup removed=1 remaining=0" in result.stderr
    assert setup["locks"].read_text(encoding="utf-8").strip() == ""
    success = json.loads((setup["state"] / "last-success.json").read_text(encoding="utf-8"))
    assert success["snapshot"] == "abc12345"
    calls = _calls(setup)
    assert calls[0] == ["cat", "config"]
    unlock_index = calls.index(["unlock"])
    backup_index = next(i for i, call in enumerate(calls) if call[0] == "backup")
    assert unlock_index < backup_index


def test_daily_prune_failure_does_not_write_success(tmp_path):
    setup = _setup(tmp_path)
    env = setup["env"]
    env["FAKE_OP_STDOUT"] = '{"message_type":"summary","snapshot_id":"abc12345"}'
    env["FAKE_FORGET_RC"] = "4"
    result = _run_backup(env)
    assert result.returncode == 4
    assert "BACKUP_FAILED: retention/prune failed" in result.stderr
    assert not (setup["state"] / "last-success.json").exists()


def test_active_lock_exits_backup_busy_without_success(tmp_path):
    setup = _setup(tmp_path, locks="active-lock\n")
    env = setup["env"]
    env.update({"FAKE_OP_RC": "11", "FAKE_RETRY_RC": "11"})
    result = _run_backup(env)
    assert result.returncode == 5
    assert "BACKUP_BUSY: restic repository lock is held by another process" in result.stderr
    assert not (setup["state"] / "last-success.json").exists()
    assert setup["locks"].read_text(encoding="utf-8").strip() == "active-lock"


def test_active_lock_during_prune_exits_backup_busy_without_success(tmp_path):
    setup = _setup(tmp_path)
    env = setup["env"]
    env.update({
        "FAKE_OP_STDOUT": '{"message_type":"summary","snapshot_id":"abc12345"}',
        "FAKE_FORGET_OP_RC": "11",
        "FAKE_FORGET_RETRY_RC": "11",
    })
    result = _run_backup(env)
    assert result.returncode == 5
    assert "BACKUP_BUSY: retention/prune is blocked by an active restic repository lock" in result.stderr
    assert not (setup["state"] / "last-success.json").exists()


def test_signal_cleans_work_dir_and_preserves_exit_status(tmp_path):
    setup = _setup(tmp_path)
    env = setup["env"]
    env.update({"FAKE_PG_DUMP_SLEEP": "5",
                "FAKE_PG_DUMP_MARKER": str(setup["marker"])})
    process = subprocess.Popen(
        ["bash", str(BACKUP / "backup.sh"), "--mode", "daily"],
        env=env, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        start_new_session=True,
    )
    try:
        deadline = time.monotonic() + 10
        while not setup["marker"].exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        assert setup["marker"].exists(), "backup did not reach pg_dump"
        os.killpg(process.pid, signal.SIGTERM)
        stdout, stderr = process.communicate(timeout=15)
    finally:
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=5)

    assert process.returncode == 143, (stdout, stderr)
    assert "BACKUP_INTERRUPTED mode=daily signal=TERM" in stderr
    assert "BACKUP_FAILED mode=daily status=143" in stderr
    assert not list(setup["state"].glob("work.*"))
    assert not (setup["state"] / "last-success.json").exists()


def test_cleanup_failure_is_reported_without_masking_original_status(tmp_path):
    setup = _setup(tmp_path)
    env = setup["env"]
    env["FAKE_FORGET_RC"] = "4"
    bin_dir = Path(env["PATH"].split(os.pathsep, 1)[0])
    _write_exec(
        bin_dir / "rm",
        "#!/bin/sh\n"
        "case \"$*\" in\n"
        "  *work.*) exit 1 ;;\n"
        "esac\n"
        "exec /bin/rm \"$@\"\n",
    )
    result = _run_backup(env)
    assert result.returncode == 4, result.stdout + result.stderr
    assert "BACKUP_CLEANUP_FAILED mode=daily" in result.stderr
    assert "BACKUP_FAILED mode=daily status=4" in result.stderr
    assert not (setup["state"] / "last-success.json").exists()
