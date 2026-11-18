import { expect, test, type Page } from "@playwright/test";

async function mount(
  page: Page,
  options: {
    status?: string;
    seconds?: number;
    registered?: boolean;
    original?: boolean;
    path?: string;
    wechat?: string;
    owned?: boolean;
    access?: boolean;
    strict?: boolean;
    delayedToken?: string;
  } = {},
) {
  const main = await (await page.request.get("/src/main.tsx")).text();
  const react = main.match(/from "([^"]*\/react\.js\?[^"]*)"/)![1];
  const dom = main.match(/from "([^"]*\/react-dom_client\.js\?[^"]*)"/)![1];
  const source = await (
    await page.request.get("/src/customer/orders.tsx")
  ).text();
  const router = source.match(
    /from "([^"]*\/react-router-dom\.js\?[^"]*)"/,
  )![1];
  await page.route("**/src/lib/store.tsx*", (route) =>
    route.fulfill({
      contentType: "text/javascript",
      body: `export const DEMO=false,ADMIN='/manage';export const useStore=()=>window.guestFixture.store;`,
    }),
  );
  await page.route("**/src/growth/identity.tsx*", (route) =>
    route.fulfill({
      contentType: "text/javascript",
      body: `export const useIdentity=()=>window.guestFixture.identity;`,
    }),
  );
  await page.route("**/src/growth/client.ts*", (route) =>
    route.fulfill({
      contentType: "text/javascript",
      body: `export const getIdentityGeneration=()=>0,advanceIdentity=()=>0;
 export const useGrowthResource=()=>({data:null,loading:false,reload:async()=>{}});
 export const growthRequest=async(path,options={})=>{
 const f=window.guestFixture;f.calls.push({path,body:options.body});
 if(path==='/order-access/exchange') {if(options.body.accessToken===f.delayedToken){await new Promise(resolve=>{f.releasePrevious=resolve;});throw Object.assign(new Error('旧链接已停止访问'),{code:'ORDER_GRANT_REVOKED'});}if(f.status!=='ACTIVE')throw Object.assign(new Error('访问已结束'),{code:f.status==='EXPIRED'?'ORDER_GRANT_EXPIRED':'ORDER_GRANT_REVOKED'});return {order:f.order,access:{orderId:'o1',sourceGrantId:'root',scopes:['ORDER_VIEW','FINAL_PDF_PREVIEW','DELIVERY_PROOF_VIEW'],allowedActions:['ORDER_VIEW'],expiresAt:f.expiresAt}};}
 if(path==='/orders/o1'){if(f.order.ownerAccountId)return f.order;throw Object.assign(new Error('不可访问'),{status:404});}
 if(path==='/customer/auth/verify-email'){f.verified=true;return {success:true};}
 if(path.endsWith('/guest-access')) {if(f.offline)throw new Error('当前离线，请联网后重试');return {status:f.status,serverNow:new Date().toISOString(),expiresAt:f.expiresAt,canClaimOnDevice:f.original};}
 if(path.endsWith('/claim')) {f.order={...f.order,ownerAccountId:'account',accessKind:'OWNER'};f.store.setData(s=>({...s,orders:[f.order]}));return {orderId:'o1',ownerAccountId:'account'};}
 throw new Error(path);
 };`,
    }),
  );
  await page.route("**/guest-order-fixture*", (route) =>
    route.fulfill({
      contentType: "text/html",
      body: `<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><div id="root"></div>
 <script type="module">import R from '/@react-refresh';R.injectIntoGlobalHook(window);window.$RefreshReg$=()=>{};window.$RefreshSig$=()=>type=>type;window.__vite_plugin_react_preamble_installed__=true;</script>
 <script type="module">
 import React from '${react}';import ReactDOM from '${dom}';import {MemoryRouter,Routes,Route,useLocation,Link} from '${router}';
 import {GuestOrderBoundary} from '/src/customer/guest-order-access.tsx';import {OrderLinkPage,CustomerAuthForm} from '/src/customer/account.tsx';import {Help} from '/src/customer/help.tsx';import {Modal} from '/src/components/ui.tsx';import {createDemo} from '/src/lib/demo.ts';import '/src/styles.css';
 const registered=${options.registered ?? false};const data=createDemo();data.config.wechat=${JSON.stringify(options.wechat ?? "dormprint")};
 const order={...data.orders[0],id:'o1',pickupCode:'ABC12',ownerAccountId:${JSON.stringify(options.owned ? "account" : null)},accessKind:${JSON.stringify(options.owned ? "OWNER" : "GUEST_OWNER")}};data.orders=[order];
 const f=window.guestFixture={calls:[],delayedToken:${JSON.stringify(options.delayedToken || "")},status:${JSON.stringify(options.status || "ACTIVE")},expiresAt:new Date(Date.now()+${options.seconds ?? 120000}*1000).toISOString(),original:${options.original ?? true},order};
 function App(){const [state,setData]=React.useState(data);const [customer,setCustomer]=React.useState({authKind:registered?'REGISTERED':'ANONYMOUS',capabilities:[],account:registered?{id:'account',verified:true}:undefined});const [access,setOrderAccess]=React.useState(${options.access ? "{orderId:'o1',sourceGrantId:'root',scopes:['ORDER_VIEW'],allowedActions:['ORDER_VIEW'],expiresAt:f.expiresAt}" : "null"});const [saved,setSaved]=React.useState({o1:'https://print.invalid/u/order-link?orderId=o1#token='+ 's'.repeat(64),o2:'other-order-credential'});
 f.store={data:state,setData,resources:{config:{hasData:true}},savedAccessLinks:saved,setSavedAccessLinks:setSaved,refresh:async()=>{},toast:()=>{},setDraft:()=>{},setEditingOrderId:()=>{},setCheckoutMemory:()=>{}};
 f.identity={generation:0,customer,staff:null,orderAccess:access,setOrderAccess,invalidate:()=>{},refreshCustomer:async()=>{if(f.verified){const verified={authKind:'REGISTERED',capabilities:[],account:{id:'account',verified:true}};setCustomer(verified);return verified;}return customer;},loginCustomer:async()=>{setCustomer({authKind:'REGISTERED',capabilities:[],account:{id:'account',verified:true}});return false;}};
 React.useEffect(()=>{const end=e=>{f.ended=e.detail;setData(current=>({...current,orders:current.orders.filter(o=>o.id!==e.detail.orderId)}));};window.addEventListener('dormprint:order-access-ended',end);return()=>window.removeEventListener('dormprint:order-access-ended',end);},[]);
 return React.createElement(Routes,null,React.createElement(Route,{path:'/u/orders/:id',element:React.createElement(GuestOrderBoundary,{orderId:'o1',navigation:React.createElement(Link,{className:'back-link',to:'/u/orders'},'返回我的订单')},React.createElement(Private))}),React.createElement(Route,{path:'/u/order-link',element:React.createElement(OrderLinkPage)}),React.createElement(Route,{path:'/u/account/login',element:React.createElement(CustomerAuthForm,{mode:'login'})}),React.createElement(Route,{path:'/u/account/register',element:React.createElement(CustomerAuthForm,{mode:'register'})}),React.createElement(Route,{path:'/u/account/verify',element:React.createElement(CustomerAuthForm,{mode:'verify'})}),React.createElement(Route,{path:'/u/help',element:React.createElement(Help)}));}
 function Private(){f.privateMounted=true;const[preview,setPreview]=React.useState(false);return React.createElement('main',{className:'content-page'},React.createElement('h1',null,'订单 ABC12'),React.createElement('button',{className:'btn',onClick:()=>setPreview(true)},'查看私有 PDF'),preview&&React.createElement(Modal,{title:'最终 PDF',onClose:()=>setPreview(false)},React.createElement('p',null,'私有文件内容')));}
 ReactDOM.createRoot(document.getElementById('root')).render(React.createElement(${options.strict ? "React.StrictMode" : "React.Fragment"},null,React.createElement(MemoryRouter,{initialEntries:[${JSON.stringify(options.path || "/u/orders/o1")}]},React.createElement(App))));
 </script></html>`,
    }),
  );
  const incoming = options.path?.startsWith("/u/order-link")
    ? options.path.slice("/u/order-link".length)
    : options.path?.startsWith("/u/account/verify")
      ? options.path.slice("/u/account/verify".length)
      : "";
  await page.goto("/guest-order-fixture" + incoming);
}

