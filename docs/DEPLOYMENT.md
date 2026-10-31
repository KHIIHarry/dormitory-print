> **本轮身份及功能覆盖**：统一普通账号登录，按账号授予管理/配送能力，单PWA从个人页进入工作区；包括员工等级优惠、配送工资、逐笔追加费用及共照批量交付。01—04原Prompt与N/F定版稿为当前范围。旧独立员工登录/三PWA说明不再生效。初始化必须填写基础账号邮箱，不能以用户名猜测邮箱或自动关联已有用户。

# DormPrint V1 生产部署说明

日期：2026-10-30。状态：**部署包已实现，尚未在真实生产服务器上线**。

## 0. 事实边界（必须先读）

本轮已交付可配置、可构建、可演练的部署代码，但以下生产事实**尚未提供**，因此不能宣称“已上线”“已商用”或“真实生产目标已达成”：

- 真实服务器、CPU/内存/磁盘容量与操作系统发行版；
- 正式域名、DNS 和受信任 TLS 证书；
- 加密异地备份的真实远端（对象存储/SFTP 等）与凭据；
- 真实 SMTP 服务商、发信域名、SPF/DKIM/DMARC；
- 实体打印机、出纸整理与装订设备。

文中所有示例域名、密码、仓库地址都是占位值。没有默认管理员口令，没有种子数据，也没有演示订单。生产环境必须由部署者填写真实值并完成“实际验收”一节。

## 1. 拓扑与服务边界

`deploy/compose.yaml` 启动以下服务：

| 服务 | 作用 | 网络 | 持久卷 | 公网端口 |
|---|---|---|---|---|
| `proxy` | 同源 Nginx：三端 SPA 静态文件 + `/api/` 反向代理 | `edge` + `internal` | 只读挂载 `dist/` | 仅本机回环，默认 `127.0.0.1:8080` |
| `api` | `uvicorn app.main:create_app --factory`，只处理 HTTP | `internal` + `egress`（SMTP 测试需要） | `private-files` | 无 |
| `file-worker` | Celery `files` 队列，仅 `ASSET`/`PREPARATION` | 仅 `internal`，**无出网** | `private-files` | 无 |
| `business-worker` | Celery `business` 队列：`EMAIL`/`EXPORT`/`PURGE` | `internal` + `egress`（SMTP） | `private-files` | 无 |
| `beat` | `app.jobs:celery_app` 定时投递两个 outbox 路由与维护任务 | 仅 `internal` | 无 | 无 |
| `postgres` | PostgreSQL 16 | 仅 `internal` | `postgres-data` | 无 |
| `redis` | Celery broker / 缓存，启用 AOF 和密码 | 仅 `internal` | `redis-data` | 无 |
| `backup` | 每小时/每日 restic 加密异地备份，持有私有卷读取权和协调锁 | `internal` + `egress` | `private-files`（`/storage`）、`backup-state` | 无 |
| `volume-init` | 一次性把卷 chown 给 UID 10001 | 无网络 | 两个卷 | 无 |

关键约束：

- API 不运行 Celery worker，不执行 Office 转换；转换只由 `file-worker` 消费。
- `file-worker` 只在 `files` 队列消费，Compose 中没有 SMTP 变量，网络也没有默认路由，不能访问互联网。
- `business-worker` 只在 `business` 队列消费；它不处理 `ASSET`/`PREPARATION`。
- `postgres`/`redis`、API 和所有 worker 都没有 `ports`。只有 `proxy` 发布端口。
- 私有上传/成品卷 `private-files` 只挂载到 API、两个 worker、备份服务（和一次性 `doctor`）；Nginx 只挂载 `dist/`，绝不挂载或 alias 上传根目录。
- 所有应用容器 `read_only: true`、`cap_drop: ALL`、`no-new-privileges`，以 `10001:10001` 非 root 运行；文件 worker 另有 tmpfs、pids/memory/cpu 限制和单并发。

## 2. 环境变量与秘密

### 2.1 两种示例文件

