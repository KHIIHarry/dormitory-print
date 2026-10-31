"""Coordination between private-storage maintenance and backups.

Backups hold an exclusive ``flock`` on
``<storage_root>/.coordination/maintenance.lock`` from before ``pg_dump``
until the restic upload finishes.  Permanent deletion must take the same
lock before it either removes rows or unlinks files, otherwise a backup can
capture the database before a purge and the storage afterwards (or the other
way around) and a restore can resurrect deleted objects.

The helpers in this module deliberately do not import ``app.maintenance``:
the maintenance owner wires ``purge_guard`` into ``process_purge_job`` and
``storage_gc_guard`` around the normal Q29 expiry sweep.  Q29 physical files
are staged until the caller's transaction ends, so a backup never sees an old
database snapshot pointing at an already-unlinked private file.
"""

from __future__ import annotations

import errno
import fcntl
import hashlib
import json
import math
import os
import secrets
import shutil
import stat
import time
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

from sqlalchemy import event, inspect as sa_inspect, select
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified
from sqlalchemy.sql.dml import Delete, Update

from .core import DomainError
from .models import AnonymousLedger, AuditLog, Base, TrashEntry
from .settings import get_settings


# Tables (and exactly which nullable FK columns) that a tombstone may move to
# NULL during replay.  Keep this in sync with deploy/backup/apply_tombstones.py.
DETACHABLE_COLUMNS: dict[str, frozenset[str]] = {
    'preparations': frozenset({'source_grant_id'}),
    'order_access_grants': frozenset({'source_grant_id'}),
    'wage_entries': frozenset({'order_id', 'assignment_id'}),
    'promotion_events': frozenset({'order_id'}),
    'coupon_grants': frozenset({'reserved_order_id', 'used_order_id'}),
    'referrals': frozenset({'qualified_order_id'}),
}

# All tombstone-replayable tables, derived from the models so a newly added
# model cannot silently become unreplayable.  apply_tombstones.py has the same
# explicit allow-list on the restore side.
CAPTURE_TABLES: frozenset[str] = frozenset(table.name for table in Base.metadata.sorted_tables)

# Child-first order: reverse of SQLAlchemy's FK topological order.  This is
# what make replay safe without disabling constraints.
CHILD_FIRST_TABLES: tuple[str, ...] = tuple(
    table.name for table in reversed(Base.metadata.sorted_tables)
)

# Minimum operational data kept on a PURGED TrashEntry.  Names, phone numbers,
# email addresses and arbitrary JSON copied by the maintenance layer are not
# part of the tombstone contract.
_PRESERVED_ENTRY_DATA_KEYS = frozenset({
    'deletionRequestId',
    'stagedFiles',
    'cleanupPending',
    'cleanupError',
})
_GC_DIR = 'gc'
_GC_MARKER_ACTION = 'PRIVATE_FILE_GC_COMMITTED'
_GC_MARKER_TARGET = 'PRIVATE_FILE_GC'


class _LockState:
    """One logical request/transaction's reentrant lock state.

    The object is threaded through the context manager and its guard callbacks;
    it is deliberately *not* keyed by OS thread.  A sync route runs in a worker
    thread while middleware may commit the same Session on the event-loop
    thread, so thread-local state either leaks ``depth=1`` into the next
    request or hides the released FD from the commit callback.
    """

    __slots__ = ('path', 'fd', 'depth')

    def __init__(self, path: Path, fd: int) -> None:
        self.path = path
        self.fd = fd
        self.depth = 1


_lock_state_var: ContextVar[_LockState | None] = ContextVar(
    'dormprint_maintenance_lock_state', default=None
)


class MaintenanceLock:
    """Handle yielded by :func:`maintenance_lock`."""

    def __init__(self, path: Path, fd: int) -> None:
        self.path = path
        self.fd = fd


def _storage_paths() -> tuple[Path, Path, Path]:
    root = Path(get_settings().storage_root).expanduser()
    if not root.is_absolute():
        root = Path.cwd() / root
    try:
        root = root.resolve()
    except OSError as exc:  # pragma: no cover - platform dependent
        raise DomainError(
            'STORAGE_PATH_INVALID',
            '私有存储根目录不可用',
            503,
            {'reason': type(exc).__name__},
        ) from exc

    if root.exists() and not root.is_dir():
        raise DomainError('STORAGE_PATH_INVALID', '私有存储根目录不是目录', 503)
    try:
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
    except OSError as exc:
        raise DomainError(
            'STORAGE_PATH_INVALID',
            '无法创建私有存储根目录',
            503,
            {'reason': type(exc).__name__},
        ) from exc

    coordination = root / '.coordination'
    if coordination.is_symlink():
        raise DomainError('STORAGE_PATH_INVALID', '协调目录不能是符号链接', 503)
    if not coordination.exists():
        try:
            coordination.mkdir(mode=0o700)
        except OSError as exc:
            raise DomainError(
                'STORAGE_PATH_INVALID',
                '无法创建协调目录',
                503,
                {'reason': type(exc).__name__},
            ) from exc
    elif not coordination.is_dir():
        raise DomainError('STORAGE_PATH_INVALID', '协调路径不是目录', 503)

    try:
        resolved_coordination = coordination.resolve()
    except OSError as exc:  # pragma: no cover - platform dependent
        raise DomainError('STORAGE_PATH_INVALID', '协调目录不可解析', 503) from exc
    if resolved_coordination.parent != root:
        raise DomainError('STORAGE_PATH_INVALID', '协调目录越出私有存储根目录', 503)

    lock_path = coordination / 'maintenance.lock'
    if lock_path.is_symlink():
        raise DomainError('STORAGE_PATH_INVALID', '维护锁不能是符号链接', 503)
    if lock_path.exists():
        try:
            if not lock_path.is_file() or lock_path.resolve().parent != resolved_coordination:
                raise DomainError('STORAGE_PATH_INVALID', '维护锁路径不安全', 503)
        except OSError as exc:
            raise DomainError('STORAGE_PATH_INVALID', '维护锁路径不可解析', 503) from exc
    return root, resolved_coordination, lock_path


def _lock_timeout(timeout: float | int | None) -> float:
    if timeout is None:
        raw = os.environ.get('DORMPRINT_MAINTENANCE_LOCK_TIMEOUT_SECONDS')
        if raw is None or raw == '':
            raw = os.environ.get('BACKUP_LOCK_TIMEOUT_SECONDS', '300')
        timeout = raw
    try:
        value = float(timeout)
    except (TypeError, ValueError) as exc:
        raise DomainError(
            'STORAGE_LOCK_INVALID',
            '维护锁超时配置无效',
            500,
        ) from exc
    if not math.isfinite(value):
        raise DomainError(
            'STORAGE_LOCK_INVALID',
            '维护锁超时配置无效',
            500,
        )
    if value < 0:
        value = 0.0
    # A maintenance request must not hang an API worker for hours because of a
    # bad environment value.
    return min(value, 3600.0)