test("server deadline ends an offline open page and closes its private preview", async ({
  page,
}) => {
  await mount(page, { seconds: 2 });
  await expect(page.getByText("游客订单链接默认有效 14 天")).toBeVisible();
  await expect(page.getByText("不足 1 小时")).toBeVisible();
  await page.getByRole("button", { name: "查看私有 PDF" }).click();
  await expect(page.getByText("私有文件内容")).toBeVisible();
  await page.evaluate(() => {
    (window as any).guestFixture.offline = true;
  });
  await expect(
    page.getByRole("heading", { name: "游客订单链接已到期" }),
  ).toBeVisible({ timeout: 6000 });
  await expect(page.getByText("私有文件内容")).toHaveCount(0);
  expect(await page.evaluate(() => (window as any).guestFixture.ended)).toEqual(
    { orderId: "o1" },
  );
  expect(
    await page.evaluate(() => (window as any).guestFixture.store.data.orders),
  ).toEqual([]);
});

test("active guest notice follows navigation with an inline account link at all supported widths", async ({
  page,
}) => {
  await mount(page, { seconds: (11 * 24 + 7) * 3600 });
  await expect(
    page.getByText("游客订单链接默认有效 14 天", { exact: true }),
  ).toBeVisible();
  for (const width of [320, 375, 390, 430, 768, 1440]) {
    await page.setViewportSize({ width, height: 900 });
    const navigation = page.getByRole("link", {
      name: "返回我的订单",
      exact: true,
    });
    const accountLink = page.getByRole("link", {
      name: "注册或登录，保存到我的订单",
      exact: true,
    });
    await expect(navigation).toBeVisible();
    const navBounds = (await navigation.boundingBox())!;
    const bannerBounds = (await page
      .locator(".guest-access-banner")
      .boundingBox())!;
    expect(navBounds.y + navBounds.height).toBeLessThanOrEqual(bannerBounds.y);
    const inline = await accountLink.evaluate((link) => {
      const style = getComputedStyle(link),
        parent = link.parentElement!;
      return {
        tag: parent.tagName,
        size: style.fontSize,
        parentSize: getComputedStyle(parent).fontSize,
        decoration: style.textDecorationLine,
        color: style.color,
        parentColor: getComputedStyle(parent).color,
        button: link.classList.contains("btn"),
        height: link.getBoundingClientRect().height,
      };
    });
    expect(inline.tag).toBe("P");
    expect(inline.size).toBe(inline.parentSize);
    expect(inline.color).not.toBe(inline.parentColor);
    expect(inline.decoration).toContain("underline");
    expect(inline.button).toBe(false);
    expect(inline.height).toBeGreaterThanOrEqual(44);
    const metrics = await page.evaluate(() => {
      const banner = document
        .querySelector(".guest-access-banner")!
        .getBoundingClientRect();
      const main = document.querySelector("main.content-page")!;
      const bounds = main.getBoundingClientRect();
      const style = getComputedStyle(main);
      return {
        left: banner.left,
        right: banner.right,
        contentLeft: bounds.left + parseFloat(style.paddingLeft),
        contentRight: bounds.right - parseFloat(style.paddingRight),
        scroll: document.documentElement.scrollWidth,
      };
    });
    expect(Math.abs(metrics.left - metrics.contentLeft)).toBeLessThanOrEqual(1);
    expect(Math.abs(metrics.right - metrics.contentRight)).toBeLessThanOrEqual(
      1,
    );
    expect(metrics.scroll).toBeLessThanOrEqual(width + 1);
  }
});

