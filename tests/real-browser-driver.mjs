import { chromium, expect } from '@playwright/test';
import { mkdirSync, readFileSync } from 'node:fs';
const base=process.env.DORMPRINT_BROWSER_ORIGIN;
const browser=await chromium.launch({channel:'chrome',headless:true});
const contexts=[];
async function session(email){
 const context=await browser.newContext({viewport:{width:1365,height:900},reducedMotion:'reduce'});contexts.push(context);
 const page=await context.newPage();
 await page.goto(base+'/u/account/login');
 await page.getByLabel('邮箱',{exact:true}).fill(email);
 await page.getByLabel('密码',{exact:true}).fill('Testing-only-Password-42!');
 await page.getByRole('button',{name:'登录',exact:true}).click();
 return {context,page};
}
async function finishLogin(page){
 const skip=page.getByRole('button',{name:'暂不关联',exact:true});
 await expect(skip).toBeVisible({timeout:20000});await skip.click();
 await page.goto(base+'/u/account');
 await expect(page.getByRole('heading',{name:'账号设置',exact:true})).toBeVisible({timeout:20000});
}
async function mutate(page,path,method,body){
 return page.evaluate(async({path,method,body})=>{
  const csrf=document.cookie.split('; ').find(v=>v.startsWith('csrfToken='))?.slice('csrfToken='.length);
  const response=await fetch('/api/v1'+path,{method,headers:{'Content-Type':'application/json','X-CSRF-Token':decodeURIComponent(csrf||''),'Idempotency-Key':crypto.randomUUID()},body:JSON.stringify(body)});
  return {status:response.status,body:await response.json()};
 },{path,method,body});
}
try{
 const normal=await session('browser-user@example.test');await finishLogin(normal.page);
 await expect(normal.page.locator('a[href^="/manage"]')).toHaveCount(0);
 await expect(normal.page.locator('a[href^="/courier"]')).toHaveCount(0);
 const manifest=await normal.page.evaluate(async()=>(await fetch('/manifest.webmanifest')).json());
 expect(manifest).toMatchObject({id:'/',scope:'/',start_url:'/u/'});
 const admin=await session('browser-admin@example.test');await finishLogin(admin.page);
 await expect(admin.page.locator('a[href^="/manage"]')).toHaveCount(1);
 const grant=await mutate(admin.page,'/admin/staff','POST',{accountId:process.env.DORMPRINT_BROWSER_ACCOUNT_ID,role:'COURIER',capabilities:[],discountLevel:2,reason:'隔离环境浏览器验收授权'});
 expect(grant.status).toBe(200);
 const staff=grant.body.data.items.find(row=>row.accountId===process.env.DORMPRINT_BROWSER_ACCOUNT_ID);
 expect(staff).toBeTruthy();
 // Exercise the real unified administrator route and locally generated PNG.
 await admin.page.goto(base+'/manage/qr-tools');
 const qrUrl='https://example.test/help?from=qr#keep-fragment';
 await admin.page.getByTestId('admin-qr-input').fill(qrUrl);
 await admin.page.getByTestId('admin-qr-check').click();
 await admin.page.getByRole('button',{name:'预览并下载二维码',exact:true}).click();
 const qrImage=admin.page.getByTestId('link-qr-image');
 await expect(qrImage).toBeVisible();
 await expect(admin.page.getByTestId('link-qr-value')).toHaveText(qrUrl);
 expect(await qrImage.getAttribute('src')).toMatch(/^data:image\/png;base64,/);
 mkdirSync('docs/qa/real-browser',{recursive:true});
 const downloaded=admin.page.waitForEvent('download');
 await admin.page.getByRole('button',{name:'下载 PNG',exact:true}).click();
 const png=await downloaded;
 await png.saveAs('docs/qa/real-browser/local-link-qr.png');
 expect(readFileSync('docs/qa/real-browser/local-link-qr.png').subarray(0,8).toString('hex')).toBe('89504e470d0a1a0a');

 await normal.page.reload();
 await expect(normal.page.locator('a[href="/courier/"]')).toBeVisible({timeout:20000});
 await expect(normal.page.locator('a[href^="/manage"]')).toHaveCount(0);
 const own=await normal.page.evaluate(async()=>({staff:(await (await fetch('/api/v1/auth/me')).json()).data,customer:(await (await fetch('/api/v1/customer/auth/me')).json()).data}));
 expect(own.staff.role).toBe('COURIER');expect(own.customer.account.id).toBe(process.env.DORMPRINT_BROWSER_ACCOUNT_ID);
 const denied=await normal.page.request.get(base+'/api/v1/admin/orders');expect(denied.status()).toBe(403);
 await normal.page.locator('a[href="/courier/"]').click();
 await expect(normal.page).toHaveURL(/\/courier\/?$/);
 await expect(normal.page.getByRole('link',{name:'返回用户端',exact:true})).toBeVisible();
 await expect(normal.page.getByRole('heading',{name:'暂时没有配送任务',exact:true})).toBeVisible({timeout:20000});
 mkdirSync('docs/qa/real-browser',{recursive:true});
 await normal.page.screenshot({path:'docs/qa/real-browser/unified-courier-desktop.png',fullPage:true});
 await normal.page.setViewportSize({width:390,height:844});
 await normal.page.screenshot({path:'docs/qa/real-browser/unified-courier-mobile.png',fullPage:true});
 const second=await session('browser-user@example.test');await finishLogin(second.page);
 await expect(second.page.locator('a[href="/courier/"]')).toBeVisible();
 const third=await session('browser-user@example.test');
 await expect(third.page.getByText(/最多.*2 台设备/).first()).toBeVisible({timeout:20000});
 const revoke=await mutate(admin.page,`/admin/staff/${staff.id}`,'PATCH',{expectedVersion:staff.version,enabled:false,reason:'隔离环境浏览器验收撤权'});
 expect(revoke.status,JSON.stringify(revoke.body.error ?? {})).toBe(200);
 await normal.page.goto(base+'/u/account');
 await expect(normal.page.locator('a[href^="/courier"]')).toHaveCount(0);
 await expect(normal.page.locator('a[href^="/manage"]')).toHaveCount(0);
 const customer=await normal.page.evaluate(async()=>(await (await fetch('/api/v1/customer/auth/me')).json()).data);
 expect(customer.authKind).toBe('REGISTERED');
 console.log('REAL_BROWSER_PASS: unified login, account grant, two devices, third-device rejection, courier authority, revocation, desktop/mobile UI, single PWA manifest, real local QR PNG download. No API interception.');
}finally{for(const context of contexts)await context.close();await browser.close();}