def _open_lock_file(lock_path: Path) -> int:
    flags = os.O_RDWR | os.O_CREAT
    flags |= getattr(os, 'O_CLOEXEC', 0)
    flags |= getattr(os, 'O_NOFOLLOW', 0)
    try:
        try:
            fd = os.open(lock_path, flags, 0o600)
        except PermissionError:
            # A read-only service account can still take an exclusive flock on
            # a read-only descriptor on Linux/macOS.  The backup script creates
            # the file writable for the common case.
            fd = os.open(
                lock_path,
                os.O_RDONLY | os.O_CREAT | getattr(os, 'O_CLOEXEC', 0) | getattr(os, 'O_NOFOLLOW', 0),
                0o600,
            )
    except OSError as exc:
        if exc.errno == errno.ELOOP:
            raise DomainError('STORAGE_PATH_INVALID', '维护锁不能是符号链接', 503) from exc
        raise DomainError(
            'STORAGE_LOCK_UNAVAILABLE',
            '无法打开维护锁',
            503,
            {'reason': exc.__class__.__name__},
        ) from exc

    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise DomainError('STORAGE_PATH_INVALID', '维护锁不是普通文件', 503)
        # The lock file is intentionally private.  The backup process runs as
        # the same service user; deployments that need group access should
        # provision the directory/file themselves and preserve this mode.
        if info.st_uid == os.geteuid():
            os.fchmod(fd, 0o600)
        elif info.st_uid != 0 and info.st_gid not in _process_groups():
            raise DomainError('STORAGE_LOCK_UNAVAILABLE', '维护锁属主不可用', 503)
        if os.fstat(fd).st_mode & stat.S_IWOTH:
            raise DomainError('STORAGE_PATH_INVALID', '维护锁不能全局可写', 503)
        return fd
    except BaseException:
        os.close(fd)
        raise


def _process_groups() -> set[int]:
    try:
        return set(os.getgroups()) | {os.getegid()}
    except OSError:  # pragma: no cover - platform dependent
        return {os.getegid()}


@contextmanager
def maintenance_lock(timeout: float | int | None = None):
    """Hold the shared maintenance/backup file lock.

    The lock path is always derived from ``get_settings().storage_root``; there
    is no second configuration knob.  A timeout raises ``DomainError`` with
    code ``TRANSIENT_IO`` and HTTP status 503 so the job framework retries the
    purge instead of losing it.
    """

    state = _lock_state_var.get()
    if state is not None and state.depth > 0 and state.fd is not None:
        state.depth += 1
        try:
            yield MaintenanceLock(state.path, state.fd)
        finally:
            state.depth -= 1
        return

    _, _, lock_path = _storage_paths()
    fd = _open_lock_file(lock_path)
    wait_for = _lock_timeout(timeout)
    deadline = time.monotonic() + wait_for
    while True:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            break
        except OSError as exc:
            if exc.errno not in (errno.EACCES, errno.EAGAIN):
                os.close(fd)
                raise DomainError(
                    'STORAGE_LOCK_UNAVAILABLE',
                    '无法获取维护锁',
                    503,
                    {'reason': exc.__class__.__name__},
                ) from exc
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                os.close(fd)
                raise DomainError(
                    'TRANSIENT_IO',
                    '备份或恢复正在使用私有存储，请稍后重试',
                    503,
                    {'lock': 'maintenance', 'timeoutSeconds': round(wait_for, 3)},
                )
            time.sleep(min(0.05, max(0.005, remaining)))

    state = _LockState(lock_path, fd)
    _lock_state_var.set(state)
    try:
        yield MaintenanceLock(lock_path, fd)
    finally:
        # This finally block may run on the middleware's event-loop thread,
        # not the worker thread that created the state.  Use the captured
        # state and never reset a ContextVar token from another context.
        state.depth = 0
        state.fd = None
        if _lock_state_var.get() is state:
            _lock_state_var.set(None)
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


def _safe_key(key: str) -> str:
    if not isinstance(key, str) or not key:
        raise DomainError('STORAGE_KEY_INVALID', '文件键不能为空', 500)
    if key.startswith('/') or '\\' in key:
        raise DomainError('STORAGE_KEY_INVALID', '文件键必须是相对 POSIX 路径', 500)
    parts = PurePosixPath(key).parts
    if not parts or any(part in ('', '.', '..') for part in parts):
        raise DomainError('STORAGE_KEY_INVALID', '文件键包含不安全路径片段', 500)
    if parts[0] == '.coordination':
        raise DomainError('STORAGE_KEY_INVALID', '文件键不能指向协调目录', 500)
    if len(key) > 512:
        raise DomainError('STORAGE_KEY_INVALID', '文件键过长', 500)
    return key


def _staging_paths(entry: TrashEntry) -> tuple[Path, Path]:
    root, coordination, _ = _storage_paths()
    if not isinstance(entry.id, str) or not entry.id or Path(entry.id).name != entry.id:
        raise DomainError('PURGE_CAPTURE_INVALID', '清理记录 ID 不能用于暂存目录', 500)
    staging = coordination / 'staging'
    if staging.is_symlink():
        raise DomainError('STORAGE_PATH_INVALID', '暂存目录不能是符号链接', 503)
    try:
        staging.mkdir(mode=0o700, exist_ok=True)
    except OSError as exc:
        raise DomainError(
            'TRANSIENT_IO',
            '无法创建私有文件暂存目录',
            503,
            {'reason': exc.__class__.__name__},
        ) from exc
    if not staging.is_dir():
        raise DomainError('STORAGE_PATH_INVALID', '暂存路径不是目录', 503)
    entry_dir = staging / entry.id
    if entry_dir.is_symlink():
        raise DomainError('STORAGE_PATH_INVALID', '清理暂存目录不能是符号链接', 503)
    try:
        entry_dir.mkdir(mode=0o700, exist_ok=True)
    except OSError as exc:
        raise DomainError(
            'TRANSIENT_IO',
            '无法创建清理暂存目录',
            503,
            {'reason': exc.__class__.__name__},
        ) from exc
    if not entry_dir.is_dir():
        raise DomainError('STORAGE_PATH_INVALID', '清理暂存路径不是目录', 503)
    return root, entry_dir


def _manifest_path(entry_dir: Path) -> Path:
    return entry_dir / 'manifest.json'


