# DormPrint 动效逻辑链 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Independent, explicitly disjoint modules may use dispatching-parallel-agents; the controller owns integration and review.

**Goal:** 在保持现有 UI/UX 的前提下，完成三端加载、过渡、任务进度与结果状态链。

**Architecture:** 共享动效变量、繁忙按钮、骨架、资源状态和窗口生命周期；业务任务保持独立状态、真实进度及身份/版本校验。公共资源与私有业务资源分开加载，不以全局 loading 阻断公共页面。用户已确认全部 52 项演示，演示排版、样式、文案和 UI 不作为界面改造依据。

**Tech Stack:** React 19、TypeScript、Vite、CSS、原生 XMLHttpRequest / AbortController、Vitest、Playwright。不得引入大型动效依赖。

## Global Constraints

- 保留现有页面布局、品牌、文字、组件形态、路由、权限、业务规则与交互路径；只加入动画逻辑链及必要的任务状态/异常说明。
- 普通弹窗窗口 320ms ease-in-out、遮罩 300ms linear 淡入；窗口与遮罩均 180ms ease-out 淡出。上传入口保持原文案、宽高、明暗；350ms 后才显示辅助提示。其他短操作约 200ms 后显示加载圈；即时防重复。
- 保留所有已有未提交修改，不重置、不覆盖，不部署、不修改生产配置，不增加文件或个人数据持久化。
- 一任务一个主要等待指示；进度必须来自真实完成量。未知总量使用不确定反馈；传输 100% 后仍要确认服务端响应。人工履约与用户选择均停止循环动画。
- 退出/切换身份/权限失效必须立即移除旧私有内容；旧请求、渲染和上传回调不得回写。
- 提交超时或断网显示结果待核实；沿用幂等键核实，不将已受理显示为最终完成。
- 每个模块先补充有意义的失败测试再实现；简单时长和样式不写复述测试。保留现有 CSS 几何尺寸。

## Shared Interfaces (controller owns)

`src/components/ui.tsx` keeps all existing exports and adds `Button` optional `busy?: boolean; busyLabel?: string`. Existing children remain the default text. `Loading` accepts `{label?: string}`. `src/components/feedback.tsx` exports `Progress({value?:number,label:string,ring?:boolean})`, `ResourceSkeleton({variant?:'cards'|'table'|'detail'|'timeline'|'stats'|'profile'|'coupons'|'paper'})`, `RefreshStatus({loading:boolean,error?:string,label?:string,retry?:()=>unknown})` and `TaskStatus({label:string,long?:boolean})`.

`src/lib/api.ts` extends `request` options with `onUploadProgress?: (progress:{loaded:number;total?:number})=>void; onUploadComplete?:()=>void`, retaining credentials, CSRF, idempotency, response decoding, abort and identity invalidation. It exports `isUncertainSubmission(error:unknown):boolean` for network/server-unknown business outcomes, without marking explicit 4xx as uncertain.

`useStore()` adds `pendingActions:Record<string,boolean>; actionIssues:Record<string,string>` and resource scope state. `act()` locks the path synchronously and keeps persistent issues when outcomes require verification. Existing boolean result remains.

`useGrowthResource<T>` retains `{data,loading,error,reload}` and adds `refreshing`, `hasData`, `stale` identity/query-safe semantics. `ResourceState` accepts optional `hasData`, `variant`, `refreshing`; no page redesign.

## Task 1 — Shared feedback and state infrastructure (controller)

Files: `src/components/ui.tsx`, new `feedback.tsx`, motion CSS/module, `src/components/layouts.tsx`, `src/main.tsx`, `src/lib/api.ts`, `src/lib/store.tsx`, `src/growth/client.ts`, customer account/home/help/orders loading branches.