- `backend/.env.example`：应用自身 `DORMPRINT_*` 配置的占位示例，供本地或非 Compose 运行参考。
- `deploy/.env.example`：Compose 插值变量（数据库/Redis 密码、restic、TLS/入口、容量阈值等）。复制为 `deploy/.env` 并 `chmod 600 deploy/.env`。

**只示例占位值**。不要把真实 `.env`、密码、restic 密码或私钥提交到 Git。仓库的 `.gitignore` 已忽略 `deploy/.env`、`backend/.env.local`、`backend/.data/`。

### 2.2 强制项

- `DORMPRINT_ENVIRONMENT=production`
- `DORMPRINT_PUBLIC_ORIGIN`：必须精确等于浏览器看到的 HTTPS 源，例如 `https://print.example.com`；`app/settings.py` 在 production 下拒绝非 HTTPS。
- `DORMPRINT_SECRET_KEY`：至少 32 字符的高熵随机值。用于 `seal/unseal` 主密钥；**独立于数据库和文件备份保存**（见 `docs/BACKUP-RESTORE.md`）。丢失后已加密载荷不可恢复。
- `POSTGRES_PASSWORD`、`REDIS_PASSWORD`：真实随机值；Compose 不会为数据服务发布端口，也不会使用测试密码。密码建议使用 URL 安全字符，因为数据库/Redis URL 由它们拼接。
- `DORMPRINT_MAIL_ALLOWED_HOSTS`：真实 SMTP 主机白名单；留空时邮件功能诚实显示不可用，不阻塞已有账号和游客业务。
- `DORMPRINT_INITIAL_ADMIN_USERNAME`：仅用户名，示例 `admin`。**初始密码没有默认值**，由 `init-admin` 交互输入。
- `DORMPRINT_BIND_ADDRESS` 默认 `127.0.0.1`；在 TLS 终止和防火墙确认前不要改成 `0.0.0.0`。

### 2.3 主密钥与指纹

把 `DORMPRINT_SECRET_KEY` 存入独立 secret manager（例如主机密钥管理、Vault、云 Secret）。可选地计算并单独保存：

```bash
printf '%s' "$DORMPRINT_SECRET_KEY" | sha256sum
```

把结果写入 `DORMPRINT_MASTER_KEY_FINGERPRINT`（只保存指纹，不保存密钥）。恢复脚本会用该指纹校验独立恢复的主密钥是否一致。

## 3. 构建前端与镜像

前端必须构建真实 API 版本，不能使用演示构建：

```bash
cd /path/to/dormprint
npm ci --legacy-peer-deps
npm run build
```

`dist/` 由 `proxy` 以只读方式挂载。未构建或误用 `build:demo` 时，代理没有可发布的真实壳。

构建后端镜像（Python 3.12 + LibreOffice Writer/Calc/Impress + Ghostscript + Noto CJK + HEIF/PDF 库；pip 只从锁定 `backend/requirements.txt` 安装）：

```bash
docker compose -f deploy/compose.yaml --env-file deploy/.env build api file-worker business-worker beat migrate doctor
```

备份工具镜像：

```bash
docker compose -f deploy/compose.yaml --env-file deploy/.env build backup
```

镜像运行时用户固定为 UID 10001。可用以下命令核对（构建完成后）：

```bash
docker run --rm dormprint-api:prod id
docker run --rm dormprint-api:prod sh -c 'soffice --version; gs --version; fc-list :lang=zh | head'
```

在真实服务器首次构建前，先运行预检：

```bash
DORMPRINT_SECRET_KEY=... DORMPRINT_PUBLIC_ORIGIN=https://... \
POSTGRES_PASSWORD=... REDIS_PASSWORD=... \
bash deploy/scripts/preflight.sh
```

预检只检查部署事实，不打印秘密；缺 `dist/`、弱/缺密钥、非 HTTPS 源会失败。

## 4. 首次启动与数据库初始化