def _write_manifest(entry: TrashEntry, entry_dir: Path, staged: list[dict]) -> None:
    path = _manifest_path(entry_dir)
    payload = {'trashId': entry.id, 'files': staged}
    temporary = entry_dir / '.manifest.json.tmp'
    try:
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, sort_keys=True),
            encoding='utf-8',
        )
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    except OSError as exc:
        raise DomainError(
            'TRANSIENT_IO',
            '无法写入私有文件暂存清单',
            503,
            {'reason': exc.__class__.__name__},
        ) from exc


def _read_manifest(entry_dir: Path) -> list[dict]:
    path = _manifest_path(entry_dir)
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return []
    files = payload.get('files') if isinstance(payload, dict) else None
    if not isinstance(files, list):
        return []
    return [
        item for item in files
        if isinstance(item, dict) and isinstance(item.get('key'), str)
    ]


def _set_staged_data(entry: TrashEntry, staged: list[dict]) -> None:
    data = dict(entry.data or {})
    data['stagedFiles'] = staged
    data['cleanupPending'] = bool(staged)
    data.pop('cleanupError', None)
    entry.data = data
    flag_modified(entry, 'data')


def _remove_staged_data(entry: TrashEntry) -> None:
    data = dict(entry.data or {})
    for key in ('stagedFiles', 'cleanupPending', 'cleanupError'):
        data.pop(key, None)
    entry.data = data
    flag_modified(entry, 'data')


def _safe_source(root: Path, key: str) -> Path:
    source = root / key
    try:
        resolved = source.resolve()
    except OSError as exc:
        raise DomainError(
            'STORAGE_PATH_INVALID',
            '私有文件路径不可解析',
            500,
            {'reason': exc.__class__.__name__},
        ) from exc
    if resolved != root and root not in resolved.parents:
        raise DomainError('STORAGE_PATH_INVALID', '私有文件越出存储根目录', 500)
    if source.is_symlink():
        raise DomainError('STORAGE_PATH_INVALID', '待清理文件不能是符号链接', 500)
    return source


def _stage_one(entry: TrashEntry, key: str, staged: list[dict]) -> dict:
    safe = _safe_key(key)
    root, entry_dir = _staging_paths(entry)
    for item in list(staged):
        if item.get('key') == safe:
            if item.get('missing') and (root / safe).exists():
                staged.remove(item)
                break
            return item
    source = _safe_source(root, safe)
    if not source.exists():
        item = {'key': safe, 'missing': True}
        staged.append(item)
        _write_manifest(entry, entry_dir, staged)
        return item
    digest = hashlib.sha256(safe.encode('utf-8')).hexdigest()
    destination = entry_dir / digest
    try:
        if not destination.exists():
            # Keep the original pathname until the caller commits. A crash
            # must leave the not-yet-committed database snapshot with readable
            # files; the quarantine is an additional hard link or copy.
            try:
                os.link(source, destination)
            except OSError:
                shutil.copy2(source, destination)
    except OSError as exc:
        raise DomainError(
            'TRANSIENT_IO',
            '无法暂存待清理私有文件',
            503,
            {'key': safe, 'reason': exc.__class__.__name__},
        ) from exc
    item = {'key': safe, 'staged': str(destination.relative_to(root))}
    staged.append(item)
    _write_manifest(entry, entry_dir, staged)
    return item


def _restore_staged(root: Path, entry_dir: Path, staged: list[dict]) -> list[str]:
    failures: list[str] = []
    for item in staged:
        relative = item.get('staged')
        key = item.get('key')
        if not isinstance(relative, str) or not isinstance(key, str):
            # The manifest is our only proof that a private path was staged.
            # A malformed item must stay pending rather than being skipped and
            # letting the purge report PURGED with unowned garbage behind it.
            failures.append(key if isinstance(key, str) else '<invalid>')
            continue
        destination = root / relative
        source = root / key
        if not destination.exists():
            continue
        try:
            source.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            if source.exists():
                # The normal copy/link protocol never removed the original;
                # rollback only has to drop the quarantine link.
                if destination.is_dir() and not destination.is_symlink():
                    shutil.rmtree(destination)
                else:
                    destination.unlink(missing_ok=True)
                continue
            os.replace(destination, source)
        except OSError:
            failures.append(key)
    if not failures:
        try:
            _manifest_path(entry_dir).unlink(missing_ok=True)
            entry_dir.rmdir()
        except OSError:
            pass
    return failures


def _gc_staged(root: Path, entry_dir: Path, staged: list[dict]) -> list[str]:
    failures: list[str] = []
    for item in staged:
        relative = item.get('staged')
        key = item.get('key')
        if not isinstance(relative, str) or not isinstance(key, str):
            continue
        destination = root / relative
        source = root / key
        for path in (source, destination):
            try:
                if path.is_dir() and not path.is_symlink():
                    shutil.rmtree(path)
                else:
                    path.unlink(missing_ok=True)
            except OSError:
                failures.append(key)
    if not failures:
        try:
            _manifest_path(entry_dir).unlink(missing_ok=True)
            entry_dir.rmdir()
        except OSError:
            pass
    return failures


def _fresh_entry_update(db: Session, entry_id: str, mutate) -> bool:
    """Update after the caller's commit in a separate short transaction."""

    fresh = Session(bind=db.get_bind(), expire_on_commit=False, autoflush=False)
    try:
        row = fresh.get(TrashEntry, entry_id)
        if row is None:
            fresh.rollback()
            return False
        mutate(row)
        fresh.commit()
        return True
    except Exception:
        fresh.rollback()
        return False
    finally:
        fresh.close()


_guard_active: ContextVar[object | None] = ContextVar(
    'dormprint_purge_guard', default=None
)


def _active_guard(entry: TrashEntry):
    guard = _guard_active.get()
    if guard is not None and getattr(guard, 'entry', None) is entry:
        return guard
    return None


def record_purged_files(entry: TrashEntry, keys) -> list[str]:
    """Record and stage the exact relative files removed by a purge.

    When called inside :func:`purge_guard`, each existing file is additionally
    hard-linked (or copied) into a private quarantine directory on the same
    filesystem. The original path is retained until the purge commits, so a
    crash cannot leave a not-yet-committed database snapshot referencing a
    missing file. Commit garbage-collects both names; rollback drops the
    quarantine link. The function itself never commits.
    """

    if not isinstance(entry, TrashEntry):
        raise TypeError('entry must be a TrashEntry')
    if isinstance(keys, str):
        keys = [keys]
    data = dict(entry.data or {})
    files = list(data.get('purgedFiles') or [])
    seen = set(files)
    incoming = []
    for key in keys or ():
        safe = _safe_key(key)
        if safe not in seen:
            files.append(safe)
            seen.add(safe)
        incoming.append(safe)
    if len(files) > 20000:
        raise DomainError('PURGE_CAPTURE_LIMIT', '清理文件数量超出墓碑上限', 500)
    guard = _active_guard(entry)
    if guard is not None:
        for key in incoming:
            guard.stage(key)
    data = dict(entry.data or {})
    data['purgedFiles'] = files
    entry.data = data
    flag_modified(entry, 'data')
    return files



