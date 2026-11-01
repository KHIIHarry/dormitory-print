# 登录设备显示浏览器与登录 IP —— 交付报告

任务：账号本人查看自己的登录会话时，显示具体浏览器（微信/Edge/Chrome/Safari 等）和登录 IP；
历史会话无法可靠恢复时显示「未记录」而不是猜测；不信任客户端可伪造的转发头；保持两设备限制、
会话撤销能力和现有隐私边界。实现范围仅限账号本人可见的会话接口与账号前端。

## 变更文件

| 文件 | 变更 |
| --- | --- |
| `backend/app/auth.py` | 登录时把紧凑客户端描述（浏览器/平台/IP）写入既有 `device_label`；会话列表解析该描述并新增 `browser`/`platform`/`ip_address` 字段；旧占位标签返回 `未记录`。IP 只取 ASGI 服务器解析后的 peer 地址（`request.client.host`），**不解析任何转发头**。 |
| `backend/app/auth_schemas.py` | `SessionDeviceView` 新增可选字段 `browser`、`platform`、`ip_address`（JSON 为 camelCase `ipAddress`），历史会话返回 `null`。 |
| `backend/tests/test_auth.py` | 新增 9 个 PostgreSQL 集成测试 + `login_from` 辅助函数和浏览器 UA 常量。 |
| `src/growth/types.ts` | `SessionDevice` 增加 `browser`/`platform`/`ipAddress: string \| null`。 |
| `src/customer/account.tsx` | 安全页会话行显示「登录 IP：<IP 或未记录>」，浏览器/平台作为设备名；撤销确认文案与 aria-label 带上登录 IP；设备数量超限选择器也显示登录 IP，便于区分同名浏览器。 |
| `src/admin/staff-account.tsx` | 员工账户页的同一会话列表补一行登录 IP（数据仍只来自本人 `account_id` 的会话）。 |
| `docs/openapi.yaml`、`src/lib/api-schema.d.ts` | 由 `npm run contract:generate` 重新生成，未手改。 |
| `tests/e2e/v1-customer.spec.ts` | 新增账号安全页端到端用例，并扩展模拟接口的 `sessions` 状态与撤销路由。 |

未部署、未提交、未 reset/stash；其他 worker 的改动（`src/admin/logistics.tsx`、`src/admin/operations.tsx`、
`src/v1/admin-api.ts` 等）保持原样。

## 实现要点

- **不新增共享表结构**：`auth_sessions` 与迁移归 Codex 所有，且 `tests/test_migrations.py` 会执行
  `alembic check`；在模型上直接加列会与迁移不一致。因此复用 `device_label String(160)` 存放
  版本化 JSON 描述：`{"v":1,"browser":"Chrome","platform":"Windows","ip":"203.0.113.9"}`。
  该列原本只是展示用标签，不含身份语义；两设备限制、`device_id`、token 与撤销逻辑均未改动。
- **浏览器识别**只保留粗粒度族名，按顺序匹配：微信（MicroMessenger）、企业微信、Edge、Opera、
  三星、UC、QQ、Firefox、Chrome、Safari；平台识别 iOS/Android/ChromeOS/Windows/macOS/Linux。
  原 UA 最多读取 1024 字符，**不落库、不返回**；测试断言 `device_label` 中不含
  `Mozilla`/`AppleWebKit`/`Chrome/131`/`Safari/537`。
- **IP 规范化**：`ipaddress` 校验，IPv6 去方括号与 zone id，IPv4-mapped IPv6（`::ffff:198.51.100.4`）
  归一为 IPv4；非法值返回 `None` 而不是字符串兜底。
- **历史会话**：旧行 `device_label = '浏览器'`（或空）没有任何网络信息，解析为
  `browser/platform/ipAddress = null`，`device` 返回「浏览器信息未记录」；前端 IP 行显示「未记录」。
  不回填、不按 `last_seen_at` 或其它字段猜测。
- **本人可见**：`GET /customer/account/sessions` 与 `GET /auth/sessions` 原本就按
  `account_id`/`staff_id` 过滤（员工接口也只列出了员工自己账号的 CUSTOMER 会话），新增字段没有改变
  过滤条件；撤销仍校验会话归属。
- **同设备重新登录**会刷新描述（IP/浏览器以最新一次登录为准），其他会话保留各自登录时的记录。
- 前端演示数据未改：DEMO 会话仍显示「当前浏览器 · 开发演示」，登录 IP 显示「未记录」（演示环境
  没有真实网络数据，不应伪造）。

## 实际执行的检查

