# DormPrint V1 私有存储备份与恢复

日期：2026-10-30。依据：Q31 A（服务器私有持久卷 + 加密异地备份，上传根不公开）、Q32 B（每小时增量保护、每日备份，7 天小时恢复点、30 天日恢复点；RPO ≤ 1 小时、RTO ≤ 4 小时为需实测目标）、R01 A（备份轮换是明确例外，正常业务记录不按年龄自动删除）。

状态：备份/恢复工具已实现并在本机做过独立数据库 + 私有文件演练；**真实异地仓库、真实生产服务器和真实 RPO/RTO 尚未提供/未实测**。本文不把本机演练当成生产验收。

## 1. 备份对象

一次快照同时包含：

1. **PostgreSQL 一致性快照**：`pg_dump --format=custom`，包含订单、账目、优惠、审计、账号、邮件任务、删除回收站等全部关系事实；
2. **私有文件卷**：`assets/`（原文件）、`normalized/`（标准化中间件）、`artifacts/`（不可变最终 PDF）、`sessions/`（断点续传分片）、`exports/`（受控导出 CSV）；排除 `tmp/` 和协调锁目录；
3. **文件 manifest**：卷内每个文件的 sha256 与相对路径；
4. **数据库期望清单**：`assets`/`artifacts`/`export_jobs` 中每个应存在文件及其记录 hash；
5. **不可变 PDF hash 清单**：`artifacts.id → asset.storage_key → artifact.sha256`；
6. **删除 tombstone 累积日志**：恢复时防止已永久清理的记录/文件复活；
7. **快照元数据**：时间、模式、数据库 dump hash、manifest hash、对象数/文件数、主密钥指纹（只存指纹，不存密钥）。

**不进入备份**：`DORMPRINT_SECRET_KEY` 主密钥、restic 加密密码、TLS 私钥、部署 `.env`、真实 SMTP 明文密码（SMTP 密码在数据库中是 `seal` 加密载荷，恢复仍需独立主密钥）。这些必须由独立 secret manager 保管。

## 2. 架构与调度

```
postgres ──pg_dump──┐
private-files ──────┤   backup container (restic)  ──加密──> 异地 restic repository
manifest / hashes ──┘              │
                          /state: 累积 tombstone 日志、last-success/last-attempt、restic cache
```

- `backup` 服务由 `backup-scheduler.sh` 驱动，每小时 UTC 整点后执行一次 `backup.sh --mode hourly`；在 `BACKUP_DAILY_UTC_HOUR`（默认 2 点 UTC）执行 `--mode daily` 并做保留轮换。
- 远端仓库必须是专用仓库；脚本不做跨应用共享仓库的静默 prune。
- 未配置 `RESTIC_REPOSITORY`/`RESTIC_PASSWORD(_FILE)` 时，脚本在任何数据库/文件操作前以非零退出并输出 `BACKUP_UNAVAILABLE`，不写 `last-success.json`，不打印成功。
- `last-success.json` 记录最近成功快照，可用于监控 RPO；`last-attempt.json` 记录最近失败，备份容器 healthcheck 会因超过 3 小时无成功而变为 unhealthy。远端不可达时不会伪造成功。

### 2.1 保留期轮换（真实命令）

每日成功后执行：

```bash
restic forget --group-by host --tag dormprint --keep-hourly 168 --keep-daily 30 --keep-last 1 --prune
```

按主机统一分组，避免每次临时dump目录变化形成独立分组而永远不淘汰。成功记录使用本次backup的JSON summary snapshot_id并验证存在，不从多分组列表猜测。

含义：保留最近 168 个小时恢复点（7 天）+ 最近 30 天中每天最后一个恢复点。该命令由 `backup.sh --mode daily` 实际执行；`--prune` 真正删除仓库中不再被保留策略引用的数据。`restic snapshots` 可核对实际保留结果。仓库必须专用，避免误删其他系统快照。

### 2.2 容量告警