class _PurgeCapture:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.rows: list[tuple[str, str]] = []
        self.detaches: list[tuple[str, str, str]] = []
        self._row_seen: set[tuple[str, str]] = set()
        self._detach_seen: set[tuple[str, str, str]] = set()

    def install(self) -> None:
        event.listen(self.db, 'do_orm_execute', self._on_execute)
        event.listen(self.db, 'before_flush', self._on_flush)

    def remove(self) -> None:
        # install() is two listens; a failure between them must not make the
        # purge_guard finally block mask the original error.
        for name, handler in (
            ('do_orm_execute', self._on_execute),
            ('before_flush', self._on_flush),
        ):
            try:
                event.remove(self.db, name, handler)
            except Exception:
                pass

    def _add_row(self, table: str, row_id: str) -> None:
        key = (table, row_id)
        if key not in self._row_seen:
            self._row_seen.add(key)
            self.rows.append(key)

    def _add_detach(self, table: str, column: str, row_id: str) -> None:
        key = (table, column, row_id)
        if key not in self._detach_seen:
            self._detach_seen.add(key)
            self.detaches.append(key)

    @staticmethod
    def _bound_value(value):
        return getattr(value, 'value', value)

    def _on_execute(self, orm_execute_state) -> None:
        statement = orm_execute_state.statement
        if isinstance(statement, Delete):
            table = statement.table
            if table.name not in CAPTURE_TABLES:
                return
            criteria = tuple(getattr(statement, '_where_criteria', ()) or ())
            if not criteria:
                raise DomainError(
                    'PURGE_CAPTURE_UNBOUNDED',
                    '永久清理不能使用无条件的批量删除',
                    500,
                )
            try:
                ids = self.db.execute(select(table.c.id).where(*criteria)).scalars().all()
            except Exception as exc:  # noqa: BLE001 - report as a stable domain error
                raise DomainError(
                    'PURGE_CAPTURE_FAILED',
                    '无法确定永久清理影响的行',
                    500,
                    {'table': table.name, 'reason': exc.__class__.__name__},
                ) from exc
            for row_id in ids:
                self._add_row(table.name, str(row_id))
            return

        if isinstance(statement, Update):
            table = statement.table
            allowed = DETACHABLE_COLUMNS.get(table.name)
            if not allowed:
                return
            criteria = tuple(getattr(statement, '_where_criteria', ()) or ())
            if not criteria:
                raise DomainError(
                    'PURGE_CAPTURE_UNBOUNDED',
                    '永久清理不能使用无条件的批量更新',
                    500,
                )
            columns = []
            for column, value in (getattr(statement, '_values', {}) or {}).items():
                name = getattr(column, 'name', str(column))
                if name in allowed and self._bound_value(value) is None:
                    columns.append(name)
            if not columns:
                return
            try:
                ids = self.db.execute(select(table.c.id).where(*criteria)).scalars().all()
            except Exception as exc:  # noqa: BLE001
                raise DomainError(
                    'PURGE_CAPTURE_FAILED',
                    '无法确定外键解除影响的行',
                    500,
                    {'table': table.name, 'reason': exc.__class__.__name__},
                ) from exc
            for row_id in ids:
                for column in columns:
                    self._add_detach(table.name, column, str(row_id))

    def _on_flush(self, session: Session, flush_context, instances) -> None:
        for obj in list(session.deleted):
            table = getattr(obj, '__tablename__', None)
            row_id = getattr(obj, 'id', None)
            if table in CAPTURE_TABLES and row_id is not None:
                self._add_row(table, str(row_id))

        for obj in list(session.dirty):
            table = getattr(obj, '__tablename__', None)
            allowed = DETACHABLE_COLUMNS.get(table)
            if not allowed:
                continue
            row_id = getattr(obj, 'id', None)
            if row_id is None:
                continue
            try:
                attrs = sa_inspect(obj).attrs
            except Exception:  # noqa: BLE001 - pending objects are handled on later flush
                continue
            for column in allowed:
                history = attrs[column].history
                if history.deleted and history.added == [None]:
                    self._add_detach(table, column, str(row_id))

    @staticmethod
    def _sort_key(item, table_index: dict[str, int]):
        table = item[0]
        return (table_index.get(table, len(table_index)), table, item[-1])

    def finalize(self, entry: TrashEntry) -> None:
        table_index = {name: index for index, name in enumerate(CHILD_FIRST_TABLES)}
        rows = sorted(self.rows, key=lambda item: self._sort_key(item, table_index))
        detaches = sorted(self.detaches, key=lambda item: self._sort_key(item, table_index))

        ledger = self.db.scalar(
            select(AnonymousLedger).where(AnonymousLedger.bucket == f'trash:{entry.id}')
        )

        old = dict(entry.data or {})

        # A retry commonly reaches this guard after the target row was already
        # removed by the first attempt.  The capture is then empty, but the
        # original tombstone is the durable evidence used by restore; never
        # replace it with an empty capture.  Merge instead, so a partial retry
        # also keeps the original child rows.
        old_rows: list[tuple[str, str]] = []
        for item in old.get('purgedRows') or []:
            if (
                isinstance(item, dict)
                and item.get('table') in CAPTURE_TABLES
                and isinstance(item.get('id'), str)
            ):
                old_rows.append((item['table'], item['id']))
        merged_rows = sorted(
            dict.fromkeys(old_rows + list(rows)),
            key=lambda item: self._sort_key(item, table_index),
        )

        old_detaches: list[tuple[str, str, str]] = []
        for item in old.get('purgedDetaches') or []:
            table = item.get('table') if isinstance(item, dict) else None
            column = item.get('column') if isinstance(item, dict) else None
            row_id = item.get('id') if isinstance(item, dict) else None
            if (
                isinstance(table, str)
                and isinstance(column, str)
                and isinstance(row_id, str)
                and column in DETACHABLE_COLUMNS.get(table, frozenset())
            ):
                old_detaches.append((table, column, row_id))
        merged_detaches = sorted(
            dict.fromkeys(old_detaches + list(detaches)),
            key=lambda item: self._sort_key(item, table_index),
        )

        data = {key: old[key] for key in _PRESERVED_ENTRY_DATA_KEYS if key in old}
        data['purgedAt'] = old.get('purgedAt') or datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')
        data['purgedRows'] = [
            {'table': table, 'id': row_id} for table, row_id in merged_rows
        ]
        if merged_detaches:
            data['purgedDetaches'] = [
                {'table': table, 'column': column, 'id': row_id}
                for table, column, row_id in merged_detaches
            ]
        old_files = old.get('purgedFiles')
        data['purgedFiles'] = list(old_files) if isinstance(old_files, list) else []
        if old.get('stagedFiles'):
            data['stagedFiles'] = list(old['stagedFiles'])
            data['cleanupPending'] = True
        if ledger is not None:
            data['ledgerBucket'] = ledger.bucket
            data['ledgerTotals'] = dict(ledger.totals or {})
        elif 'ledgerBucket' in old or 'ledgerTotals' in old:
            # The retry may run before _ledger() is reached, or the ledger row
            # may already exist under the original bucket.  Keep the durable
            # snapshot rather than dropping totals from the tombstone.
            if 'ledgerBucket' in old:
                data['ledgerBucket'] = old['ledgerBucket']
            if 'ledgerTotals' in old:
                data['ledgerTotals'] = old['ledgerTotals']
        entry.data = data
        flag_modified(entry, 'data')


