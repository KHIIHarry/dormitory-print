# 配置数字输入清空与重新输入 —— 交付报告

任务：所有配置页面的数字输入允许用退格删空并正常重新输入，不能在空输入时立刻强制回默认数字；允许编辑中的空值和小数中间态；提交时明确校验范围与必填；保留单位、数值精度、既有业务规则和 UI 风格。范围仅限数字表单前端与测试，不修改账号、后端，不部署、不提交、不 reset。

## 结论

已完成。配送时段容量、SMTP 端口/超时、运营配置的服务限额/留白/照片尺寸/价格/工作流参数，以及校区地图排序与热点坐标，全部改为共享 `NumberInput`：编辑期间保留原始草稿，清空显示空值，不在 `onChange` 里 `Math.max`/`|| 默认值`；失焦或提交时再按各字段的后端范围校验。价格仍以“分”存储、以“元”展示，提交时严格换算为整数分。

## 变更文件

| 文件 | 变更 |
| --- | --- |
| `src/components/number-input.tsx`（新增） | 受控数字输入：聚焦时保存字符串草稿，空串/非法值向父组件发 `null`，不强行回填；支持 `scale`（价格元↔分）；失焦后与外部数值同步。 |
| `src/components/ui.tsx` | `Field` 识别 `NumberInput` 子组件，继续为 label、`id`、`aria-invalid`、`aria-describedby`、hint/error 建立关联。 |
| `src/admin/logistics.tsx` | 删除 `edit.capacity || 30` 的显示与保存回退；容量改用 `NumberInput`；保存时校验整数 1–100000，错误 toast「预约容量必须是 1 到 100000 之间的整数」；新建时段仍预填 30。 |
| `src/admin/growth/logic.ts` | 新增 `MailDraft`（`port`/`timeoutSeconds` 允许 `null`）和 `mailInteger`；`buildMailPatch` 在保留原有原因、安全策略、清空密钥逻辑的前提下，校验端口 1–65535、超时 1–120。 |
| `src/admin/growth/mail.tsx` | 邮件配置草稿改用 `MailDraft`；端口/超时改用 `NumberInput`，可清空后重新输入。 |
| `src/admin/operations.tsx` | 价格改 `NumberInput scale={100}`，删除 `priceValue` 与“每次按键都重算并取整”的旧逻辑；服务限额、绑定上限、留白、照片尺寸、工作流参数全部改 `NumberInput`；编辑期不再 clamp，提交统一走 `configSectionPayload`。 |
| `src/admin/campus-map.tsx` | 排序、新建排序、热点 X/Y 改 `NumberInput`；排序/坐标的 0 仍是合法值，清空编辑态不闪回默认值。 |
| `src/v1/admin-api.ts` | `configSectionPayload` 增加服务限额、绑定上限、照片尺寸/留白、工作流参数的范围与整数/小数校验；价格仍允许空值 `null`，保留原有价目语义。 |
| `tests/config-numeric-drafts.test.ts`（新增） | 7 个纯逻辑用例，覆盖价格空值、服务参数空/小数/越界、照片小数与越界、工作流必填/可选空值、邮件端口空/0/小数与重新输入。 |
| `tests/e2e/numeric-drafts.spec.ts`（新增） | 4 个真实浏览器交互用例，覆盖配送容量、服务限额+留白小数、价格元/分精度、SMTP 端口清空重输；桌面与移动视口均通过。 |

未改后端、账号页面、权限逻辑或其他 worker 文件。工作区中 `backend/app/auth.py`、`src/admin/staff-account.tsx`、`src/customer/account.tsx`、`docs/openapi.yaml` 等属于其他 worker 的改动，保持原样。

## 实现要点