- [ ] Write failing behavior tests for request sequencing, progress normalization/unknown mode, action ambiguity, independent resource loads and modal rapid reopen cleanup.
- [ ] Run focused Vitest; confirm failures describe missing behavior.
- [ ] Implement shared presence/reduced-motion hooks, modal stack/focus/scroll cleanup, stable busy buttons, scoped skeletons and progress semantics.
- [ ] Split initial/refresh/empty/error handling; preserve current authorized content and inputs; immediately invalidate private identities and suppress stale request generations.
- [ ] Wire existing page categories to scoped feedback, preserve route shell and safe focus/scroll restoration. Public content remains readable when unrelated orders fail.
- [ ] Run focused tests and review baseline diff for geometry/copy changes.

## Task 2 — Upload state chain (upload worker)

Files: `src/customer/upload.tsx`, `src/lib/types.ts`, new upload-specific helpers/tests. Do not edit shared API/UI/CSS.

- [ ] Tests: legal items queued immediately; unknown progress; sent bytes vs ready; stage-specific retry with no assetId; deletion/retry/identity/unmount invalidates callbacks; single in-flight polling and visibility recovery.
- [ ] Map file states with additive phase/version metadata while preserving existing PrintFile.status business compatibility.
- [ ] Integrate request progress/abort callbacks. Keep local demo explicit and never simulate network percentages.
- [ ] Keep image paper selection/A4 conversion rules; images/PDF use proper names; failures retain originals; multiple failures produce one summary.
- [ ] Consume shared Progress/TaskStatus in existing row positions, retain upload button and current layout.
- [ ] Run tests, document actual API gaps in module report.

## Task 3 — Final PDF and preview (preview worker)

Files: `src/customer/review.tsx`, `src/components/doc-preview.tsx`, `src/lib/pdf.ts`, related tests; may add focused helper. Do not edit settings/UI/CSS/API/store.

- [ ] Tests first for stale render completion, cancellation, displayed vs target page, independent final/quote pending, submission uncertainty and identity/version invalidation.
- [ ] Render to private offscreen canvases then commit only the current render; abort PDF.js tasks on change/close. Keep current paper geometry and toolbar.
- [ ] Keep final generation, page rendering, pricing and submit independent. No fake zero price; no success until confirmed; no relaxed PDF/amount guards.
- [ ] Preserve forms/checklists/acknowledgments according to existing checkout fingerprints; protect immediate submit locking.
- [ ] Use shared feedback in existing areas; run focused tests and report true backend gaps.

## Task 4 — Management and courier operations (operations worker)

Files: `src/admin` pages and growth/common, excluding `resources.ts`, navigation and CSS; `src/courier/pages.tsx`, relevant tests. Do not edit shared UI/store/API/growth client or customer files.

- [ ] Tests for immediate action locks, uncertain results, export accepted vs ready, locating outcomes, photo stale callbacks and failure retention.
- [ ] Wire existing busy state and store action state to buttons; replace generic initial waits with category-specific skeletons, preserve refresh content.
- [ ] Keep SMTP connect/send/save separate; export only actual states; print attempts remain manual confirmation; codes/amount/pages remain immediately readable.
- [ ] Courier geolocation is device-only, guarded against duplicate callbacks; local photo read/real upload progress independent of position and submit.
- [ ] Preserve original layout/copy and error paths; use shared feedback without introducing demo UI.
- [ ] Run focused tests; append page coverage and backend limitations to report.

## Task 5 — Integration and acceptance (controller + independent reviewer)

- [ ] Review each task for scope compliance and correctness against `/tmp/dormprint-motion-baseline-20261030` (Git baseline already contains unrelated modifications).
- [ ] Run `npm test`, `npm run build`, `npm run build:demo`, `git diff --check` and mechanical detector on changed UI files.
- [ ] Desktop/mobile browser checks: existing workflows, slow/fast requests, initial/refresh failure, mixed upload, PDF racing, submit unknown, rapid modal reopen, focus/scroll, reduced motion and offline recovery.
- [ ] Run broad independent review and fix material findings, then only rerun affected verification.
- [ ] Deliver `docs/MOTION.md`, three-end route/interaction coverage, QA evidence and real backend gap list. Distinguish implementation verified in demo/mocks from real backend integration.