class _PurgeGuard:
    """Protocol state for one purge; the file lock outlives the with-block.

    The caller owns the database transaction. This object therefore never
    commits; it keeps the maintenance lock until SQLAlchemy reports that the
    caller's root transaction ended. On commit it garbage-collects quarantined
    files, on rollback it puts them back. A crash between commit and GC leaves
    the durable ``cleanupPending``/``stagedFiles`` marker for recovery.
    """

    def __init__(self, db: Session, entry: TrashEntry, timeout) -> None:
        self.db = db
        self.entry = entry
        self.staged: list[dict] = list((entry.data or {}).get('stagedFiles') or [])
        self.root_tx = None
        self.committed = False
        self.rolled_back = False
        self.failed = False
        self._listening = False
        self._released = False
        self._lock_context = maintenance_lock(timeout=timeout)
        self._lock_context.__enter__()

    def stage(self, key: str) -> dict:
        item = _stage_one(self.entry, key, self.staged)
        _set_staged_data(self.entry, self.staged)
        return item

    def bind(self, transaction) -> None:
        self.root_tx = transaction
        if not self._listening:
            event.listen(self.db, 'after_commit', self._on_commit)
            event.listen(self.db, 'after_rollback', self._on_rollback)
            event.listen(self.db, 'after_transaction_end', self._on_transaction_end)
            self._listening = True

    def _remove_listeners(self) -> None:
        if not self._listening:
            return
        for name, handler in (
            ('after_commit', self._on_commit),
            ('after_rollback', self._on_rollback),
            ('after_transaction_end', self._on_transaction_end),
        ):
            try:
                event.remove(self.db, name, handler)
            except Exception:
                pass
        self._listening = False

    def _on_commit(self, session: Session) -> None:
        if session.get_nested_transaction() is None:
            self.committed = True

    def _on_rollback(self, session: Session) -> None:
        if session.get_nested_transaction() is None:
            self.rolled_back = True

    def _on_transaction_end(self, session: Session, transaction) -> None:
        if transaction is not self.root_tx:
            return
        # Removing an event listener from inside its own dispatch mutates the
        # deque SQLAlchemy is iterating. The listeners are harmless no-ops for
        # later transactions (root_tx no longer matches); let the session own
        # them and release the lock here.
        try:
            if self.committed and not self.rolled_back and not self.failed:
                self._finalize_commit()
            else:
                self._finalize_rollback()
        except Exception:
            # Never turn a completed commit into a caller-visible exception:
            # the caller's transaction is already durable, and the staging
            # directory plus a truthful pending marker are the recovery path.
            if self.committed and not self.rolled_back:
                _fresh_entry_update(self.db, self.entry.id, self._mark_finalize_failed)
        finally:
            self.release()

    def _finalize_commit(self) -> None:
        pending = bool((self.entry.data or {}).get('cleanupPending'))
        if self.staged:
            root, entry_dir = _staging_paths(self.entry)
            failures = _gc_staged(root, entry_dir, self.staged)
        else:
            failures = []
        if failures or (pending and not self.staged):
            # The purge rows are committed, but private garbage is still
            # quarantined, or a prior pending marker has no staging manifest to
            # prove completion. Keep the truthful pending marker; maintenance
            # retry/recovery must finish the cleanup before the tombstone is
            # exported.  A missing manifest must never silently become PURGED.
            _fresh_entry_update(self.db, self.entry.id, self._mark_pending)
        else:
            _fresh_entry_update(self.db, self.entry.id, self._mark_purged)

    def _finalize_rollback(self) -> None:
        try:
            root, entry_dir = _staging_paths(self.entry)
            failures = _restore_staged(root, entry_dir, self.staged) if self.staged else []
        except DomainError:
            failures = [item.get('key') for item in self.staged]
        if self.committed and self.failed:
            # The caller committed a transaction that had failed inside the
            # guard. Files were restored above; remove stale staging metadata
            # and keep a visible retry marker rather than claiming success.
            _fresh_entry_update(self.db, self.entry.id, self._mark_failed_commit)
        elif failures:
            # A rollback could not restore every file. Leave the quarantine
            # directory for startup recovery; the original exception wins.
            pass

    @staticmethod
    def _mark_purged(row: TrashEntry) -> None:
        row.status = 'PURGED'
        data = dict(row.data or {})
        data['purgedAt'] = data.get('purgedAt') or datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')
        data.pop('stagedFiles', None)
        data.pop('cleanupPending', None)
        data.pop('cleanupError', None)
        row.data = data
        flag_modified(row, 'data')

    @staticmethod
    def _mark_pending(row: TrashEntry) -> None:
        row.status = 'PURGE_PENDING'
        data = dict(row.data or {})
        data['cleanupPending'] = True
        data['cleanupError'] = 'STAGING_GC_FAILED'
        row.data = data
        flag_modified(row, 'data')

    @staticmethod
    def _mark_failed_commit(row: TrashEntry) -> None:
        row.status = 'PURGE_PENDING'
        data = dict(row.data or {})
        data.pop('stagedFiles', None)
        data['cleanupPending'] = False
        data['cleanupError'] = 'PURGE_GUARD_FAILED'
        row.data = data
        flag_modified(row, 'data')

    @staticmethod
    def _mark_finalize_failed(row: TrashEntry) -> None:
        row.status = 'PURGE_PENDING'
        data = dict(row.data or {})
        data['cleanupPending'] = True
        data['cleanupError'] = 'STAGING_GC_FAILED'
        row.data = data
        flag_modified(row, 'data')

    def finish_without_transaction(self) -> None:
        try:
            if self.failed or self.staged:
                root, entry_dir = _staging_paths(self.entry)
                _restore_staged(root, entry_dir, self.staged)
        except DomainError:
            pass
        finally:
            self.release()

    def release(self) -> None:
        if self._released:
            return
        self._released = True
        self._lock_context.__exit__(None, None, None)


