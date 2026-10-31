# 本机 Docker 运行方案

用户已选择全部服务在本机 Docker 运行，通过 http://localhost:8080 访问。

复用生产 Compose 的 API、两个 Celery worker、beat、PostgreSQL、Redis 和卷初始化服务，新增本地 overlay。使用独立项目名 dormprint-local、独立镜像标签和命名卷，避免复用现有联调数据库。本地环境使用 development 和 HTTP cookie；生产配置继续要求 HTTPS。

前端通过 Node 多阶段镜像编译真实 API 版本，Nginx 镜像直接包含静态文件和代理配置。只发布 127.0.0.1:8080，数据库和 Redis 不发布端口。Nginx 模板写入 tmpfs，兼容只读根文件系统。

启动脚本首次生成仅当前用户可读的随机密钥文件，构建镜像、等待数据服务健康、执行迁移，再启动应用和任务服务。重复启动保留密钥和数据，不自动创建管理员或写入演示数据。管理员使用真实邮箱交互初始化。异地备份作为可选服务，不在本机默认启动。

验证包含实际 Compose 合并检查、构建上下文秘密排除检查、真实镜像构建、容器启动、HTTP 页面/API 健康检查和 Celery 队列检查。SMTP、实体打印机和生产 HTTPS 不属于本机启动验收。
