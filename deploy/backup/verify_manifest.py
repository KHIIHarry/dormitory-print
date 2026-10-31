#!/usr/bin/env python3
"""Verify a private-volume manifest and immutable final-PDF hashes.

The tool recomputes sha256 from the restore target; it never trusts a manifest
or a database row without checking the file itself.  It exits non-zero on the
first category of mismatch so a restore cannot report success on corrupt or
missing private files.
"""
from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path
import subprocess
import sys


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def safe_relative(path: str) -> Path:
    candidate = Path(path.strip())
    if candidate.is_absolute():
        candidate = Path(*candidate.parts[1:])
    while candidate.parts and candidate.parts[0] == ".":
        candidate = Path(*candidate.parts[1:])
    if not candidate.parts or ".." in candidate.parts:
        raise ValueError("unsafe path")
    return candidate


def parse_manifest(path: Path) -> list[tuple[str, Path]]:
    rows: list[tuple[str, Path]] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        if not raw.strip():
            continue
        parts = raw.split(None, 1)
        if len(parts) != 2 or len(parts[0]) != 64:
            raise ValueError(f"invalid manifest line: {raw!r}")
        digest, name = parts
        name = name.lstrip(" *")
        rows.append((digest.lower(), safe_relative(name)))
    return rows


def check_manifest(root: Path, manifest: Path) -> list[str]:
    errors: list[str] = []
    rows = parse_manifest(manifest)
    for expected, relative in rows:
        path = root / relative
        if not path.is_file():
            errors.append(f"MANIFEST_MISSING {relative}")
            continue
        actual = sha256_file(path)
        if actual != expected:
            errors.append(f"MANIFEST_MISMATCH {relative} expected={expected} actual={actual}")
    return errors


def check_immutable(root: Path, immutable: Path) -> list[str]:
    errors: list[str] = []
    if not immutable.exists():
        return [f"MANIFEST_MISSING immutable-list={immutable}"]
    for raw in immutable.read_text(encoding="utf-8").splitlines():
        if not raw.strip():
            continue
        fields = raw.split("\t")
        if len(fields) < 3:
            errors.append(f"IMMUTABLE_INVALID {raw!r}")
            continue
        artifact_id, key, expected = fields[0], fields[1], fields[2].lower()
        try:
            relative = safe_relative(key)
        except ValueError:
            errors.append(f"IMMUTABLE_INVALID artifact={artifact_id}")
            continue
        path = root / relative
        if not path.is_file():
            errors.append(f"IMMUTABLE_MISSING artifact={artifact_id} key={key}")
            continue
        actual = sha256_file(path)
        if actual != expected:
            errors.append(
                f"IMMUTABLE_MISMATCH artifact={artifact_id} key={key} "
                f"expected={expected} actual={actual}")
    return errors


def psql_rows(database: str, query: str) -> list[list[str]]:
    command = ["psql", "-X", "--no-password", "-d", database, "-A", "-t", "-F", "\t", "-c", query]
    result = subprocess.run(command, text=True, capture_output=True)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "psql failed")
    return [line.split("\t") for line in result.stdout.splitlines() if line]


def check_database(root: Path, database: str) -> list[str]:
    errors: list[str] = []
    artifact_query = """
        SELECT ar.id, a.storage_key, ar.sha256
          FROM artifacts ar JOIN assets a ON a.id = ar.asset_id
         ORDER BY ar.id
    """
    for row in psql_rows(database, artifact_query):
        if len(row) < 3:
            continue
        artifact_id, key, expected = row[0], row[1], row[2].lower()
        try:
            relative = safe_relative(key)
        except ValueError:
            errors.append(f"DATABASE_IMMUTABLE_INVALID artifact={artifact_id}")
            continue
        path = root / relative
        if not path.is_file():
            errors.append(f"DATABASE_IMMUTABLE_MISSING artifact={artifact_id} key={key}")
            continue
        actual = sha256_file(path)
        if actual != expected:
            errors.append(
                f"DATABASE_IMMUTABLE_MISMATCH artifact={artifact_id} key={key} "
                f"expected={expected} actual={actual}")
    asset_query = """
        SELECT id, storage_key, sha256 FROM assets
         WHERE deleted_at IS NULL AND storage_key <> '' ORDER BY id
    """
    for row in psql_rows(database, asset_query):
        if len(row) < 3:
            continue
        asset_id, key, expected = row[0], row[1], row[2].lower()
        try:
            relative = safe_relative(key)
        except ValueError:
            errors.append(f"DATABASE_ASSET_INVALID asset={asset_id}")
            continue
        path = root / relative
        if not path.is_file():
            errors.append(f"DATABASE_ASSET_MISSING asset={asset_id} key={key}")
            continue
        actual = sha256_file(path)
        if actual != expected:
            errors.append(
                f"DATABASE_ASSET_MISMATCH asset={asset_id} key={key} "
                f"expected={expected} actual={actual}")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--immutable", type=Path)
    parser.add_argument("--database-name")
    args = parser.parse_args()

    if not args.root.is_dir():
        print(f"MANIFEST_MISMATCH root-missing={args.root}", file=sys.stderr)
        return 1
    if not args.manifest.is_file():
        print(f"MANIFEST_MISMATCH manifest-missing={args.manifest}", file=sys.stderr)
        return 1

    try:
        errors = check_manifest(args.root, args.manifest)
        if args.immutable:
            errors.extend(check_immutable(args.root, args.immutable))
        if args.database_name:
            errors.extend(check_database(args.root, args.database_name))
    except Exception as error:  # noqa: BLE001 - surface a stable failure
        print(f"MANIFEST_VERIFY_FAILED {type(error).__name__}: {error}", file=sys.stderr)
        return 2

    if errors:
        for error in errors[:100]:
            print(error, file=sys.stderr)
        print(f"MANIFEST_MISMATCH count={len(errors)}", file=sys.stderr)
        return 1

    manifest_count = len(parse_manifest(args.manifest))
    print(f"MANIFEST_OK files={manifest_count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