test("closing or resetting a link while visible stops access on resume", async ({
  page,
}) => {
  await mount(page);
  await expect(page.getByRole("heading", { name: "订单 ABC12" })).toBeVisible();
  await page.evaluate(() => {
    (window as any).guestFixture.status = "CLOSED";
    window.dispatchEvent(new Event("online"));
  });
  await expect(
    page.getByRole("heading", { name: "游客订单链接已停止访问" }),
  ).toBeVisible();
  await expect(page.getByRole("button", { name: "查看私有 PDF" })).toHaveCount(
    0,
  );
});

test("expiry screen matches the mobile layout and contact action focuses the help card", async ({
  page,
}, info) => {
  await mount(page, { status: "EXPIRED" });
  await expect(
    page.getByRole("heading", { name: "游客订单链接已到期" }),
  ).toBeVisible();
  for (const width of [320, 375, 390, 430, 768, 1440]) {
    await page.setViewportSize({ width, height: 1000 });
    const metrics = await page.evaluate(() => ({
      width: innerWidth,
      scroll: document.documentElement.scrollWidth,
      controls: [
        ...document.querySelectorAll(".guest-status-actions .btn"),
      ].map((el) => {
        const rect = el.getBoundingClientRect(),
          parent = el.closest(".guest-status-card")!,
          r = parent.getBoundingClientRect(),
          css = getComputedStyle(parent);
        return {
          left: rect.left,
          right: rect.right,
          height: rect.height,
          min: r.left + parseFloat(css.paddingLeft),
          max: r.right - parseFloat(css.paddingRight),
        };
      }),
    }));
    expect(metrics.scroll).toBeLessThanOrEqual(width + 1);
    for (const c of metrics.controls) {
      expect(c.height).toBeGreaterThanOrEqual(44);
      expect(c.left).toBeGreaterThanOrEqual(c.min - 1);
      expect(c.right).toBeLessThanOrEqual(c.max + 1);
    }
    if ([390, 1440].includes(width))
      await page.screenshot({
        path: info.outputPath("guest-expired-" + width + ".png"),
        fullPage: true,
      });
  }
  await page.getByRole("link", { name: "联系管理员" }).click();
  await expect(page.locator("#contact-admin")).toBeFocused();
  await expect(
    page.getByRole("button", { name: "复制管理员微信号" }),
  ).toBeVisible();
});

