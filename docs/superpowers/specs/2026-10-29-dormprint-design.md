# 宿舍打印前端实施设计
需求权威：用户本次开发请求与 01-前端开发Prompt.md。文档中的“仅编写 Prompt”是来源背景，不覆盖本次实际开发授权。

## 范围与边界
先完成前端全部页面及可交互演示，不开发后端，不上线生产。开发演示使用内存数据，刷新重置；生产仅调用 /api/v1，失败显示真实错误。文件与联系方式不写浏览器持久存储。真实收款、转换、不可变最终 PDF、受权存储及后端权限需接口联调。

## 路由
用户：/u/、/u/new、/u/new/settings、/u/new/photo/:id、/u/review、/u/orders、/u/orders/:id、/u/orders/:id/contact、/u/help、/u/profile。
管理：/manage/login、/manage/、/manage/orders、/manage/orders/:id、/manage/print、/manage/print/:id、/manage/binding、/manage/packing、/manage/batches、/manage/batches/:id、/manage/slots、/manage/map、/manage/settings、/manage/staff、/manage/exceptions、/manage/trash、/manage/audit。
配送：/courier/login、/courier/、/courier/batches/:id、/courier/orders/:id、/courier/orders/:id/proof、/courier/orders/:id/exception、/courier/profile。
实际管理根路径由 VITE_ADMIN_BASE_PATH 构建设置，用户界面不含该入口。

## 硬约束
A4 单页单面/双面按张计价，双面奇数页每份补背面；分段独立。每文件独立装订。用户不定位、不输入房号。微信线下付款；浏览器打印不证明出纸。装袋与定位、照片条件不可绕过。离线禁写。

## 未决清单
D-01 付款后内容替换、D-02 指定纸张补打、D-03 正常资产保留、D-04 装订收费/不同份数策略、D-05 已付款删除/退款、D-06 跨设备验证、D-07 连续批量打印、D-08 生产技术/服务器/存储，全部 UNRESOLVED。显示配置状态、关闭相关危险功能。技术参考栈仅用于本次本地前端实现。

## 接口与测试
OpenAPI 3.x 描述已实现调用，类型脚本生成；其余完整接口依赖保留在原始需求目录。重要写操作携带 Idempotency-Key 与 expectedVersion。测试页码分配、逐任务纸张/页数、生产阻断、交付权限；浏览器检验上传至交付的演示流程与各角色页面、360/390/768/1440 布局。