```bash
cd /path/to/dormprint
cp deploy/.env.example deploy/.env
chmod 600 deploy/.env
# 填写真实值；至少替换数据库/Redis 密码、SECRET_KEY、PUBLIC_ORIGIN、PUBLIC_HOST
docker compose -f deploy/compose.yaml --env-file deploy/.env config >/dev/null
```

**不要**用 `create_all` 或测试夹具初始化生产数据库。迁移和首次管理员初始化通过已约定的 CLI 完成：

```bash
# 1) 数据服务、网络和卷
docker compose -f deploy/compose.yaml --env-file deploy/.env up -d postgres redis
docker compose -f deploy/compose.yaml --env-file deploy/.env run --rm volume-init

# 2) Alembic 迁移（等价 python -m app.cli migrate）
docker compose -f deploy/compose.yaml --env-file deploy/.env run --rm migrate

# 3) 首次管理员：交互输入密码，无默认口令
docker compose -f deploy/compose.yaml --env-file deploy/.env run --rm init-admin

# 4) 依赖/数据库/Redis/Office/Ghostscript 检查（等价 python -m app.cli doctor）
docker compose -f deploy/compose.yaml --env-file deploy/.env run --rm doctor

# 5) 启动全部服务
docker compose -f deploy/compose.yaml --env-file deploy/.env up -d
```

`app/cli.py` 由 Codex 维护；`init-admin` 要求 `--username` 和 `--email`（Compose 取 `DORMPRINT_INITIAL_ADMIN_USERNAME`、`DORMPRINT_INITIAL_ADMIN_EMAIL`），显式创建经部署负责人核验的基础客户账号并授予超管，交互读取两次密码，不接受默认密码。`doctor` 输出数据库、迁移版本、Redis、私有卷、LibreOffice/Ghostscript 状态；任何关键项缺失返回非零。

首次启动后核对：

```bash
curl -fsS http://127.0.0.1:8080/api/v1/health/live
curl -fsS http://127.0.0.1:8080/api/v1/health/ready
docker compose -f deploy/compose.yaml --env-file deploy/.env ps
```

`health/live` 只证明进程存活；`health/ready` 需要数据库。真实用户、价格、SMTP 等业务配置由管理员在后台按现有配置界面填写，部署脚本不会写入示例价目、假订单、假联系方式或默认密码。

## 5. Worker、队列与隔离

`app/jobs.py` 已定义：

- `dormprint.files` → `files` 队列，只取 `ASSET`、`PREPARATION`；
- `dormprint.business` → `business` 队列，只取 `EMAIL`、`EXPORT`、`PURGE`；
- `dormprint.maintenance` → `business` 队列；
- beat 每 2 秒投递两个 outbox 路由，每 30 秒投递维护扫描。

Compose 命令显式使用 `-Q files` / `-Q business`，并设置 `--concurrency=1`、`--prefetch-multiplier=1`、`--max-tasks-per-child`、Celery 时间上限和容器 memory/cpu/pids 限制。LibreOffice 在 `file-worker` 的 tmpfs `/tmp` 下创建临时 profile；容器根文件系统只读。文件 worker 没有任何 SMTP 变量且无出网，不能发信；business worker 也不领取文件转换任务。

真实 Office/HEIC/PDF 转换的成品验收必须在包含 LibreOffice/GS/字体的 `file-worker` 镜像内用真实样例执行；本部署文档不把“镜像里安装了包”当成“所有文档类型已验收”。

## 6. Nginx 同源、私有 API 与 TLS

`deploy/nginx/templates/default.conf.template` 提供：

- `/u/`、`/manage/`、`/courier/` 和 `/` 的 SPA fallback 到同一个 `index.html`；
- `/api/` 反代到 `api:8000`，`Cache-Control: no-store`，`X-Content-Type-Options: nosniff`，`Referrer-Policy: no-referrer`；API 访问日志关闭以免把高熵订单链接写入代理日志；
- 显式转发 `Range` / `If-Range`，并关闭代理缓冲，二进制最终 PDF/交付凭证的断点下载不被 Nginx 缓存或整段缓冲；
- `client_max_body_size` 取 `DORMPRINT_UPLOAD_LIMIT_MB`（默认 220MB），与 `DORMPRINT_MAX_REQUEST_BYTES` 对齐；服务端仍独立校验；
- 只有 `dist/` 静态目录，没有上传根的 `root`/`alias`；
- PWA 的 `sw.js`/`manifest.webmanifest` no-cache，带 hash 的 `/assets/` 长缓存。