test("help anchor preserves the missing contact configuration message", async ({
  page,
}) => {
  await mount(page, { status: "EXPIRED", wechat: "" });
  await page.getByRole("link", { name: "联系管理员" }).click();
  await expect(page.locator("#contact-admin")).toBeFocused();
  await expect(
    page.getByText("管理员联系方式尚未配置，请通过校园服务渠道联系。"),
  ).toBeVisible();
});

test("login returns to a safe order path and requires an explicit claim on the original device", async ({
  page,
}) => {
  await mount(page, { access: true });
  await page.getByRole("link", { name: "注册或登录，保存到我的订单" }).click();
  await page.getByLabel("邮箱", { exact: true }).fill("student@example.com");
  await page.getByLabel("密码", { exact: true }).fill("Password123!");
  await page.getByRole("button", { name: "登录", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "确认关联到我的订单" }),
  ).toBeVisible();
  expect(
    await page.evaluate(() =>
      (window as any).guestFixture.calls.filter((c) =>
        c.path.endsWith("/claim"),
      ),
    ),
  ).toEqual([]);
  await page.getByRole("button", { name: "确认关联到我的订单" }).click();
  expect(
    await page.evaluate(() =>
      (window as any).guestFixture.calls.filter((c) =>
        c.path.endsWith("/claim"),
      ),
    ),
  ).toEqual([{ path: "/customer/orders/o1/claim", body: { confirm: true } }]);
  await expect(page.getByText("游客订单链接默认有效 14 天")).toHaveCount(0);
  expect(
    await page.evaluate(
      () => (window as any).guestFixture.store.savedAccessLinks,
    ),
  ).toEqual({ o2: "other-order-credential" });
  expect(
    await page.evaluate(
      () => (window as any).guestFixture.identity.orderAccess,
    ),
  ).toBeNull();
});