| 命令 | 结果 |
| --- | --- |
| `cd backend && .venv/bin/python -m pytest tests/test_auth.py -q` | **33 passed**，1 条 starlette/httpx 弃用警告，12.96s |
| `cd backend && .venv/bin/python -m pytest -q` | **642 passed, 2 skipped**（需显式开启的真实浏览器用例），316.04s |
| `npx tsc -b --pretty false` | 通过（无输出） |
| `npx vitest run tests/customer-account.test.ts tests/growth-contract.test.ts tests/contract-generation.test.ts` | 16 passed |
| `npx vitest run` | **58 个测试文件 / 330 个测试全部通过** |
| `npx playwright test tests/e2e/v1-customer.spec.ts --project=desktop -g "security page shows"` | 1 passed（新用例） |
| `npx playwright test tests/e2e/v1-customer.spec.ts --project=mobile -g "security page shows"` | 1 passed（新用例） |
| `npx playwright test tests/e2e/v1-customer.spec.ts --project=desktop --reporter=line` | 10 passed，2 failed（见下，均为 HEAD 上既有的陈旧断言，与本改动无关） |

新增后端测试覆盖：

1. 账号本人看到 `Chrome · Windows`、`203.0.113.9`；他人会话无权限撤销（404）。
2. 历史 `device_label='浏览器'` → `browser/platform/ipAddress=null`、`device=浏览器信息未记录`。
3. 客户端伪造 `X-Forwarded-For`/`X-Real-IP`/`Forwarded` 不影响记录的 IP。
4. IPv6 `2001:db8::7` 与 IPv4-mapped `::ffff:198.51.100.4` → `198.51.100.4`。
5. 微信 iOS/Android、Edge、Chrome、Safari、Firefox、curl、空 UA 的识别结果。
6. 未知 UA 保留真实 IP，但不猜浏览器。
7. 落库标签不超过 160 字符且不含原始 UA。
8. 同设备重新登录刷新 IP/浏览器（仍只有 1 个活跃会话）。
9. 截断 JSON 或未来版本描述（无法解析）→ `未记录`，且不把原始 JSON 字符串当设备名展示。

新增前端用例：安全页逐会话显示浏览器与 IPv4/IPv6 登录 IP；历史行显示「未记录」且不出现任何浏览器名；
撤销时确认文案含 IP，POST 体为 `{sessionId:"S-IPV6"}`，撤销后重新拉取列表。

### 已确认与本改动无关的既有 e2e 失败

以下 2 个用例在 HEAD（`6ab8285`）上即失败，失败原因不在本模块文件：

- `deletion request needs explicit confirmation...`：断言的文案「注销申请等待管理员审核」在
  `git show HEAD:src/v1/customer.tsx` 中出现 0 次，UI 文案已变更但测试未同步。
- `upload waits for account hydration...`：断言 `.header-profile` 是 `href="/u/account"` 的链接，
  但 HEAD 的 `src/components/account-menu.tsx` 已把已登录态的 `.header-profile` 改为
  `<button class="account-menu-trigger">`（下拉账户菜单），测试未同步。

## 安全边界与剩余风险

1. **部署侧代理信任已由后续修复处理（待 Codex 验收）**：本报告初版发现 compose 的 api 使用
   `--forwarded-allow-ips "*"` 且 nginx 用 `$proxy_add_x_forwarded_for` 追加客户端值，能直连 nginx
   的调用方可伪造登录 IP。后续已改为 nginx **覆盖** `X-Forwarded-For $remote_addr`，api 只信任
   固定代理地址，并新增代理链路配置测试；详见
   `docs/incremental/2026-11-01-proxy-ip-trust-report.md`。真实部署仍须重新渲染 compose 并重启。
2. **无专用列**：IP/浏览器寄存在 `device_label`，虽为展示用途、长度受控，但查询/统计不便。若后续要
   按 IP 风控或审计，应由 Codex 增加 `login_ip`/`login_browser` 等列与迁移，并同步 DTO。历史会话
   仍无法恢复。
3. **IP 是网络出口地址**：NAT/校园网/移动网络下多人可能共享同一 IP，且 IP 会随网络切换；
   它只用于帮助本人识别设备，不应当作身份凭据或风控唯一依据。
4. **未记录不等于异常**：升级前创建的会话、缺少 UA 的非浏览器客户端都会显示未记录；前端不得据此
   推断为攻击。
5. 员工账户页展示的是员工本人账号下的 CUSTOMER 会话（接口既有作用域），不会暴露其他账号的 IP。
6. 尚未做真实浏览器 + 真实 nginx 的代理链路验收（本次未部署）；e2e 在 Vite + 网络拦截层验证
   DTO → UI 的完整路径，后端 IP 语义由 PostgreSQL 集成测试覆盖。

## 复现

```bash
cd backend && .venv/bin/python -m pytest tests/test_auth.py -q
cd .. && npx tsc -b --pretty false
npx vitest run
npx playwright test tests/e2e/v1-customer.spec.ts --project=desktop -g "security page shows"
npx playwright test tests/e2e/v1-customer.spec.ts --project=mobile -g "security page shows"
```

测试 PostgreSQL：`127.0.0.1:55432`（`dormprint-test-postgres`），由 `tests/conftest.py` 建独立 schema。
