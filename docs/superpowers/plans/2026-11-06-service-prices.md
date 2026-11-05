# 服务价目配置 Implementation Plan

**Goal:** 将两种装订费和可配置配送费接入价目、后台与报价，零元显示红色免费。

**Architecture:** 扩展现有 prices JSON 分区的 delivery 整数分字段；Quote 独立配送费加到应付，不加入优惠商品行。前端共享价目元数据，保留现有移动样式。

**Tech Stack:** React、TypeScript、FastAPI、PostgreSQL、Vitest、Playwright。

## 约束

遵循 DESIGN.md、src/AGENTS.md 与 docs/UIUX-移动端布局规范.md；装订单位份，配送单位次；null 不是免费；只影响新报价；不发布或提交无关内容。

## 执行步骤

- [x] 先增加失败测试：配置 delivery=0/非零/null 保存；报价只加一次配送费、优惠不抵配送、历史快照不变；客户端验证新的应付公式。
- [x] 扩展 backend/app/config.py、growth.py、growth_schemas.py。配置默认兼容已有免费配送，显式空值拒绝报价。运行 PostgreSQL 用例验证红绿。
- [x] 通过 npm run contract:generate 更新实际 OpenAPI 和生成类型，扩展 src/lib/types.ts、demo.ts、v1/admin-api.ts 与 growth/pricing.ts。
- [x] 更新 help.tsx、operations.tsx 和 components/ui.tsx：八项价目、两种装订、配送输入、正确单位和红色免费，移除旧提示。保留加载与失败行为。
- [x] 加入价格页面的 Chromium/WebKit 回归到 mobile-ui 配置，覆盖 320、375、390、430、768、1440px，实际编辑/保存配送费与零元显示。成批检查桌面和手机截图。
- [x] 运行相关后端测试、npm run test:e2e:mobile-ui、npm test、npm run build 和 git diff --check，记录真实范围及限制。