test("a forwarded link stays read-only after login and directs account linking to the administrator", async ({
  page,
}) => {
  await mount(page, { registered: true, original: false });
  await expect(
    page.getByText("请联系管理员核验订单与账号后关联。"),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "确认关联到我的订单" }),
  ).toHaveCount(0);
});

test("expired original link and QR token never persist or render private order content", async ({
  page,
}) => {
  const token = "s".repeat(64);
  await mount(page, {
    status: "EXPIRED",
    path: "/u/order-link?orderId=o1#token=" + token,
  });
  await expect(
    page.getByRole("heading", { name: "游客订单链接已到期" }),
  ).toBeVisible();
  await expect(page.getByRole("heading", { name: "订单 ABC12" })).toHaveCount(
    0,
  );
  expect(await page.evaluate(() => location.hash)).toBe("");
  expect(
    await page.evaluate(
      () => (window as any).guestFixture.privateMounted || false,
    ),
  ).toBe(false);
  const storage = await page.evaluate(() =>
    JSON.stringify({
      local: { ...localStorage },
      session: { ...sessionStorage },
      history: history.state,
    }),
  );
  expect(storage).not.toContain(token);
});

test("new private link exchanges its fragment once and opens the guarded order", async ({
  page,
}) => {
  const token = "q".repeat(64);
  const requests: string[] = [];
  page.on("request", (request) => requests.push(request.url()));
  await mount(page, {
    original: false,
    path: "/u/order-link?orderId=o1#token=" + token,
  });
  await expect(page.getByRole("heading", { name: "订单 ABC12" })).toBeVisible();
  const calls = await page.evaluate(() =>
    (window as any).guestFixture.calls.filter(
      (c) => c.path === "/order-access/exchange",
    ),
  );
  expect(calls).toEqual([
    {
      path: "/order-access/exchange",
      body: { orderId: "o1", accessToken: token },
    },
  ]);
  expect(await page.evaluate(() => location.hash)).toBe("");
  expect(requests.every((url) => !url.includes(token))).toBe(true);
  expect(
    await page.evaluate(() =>
      JSON.stringify({ ...localStorage, ...sessionStorage }),
    ),
  ).not.toContain(token);
});

test("closed original QR token shows stopped access and clears its in-memory credential", async ({
  page,
}) => {
  await mount(page, {
    status: "CLOSED",
    path: "/u/order-link?orderId=o1#token=" + "r".repeat(64),
  });
  await expect(
    page.getByRole("heading", { name: "游客订单链接已停止访问" }),
  ).toBeVisible();
  await expect(page.getByRole("button", { name: "重新兑换" })).toHaveCount(0);
  expect(await page.evaluate(() => (window as any).guestFixture.ended)).toEqual(
    { orderId: "o1" },
  );
});

test("verification email returns to the remembered order and offers explicit association", async ({
  page,
}) => {
  await page.addInitScript(() =>
    localStorage.setItem("dormprint:customer-order-return", "/u/orders/o1"),
  );
  const token = "verify-token-secret";
  await mount(page, { path: "/u/account/verify#token=" + token });
  await page.getByRole("button", { name: "确认验证邮箱" }).click();
  await expect(
    page.getByRole("button", { name: "确认关联到我的订单" }),
  ).toBeVisible();
  expect(
    await page.evaluate(() =>
      localStorage.getItem("dormprint:customer-order-return"),
    ),
  ).toBeNull();
  expect(
    await page.evaluate(() =>
      JSON.stringify({ ...localStorage, ...sessionStorage }),
    ),
  ).not.toContain(token);
  expect(
    await page.evaluate(() =>
      (window as any).guestFixture.calls.filter((c) =>
        c.path.endsWith("/claim"),
      ),
    ),
  ).toEqual([]);
});