`check_capacity.sh` 用 `df` 检查私有卷使用率，超过 `DORMPRINT_CAPACITY_ALERT_PERCENT`（默认 85%）返回 10 并输出 `CAPACITY_ALERT`。备份调度会调用它并记录；请把该退出码接入真实监控/告警通道。脚本本身不宣称已送达外部告警。

## 3. 一致性协议（必须让 maintenance 接入）

### 3.1 问题

`pg_dump` 只给数据库一个时间点；私有文件是另一个时间点。若清理任务在数据库快照之后、文件扫描/上传之前删除文件，会得到“数据库引用存在但文件缺失”的不一致备份。

### 3.2 协调锁协议

- 备份在读取 `pg_dump` 前获取 **存储协调文件锁**：`${DORMPRINT_STORAGE_ROOT}/.coordination/maintenance.lock`，使用 `flock -x` 会话级排他锁，并一直持有到 restic 上传完成。备份等待 `BACKUP_LOCK_TIMEOUT_SECONDS`（默认 300 秒）后以 `BACKUP_BUSY` 失败，不会降级为无锁备份。
- 永久清理和恢复回放必须获取同一路径、同一语义的锁。锁文件由 `DORMPRINT_STORAGE_ROOT` 推导，禁止第二套配置。
- 实现入口在 `backend/app/storage_coordination.py`：
  - `maintenance_lock(timeout=None)`：上下文管理器。使用 `fcntl.flock(fd, LOCK_EX)` 非阻塞重试；超时抛 `DomainError('TRANSIENT_IO', ..., status=503)`，使 PURGE job 按 `TRANSIENT_IO` 重试，而不是无锁删除。
  - 锁目录创建为 0700，锁文件为 0600；拒绝 `.coordination` 或锁文件是符号链接、拒绝解析后逃逸 storage root。超时取 `DORMPRINT_MAINTENANCE_LOCK_TIMEOUT_SECONDS`，其次 `BACKUP_LOCK_TIMEOUT_SECONDS`，默认 300 秒，上限 3600 秒。
  - 重入状态不是 `threading.local`：同步路由在 worker thread 执行，而请求中间件可能在 event-loop thread 提交同一个 Session。上下文管理器用 `ContextVar` 保存可变的 `LockState`，guard/commit 回调释放**捕获的** FD；只有当前 context 仍持有同一 state 时才清空它，绝不跨 context `reset(token)`。`storage_gc_guard` 则把 guard 放在 `Session.info`，所以 `after_commit`/`after_rollback` 在另一个线程也能找到并 finalize；`ContextVar` 只在 guard body 内供 helper 使用。
- `purge_guard(db, entry, timeout=None, files=None)` 是 `process_purge_job` 的唯一推荐入口：

```python
from app.storage_coordination import purge_guard, record_purged_files

with purge_guard(db, entry, files=plan["files"]):
    # 只做数据库删除/更新；不要把 plan["files"] 先 unlink，
    # 也不要在这里 commit。
    ...
db.commit()   # 由调用方/请求/job 框架提交
```

  - guard 在进入时取锁，并把文件额外 hard-link/copy 到同文件系统的私有暂存区 `.coordination/staging/<trashId>/`；原路径保持可读，直到调用方根事务真正 commit 或 rollback。退出 guard 后锁仍然保持。
  - commit 成功后 GC 原文件与暂存文件、把 `TrashEntry` 置为 `PURGED`；rollback 只删除暂存链接，原文件不动，不会留下“数据库回滚但文件已删”。进程若在 commit 前崩溃，旧快照仍能读到原文件。
  - GC 失败时保留暂存文件和 durable 标记：`status=PURGE_PENDING`、`data.cleanupPending=true`、`data.cleanupError=STAGING_GC_FAILED`。不得在私有垃圾不可恢复时报告 `PURGED`。
  - `files=...` 或动态文件用 `record_purged_files(entry, keys)` 登记；两者都会校验相对 key 并去重。调用方不得在 guard 外直接 unlink PURGE 目标文件。
  - guard 不 commit、不关闭请求 session；进程崩溃后的暂存由 `recover_orphaned_staging(db, timeout=None)` 恢复/完成，返回 `{completed, restored, pending, failed, gcCompleted, gcRestored, gcPending, gcFailed}`。
