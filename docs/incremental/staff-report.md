# 员工账号模块交付

新增 `src/admin/staff-account.tsx`，导出 StaffAccountPage({courier?})、StaffCredentialPage({mode?:'setup'|'forgot'|'reset'|'verify'})、StaffManagementPage。复用现有增长模块通用状态/确认组件和 growth.css，没有修改路由、store、共享身份实现或 API 适配器。

- 个人账号从 useIdentity().staff 读取，支持当前/新密码改密、员工邮箱验证、GET /auth/sessions、单设备/ALL_OTHERS 会话撤销、退出。mustChangePassword=true 时仅改密和退出可操作，设备接口不发起请求。改密后清空密码并刷新员工身份。
- 邮件落地页只纯读取 fragment token，提交前以 layout effect 清除 URL fragment 并保留 history.state；用户明确点击后才 POST 对应员工 setup/reset/verify 接口。凭据不进入 URL 参数、存储或日志；成功后清空内存 token/密码。首次邀请直接验证邮箱并设密，不要求预验证。
- 员工管理使用现有 store.data.staff，普通 POST/PATCH 经 store.act；安全操作走 growthRequest。编辑带 expectedVersion/reason，禁止缺版本的编辑。创建默认 PENDING_SETUP 与强制改密，不收集初始/旧密码。角色/状态/展示名/邮箱可编辑；邮箱留空保持。
- 前端防止自己停用、降级或移入待设置状态，防止最后活跃完整权限管理员被停用/降级；服务端仍须对完整集合和并发请求校验。能力检查 staff.manage 或 *，可选 allowedActions 再裁剪按钮。
- send-setup、revoke-sessions、issue-temporary-password 均有原因确认。临时密码仅来自本次响应，放在当前弹窗状态，关闭或身份代次变化即清除，不写入员工记录或日志。

验证：`npx tsc --noEmit` 通过；通过 TypeScript 编译后直接执行 staffChangeBlocker 的 7 个安全场景断言。customer-account 现有 6 项测试通过。共享 admin-growth 测试在主代理新增契约函数的红灯阶段出现 3 项缺失函数失败，本任务未改这些共享测试/实现。按约束未操作浏览器，未验证真实邮件和真实后端。

集成注意：个人退出依赖 `POST /auth/logout`，当前共享 demo 适配器尚未提供该分支，需要主代理补齐，生产保留真实 API 调用。主路由须接入上述三个组件；mustChangePassword 的其他业务路由拦截由主代理统一实现。Staff Credential mode 切换会清空旧凭据并重新读取新 fragment。
