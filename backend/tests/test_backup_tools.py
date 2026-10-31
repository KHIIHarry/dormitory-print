"""Focused checks for the backup verification and tombstone replay tools.

The tools are run as real programs so a manifest mismatch or an incomplete
tombstone journal cannot silently report success.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

BACKUP = Path(__file__).resolve().parents[2] / "deploy" / "backup"


def _run(args, cwd=None):
    return subprocess.run([sys.executable, *map(str, args)], text=True,
                          capture_output=True, cwd=cwd, timeout=30)


def test_manifest_verifier_rejects_tampered_immutable_pdf(tmp_path):
    root = tmp_path / "storage"
    file = root / "artifacts" / "aa" / "final.pdf"
    file.parent.mkdir(parents=True)
    file.write_bytes(b"%PDF-1.4 immutable final\n")
    digest = hashlib.sha256(file.read_bytes()).hexdigest()
    manifest = tmp_path / "files.manifest"
    manifest.write_text(f"{digest}  ./artifacts/aa/final.pdf\n", encoding="utf-8")
    immutable = tmp_path / "immutable.tsv"
    immutable.write_text(f"artifact-1\tartifacts/aa/final.pdf\t{digest}\n", encoding="utf-8")

    result = _run([BACKUP / "verify_manifest.py", "--manifest", manifest,
                   "--root", root, "--immutable", immutable])
    assert result.returncode == 0, result.stdout + result.stderr
    assert "MANIFEST_OK" in result.stdout

    file.write_bytes(b"%PDF-1.4 tampered\n")
    result = _run([BACKUP / "verify_manifest.py", "--manifest", manifest,
                   "--root", root, "--immutable", immutable])
    assert result.returncode != 0
    assert "MANIFEST_MISMATCH" in result.stdout + result.stderr


def test_manifest_verifier_rejects_missing_immutable_artifact(tmp_path):
    root = tmp_path / "storage"
    root.mkdir()
    manifest = tmp_path / "files.manifest"
    manifest.write_text("", encoding="utf-8")
    immutable = tmp_path / "immutable.tsv"
    immutable.write_text("artifact-1\tartifacts/missing.pdf\t" + "0" * 64 + "\n",
                         encoding="utf-8")
    result = _run([BACKUP / "verify_manifest.py", "--manifest", manifest,
                   "--root", root, "--immutable", immutable])
    assert result.returncode != 0
    assert "MISSING" in result.stdout + result.stderr


def test_tombstone_replay_requires_complete_rows_and_never_fakes_success(tmp_path):
    journal = tmp_path / "tombstones.jsonl"
    incomplete = {"version": 1, "trashId": "trash-1", "targetType": "ORDER",
                  "targetId": "order-1", "purgedAt": "2026-10-30T10:00:00+00:00",
                  "rows": []}
    journal.write_text(json.dumps(incomplete) + "\n", encoding="utf-8")
    result = _run([BACKUP / "apply_tombstones.py", "--journal", journal,
                   "--dry-run", "--storage", tmp_path / "storage"])
    assert result.returncode != 0
    assert "TOMBSTONE_INCOMPLETE" in result.stdout + result.stderr

    complete = dict(incomplete)
    complete["rows"] = [
        {"table": "print_tasks", "id": "print-1"},
        {"table": "orders", "id": "order-1"},
    ]
    complete["files"] = ["artifacts/aa/final.pdf"]
    journal.write_text(json.dumps(complete) + "\n", encoding="utf-8")
    result = _run([BACKUP / "apply_tombstones.py", "--journal", journal,
                   "--dry-run", "--storage", tmp_path / "storage"])
    assert result.returncode == 0, result.stdout + result.stderr
    assert "DELETE FROM print_tasks" in result.stdout
    assert "DELETE FROM orders" in result.stdout
    assert "TOMBSTONE_OK" in result.stdout
    assert "artifacts/aa/final.pdf" in result.stdout


def test_tombstone_whitelist_rejects_caller_supplied_table(tmp_path):
    journal = tmp_path / "tombstones.jsonl"
    entry = {"version": 1, "trashId": "trash-1", "targetType": "ORDER",
             "targetId": "order-1", "purgedAt": "2026-10-30T10:00:00+00:00",
             "rows": [{"table": "pg_shadow", "id": "x"}]}
    journal.write_text(json.dumps(entry) + "\n", encoding="utf-8")
    result = _run([BACKUP / "apply_tombstones.py", "--journal", journal,
                   "--dry-run", "--storage", tmp_path / "storage"])
    assert result.returncode != 0
    assert "TOMBSTONE_INVALID" in result.stdout + result.stderr


def test_restore_refuses_to_overwrite_the_primary_database(tmp_path):
    env = os.environ.copy()
    env.update({"PGDATABASE": "dormprint", "RESTIC_REPOSITORY": "", "RESTIC_PASSWORD": ""})
    result = subprocess.run(
        ["bash", str(BACKUP / "restore.sh"), "--snapshot", "latest",
         "--target-database", "dormprint", "--target-storage", str(tmp_path)],
        env=env, text=True, capture_output=True, timeout=30)
    assert result.returncode != 0
    assert "REFUS" in result.stdout + result.stderr