@contextmanager
def purge_guard(
    db: Session,
    entry: TrashEntry,
    timeout: float | int | None = None,
    files=None,
):
    """Capture and stage a permanent purge without committing the caller.

    Usage::

        with purge_guard(db, entry, files=plan['files']):
            ... delete rows and NULL detaches ...
            entry.status = 'PURGED'
        db.commit()   # backup lock remains held until this returns

    The maintenance file lock is acquired before any row/file change and is
    released only when the caller's root transaction commits or rolls back.
    Files are hard-linked (or copied) into ``.coordination/staging/<trashId>``
    before the database commit while the original path stays readable; commit
    garbage-collects both names, rollback drops the quarantine link. ``db`` is
    never committed by this helper.
    """

    if not isinstance(entry, TrashEntry):
        raise TypeError('entry must be a TrashEntry')
    if not isinstance(db, Session):
        raise TypeError('db must be a SQLAlchemy Session')
    if entry not in db:
        raise DomainError('PURGE_CAPTURE_INVALID', '清理记录不在当前事务中', 500)

    capture = _PurgeCapture(db)
    guard = None
    previous = _guard_active.get()
    try:
        guard = _PurgeGuard(db, entry, timeout)
        _guard_active.set(guard)
        capture.install()
        try:
            if files:
                record_purged_files(entry, files)
            yield entry
            db.flush()
            capture.finalize(entry)
            db.flush()
        except BaseException:
            guard.failed = True
            raise
    finally:
        capture.remove()
        if _guard_active.get() is guard:
            _guard_active.set(previous)
        if guard is not None:
            root_tx = db.get_transaction()
            if root_tx is None:
                guard.finish_without_transaction()
            else:
                guard.bind(root_tx)


def recover_orphaned_staging(db: Session, timeout: float | int | None = None) -> dict:
    """Recover purge quarantine left by a crash between commit and GC.

    Called from startup or maintenance while the maintenance lock is held
    (the lock is reentrant in the same thread). A durable ``cleanupPending``
    marker means the purge transaction committed and only file GC remains; a
    directory without that marker belongs to a rolled-back/crashed purge and
    its files are restored to the original keys.
    """

    if not isinstance(db, Session):
        raise TypeError('db must be a SQLAlchemy Session')
    _, coordination, _ = _storage_paths()
    staging = coordination / 'staging'
    result = {
        'completed': 0,
        'restored': 0,
        'pending': 0,
        'failed': 0,
        'gcCompleted': 0,
        'gcRestored': 0,
        'gcPending': 0,
        'gcFailed': 0,
    }
    with maintenance_lock(timeout=timeout):
        root = _storage_paths()[0]
        if staging.exists():
            if staging.is_symlink():
                raise DomainError('STORAGE_PATH_INVALID', '暂存目录不能是符号链接', 503)
            for entry_dir in sorted(staging.iterdir()):
                if not entry_dir.is_dir() or entry_dir.is_symlink():
                    continue
                staged = _read_manifest(entry_dir)
                row = db.get(TrashEntry, entry_dir.name)
                pending = bool(row is not None and (row.data or {}).get('cleanupPending'))
                if not staged:
                    # A committed purge with cleanupPending must have a
                    # manifest naming every quarantined file.  Do not delete an
                    # unmanifested directory or claim completion: that would
                    # lose ownership of private garbage.
                    if pending:
                        if row is not None:
                            data = dict(row.data or {})
                            data['cleanupError'] = 'STAGING_MANIFEST_MISSING'
                            row.data = data
                            flag_modified(row, 'data')
                            db.flush()
                        result['pending'] += 1
                    else:
                        result['failed'] += 1
                    continue
                if pending:
                    failures = _gc_staged(root, entry_dir, staged)
                    if failures:
                        result['pending'] += 1
                        continue
                    row.status = 'PURGED'
                    _remove_staged_data(row)
                    db.flush()
                    result['completed'] += 1
                else:
                    failures = _restore_staged(root, entry_dir, staged)
                    if failures:
                        result['failed'] += 1
                        continue
                    result['restored'] += 1

        gc_root = _storage_paths()[1] / _GC_DIR
        if gc_root.exists() and not gc_root.is_symlink():
            for entry_dir in sorted(gc_root.iterdir()):
                if not entry_dir.is_dir() or entry_dir.is_symlink():
                    continue
                manifest_id = entry_dir.name
                if not _valid_manifest_id(manifest_id):
                    continue
                staged = _read_manifest(entry_dir)
                if not staged:
                    if _gc_marker_committed(db, manifest_id):
                        result['gcPending'] += 1
                    else:
                        result['gcFailed'] += 1
                    continue
                if _gc_marker_committed(db, manifest_id):
                    failures = _gc_staged(root, entry_dir, staged)
                    if failures:
                        result['gcPending'] += 1
                    else:
                        result['gcCompleted'] += 1
                else:
                    failures = _restore_staged(root, entry_dir, staged)
                    if failures:
                        result['gcFailed'] += 1
                    else:
                        result['gcRestored'] += 1
    return result



def recover_purge_cleanup(
    db: Session,
    entry: TrashEntry,
    timeout: float | int | None = None,
) -> bool:
    """Finish a committed purge's file cleanup before treating it as final.

    This is the idempotency check for ``process_purge_job`` when the target row
    is already gone (retry after a partial purge): the tombstone rows, NULL
    detaches, ledger snapshot and ``purgedAt`` are already durable.  If
    ``cleanupPending`` is set, run ``recover_orphaned_staging`` and require the
    entry to become ``PURGED``.  If the staging manifest/directory is missing,
    fail closed with ``PURGE_STORAGE_PENDING``: never clear the marker or claim
    PURGED while private garbage cannot be enumerated.

    Returns ``True`` when the entry is already/finished ``PURGED``; ``False``
    when there is no pending cleanup and the caller may finalize the tombstone
    (for example through ``purge_guard``).
    """

    if not isinstance(db, Session):
        raise TypeError('db must be a SQLAlchemy Session')
    if not isinstance(entry, TrashEntry):
        raise TypeError('entry must be a TrashEntry')
    if entry not in db:
        raise DomainError('PURGE_CAPTURE_INVALID', '清理记录不在当前事务中', 500)
    data = entry.data or {}
    cleanup_pending = bool(data.get('cleanupPending'))
    if entry.status == 'PURGED' and not cleanup_pending:
        return True
    if not cleanup_pending:
        return False

    if (
        not isinstance(entry.id, str)
        or not entry.id
        or Path(entry.id).name != entry.id
    ):
        raise DomainError('PURGE_CAPTURE_INVALID', '清理记录 ID 不能用于暂存目录', 500)

    _, coordination, _ = _storage_paths()
    entry_dir = coordination / 'staging' / entry.id
    if (
        not entry_dir.exists()
        or not entry_dir.is_dir()
        or entry_dir.is_symlink()
    ):
        raise DomainError(
            'PURGE_STORAGE_PENDING',
            '清理暂存清单缺失，私有文件清理仍待恢复',
            503,
            {'reason': 'STAGING_MISSING'},
        )

    result = recover_orphaned_staging(db, timeout=timeout)
    db.refresh(entry)
    if entry.status == 'PURGED' and not (entry.data or {}).get('cleanupPending'):
        return True
    raise DomainError(
        'PURGE_STORAGE_PENDING',
        '私有文件清理尚未完成，将自动重试',
        503,
        {
            'reason': 'STAGING_PENDING',
            'pending': result.get('pending', 0),
            'failed': result.get('failed', 0),
        },
    )