- **重试与幂等（target 已不存在）**：`process_purge_job` 的 `target is None` 分支必须先调用 `recover_purge_cleanup(db, entry, timeout=None)`：
  - 返回 `True` 表示 entry 已 `PURGED`（或本次恢复完成），直接返回，不再进入空 capture；
  - 返回 `False` 表示没有 `cleanupPending`，再进入 `purge_guard(db, entry)` 并把 entry 置为 `PURGED`；
  - 若 `cleanupPending=true` 但暂存目录/manifest 缺失，抛 `DomainError('PURGE_STORAGE_PENDING', ..., 503)`，job 重试，绝不把不可枚举的私有垃圾标记为 `PURGED`。
  - 空 capture 的 `purge_guard` 会**合并**旧 `purgedRows`/`purgedDetaches`/`purgedFiles`/`purgedAt`/ledger snapshot，而不是用新空列表覆盖；重试和恢复只允许改 `status` 与暂存字段。`jobs.py` 若在 commit 前看到 `PURGE_PENDING`，应把 job 放回 `QUEUED` 做有界重试（推荐 2 秒），而不是永久失败。
  - 如果 entry 已是 `PURGED` 但 `data.cleanupPending=true`（例如 commit 后 GC 标记更新失败），`process_purge_job` 必须在早期 `status == 'PURGED'` 返回**之前**调用 `recover_purge_cleanup`；否则 job 会跳过恢复，永久保留不可枚举的私有垃圾。
  - 推荐调用形状（root 只需改自己拥有的 `maintenance.py` 分支）：
```python
if target is None:
    if recover_purge_cleanup(db, entry):
        return
    with purge_guard(db, entry):
        entry.status = 'PURGED'
        touch(entry)
    return
```
- **HTTP 手工 purge 的返回状态**：`purge_trash` 只做无锁的 404/版本/恢复态早期检查，不修改 `entry.data`、不在这里 `enqueue_job`。`process_purge_job(db, ..., reason=...)` 在取得文件锁和 Trash 行锁之后才写 `purgeReason` 并 enqueue，避免 dirty Trash 行的 flush 先拿数据库写锁。路由在序列化响应时若 `entry.data.cleanupPending` 或 `entry.data.stagedFiles` 仍存在，只对 view 做 `model_copy(update={'status': 'PURGE_PENDING'})`，不修改数据库；commit 回调完成 GC 后，下一次读取/GET 才返回真实的 `PURGED` 或 `PURGE_PENDING`。因此“响应已返回 pending”不等于 DB 已 pending，也不允许把有暂存文件的清理说成 `PURGED`。
- **正常 Q29/定时过期**（不是用户手工 Trash 的 24h 永久清理）必须用 `storage_gc_guard(db, timeout=None)` 包住整个 `maintenance_sweep(db)`：
  - `maintenance.py::maintenance_sweep` 现已自行 `with storage_gc_guard(db): recover_orphaned_staging(db); ...`；调用方仍必须在 guard 外 `db.commit()`。
  - 进入时取同一把锁；guard 内调用 `purge_storage_key_locked(key)` / `purge_storage_tree_locked(key)` 时**只暂存、不立即 unlink**，原路径一直可读到调用方根事务 commit 或 rollback。
  - 文件暂存在 `.coordination/gc/<manifestId>/`，`manifest.json` 记录相对 key；目录树用 `copytree(..., symlinks=True)` 暂存，不跟随树内符号链接；同一事务写入最小 `AuditLog`（`action=PRIVATE_FILE_GC_COMMITTED`、`target_type=PRIVATE_FILE_GC`、`target_id=manifestId`，`data` 只有 manifestId，无 PII）。不创建 TrashEntry，不走管理员 Trash/24h 流程。
  - commit 后按 manifest GC 原文件和暂存文件；rollback 只删除暂存副本，原文件与 `deleted_at`/`storage_key` 等 DB 变更一起回滚。退出 guard 后锁保持到调用方 commit/rollback 结束。
  - 进程在 commit 前崩溃：无 audit marker，恢复时 restore；在 commit 后、GC 前崩溃：marker 已提交，恢复时完成 GC。恢复入口仍是 `recover_orphaned_staging`，它先查 marker 再决定 GC 还是 restore。
  - 独立 CLI unlink 仍在 guard 外使用 `purge_storage_key_locked`，此时保留“取锁后立即删除”的旧语义。
