# 配送时段按校区管理

日期：2026-11-03。代码改动及本地验收记录，不代表已部署或已修改业务库。

## 最终规则

- 每个新时段必须选择一个校区。不同校区可使用相同日期、名称和时间，各自拥有容量及封批状态。
- 只在人工收款或零元结算成功时占容量；拆批、并批和售后配送分配不重复占容量。
- 下单、修订、时段建议、客户接受、结算、改期、自动归批及调度都校验楼栋、时段、组和批次的校区。
- 封批弹窗显示并锁定时段校区，影响预览及提交绑定相同范围；名单或版本改变后须重新预览。
- 空批次创建时就有确定校区。跨校区加单、移单、并批、地址复核或执行不能绕过校验。
- 同校区改楼栋沿用地址复核；跨校区待确认订单通过完整修订重选，已结算订单不能直接跨校区改地址。
- 校区停止新选点不取消已有合法履约。旧批次缺校区时，只允许已有成员唯一证明归属的执行继续，不能据此接新单。
- 历史请求、结算、配送变更及通知快照保留；订单 API 单独返回经过字段白名单处理的 locationSnapshot，供历史楼栋及校区展示。

## 升级与存量预检

新增 Alembic 版本 `e3f5a7b9c011`，在 `delivery_slots` 增加可空的校区外键及索引。新建接口仍强制非空；数据库可空仅用于保留待核对的旧历史。

迁移检查订单、组、批次、分配、待确认建议及配送变更历史中的归属证据。唯一且有效的校区可回填；共享、缺失或已删除校区的证据保留为待核对，不分配到默认校区。原容量、占用及封批事实不被自动拆分或扩大；旧操作预览失效。

在 backend 目录执行已有迁移命令后，通过管理端配送时段页的“存量配送检查”查看 `campusScopeIssues` 等诊断。该界面只读，不自动归批或修改旧记录。

```sh
.venv/bin/alembic upgrade head
.venv/bin/python -m app.cli delivery-campus-preview --slot-id OLD_SLOT_ID
```

预检返回 blockers、各校区已占用数、批次归属以及 manifest（slotId、expectedVersion、fingerprint）。不得通过修改指纹绕过阻断；缺归属、历史证据冲突、批次/组/时段错配或占用账目不一致，均须先核对。

## 经核对后拆分旧共享时段

将预检返回的 **manifest 对象** 保存到本地 JSON，补充 reason 和 capacities。例如仅在已核实原总容量为 6、两个校区各已占 1 单时，分配 4 和 2：

```json
{
  "slotId": "预检返回的旧时段ID",
  "expectedVersion": 3,
  "fingerprint": "预检返回的指纹",
  "reason": "核对订单、配送组与剩余运力后按校区拆分",
  "capacities": {
    "预检中的东校区ID": 4,
    "预检中的西校区ID": 2
  }
}
```

示例数字不构成实际营业配置，须使用预检的真实版本、指纹和校区 ID。每个目标容量必须为正整数，且不小于对应已占用数；容量之和必须等于原总容量。无引用的旧空时段须由操作者明确指定唯一校区，不能自动猜测。

```sh
.venv/bin/python -m app.cli delivery-campus-apply --manifest /absolute/path/reviewed-manifest.json --admin-id ACTIVE_DELIVERY_ADMIN_ID
```

该操作核验当前有效管理员及 `delivery.manage` 能力，以真实事务锁重新核对预检指纹，创建校区时段并迁移引用。保留分配 ID、收款和退款事实、封批状态及历史快照，不重新收费、不自动恢复垃圾桶成员、不自动替客户接受建议、不发送迁移通知。原共享时段关闭并归档，保留迁移映射与审计。当前有效安排使用映射后的校区身份，历史申请和结算仍显示原快照。

正常改期留下的失效分配只作为历史证据；不会强迫订单从已确认的新时段迁回旧时段。仍有效的分配必须与当前确认时段匹配。

迁移前按部署手册完成备份，迁移后核对容量总额、有效分配、订单状态及账目。已经创建新校区业务后，回退应使用已核实的备份恢复流程，不能靠删除 campus_id 丢失归属。

## 验收记录

本轮已完成实现与复核。原始测试日志保存在本机 `/tmp/campus-delivery-work/`，截图在已忽略的 `docs/qa/`；不提交真实账号、订单或凭据。

| 实际验证 | 结果 |
| --- | --- |
| 全部 57 个后端测试文件，按进程隔离 PostgreSQL schema 分 3 批并行执行 | 279 + 280 + 273 = **832 通过**，2 个需显式启用的真实浏览器测试默认跳过 |
| `DORMPRINT_RUN_BROWSER_TESTS=1 NO_PROXY=127.0.0.1,localhost,::1 .venv/bin/pytest -q tests/test_real_checkout_browser.py tests/test_real_browser.py --tb=short` | 上述 2 个真实浏览器测试单独启用后 **2 通过** |
| `npm test` | **360 通过** |
| `npm run test:e2e -- tests/e2e/delivery-campus-admin.spec.ts tests/e2e/delivery-campus-customer.spec.ts tests/e2e/delivery-ui-refresh.spec.ts` | 桌面、手机共 **14 通过** |
| `npm run contract:generate` | 从真实 FastAPI 生成 OpenAPI 和 TypeScript，成功 |
| `npm run build` | TypeScript 与生产构建成功 |
| 最终调整迁移组锁顺序后的迁移工具及旧 schema 回归 | **13 通过** |
| `git diff --check` | 通过 |

后端全量执行命令为 `python3 /tmp/campus-delivery-work/run_backend_shards.py`；三个文件清单及日志在同目录 `backend-final-shard-{1,2,3}.args/.log`。单进程复现可在 backend 运行 `NO_PROXY=127.0.0.1,localhost,::1 .venv/bin/pytest -q --tb=short`。本机配置了网络代理，测试进程明确将回环地址加入 NO_PROXY，保证临时本地 HTTP 服务直接连接。

真实浏览器验收没有拦截业务 API：实际上传 PDF、执行文件作业并核对最终文件 hash，完成校区/楼栋/时段选择及下单，再验证收款、自动归批、客户确认改期、生产装袋与配送；另有真实账号授权和撤权验收。检查了下单页与手机配送端的实际截图。

新增回归覆盖不同校区同窗口独立容量、封批预览范围、空批次第一单、跨校区写操作拒绝、退款/改期来源校验、历史快照、旧 JSON 引用、失效分配、回收站及容量守恒。真实并发测试确认：东校区行锁不阻塞西校区结算；封批先提交时，等待中的结算保留唯一待核对实收且不占名额、不创建分配。

复核发现的来源占用异常、批次/组归属漂移、遗漏历史快照证据、已删除校区历史、正常改期失效分配被误拦截、迁移后有效安排身份、HTTP 历史楼栋快照遗漏均已补回归并修复。最终复核无遗留问题。

现有 Starlette/httpx、SWIG 及 Rollup 提示仍存在，不构成测试失败。本轮未提交、未部署，未执行实际业务库的升级或存量改写；实际旧共享时段仍须按上面的预检、容量分配与迁移流程处理。