### 6.1 推荐：主机 TLS 终止

默认只监听容器内 8080，Compose 发布到 `127.0.0.1:8080`。推荐在主机上使用 Caddy/Nginx/云负载均衡终止 TLS，再反代到该回环端口。要求：

1. 证书链和私钥由部署者提供并自动续期；
2. `DORMPRINT_PUBLIC_ORIGIN` 必须与外部 HTTPS 源完全一致；
3. 将 `X-Forwarded-Proto` 设为 `https`（Compose 内的 Nginx 已设置自己的 scheme；若再加一层代理，请保持该头可信）；
4. 外部代理不能绕过 `/api/` 的 no-store，不能缓存私有资产或导出；
5. 防火墙只开 443（及必要 80 做 ACME 跳转），不要公开 5432/6379/8000/8080。

### 6.2 直接 TLS overlay

仓库提供 `deploy/nginx/templates-tls/default.conf.template` 与 `deploy/compose.tls.yaml`。把真实 `fullchain.pem`、`privkey.pem` 放入 `deploy/nginx/tls/` 后：

```bash
docker compose -f deploy/compose.yaml -f deploy/compose.tls.yaml \
  --env-file deploy/.env up -d
```

该模板监听 443、重定向 80 到 HTTPS、启用 HSTS，并且只发布到回环 `127.0.0.1:8443`。是否由主机/防火墙对外开放仍由部署者决定；未提供证书时不启用此 overlay。示例不包含可用的证书或域名，不能据此声称 TLS 已验收。

## 7. 私有存储、权限与容量告警

- 所有上传原件、标准化文件、最终 PDF、导出 CSV 和交付凭证都在 `private-files` 卷的 `${DORMPRINT_STORAGE_ROOT}`（容器内 `/var/lib/dormprint`）下。
- `volume-init` 一次性把卷 chown 到 `10001:10001` 并设为 `0700`；应用容器非 root。若使用主机 bind mount，必须由部署者预先执行等价 `install -d -o 10001 -g 10001 -m 700`，不要把目录设为 777。
- `proxy` 不挂载该卷；`postgres`/`redis` 也不共享它。
- 备份容器为了协调锁需要写 `.coordination/maintenance.lock`，但只读取业务文件，不修改上传根内容；锁协议见备份文档。
- 容量告警是真实命令：

```bash
docker compose -f deploy/compose.yaml --env-file deploy/.env exec backup \
  env DORMPRINT_CAPACITY_ALERT_PERCENT=85 /opt/dormprint/check_capacity.sh
```

返回码 10 表示超过阈值，需接入主机/云监控。备份流程也会调用该检查并在日志中输出 `CAPACITY_ALERT`，但不会伪造外部告警已送达。

## 8. SMTP 出网与真实连接测试

SMTP 主机、端口、用户名和加密方式通过管理员后台“邮件”配置保存；密码由 `seal` 加密存库，不写在 `.env`。部署侧只提供：

- `DORMPRINT_MAIL_ALLOWED_HOSTS`：真实 SMTP 主机白名单（部署事实，不能由界面凭空提供）；
- `DORMPRINT_ALLOW_INSECURE_SMTP=false`（生产默认拒绝明文）；
- `DORMPRINT_ALLOW_INTERNAL_SMTP=false`（默认拒绝内网地址，除非部署者明确批准）；
- `DORMPRINT_MAIL_TIMEOUT_SECONDS`。

API 和 `business-worker` 接入 `egress` 网络，因此 API 的真实 SMTP 连接测试和 worker 的真实投递具备出网路径；`file-worker` 没有。SMTP 被接受不等于进入收件箱，后台状态只报告真实队列/受理结果。