- **account-deletion 的 export 文件**：`approve_deletion_request` 与 ACCOUNT 手工 purge 的 `_revoke_account_credentials` 调用已包在 `storage_gc_guard(db)` 内；业务删除方法本身不归本 worker 修改。
- `deploy/backup/apply_tombstones.py` 在回放数据库删除和 unlink 私有文件期间也持有同一把锁（CLI 等价实现），避免恢复回放与备份文件扫描竞态。
- 正常业务写入不获取此锁；`pg_dump` 的 REPEATABLE READ 快照负责数据库一致性。锁只保护“已提交文件的删除”不会与文件扫描/上传竞态。新增文件可能出现在快照之后，属于无害的多余文件；恢复以 manifest + 数据库清单为准。

**集成状态**：`process_purge_job` 已由 maintenance owner 包装 `purge_guard`，`reason`/enqueue 在文件锁与行锁之后，`target is None` 分支先调用 `recover_purge_cleanup`；`maintenance_sweep` 已自行包装 `storage_gc_guard` 并先调用 `recover_orphaned_staging`；account-deletion 的 `_revoke_account_credentials` 已在 `storage_gc_guard` 内。HTTP view 的 pending override 只影响本次响应序列化，DB 仍由 commit 回调决定。根集成已在 `jobs.py` 补齐有界 `QUEUED` 重试及提交后持久状态的READY判定，相关作业回归已通过；guard自身不提交事务。

### 3.3 删除 tombstone 协议

管理员删除先进入 24h 回收站；真正永久清理时，`TrashEntry` 保持一条 `status=PURGED` 记录，并在同一数据库事务中把以下字段写入其 `data`：

```json
{
  "purgedAt": "2026-10-30T10:00:00+00:00",
  "purgedRows": [
    {"table": "print_tasks", "id": "..."},
    {"table": "order_files", "id": "..."},
    {"table": "orders", "id": "..."}
  ],
  "purgedDetaches": [
    {"table": "wage_entries", "column": "order_id", "id": "..."},
    {"table": "order_access_grants", "column": "source_grant_id", "id": "..."}
  ],
  "purgedFiles": ["artifacts/aa/....pdf", "assets/ab/....pdf"],
  "ledgerBucket": "trash:<trashEntryId>",
  "ledgerTotals": {"orderCount": 1, "payableCents": 1200}
}
```

