# 独立任务：新增管理端增长运营页面
请只创建 src/admin/growth.tsx（如超过800行拆 src/admin/growth/*）以及 src/admin/growth.css（在模块内import）；可创建本任务纯逻辑 tests/admin-growth.test.ts。不要改任何其他现有文件、主路由、store、共享growth文件。不要commit。
读取03增量文件第4/5/6章和0A.10、04第8章接口。复用Panel/Field/Button/Modal/Notice/PageHeading/Empty与现有暖棕样式，不能重做UI。实现导出 CustomersPage、CustomerDetailPage、MailSettingsPage、MailTemplatesPage、PromotionSettingsPage、PromotionRecordsPage、VolumeDiscountsPage。
依赖接口：src/growth/types.ts已有完整类型；src/growth/client.ts将提供growthRequest<T>(path,{method?,body?})和useGrowthResource<T>(path)->{data:T|null,loading,error,reload};useStore().toast；ADMIN拼接管理路径。
GET列表使用{items:T[]}；配置GET返回对象直接；具体类型从types.ts导入。写操作使用expectedVersion、原因等契约字段，提交成功后reload。开发mock由主agent统一提供。表单可交互、加载失败空权限离线提交、409显示错误，不假成功。
SMTP：不回显secret，空串不提交secret；显式清除二次确认；保存与连接/测试发信分开；测试状态QUEUED/SMTP_ACCEPTED不说送达。NONE仅allowInsecure时可选。
模板：7种后端默认中文模板列表、编辑subject/text/HTML，插入白名单变量、预览调用后端(纯文本/安全iframe sandbox无脚本)、测试、恢复默认确认。无需前端任意执行HTML或token。
营销：3活动固定金额，金额元转整数分，开关/时段/预算/发行限额/门槛/适用服务，每单1券不叠加只读；新增编辑。记录搜索过滤，freeze/unfreeze/revoke必须原因+version；export调用job接口显示状态与受控下载，不导出敏感内容。
满量：GET/PATCH /admin/config/volume-discounts，默认off空，增删档位，折数支持三位精确转bps，正整数门槛且无重复，高门槛不得更差，生效时间版本描述，影响预览只新报价；同一规则预览可用主agent提供的 validateVolumePolicy, parsePayRate, selectVolume（将在src/growth/pricing.ts）或自己局部实现后通知主agent合并。不要seed营业默认档位。
客户：列表验证/状态/邀请码/注册日期等筛选；详情仅掩码邮箱，订单/优惠/邀请统计；suspend/restore/resend-verification/send-recovery 均权限/原因/确认，无冒充登录、密码读取。
完成后自己typecheck并告知主agent新导出、依赖接口、未验证边界；报告写docs/incremental/admin-report.md，回答简短。
