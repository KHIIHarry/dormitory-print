# 登录 IP 的可信代理边界：deploy/nginx 修复与隔离配置测试

日期：2026-11-01（Asia/Shanghai）
范围：`deploy/nginx`、`deploy/compose.yaml` 的代理信任参数、`backend/Dockerfile` 独立运行默认值、`deploy/local.sh` 本地隔离、部署文档，以及新增的代理链路配置测试。未改应用业务代码，未触碰数字表单相关文件，未部署、未提交、未 reset。

## 结论

1. 容器内 Nginx 对 `/api/` 现在**覆盖**（不再追加）`X-Real-IP` 与 `X-Forwarded-For` 为 `$remote_addr`，HTTP 模板与 TLS 模板一致。客户端自带的 `X-Forwarded-For` 链不会到达 API。
2. API 的 `--forwarded-allow-ips` 从 `*` 收紧为固定的代理地址 `DORMPRINT_PROXY_IP`（默认 `172.30.0.2`），该地址位于 `internal` 网络固定子网 `172.30.0.0/24` 内；只有 proxy 能影响 API 看到的客户端地址，`api` 仍不发布端口。
3. `backend/Dockerfile` 在脱离 Compose 直接运行时默认只信任 `127.0.0.1`，不再默认信任所有代理。
4. 本地栈（`deploy/local.sh`）使用 `172.31.0.0/24` + `172.31.0.2`，与生产默认网段错开，避免同主机并行运行两套栈时地址冲突。
5. 新增 `backend/tests/test_proxy_headers.py`（7 个测试）：静态配置断言、真实 Nginx 容器链路（隔离的临时网络/容器，跑完清理）、uvicorn 信任语义（含 IPv6）。伪造的 `X-Forwarded-For` / `X-Real-IP` 在真实代理链路中被丢弃。

## 拓扑与信任边界

```text
浏览器 / 外部 LB
      │  只发布 127.0.0.1:8080（TLS overlay 为 127.0.0.1:8443）
      ▼
proxy (nginx-unprivileged, edge + internal, 固定 internal 地址 172.30.0.2)
      │  /api/ -> http://api:8000
      │  X-Real-IP: $remote_addr        （覆盖）
      │  X-Forwarded-For: $remote_addr （覆盖，不追加）
      ▼
api (internal + egress, 无 ports, --forwarded-allow-ips 172.30.0.2)
      │  request.client.host 由 uvicorn 只对可信 peer 解析 X-Forwarded-For 得到
      ▼
登录会话记录 device_label 描述 -> /customer/account/sessions
```

关键点：

- 修复前：`X-Forwarded-For $proxy_add_x_forwarded_for` 会保留客户端自带条目，compose 又是 `--forwarded-allow-ips "*"`；uvicorn 0.54 在 always-trust 模式取 `X-Forwarded-For` **最左**一项，客户端可伪造。
- 修复后：Nginx 只写一个由它自己看到的 TCP 对端地址；uvicorn 也只接受来自固定 proxy 地址的头。即使有调用方绕过 Nginx 直连 API（仓库拓扑下 API 无发布端口），也不能让 API 信任其转发头。
- 历史会话不因本次修复而改变：没有记录字段的旧会话仍显示“未记录”，不会用新逻辑反推。

## 变更文件

| 文件 | 变更 |
|---|---|
| `deploy/nginx/templates/default.conf.template` | `/api/` 的 `X-Forwarded-For` 改为 `$remote_addr`（覆盖），保留 `X-Real-IP $remote_addr`；注释说明为什么不能追加客户端值。 |
| `deploy/nginx/templates-tls/default.conf.template` | 同上，TLS 直连模板同步。 |
| `deploy/compose.yaml` | `internal` 网络增加 `ipam` 子网 `${DORMPRINT_INTERNAL_SUBNET:-172.30.0.0/24}`；proxy 在 internal 上固定 `${DORMPRINT_PROXY_IP:-172.30.0.2}`；api `--forwarded-allow-ips` 改为 `${DORMPRINT_PROXY_IP:-172.30.0.2}`。 |
| `backend/Dockerfile` | 独立运行 CMD 的 `--forwarded-allow-ips` 从 `*` 改为 `127.0.0.1`。 |
| `deploy/local.sh` | 导出本地栈专用 `DORMPRINT_INTERNAL_SUBNET=172.31.0.0/24`、`DORMPRINT_PROXY_IP=${DORMPRINT_INTERNAL_SUBNET%.*}.2`（默认 `172.31.0.2`），允许调用方覆盖。 |
| `deploy/.env.example` | 增加两个变量的说明与默认值。 |
| `docs/DEPLOYMENT.md` | 2.2 增加变量要求；6.1 补充客户端 IP 指引；新增 6.3「客户端 IP 与可信代理边界」：覆盖语义、固定 hop、本地隔离、外部 LB 未识别时的安全默认与 `real_ip` 配置方式、历史“未记录”。 |
| `backend/tests/test_proxy_headers.py` | 新增 7 个隔离配置测试（见下）。 |
| `docs/incremental/2026-11-01-login-device-report.md` | 原「部署侧代理信任仍是宽配置」风险项改为已由本次修复处理并指向本报告。 |

