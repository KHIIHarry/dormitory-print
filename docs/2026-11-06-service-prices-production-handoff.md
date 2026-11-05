# 2026-11-06 价目和配送费更新：生产交接

本次需要同时更新前端、API、文件 worker、业务 worker 和 beat，并先迁移数据库。仅 pull、仅更新前端或仅重启 API 均不完整。无需新增环境变量或重建数据库。

## 发布顺序

1. 确认生产仓库没有未处理的本地修改，记录当前提交和数据库迁移版本，按生产既有备份流程备份数据库。
2. 安排维护窗口，暂停外部下单，让正在执行的文件任务正常结束。优雅停止 proxy、API、两个 worker 和 beat；保留 PostgreSQL、Redis 与持久卷。
3. `git pull --ff-only origin main`。保留生产 `.env`、域名、TLS overlay、Compose project name 等现有设置，不复制示例配置覆盖生产。
4. 构建真实模式前端并重建后端镜像。执行新镜像中的迁移，成功后再启动新版应用和 worker。迁移失败时停止发布，不继续启动。
5. 运行 doctor，确认健康检查、迁移版本与业务验收，再恢复访问。

## 使用仓库生产 Compose 时的命令

在项目根目录执行。以下使用基础生产 Compose；生产若使用额外 `-f`、`-p` 或其他 env 路径，所有命令必须沿用现有参数，不能另起一套栈。维护/备份按上述顺序由部署者先完成。

```bash
dc() { docker compose -f deploy/compose.yaml --env-file deploy/.env "$@"; }

# 维护窗口：正常等待正在执行的任务结束，不强制杀任务。
dc stop proxy api file-worker business-worker beat

git pull --ff-only origin main

npm ci --legacy-peer-deps
npm run build

# API、两个 worker、beat、migrate、doctor 使用同一个后端镜像。
dc build api
dc run --rm --no-deps migrate
dc run --rm --no-deps migrate python -m alembic current

dc up -d --no-deps --force-recreate --wait api file-worker business-worker beat
dc run --rm --no-deps doctor
dc up -d --no-deps --force-recreate --wait proxy
dc ps
```

迁移版本应为 `a6c8e0f2b014`（或后续已审核的新 head）。应用数据库新增 `quotes.delivery_fee_cents`，并把约束更新为“商品小计 − 优惠 + 配送费 = 应付”。现有报价补 0，不改写历史应付或报价 JSON。非 Compose 部署在使用生产数据库配置的新版 backend 环境执行 `.venv/bin/python -m app.cli migrate`，再重启对应进程并发布新 `dist/`。

完成后按实际入口验证 `/api/v1/health/live` 和 `/api/v1/health/ready`；检查 API/worker 日志，不应出现缺列、金额约束或报价校验错误。前端使用 `npm run build`，不能使用 `build:demo`。已有 PWA 页面应刷新或重新打开以加载新版代码。

## 业务验收

- 后台“系统配置 → 打印价目”出现配送费（元/次）；两种装订为元/份，保存后刷新仍保留。
- 旧配置没有配送费字段时，公开配置兼容返回 0，继续免费配送；不会自动开始收费。需要收费时由管理员明确保存实际金额。
- 帮助页显示五项打印、两项装订和配送价目；装订单位份、配送单位次；旧固定免费/未配置装订提示已移除。
- 任一价格设为 0，该项显示红色“免费”；留空显示“暂未配置”。配送费留空将阻止新报价，不应将空值当成免费。
- 在测试订单中核对：多文件也只收一次配送费；应付等于商品小计减优惠加配送费；商品优惠不抵配送。测试后恢复真实营业价格。
- 查看更新前订单，金额和配送费仍取原报价快照，不随新价变化。使用 iPhone Safari 检查输入、保存、金额和导航，不将桌面模拟当作真机验收。

## 回滚边界

如果已经产生非零配送费报价，不可直接回滚至只支持免费配送的旧前后端，也不可强行降级数据库。本迁移会拒绝在存在收费报价时降级，避免篡改账价；此时保留新金额结构并向前修复。如必须灾难恢复，应由部署负责人按备份恢复方案统筹，不能删除报价或把配送费改 0 来绕过约束。

## 本地验证范围

前端 385 项单元测试、44 项 Chromium/WebKit 回归（320–1440px）及生产构建通过；相关 PostgreSQL 报价、订单、收款与迁移测试通过。推送前再次验证 22 项价格相关前端测试，以及 5 项配送计价/全新隔离 schema 迁移测试。没有执行生产部署或真实 iPhone 验收。