_GC_SESSION_KEY = 'dormprint_storage_gc_guard'
_gc_active: ContextVar[object | None] = ContextVar(
    'dormprint_storage_gc_guard', default=None
)


def _gc_guard_for(session: Session):
    """Return the GC guard bound to this Session, across threads.

    Session lifecycle is sequential but not thread-affine: a sync route may run
    in a worker thread and the request middleware commits on the event-loop
    thread. Session.info is shared by both, unlike a thread-local registry.
    """

    return session.info.get(_GC_SESSION_KEY)


def _active_gc_guard() -> object | None:
    return _gc_active.get()


def _valid_manifest_id(value: str) -> bool:
    return len(value) == 32 and all(char in '0123456789abcdef' for char in value)


def _gc_entry_dir(manifest_id: str) -> tuple[Path, Path]:
    root, coordination, _ = _storage_paths()
    gc_root = coordination / _GC_DIR
    if gc_root.is_symlink():
        raise DomainError('STORAGE_PATH_INVALID', '正常清理暂存目录不能是符号链接', 503)
    try:
        gc_root.mkdir(mode=0o700, exist_ok=True)
    except OSError as exc:
        raise DomainError(
            'TRANSIENT_IO',
            '无法创建正常清理暂存目录',
            503,
            {'reason': exc.__class__.__name__},
        ) from exc
    if not gc_root.is_dir():
        raise DomainError('STORAGE_PATH_INVALID', '正常清理暂存路径不是目录', 503)
    entry_dir = gc_root / manifest_id
    if entry_dir.is_symlink():
        raise DomainError('STORAGE_PATH_INVALID', '正常清理清单不能是符号链接', 503)
    try:
        entry_dir.mkdir(mode=0o700, exist_ok=True)
    except OSError as exc:
        raise DomainError(
            'TRANSIENT_IO',
            '无法创建正常清理清单目录',
            503,
            {'reason': exc.__class__.__name__},
        ) from exc
    if not entry_dir.is_dir():
        raise DomainError('STORAGE_PATH_INVALID', '正常清理清单不是目录', 503)
    return root, entry_dir


def _write_manifest_payload(entry_dir: Path, payload: dict) -> None:
    path = _manifest_path(entry_dir)
    temporary = entry_dir / '.manifest.json.tmp'
    try:
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, sort_keys=True),
            encoding='utf-8',
        )
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    except OSError as exc:
        raise DomainError(
            'TRANSIENT_IO',
            '无法写入私有文件暂存清单',
            503,
            {'reason': exc.__class__.__name__},
        ) from exc


def _write_gc_manifest(entry_dir: Path, manifest_id: str, staged: list[dict]) -> None:
    _write_manifest_payload(entry_dir, {
        'kind': 'PRIVATE_FILE_GC',
        'manifestId': manifest_id,
        'files': staged,
    })


def _stage_gc_path(
    root: Path,
    entry_dir: Path,
    manifest_id: str,
    key: str,
    staged: list[dict],
    *,
    tree: bool,
) -> dict:
    safe = _safe_key(key)
    for item in list(staged):
        if item.get('key') == safe:
            if item.get('missing') and (root / safe).exists():
                staged.remove(item)
                break
            return item
    source = _safe_source(root, safe)
    if not source.exists():
        item = {'key': safe, 'missing': True}
        staged.append(item)
        _write_gc_manifest(entry_dir, manifest_id, staged)
        return item
    digest = hashlib.sha256(safe.encode('utf-8')).hexdigest()
    destination = entry_dir / digest
    try:
        if not destination.exists():
            # Keep the original pathname until the caller commits. The old DB
            # snapshot must keep resolving while the transaction is uncommitted.
            if tree or source.is_dir():
                shutil.copytree(source, destination, symlinks=True)
            else:
                try:
                    os.link(source, destination)
                except OSError:
                    shutil.copy2(source, destination)
    except OSError:
        raise
    item = {'key': safe, 'staged': str(destination.relative_to(root))}
    if tree or source.is_dir():
        item['tree'] = True
    staged.append(item)
    _write_gc_manifest(entry_dir, manifest_id, staged)
    return item


def _gc_marker_committed(db: Session, manifest_id: str) -> bool:
    return db.scalar(select(AuditLog.id).where(
        AuditLog.action == _GC_MARKER_ACTION,
        AuditLog.target_type == _GC_MARKER_TARGET,
        AuditLog.target_id == manifest_id,
    ).limit(1)) is not None