**尚未提供真实 SMTP**，所以没有真实发信、SPF/DKIM/DMARC 或收件箱验收记录；不得在未配置时宣称邮件已可用。未配置时新注册按 Q24 保持不可用，已有账号/游客业务不因邮件故障停摆。

## 9. 实际验收与未验收项

已实际执行（2026-10-30，本机）：

- `docker compose -f deploy/compose.yaml config`：Compose 渲染成功；缺强制秘密/源时失败关闭；
- `pytest backend/tests/test_deployment.py backend/tests/test_backup_tools.py`：见本轮报告与下方结果；
- 备份脚本在未配置远端时以非零退出并输出 `BACKUP_UNAVAILABLE`，不写成功记录；
- 本机临时 PostgreSQL + 本地加密 restic 仓库的恢复演练结果见 `docs/BACKUP-RESTORE.md`（只证明脚本与恢复流程，不代表真实异地仓库/生产服务器）。

尚未验收（必须由部署者提供事实后执行）：

- 真实服务器上的镜像构建、资源限制、系统启动、监控和外部 TLS；
- 真实域名/DNS/证书/HSTS 浏览器验收；
- 真实异地 restic 仓库的上传、下载、保留轮换与 RPO/RTO 实测；
- 真实 SMTP 连接、投递、退信和 SPF/DKIM/DMARC；
- 实体打印机单双面/翻转/灰度/照片纸、装订和移动设备相机/定位/PWA 实机；
- 生产容量的长期容量告警演练。

在上述项目真实完成前，本文件只声明“部署包可配置、可构建、已做本机演练”，不声明生产上线。

## 10. 日常操作

```bash
# 状态与日志
docker compose -f deploy/compose.yaml --env-file deploy/.env ps
docker compose -f deploy/compose.yaml --env-file deploy/.env logs -f api file-worker business-worker
docker compose -f deploy/compose.yaml --env-file deploy/.env logs -f backup

# 升级代码后：先备份并验证恢复点，再迁移，再滚动重启
docker compose -f deploy/compose.yaml --env-file deploy/.env run --rm backup /opt/dormprint/backup.sh --mode daily
docker compose -f deploy/compose.yaml --env-file deploy/.env run --rm migrate
docker compose -f deploy/compose.yaml --env-file deploy/.env up -d --build

# 停止但保留数据卷
docker compose -f deploy/compose.yaml --env-file deploy/.env down
```

迁移是前向的；回滚代码前先确认新迁移与旧代码兼容，必要时从独立恢复库恢复，不要对生产库执行 `DROP`/`TRUNCATE`。升级前必须保留可恢复的加密备份点。

## PDF 预览脚本与 MIME 类型

PDF.js 的工作线程及浏览器回退加载都要求脚本以 JavaScript 类型响应。若 `.mjs` 被静态服务器当成 `application/octet-stream`，会出现 `Setting up fake worker failed` 或 `Failed to fetch dynamically imported module`，即使脚本请求返回 200 也不能预览。

仓库的 Nginx 主配置显式将 `.mjs` 映射为 `application/javascript`；Vite 同时把 PDF 工作线程产物命名为带哈希的 `.js`，让新页面避开旧 `.mjs` 地址缓存的错误响应。发布时需要重新构建前端，并更新、重载 Nginx 配置；使用前端镜像时重新构建并替换镜像。只修改前端源码而不发布产物不会修复线上响应。发布后重新打开页面，并检查实际 worker 请求的 `Content-Type` 为 `application/javascript` 或 `text/javascript`。

可在本地使用 Docker 与 Chrome 复现生产静态服务并验证正常工作线程、回退渲染和最终 PDF 确认（仅使用虚构演示文件，不连接私有 API）：

```sh
npx vite build --mode demo --outDir output/pdf-preview-qa/dist
node --test tests/deploy/pdf-preview.smoke.mjs
```

演示构建仅写入已忽略的本地测试目录；正式发布仍使用 `npm run build` 的 `dist/`。