- `purgedRows` 必须按“子表先、父表后”的可删除顺序列出实际删除的所有行；只列目标 id 不够，订单/账号等图关系必须包含所有被清理的子行。`purge_guard` 通过 SQLAlchemy session 事件捕获本次事务实际执行的 Delete 精确 id，并捕获 ORM 删除，不做全库扫描。
- `purgedDetaches` 只记录显式白名单中的 FK 置空：`preparations.source_grant_id`、`order_access_grants.source_grant_id`、`wage_entries.order_id`/`assignment_id`、`promotion_events.order_id`、`coupon_grants.reserved_order_id`/`used_order_id`、`referrals.qualified_order_id`。这些行在删除图里保留，不能靠 DELETE 重放“复原”。
- `purgedFiles` 使用相对私有卷根的 storage key，不能是绝对路径或含 `..`。tombstone 不写姓名、邮箱、电话、密码摘要、加密 payload 或任意调用方 SQL。
- `ledgerBucket`/`ledgerTotals` 是同一事务中 `AnonymousLedger` 的匿名总计快照；恢复后按 bucket 幂等 upsert，保留删除前的匿名报表口径。
- `apply_tombstones.py` 支持 v1（仅 rows/files）和 v2 journal；只接受 51 张白名单表名、白名单列名和合法 id，不执行调用方 SQL。行按显式 child-first 表顺序分组执行，先应用 detaches，再 DELETE；同一 `trashId` 以 `purgedAt` 最新者为准（外部日志和快照日志是拼接的，不能依赖拼接顺序）。缺少完整 rows 的条目报 `TOMBSTONE_INCOMPLETE` 并退出 3。
- 回放会幂等补齐最小 `TrashEntry(status=PURGED)`、`AnonymousLedger`（确定性 id 和 totals 合并）和 `RESTORE_LEDGER_PRESERVED` 审计，不写 PII。
- 备份每次把当前 `trash_entries` 中的 tombstone 合并进累积日志，和快照一起上传。导出规则必须区分“已提交删除”与“未执行/仅暂存失败”：
  - 所有 `status=PURGED` 的 entry 都必须进入导出候选，即使 `purgedRows` 为空或格式异常；导出器随后执行与回放相同的 `validate_entries`，遇到空 rows 立即以 `TOMBSTONE_INCOMPLETE` 退出 3，绝不静默遗漏（静默遗漏会让旧快照复活该行）。
  - `status=PURGE_PENDING` 仅当 `data.purgedRows` 是非空数组且 `data.purgedAt` 非空时才导出。这表示数据库删除已提交但 commit 后 GC 失败，物理文件可能仍在，tombstone 仍必须携带实际 rows/files 供恢复回放。由 `storageCleanup`/暂存失败导致、尚无实际 DELETE 的 pending entry（空 rows）不导出，因为它没有可防复活的已提交删除。
  - 恢复时把快照内日志与“外部更新日志”合并后回放，再执行撤权；因此快照时间点之后被永久删除的对象不会重新暴露。

**授权 overlay 的 fail-closed 要求**：仅回放普通删除不够。备份 journal 还携带 `AUTHORITY` 行（当前 staff/account 的 enabled/status/role/capabilities/sessionVersion 等最小事实，无 PII）。恢复时只有存在“可证明比快照更新”的外部 authority manifest 才覆盖当前授权；否则 `post_restore.sql` 禁用全部 staff（含 super admin），账号按快照状态恢复但不自动重新授权。super admin 永远需要人工 bootstrap review。旧快照里的 employee grant 不能在用户重新登录后自动恢复旧权限。

## 4. 配置

在 `deploy/.env` 中至少设置：

```dotenv
RESTIC_REPOSITORY=s3:https://s3.example.com/dormprint-prod-backup
RESTIC_PASSWORD_FILE=/run/secrets/restic-password   # 推荐；或 RESTIC_PASSWORD
RESTIC_CACERT=                                      # 私有 CA 时提供
AWS_ACCESS_KEY_ID=...
AWS_SECRET_ACCESS_KEY=...
AWS_DEFAULT_REGION=...
AWS_ENDPOINT=...
BACKUP_DAILY_UTC_HOUR=2
DORMPRINT_CAPACITY_ALERT_PERCENT=85
DORMPRINT_MASTER_KEY_FINGERPRINT=...                # 独立保存的 sha256(secret)
```

- `RESTIC_PASSWORD_FILE` 应由 Docker secret/主机文件挂载提供，不要写进 Git。仓库创建时使用的密码必须与恢复时一致；丢失则备份不可解。
- 远端可以是 S3/兼容对象存储、SFTP、rest-server 等 restic 支持后端；实际凭据和网络由部署者提供。
- 首次使用需显式初始化一次（脚本不会在普通每小时任务里静默创建新仓库）：