class _StorageGCGuard:
    """Transaction-aware normal-GC protocol for Q29 expiry.

    The caller (maintenance sweep) owns the transaction. Files are staged while
    the original pathname stays readable, and the exclusive maintenance lock is
    held until SQLAlchemy reports the caller's root transaction ended. A
    same-transaction ``AuditLog`` marker records whether the DB changes
    committed; recovery uses that marker rather than guessing from timestamps.
    """

    def __init__(self, db: Session, timeout: float | int | None) -> None:
        self.db = db
        self.manifest_id = secrets.token_hex(16)
        self.staged: list[dict] = []
        self.root = None
        self.entry_dir = None
        self.root_tx = None
        self.committed = False
        self.rolled_back = False
        self.failed = False
        self._marker_added = False
        self._released = False
        self._lock_context = maintenance_lock(timeout=timeout)
        self._lock_context.__enter__()

    def _ensure_dir(self) -> tuple[Path, Path]:
        if self.root is None or self.entry_dir is None:
            self.root, self.entry_dir = _gc_entry_dir(self.manifest_id)
        return self.root, self.entry_dir

    def stage(self, key: str, *, tree: bool = False) -> dict:
        root, entry_dir = self._ensure_dir()
        item = _stage_gc_path(
            root, entry_dir, self.manifest_id, key, self.staged, tree=tree
        )
        if not item.get('missing'):
            self._ensure_marker()
        return item

    def discard_since(self, index: int) -> None:
        """Restore files staged after ``index`` without ending the transaction.

        A per-object savepoint may roll back while the surrounding sweep still
        commits.  Staged paths must therefore be restored and removed from this
        guard's manifest before that commit, otherwise files belonging to a
        rolled-back row graph would be garbage-collected.
        """
        if index < 0:
            index = 0
        if index >= len(self.staged):
            return
        root, entry_dir = self._ensure_dir()
        extra = self.staged[index:]
        # Missing keys have no staged copy to restore; only real quarantine
        # entries matter here.
        restorable = [item for item in extra if item.get('staged')]
        failures = _restore_staged(root, entry_dir, restorable)
        if failures:
            raise OSError('无法回滚私有文件暂存：' + ','.join(failures))
        del self.staged[index:]
        if self.staged:
            # _restore_staged removes the manifest when its whole input list
            # restores; earlier staged files still need their manifest and a
            # marker that the savepoint rollback may have removed with the
            # failed account's audit row.
            _write_gc_manifest(entry_dir, self.manifest_id, self.staged)
            self._marker_added = _gc_marker_committed(self.db, self.manifest_id)
            if not self._marker_added:
                self._ensure_marker()
        else:
            self._marker_added = False

    def _ensure_marker(self) -> None:
        if self._marker_added:
            return
        self.db.add(AuditLog(
            actor_id='system',
            action=_GC_MARKER_ACTION,
            target_type=_GC_MARKER_TARGET,
            target_id=self.manifest_id,
            data={'manifestId': self.manifest_id},
        ))
        self._marker_added = True

    def bind(self, transaction) -> None:
        self.root_tx = transaction

    def _on_commit(self, session: Session) -> None:
        if session.get_nested_transaction() is None:
            self.committed = True

    def _on_rollback(self, session: Session) -> None:
        if session.get_nested_transaction() is None:
            self.rolled_back = True

    def _on_transaction_end(self, session: Session, transaction) -> None:
        if transaction is not self.root_tx:
            return
        try:
            if self.committed and not self.rolled_back and not self.failed:
                self._finalize_commit()
            else:
                self._finalize_rollback()
        except Exception:
            # A committed marker remains the truth for restart recovery; a
            # missing marker means the only safe action is to restore.
            pass
        finally:
            if session.info.get(_GC_SESSION_KEY) is self:
                session.info.pop(_GC_SESSION_KEY, None)
            self.release()

    def _finalize_commit(self) -> None:
        if not self.staged:
            return
        root, entry_dir = self._ensure_dir()
        _gc_staged(root, entry_dir, self.staged)

    def _finalize_rollback(self) -> None:
        root, entry_dir = self._ensure_dir()
        _restore_staged(root, entry_dir, self.staged)

    def finish_without_transaction(self) -> None:
        try:
            if self.root is not None and self.entry_dir is not None:
                _restore_staged(self.root, self.entry_dir, self.staged)
        finally:
            self.release()

    def release(self) -> None:
        if self._released:
            return
        self._released = True
        self._lock_context.__exit__(None, None, None)


def staged_file_count(db: Session) -> int:
    guard = _gc_guard_for(db)
    return len(guard.staged) if guard is not None else 0


def discard_staged_since(db: Session, index: int) -> None:
    guard = _gc_guard_for(db)
    if guard is not None:
        guard.discard_since(index)


def _gc_after_commit(session: Session) -> None:
    guard = _gc_guard_for(session)
    if guard is not None:
        guard._on_commit(session)


def _gc_after_rollback(session: Session) -> None:
    guard = _gc_guard_for(session)
    if guard is not None:
        guard._on_rollback(session)


def _gc_after_transaction_end(session: Session, transaction) -> None:
    guard = _gc_guard_for(session)
    if guard is not None:
        guard._on_transaction_end(session, transaction)


_gc_events_installed = False


def _install_gc_events() -> None:
    global _gc_events_installed
    if _gc_events_installed:
        return
    # One listener per Session class, not one per sweep. Q29 maintenance runs
    # frequently; per-call listeners would accumulate for the process lifetime.
    event.listen(Session, 'after_commit', _gc_after_commit)
    event.listen(Session, 'after_rollback', _gc_after_rollback)
    event.listen(Session, 'after_transaction_end', _gc_after_transaction_end)
    _gc_events_installed = True


_install_gc_events()


@contextmanager
def storage_gc_guard(
    db: Session,
    timeout: float | int | None = None,
):
    """Stage normal Q29 expiry files until the caller's transaction ends.

    Usage::

        with storage_gc_guard(db):
            ... maintenance_sweep(db) ...
        db.commit()   # lock is held until this returns; files then GC

    The helper never commits. On commit the audit marker is durable and staged
    originals are removed; on rollback originals are restored. A crash leaves a
    manifest under ``.coordination/gc`` and the same-transaction audit marker,
    so ``recover_orphaned_staging`` can decide GC versus restore.
    """

    if not isinstance(db, Session):
        raise TypeError('db must be a SQLAlchemy Session')
    if _active_gc_guard() is not None or db.info.get(_GC_SESSION_KEY) is not None:
        raise DomainError(
            'STORAGE_GC_NESTED',
            '正常清理 guard 不能嵌套',
            500,
        )
    guard = _StorageGCGuard(db, timeout)
    previous_current = _active_gc_guard()
    _gc_active.set(guard)
    db.info[_GC_SESSION_KEY] = guard
    try:
        try:
            yield guard
            db.flush()
        except BaseException:
            guard.failed = True
            raise
    finally:
        if _gc_active.get() is guard:
            _gc_active.set(previous_current)
        root_tx = db.get_transaction()
        if root_tx is None:
            if db.info.get(_GC_SESSION_KEY) is guard:
                db.info.pop(_GC_SESSION_KEY, None)
            guard.finish_without_transaction()
        else:
            # Session.info keeps the guard reachable until the root transaction
            # ends, including when that commit happens on another thread.
            guard.bind(root_tx)


def purge_storage_key_locked(key: str, timeout: float | int | None = None) -> bool:
    """Stage (inside a GC guard) or unlink one private key under the lock."""

    guard = _active_gc_guard()
    if guard is not None:
        guard.stage(key, tree=False)
        return True

    from .exports import purge_storage_key

    with maintenance_lock(timeout=timeout):
        return purge_storage_key(key)


def purge_storage_tree_locked(key: str, timeout: float | int | None = None) -> bool:
    """Stage (inside a GC guard) or remove one private tree under the lock."""

    guard = _active_gc_guard()
    if guard is not None:
        guard.stage(key, tree=True)
        return True

    from .exports import purge_storage_tree

    with maintenance_lock(timeout=timeout):
        return purge_storage_tree(key)