## 关键决策

- **为什么覆盖而不是依赖 uvicorn 的“从右往左”解析**：覆盖后 API 只看到一个由 Nginx 写入的地址，链式伪造无从谈起；收紧 trust 是第二道边界。两者同时做，任何一层单独失效都不至于直接可伪造。
- **为什么需要固定子网**：uvicorn 的 `--forwarded-allow-ips` 需要具体地址/CIDR，而 Docker 自动分配的网段要在容器启动后才知道。Compose 只能在渲染期插值，因此把 `internal` 子网与 proxy 地址固定为可覆盖的部署变量；默认值与本地值错开。
- **为什么不给外部 LB 预置 `set_real_ip_from`**：仓库不知道部署者的代理地址；默认信任未知代理等于重新打开伪造面。默认行为是显示最外层 TCP 对端，文档说明如何在**容器内 Nginx** 显式声明可信外层以还原真实客户端。
- **为什么不改应用代码**：应用此前已只读 `request.client.host`、不解析任何转发头；本次修复的是这个 `client.host` 的来源可信性。

## 实际执行的检查

| 命令 | 结果 |
|---|---|
| `cd deploy && docker compose --env-file .env.example config --format json` | exit 0；api `--forwarded-allow-ips` = `172.30.0.2`；proxy internal `ipv4_address` = `172.30.0.2`；internal subnet = `172.30.0.0/24`；api 无 ports。 |
| `DORMPRINT_LOCAL_ENV_FILE=/tmp/... bash deploy/local.sh config --format json` | exit 0；本地 api 信任 `172.31.0.2`，proxy `172.31.0.2`，子网 `172.31.0.0/24`，与生产默认不同。 |
| 修复前真实代理链路（临时容器，已清理） | 从上游容器伪造 `X-Forwarded-For: 6.6.6.6` 请求 → 上游看到 `xff=[6.6.6.6, 172.28.0.2]`，伪造值被追加并到达后端。 |
| 修复后同一链路（HTTP 模板） | 伪造 `X-Forwarded-For: 6.6.6.6, 7.7.7.7`、`X-Real-IP: 6.6.6.6` → 上游只看到 `xff=[172.28.0.2] realip=[172.28.0.2]`（即上游自身 TCP 地址），伪造值消失。 |
| 修复后 TLS 模板链路（自签证书，临时容器，已清理） | 上游只看到 `xff=[172.28.0.2] realip=[172.28.0.2]`。 |
| `cd backend && .venv/bin/python -m pytest tests/test_proxy_headers.py -q` | **7 passed**，1 个既有 starlette/httpx 弃用 warning；含真实 Docker 链路测试。 |
| `cd backend && .venv/bin/python -m pytest tests/test_deployment.py -q` | **14 passed**，未被本次 compose/Dockerfile 改动破坏。 |
| `cd backend && .venv/bin/python -m pytest tests/test_proxy_headers.py tests/test_deployment.py -q` | **21 passed**，7.81s。 |
| `node --test tests/deploy/local-docker.test.mjs` | 1 pass；本地栈渲染仍满足既有隔离断言。 |
| `git diff --check`（本次改动文件） | exit 0。 |
| `cd backend && .venv/bin/python -m pytest -q`（全量回归） | **649 passed, 2 skipped**，290.40s；skip 为需要真实浏览器和 `DORMPRINT_RUN_BROWSER_TESTS=1` 的既有可选测试。 |

`backend/tests/test_proxy_headers.py` 的 7 个测试：