```bash
docker compose -f deploy/compose.yaml --env-file deploy/.env run --rm backup \
  /opt/dormprint/backup.sh --mode hourly --init
```

- 普通手动备份：

```bash
docker compose -f deploy/compose.yaml --env-file deploy/.env run --rm backup \
  /opt/dormprint/backup.sh --mode hourly
```

只有同时满足 pg_dump 非空、manifest/不可变 PDF 校验通过、restic 上传成功、快照可确认（每日还包括 prune 成功）时，脚本才打印 `BACKUP_OK` 并写 `last-success.json`。任何一步失败都以非零退出，并保留/输出失败类别（`BACKUP_FAILED`/`BACKUP_INTEGRITY`/`BACKUP_UNAVAILABLE`）。

## 5. 恢复 Runbook

**原则：恢复到独立数据库和独立空存储卷，不做生产原地覆盖。** 生产切换前先在隔离环境启动一套 API 验证。

### 5.1 恢复前

1. 从独立 secret manager 恢复 `DORMPRINT_SECRET_KEY`；若有指纹，核对 `sha256(secret)`。
2. 恢复 restic 密码/凭据；检查目标 restic 快照存在：`restic snapshots`。
3. 准备新的空 PostgreSQL 数据库（脚本自动创建，名称需以 `dormprint_restore_` 开头）。
4. 准备新的空持久卷作为恢复目标；不要挂载生产私有卷。
5. 准备外部 tombstone 日志（如果备份点之后又有永久清理），或在恢复后先核对。

### 5.2 执行

```bash
# 在可访问 postgres 与 restic 的临时容器/主机执行
export PGHOST=postgres PGPORT=5432 PGUSER=dormprint PGDATABASE=dormprint
export PGPASSWORD=...                       # 不写入 shell 历史时用 env file/secret
export RESTIC_REPOSITORY=... RESTIC_PASSWORD_FILE=...
export DORMPRINT_SECRET_KEY=...             # 独立恢复的主密钥

/opt/dormprint/restore.sh \
  --snapshot latest \
  --target-database dormprint_restore_20261030 \
  --target-storage /restore-target \
  --journal /state/tombstones.jsonl
```

脚本按顺序：

1. 拒绝目标数据库等于 `PGDATABASE`；拒绝非 `dormprint_restore_` 前缀（除非显式 `--allow-any-database`）；拒绝目标存储等于生产卷；目标卷必须为空；
2. 要求主密钥已独立恢复且长度 ≥32；有指纹时校验；
3. `restic restore` 到临时目录，找到 `db.dump`、`files.manifest`、`immutable.tsv`、快照内 tombstone；
4. 在目标库 `pg_restore --exit-on-error`；
5. 把私有文件复制到目标卷，用 `verify_manifest.py` 逐文件重算 sha256，并对照数据库 `artifacts`/`assets` 记录核对不可变 PDF hash；
6. 合并外部/快照 tombstone 并执行 `apply_tombstones.py`；不完整条目直接失败，绝不带着复活风险继续；
7. 执行 `post_restore.sql`：全部 staff 先 fail-closed 禁用、authority manifest 仅 overlay 非 super admin、会话/链接授权全撤、一次性 token/幂等响应清除、受保护邮件 payload 清除、worker 租约重置；
8. 写 `last-restore.json`，打印 `RESTORE_OK`。

### 5.3 恢复后全量撤权（脚本自动执行，SQL 也可审计）

`post_restore.sql` 的真实内容包含：

