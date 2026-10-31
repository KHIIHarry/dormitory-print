# 独立任务：客户账号/优惠邀请/订单链接页面
只创建 src/customer/account.tsx（可拆src/customer/account/*）、src/customer/account.css以及本任务tests。不要改现有main/layout/store、现有orders/review、共享growth文件。不要commit。
读取03第1/2/3/4章及0A.1/2/7。复用UI暖棕现有组件，不改整体视觉。实现并导出 CustomerAuthForm({mode:'login'|'register'|'forgot'|'verify'|'reset',embedded?:boolean,onSuccess?:()=>void})、AccountLoginPage/RegisterPage/VerifyPage/ForgotPage/ResetPage（可用包装）、AccountPage、AccountSecurityPage、RewardsPage、InvitePage、OrderLinkPage。
接口依赖主agent会提供：src/growth/client.ts growthRequest<T>(path,{method?,body?}) / useGrowthResource<T>(path)->{data:T|null,loading,error,reload}；useIdentity()提供customer、staff、orderAccess、generation、refreshCustomer、refreshStaff、loginCustomer(email,password)、logoutCustomer、demoCustomer、setStaff、setOrderAccess、invalidate。customer/staff DTO见src/growth/types.ts。useStore有toast/data/setData/refresh；客户操作不改staff role。ADMIN不出现在用户页面。
邮箱页面：密码显示确认、邀请码可选协议；注册收邮件能力缺失真实提示游客回退；verify/reset读取fragment一次后立即history.replaceState清除，只明确按钮POST消费，不自动useEffect消费；更正待验证邮箱需registrationChallengeId，验证码/邮箱待验证不能当正式账号。emailchange Confirm也可由VerifyPage处理mode query。demoCustomer仅DEMO显式按钮；安全动作使用真实APIwrapper(开发adapter主agent实现)，无真邮件成功假象。
账号：资料/密码/邮箱换绑、通知偏好、显式link-guest确认与session轮换说明、已授权单单import提示由OrderLink/详情处理；security会话撤销。退出客户不退出员工，保留原打印流程。
Rewards GET /customer/coupons {items:Coupon[]}，tabs可用预约/使用/失效+冻结独立riskHold，实际门槛/服务/日期/原因。
Invite GET /customer/referrals {items:ReferralRecord[],total,qualified,granted};GET /customer/referrals/code {code,shareText,rules};保护invitee仅masked展示；未配置无奖励承诺。
OrderLink读?orderId=#token=；立即清除fragment并显式POST /order-access/exchange（这里为授权兑换可effect，与邮件消费不同），响应 {order,access:OrderAccess}；setOrderAccess+在store setData仅加入这一单不扩其他单；navigate orders/id。无token但已有该单授权可重开；失效页联系管理员无手机号查私有信息。提供已登录verified用户显式“添加这一单到账号”confirm调用import-order，不自动合并游客。请避免StrictMode双effect重复兑换：使用useRef或全模块inflight map按token去重，token不打印不持久化。
资源请求401/403/429/offline失败可重试，不无限loading，不用alert。仅当前内存registrationChallengeId，不能持久化密码/token。
完成后自查typecheck并写docs/incremental/customer-report.md，简短报告接口依赖和未实机项。