- **空值不是 0**：`NumberInput` 在输入框为空时向表单状态写 `null`，而不是 `Number("") === 0`；必填字段由提交校验报错，可选字段保持未配置。
- **小数中间态**：价格、留白、照片尺寸、热点坐标使用十进制 `NumberInput`，输入过程中不四舍五入；价格只在提交 payload 中由元换算为整数分。
- **范围仍由业务规则决定**：服务限额 1–100/1–200/1–2000/1–10000/1–1000/1–100000/1–100000000/1–2000；照片宽高 0.1–500mm、留白 0–100mm；工作流 5–10080/10–3600/1–1000/5–12；绑定上限 ≥1；配送容量 1–100000；SMTP 端口 1–65535、超时 1–120。与后端 Pydantic 约束一致。
- **不改变价格/限额语义**：价格仍以整数分提交，空价目仍为 `null`；服务开关、格式、装订限制、暂停项、保留策略均未改。价格 e2e 明确断言 30 分显示 `0.3`，输入 `0.35` 后提交 `bwDuplex: 35`。
- **保留 UI 风格**：继续复用 `Field`、`form-grid`、`Notice`、错误 toast 和现有中文标签；`NumberInput` 仍渲染 `type="number"`，保留浏览器数字键盘/步进外观。

## 实际执行的检查

| 命令 | 结果 |
| --- | --- |
| `npm test` | **58 个测试文件 / 330 个测试全部通过**（含新增 7 个） |
| `npx tsc -b` | 通过（无输出） |
| `npm run build` | 通过，`✓ built in 2.31s` |
| `npx playwright test tests/e2e/numeric-drafts.spec.ts --project=desktop` | **4 passed** |
| `npx playwright test tests/e2e/numeric-drafts.spec.ts --project=mobile` | **4 passed** |
| `git diff --check`（本任务文件） | 通过，无空白错误 |

新增 e2e 覆盖的具体交互：

1. 新建配送时段：容量显示 30 → 清空显示空 → 输入 45；再清空后保存，出现范围错误 toast，不静默保存 30。
2. 服务与限制：`单订单最多文件数` 10 → 空 → 25；打印校准上留白 3 → 空 → 3.5，均保持编辑态。
3. 价格：`A4 黑白双面` 30 分显示 0.3 → 清空 → 输入 0.35 → 预览并保存；拦截 PATCH 断言 `bwDuplex: 35`、空价目仍为 `null`、`expectedVersion` 正确。
4. SMTP：端口 465 → 清空显示空 → 输入 587，不闪回 0。

### 已确认与本任务无关的既有 e2e 失败

`tests/e2e/mail-draft.spec.ts`（`playwright.settings.config.ts` / `smtp-desktop`）有 3 个用例失败，均卡在查找文本 `SMTP 配置 · 版本 1/2`；当前 `src/admin/growth/mail.tsx` 及 HEAD 版本都没有渲染该文本。已在 HEAD `6ab8285` 的干净 detached worktree 中复跑其中一个用例，得到相同失败，说明是既有测试/UI 文案漂移，不是本次数字输入改动引入。未在本次范围内修改该版本标签。

## 风险与边界

- `NumberInput` 仍是 `type="number"`：Chrome 中输入 `1.` 时 DOM 可能暂不显示末尾小数点，但继续输入 `5` 会得到 `1.5`；清空、整数、普通小数和提交流程均已通过 e2e。若要求所有浏览器都逐字符显示 `1.`，需要改为 `type="text" inputMode="decimal"`，会失去原生步进箭头，属于 UI 取舍，本次未改。
- 服务限额等必填字段在编辑中允许为空，错误只在保存/预览时统一显示；这是“允许编辑中空值”的预期行为。
- 前端范围常量与后端 Pydantic 目前一致；若后端后续调整范围，需要同步 `SERVICE_LIMIT_RULES`/`mailInteger`/配送容量提示。
- 校区地图排序/坐标清空后父状态会落到 0（这些字段 0 是合法业务值）；输入框在聚焦编辑期间仍显示空，不会在按键过程中闪回 0。
- 其他 worker 的工作区改动未触碰；未执行 stage/commit/reset/deploy，`dist/` 构建产物也未纳入本次交付。

## 验收建议

1. 在 `/manage/slots` 新建时段，清空容量后直接保存，确认提示范围错误；重新输入 45 后可保存。
2. 在 `/manage/settings` 服务与限制、打印校准中清空整数/留白并重新输入，确认不闪回默认值。
3. 在价目页清空价格后保存，确认价目为空而不是 0；输入 0.35 保存后请求体为 35 分。
4. 在 `/manage/settings/mail` 清空端口/超时，确认输入框保持空，重新输入 587/10 后可保存。