```sql
-- authority 表由 apply_tombstones.py 写入；没有更新的外部 manifest 时
-- latest=false，以下置空后不会自动恢复任何员工权限。
UPDATE staff SET enabled=false, status='DISABLED', capabilities='[]'::jsonb;
UPDATE customer_accounts SET session_version = session_version + 1;
UPDATE staff SET session_version = session_version + 1;
UPDATE auth_sessions SET revoked_at = now() WHERE revoked_at IS NULL;
UPDATE order_access_grants SET revoked_at = now() WHERE revoked_at IS NULL;
DELETE FROM one_time_tokens;
DELETE FROM idempotency_records;
UPDATE jobs SET status='QUEUED', lease_until=NULL, lease_token=NULL WHERE status='PROCESSING';
UPDATE email_outbox
SET protected_payload=NULL, status='CANCELLED',
    data = data - 'recipient' - 'email' - 'phone' - 'name' - 'subject' - 'body'
WHERE protected_payload IS NOT NULL OR status IN ('QUEUED','RETRY','RETRY_PENDING');
```

因此：旧浏览器会话、旧订单链接、旧验证/重置/临时密码 token、旧幂等重放、旧受保护邮件载荷都不能在恢复后继续使用。所有用户/员工必须重新登录。**员工角色/能力不能仅凭恢复库中的旧 grant 行复活**：只有 journal 中可证明更新的 authority manifest 才会 overlay 非 super admin 的当前事实；否则全部 staff 保持 disabled，等待人工 bootstrap review，super admin 永不自动恢复。主密钥不在 SQL 中，必须独立恢复。

### 5.4 切换验证

- 用目标数据库/卷启动一套临时 API（不要接生产流量），执行 `/api/v1/health/ready`；
- 抽查订单、不可变 PDF 下载 hash、回收站、退款账目、会话列表（应为空/已撤销）；
- 确认 tombstone 目标不存在，且目标文件已删除；
- 确认 `DORMPRINT_SECRET_KEY` 对应的加密载荷可读取（如 SMTP 配置、受保护任务载荷）；若不能，停止切换并修正密钥；
- 记录实际耗时，用于 RTO 报告。

## 6. RPO / RTO 目标与实测状态

- **RPO 目标 ≤ 1 小时**：调度每小时整点后创建快照；`last-success.json` 的 `epoch` 可监控。若远端故障/备份失败，实际 RPO 会变差，监控必须告警。
- **RTO 目标 ≤ 4 小时**：恢复时间 = 远端下载 + pg_restore + 私有卷复制 + 校验 + 撤权/验证。生产 RTO 取决于仓库带宽、数据量和主机性能，必须以真实仓库演练计时；本机小数据演练不能外推。
- 本机演练结果记录见第 8 节；**尚未在真实异地仓库和生产数据量上实测，故不宣称已达成生产 RPO/RTO**。

## 7. 失败语义与残余风险

- 未配置远端：`BACKUP_UNAVAILABLE`，退出 3，无成功记录；
- 仓库不可达/未初始化：`BACKUP_UNAVAILABLE` 或 `BACKUP_FAILED`，不写成功；
- 数据库 dump 为空、文件缺失/篡改、不可变 PDF hash 不匹配：`BACKUP_INTEGRITY`，不上传/不宣称成功；
- restic 上传或 prune 失败：`BACKUP_FAILED`；
- 恢复目标不安全、主密钥缺失/指纹不符、无 tombstone、tombstone 不完整、文件校验失败：`REFUSED`/`RESTORE_FAILED`，不暴露恢复库；
- **残余风险（外部重试）**：`backend/app/storage_coordination.py` 和 `deploy/backup/apply_tombstones.py` 已实现并测试协调锁、暂存/恢复、完整 tombstone、authority fail-closed。`process_purge_job` 已包装 `purge_guard`，`maintenance_sweep` 已自行包装 `storage_gc_guard`，account-deletion 的 `_revoke_account_credentials` 已包装；剩余外部依赖是 root 拥有的 `jobs.py` 对 commit 前 `PURGE_PENDING` 做有界 `QUEUED` 重试。该文件不在本 worker 所有权内，未修改。
- **残余风险（恢复后权限）**：旧快照可能包含备份后已被撤销的员工 grant。`post_restore.sql` 在无更新 authority manifest 时禁用全部 staff（含 super admin），不会让旧权限仅凭重新登录复活；需要人工 bootstrap review 后重新授权。
- **残余风险（非普通文件清理）**：`purge_guard` 的暂存只覆盖普通文件；手工永久清理遇到目录/符号链接时，maintenance 集成在**任何 unlink 之前**拒绝并保留 `PURGE_PENDING`/`storageCleanup`，不会声称 `PURGED`，也不会产生“先删后无法回滚”的窗口。此类目标需人工转换为普通文件或走单独 Q29 路径后才能清理。
- **演练边界**：本机只验证了隔离 schema 的锁/暂存/回放/SQL 行为，未在真实 restic 仓库、生产数据量或独立恢复主机上计时，因此不宣称生产 RPO/RTO。