test("registration navigation carries only the safe order return path", async ({
  page,
}) => {
  await mount(page);
  await page.getByRole("link", { name: "注册或登录，保存到我的订单" }).click();
  await expect(page.getByRole("link", { name: "注册账号" })).toHaveAttribute(
    "href",
    "/u/account/register?returnTo=%2Fu%2Forders%2Fo1",
  );
  expect(
    await page.evaluate(() =>
      localStorage.getItem("dormprint:customer-order-return"),
    ),
  ).toBe("/u/orders/o1");
});

test("the verified owner opens an old revoked link through account ownership", async ({
  page,
}) => {
  await mount(page, {
    status: "CLOSED",
    registered: true,
    owned: true,
    access: true,
    path: "/u/order-link?orderId=o1#token=" + "z".repeat(64),
  });
  await expect(page.getByRole("heading", { name: "订单 ABC12" })).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "游客订单链接已停止访问" }),
  ).toHaveCount(0);
  expect(
    await page.evaluate(
      () => (window as any).guestFixture.store.savedAccessLinks,
    ),
  ).toEqual({ o2: "other-order-credential" });
  expect(
    await page.evaluate(
      () => (window as any).guestFixture.identity.orderAccess,
    ),
  ).toBeNull();
});

test("unrecognized guest metadata shows retry feedback before any private content", async ({
  page,
}) => {
  await mount(page, { status: "UNKNOWN" });
  await expect(
    page.getByText("订单访问状态暂时无法核验，请重试。"),
  ).toBeVisible();
  await expect(page.getByRole("button", { name: "重试" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "订单 ABC12" })).toHaveCount(
    0,
  );
});

test("a replacement token received in the same document exchanges once under StrictMode", async ({
  page,
}) => {
  const previous = "p".repeat(64),
    replacement = "n".repeat(64);
  await mount(page, {
    status: "CLOSED",
    strict: true,
    path: "/u/order-link?orderId=o1#token=" + previous,
  });
  await expect(
    page.getByRole("heading", { name: "游客订单链接已停止访问" }),
  ).toBeVisible();
  await page.evaluate((token) => {
    (window as any).guestFixture.status = "ACTIVE";
    location.hash = new URLSearchParams({ token }).toString();
  }, replacement);
  await expect(page.getByRole("heading", { name: "订单 ABC12" })).toBeVisible();
  expect(
    await page.evaluate(() =>
      (window as any).guestFixture.calls
        .filter((c) => c.path === "/order-access/exchange")
        .map((c) => c.body.accessToken),
    ),
  ).toEqual([previous, replacement]);
  expect(await page.evaluate(() => location.hash)).toBe("");
  expect(
    await page.evaluate(() =>
      JSON.stringify({
        history: history.state,
        local: { ...localStorage },
        session: { ...sessionStorage },
      }),
    ),
  ).not.toContain(replacement);
});

test("a late response for an older fragment cannot clear replacement access", async ({
  page,
}) => {
  const previous = "a".repeat(64),
    replacement = "b".repeat(64);
  await mount(page, {
    strict: true,
    delayedToken: previous,
    path: "/u/order-link?orderId=o1#token=" + previous,
  });
  await expect
    .poll(() =>
      page.evaluate(() => typeof (window as any).guestFixture.releasePrevious),
    )
    .toBe("function");
  await page.evaluate((token) => {
    location.hash = new URLSearchParams({ token }).toString();
  }, replacement);
  await expect(page.getByRole("heading", { name: "订单 ABC12" })).toBeVisible();
  await page.evaluate(() => (window as any).guestFixture.releasePrevious());
  await expect
    .poll(() =>
      page.evaluate(
        () =>
          (window as any).guestFixture.calls.filter(
            (c) => c.path === "/order-access/exchange",
          ).length,
      ),
    )
    .toBe(2);
  await expect(page.getByRole("heading", { name: "订单 ABC12" })).toBeVisible();
  expect(
    await page.evaluate(() => (window as any).guestFixture.ended),
  ).toBeUndefined();
});