1. 两个 Nginx 模板都覆盖 `X-Forwarded-For $remote_addr`，不含 `$proxy_add_x_forwarded_for`、`$http_x_forwarded_for`、`$http_x_real_ip`。
2. compose 的 api 信任地址 == proxy 固定 internal 地址，位于子网内，且不是 `*`/`0.0.0.0/0`；api 无 ports，proxy 只发布回环。
3. 本地栈导出的子网/代理地址位于子网内，且与生产默认不同；真实渲染的本地 compose 使用同一组值。
4. Dockerfile 直接运行默认只信任 `127.0.0.1`。
5. 部署文档包含 `DORMPRINT_PROXY_IP`、`set_real_ip_from`、禁止 `0.0.0.0/0`、历史“未记录”。
6. uvicorn 只接受可信 peer 的头；不可信 peer 无法覆盖 `client.host`；即使伪造链到达，受限信任列表从右往左解析会忽略最左伪造项；IPv6 字面量可正确解析。
7. 真实 Nginx 容器 + 上游 echo 容器：伪造 `X-Forwarded-For`/`X-Real-IP` 后，上游只看到请求方的真实 TCP 地址，且 XFF 只有一个值；测试在 `finally` 删除容器与网络，Docker/镜像不可用时显式 skip 而不是伪造结果。

## 安全边界与剩余风险

1. **既有生产 proxy 只读启动缺陷（与本改动无关，未修）**：`deploy/compose.yaml` 的 proxy 是 `read_only: true`，但上游 nginx 镜像启动时要 `envsubst` 写 `/etc/nginx/conf.d/default.conf`，实测报
   `can't create /etc/nginx/conf.d/default.conf: Read-only file system` 并退出。HEAD 即如此，本地栈因使用 `dormprint-web:local` + `/tmp/conf.d` 不受影响。建议 Codex 决定：给 proxy 增加 `/etc/nginx/conf.d` 的 tmpfs，或改用预先渲染配置的镜像。本次未改，避免越权扩大部署变更；不修则生产 proxy 无法启动，代理信任修复也无法上线。
2. **固定子网是新的部署约束**：已有部署升级后需要重建 `internal` 网络（会短暂中断）；`DORMPRINT_INTERNAL_SUBNET` 与 `DORMPRINT_PROXY_IP` 必须一起改且地址在子网内，否则 `up` 失败或 API 不信任代理。若与主机上其他 Docker 网段冲突，用环境变量错开。
3. **外部反向代理未识别时保持安全但不精确**：主机 Caddy/Nginx/云 LB 场景下，容器 Nginx 的 `$remote_addr` 是外层代理/网关地址，设备页显示该地址；这是有意的安全默认。要显示终端用户 IP，需在容器内 Nginx 显式配置受信外层 `set_real_ip_from` + `real_ip_header` + `real_ip_recursive`，仓库没有预置未知代理。
4. **未做真实部署与真实 IPv6 网络链路**：验证是隔离容器链路，不是线上 nginx/云 LB 验收；IPv6 只覆盖了 uvicorn 解析，未在容器网络里跑 IPv6 客户端。
5. **共享出口**：NAT/校园网/公司出口会让多人显示同一 IP，这是网络事实；设备页仍只用于本人识别会话，不作为身份凭据。
6. 历史会话仍显示“未记录”，本次修复不追溯、不猜测。

## 复现

```bash
cd deploy && docker compose --env-file .env.example config --format json
DORMPRINT_LOCAL_ENV_FILE=/tmp/dormprint-local.env bash deploy/local.sh config --format json
cd ../backend && .venv/bin/python -m pytest tests/test_proxy_headers.py tests/test_deployment.py -q
cd .. && node --test tests/deploy/local-docker.test.mjs
```

真实代理链路（测试内自动执行，手动复现时可参考）：起一个返回 `$http_x_forwarded_for`/`$http_x_real_ip` 的上游 nginx，挂载仓库 `deploy/nginx/nginx.conf` 与 `templates` 起 proxy，从上游容器带伪造头发请求，确认上游只看到上游自身的 TCP 地址。

## 未做

- 未部署、未重启线上、未修改线上配置；未 git commit/reset。
- 未改应用业务代码、数据库、迁移、前端、数字表单相关文件。
- 未修改 Codex 拥有的 `backend/tests/test_deployment.py`、`backend/tests/conftest.py` 等文件。