## 8. 本机演练证据（仅限本机）

> 本节由实际命令生成；真实远端未提供，演练使用本机临时加密 restic 仓库和独立临时 PostgreSQL 容器，不代表异地备份/生产 RPO/RTO。

2026-10-31 实际执行：

```bash
cd /path/to/dormprint/backend
.venv/bin/pytest tests/test_local_restore_drill.py -s -q
```

结果：**1 passed, 1 warning in 23.06s**，退出码 0。完整记录见 [`docs/qa/2026-10-31-local-restore-drill.md`](qa/2026-10-31-local-restore-drill.md)。

- 环境：`dormprint-backup:prod`（`linux/arm64`，restic `0.18.1`）；`dormprint-drill-pg` `127.0.0.1:55433`；一次性 `dormprint_drill_f50b6458d7` / `dormprint_restore_f50b6458d7`；隔离 named volumes；最新 `deploy/backup` 只读挂载。
- 运行时探针：bind-mount 上 restic 读单个文件报 `read /src/a.txt: input/output error`，退出码 3（即使写出了 snapshot id）；同一批真实字节经 `docker cp` 放入 named volume 后 `files_new=1`、退出码 0。最终测试因此使用 named volumes + `docker cp`，仅脚本从 host 只读挂载。
- 快照：hourly `fd895cee35e7e288c9b68b2887927028933101072bfc22f27d7419d96bc0c18e`（`dormprint,hourly`，2026-10-30T23:00:33Z）；daily `50682996c14964a9ec58f035e37fd80bf9a62db006b681a630da10a18e4a1084`（`dormprint,daily`，23:00:36Z）。两个 ID 不同；daily `last-success.snapshot` 精确等于实际 daily 行。
- 恢复：使用 daily 快照 `50682996`，`RESTORE_OK`；快照后删除的 PDF 文件与行未复活，保留 PDF hash `9bc37641…` 匹配；AuthSession 撤销、`session_version=2`、恢复阶段全部 staff `DISABLED`、authority 来自外置 journal、匿名账本保留；`init-admin` 新建 `bootstrap-admin`（superadmin、enabled），种子 superadmin 仍禁用。
- 本机耗时（合成小数据）：hourly 5120ms、daily 3444ms、恢复 3762ms、bootstrap 601ms，测试内合计 20147ms；**不宣称生产 RPO/RTO**。
- 本次暴露并修复两个 root-owned 脚本缺陷：`last-success` 曾因 `restic snapshots --latest 1` 的 host+paths 分组选中 hourly 旧 ID，现改为解析 `restic backup --json` 的 `summary.snapshot_id` 并用 `restic cat snapshot` 校验；daily retention 曾按默认 host+paths 分组导致每次运行永久保留，现改为 `--group-by host --tag dormprint` 的 168 hourly + 30 daily 策略。
- 清理：测试 finally 删除一次性数据库和 named volumes，演练后无 `dormprint_drill_*` / `dormprint_restore_*` 残留；未触碰 55432 测试库、`date-*`/`userdate-*` 或其他 worker 容器。
- 边界：未验证真实远端、异地恢复、生产数据量或旧 `--time` 快照 age-prune；本记录不是生产备份验收。
