# DormPrint · 宿舍打印系统

React/TypeScript统一应用，FastAPI + PostgreSQL + Redis/Celery后端，LibreOffice/PyMuPDF处理私有文件。V1已实现已确认业务范围，并提供真实后端、迁移、作业、前端与部署工具；本地验收记录和真实营业环境的上线检查分别列明。

## 当前业务范围

权威规则：[V1定版稿](docs/superpowers/specs/2026-10-30-dormprint-v1-scope.md)、[本轮N/F增补](docs/superpowers/specs/2026-10-30-dormprint-v1-adjustments.md)、[功能交付矩阵](docs/2026-10-30-DormPrint-V1-功能交付矩阵.md)。原01—04 Prompt位于docs目录，已同步覆盖冲突规则。

一个普通客户账号登录，超管向对应账号授权；个人页只为获权账号显示管理/配送入口，各工作区可返回用户端。使用唯一PWA身份和根Service Worker，保留/u、配置管理路径及/courier作为受保护深链接，取消三套独立安装/独立员工密码体系。后台路由可见不代表有权调用接口。

上传/断点续传→真实转换→最终PDF确认/报价→人工线下收款或零元结算→打印/装订/装袋→批次/同楼栋共照批量交付→售后重做/退款。包含本地链接二维码、1—6级员工整单折扣与超管原打印单免单、按定额或净实收比例统计配送工资、人工发放及退款调整。

管理员可在原单下追加最终金额，每笔必须填用途并选择是否计入比例工资；原单付款/送达后仍可追加。追加费用即时生效、客户可提异议，独立应收/实收/退款/欠收，不重算原单优惠、不回滚履约。只有实际收到且勾选的追加费用参与比例工资，定额工资不随追加金额变化。

## 本机 Docker 一键运行

安装并启动 Docker Desktop（Compose >=2.24.4），在项目根目录执行：

```sh
bash deploy/local.sh up
```

访问 http://localhost:8080。前端在 Docker 内编译，API、PostgreSQL、Redis、文件转换 worker、业务 worker 和定时任务全部在容器中运行，无需本机安装 Node、Python 或 LibreOffice。首次构建需要下载依赖；启动脚本会自动等待数据库/Redis、执行迁移并检查运行环境。

本地凭据首次生成在已忽略的 `deploy/.env.local`（权限 600），重复启动会复用；数据库和私有文件放在 `dormprint-local` 独立命名卷。保留该凭据文件，不要在保留旧数据卷时删除或替换它。此模式只监听本机回环地址，使用 HTTP，仅用于本机运行；正式部署参见部署手册。

首次使用，替换下面的邮箱为你的真实邮箱，再交互设置管理员密码（不提供默认账号密码）：

```sh
bash deploy/local.sh init-admin --username admin --email 你的真实邮箱
```

随后从 `/u/account` 登录。新注册需要另行配置 SMTP；未配置时可使用游客业务和已初始化的账号。

```sh
bash deploy/local.sh ps
bash deploy/local.sh logs -f api file-worker business-worker
bash deploy/local.sh doctor
bash deploy/local.sh down   # 停止服务，保留数据卷
bash deploy/local.sh up     # 再次启动，也可在更新代码后重建
```

本机默认不启动异地备份服务；上线备份和 HTTPS 配置参见 [部署手册](docs/DEPLOYMENT.md)。

## 本地开发运行

Node >=20.19、Python3.12、PostgreSQL、Redis，以及LibreOffice、Ghostscript和实际中文字体。真实模式没有自动mock回退。

```sh
npm ci --legacy-peer-deps
cp .env.example .env.local
npm run dev
```

Vite在5173端口代理/api/v1到127.0.0.1:8000，可用DORMPRINT_DEV_API_TARGET调整开发代理。普通用户与获权员工统一从/u/account登录；旧管理/配送登录地址仅兼容跳转到基础账号登录。

```sh
cd backend
python3.12 -m venv .venv
.venv/bin/pip install -r requirements.txt
# 复制.env.example到.env.local并填写专属数据库、Redis、存储及密钥；不要提交秘密。
.venv/bin/python -m app.cli migrate
.venv/bin/python -m app.cli init-admin --username admin --email owner@example.com
.venv/bin/uvicorn app.main:create_app --factory --host 127.0.0.1 --port 8000
```

邮箱示例必须换成由部署负责人核验的真实基础账号邮箱。初始化交互设置密码，无默认密码、示例收费或演示订单。员工账号、普通客户共用最多两台设备额度，员工权限从当前账号授权动态派生。

另开终端运行文件/业务作业与维护调度；生产由Compose内的队列服务承担：

```sh
cd backend
.venv/bin/celery -A app.jobs:celery_app worker -Q files,business --concurrency=2
.venv/bin/celery -A app.jobs:celery_app beat
```

生产文件转换worker与业务发信worker按部署文件分网、分队列；本地单worker仅用于联调。配置SMTP前新注册会明确暂不可用，已有账号和游客业务不依赖SMTP成功。

## 构建、契约和验证

```sh
npm run contract:generate
npm test
npm run build
# 后端测试仅使用独立DormPrint测试库，各进程自动创建隔离schema
cd backend
.venv/bin/pytest -q
.venv/bin/alembic check
.venv/bin/python -m app.cli doctor
```

契约由实际FastAPI OpenAPI生成，docs/openapi.yaml及src/lib/api-schema.d.ts不手改。测试库默认本机55432；生产凭据必须使用环境配置。Playwright包含桌面和移动视口，模拟接口测试不等于真实后端或实机验收。真实模式构建输出dist；build:demo仅供明确演示，不能用于营业。

## 部署与交付证据

[部署手册](docs/DEPLOYMENT.md)、[备份恢复](docs/BACKUP-RESTORE.md)。验收记录、截图、日志及浏览器追踪仅在本地 `docs/qa/` 和 `output/` 保留，不纳入版本控制。模块代码完成/单项测试通过不代表全部V1验收或已经上线；最终证据以本地最新验收记录为准。

仓库保留测试代码及其虚构样例；运行产生的数据库、上传文件、备份、真实账号资料和本地密钥均不得提交。`.env.example` 仅提供配置占位值，真实值放在已忽略的本地环境文件或部署密钥管理中。

私有文件不作为公开静态目录，API/令牌/文件/定位/交付照片不进入离线缓存。普通业务记录不按年龄删除；文件/草稿/导出、临时秘密、备份轮换按Q/R例外清理。管理员主动删除进入24小时垃圾桶，永久清理必须检查未结业务、费用、工资和有效引用。

真实服务器/域名、SMTP服务商、异地备份目标、打印机/纸型和设备定位验收尚需对实际环境配置与验证；仓库中的配置机制和本地测试不构成生产RPO/RTO或实体出纸通过声明。
