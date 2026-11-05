/* Real browser smoke on a disposable localhost fixture, not the shipped database. */
const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const path=require('node:path');
const fs=require('node:fs');
const base=process.env.CARE_BROWSER_URL;
if(!base||new URL(base).hostname!=='127.0.0.1')throw Error('Use disposable localhost CARE_BROWSER_URL');
const out=process.env.CARE_BROWSER_OUTPUT||path.join(__dirname,'browser-output');fs.mkdirSync(out,{recursive:true});
(async()=>{
  const executablePath=process.env.CARE_BROWSER_EXECUTABLE;
  const browser=await chromium.launch({...(executablePath?{executablePath}:{}),headless:true});
  const errors=[];const results=[];
  try{
    const context=await browser.newContext({viewport:{width:1440,height:1000}});
    await context.route('**/*',route=>{const url=new URL(route.request().url());return ['127.0.0.1','localhost'].includes(url.hostname)||['data:','blob:'].includes(url.protocol)?route.continue():route.abort();});
    const page=await context.newPage();page.on('pageerror',e=>errors.push(e.message));
    const serviceCode='AC-BROWSER-'+Date.now();
    async function logout(){await page.locator('[data-action=logout]').click();await page.locator('#loginRole').waitFor({state:'visible'});}
    async function login(role,password){await page.goto(base);await page.locator('#loginRole').selectOption(role);await page.locator('#loginUsername').fill(role);await page.locator('#loginPassword').fill(password);await page.locator('#loginForm button[type=submit]').click();await page.locator('#appShell').waitFor({state:'visible'});await page.waitForTimeout(400);}
    async function nav(id){await page.locator(`[data-page="${id}"]`).click();await page.waitForTimeout(300);assert.ok((await page.locator('#viewRoot').innerText()).trim());results.push(id);}
    await login('admin','Admin123!');
    for(const id of ['dashboard','elders','providers','orders','services','acceptance-institutions','acceptance-packages','acceptance-medications','acceptance-visits','acceptance-thresholds','acceptance-credentials','acceptance-training','acceptance-records','acceptance-complaints','acceptance-locations','acceptance-attachments','acceptance-device','notification-inbox'])await nav(id);
    await nav('services');await page.screenshot({path:path.join(out,'admin-services.png'),fullPage:true});
    // Persist a genuine service through the actual browser form, then reload and query.
    await page.locator('[data-ac=new]').click();
    await page.locator('#acceptance-form [name=service_code]').fill(serviceCode);
    await page.locator('#acceptance-form [name=service_name]').fill('浏览器验收测试服务');
    await page.locator('#acceptance-form [name=price]').fill('25');
    await page.locator('#acceptance-form [name=duration_minutes]').fill('60');
    await page.locator('#acceptance-form button[type=submit]').click();
    await page.locator('#modalRoot').waitFor({state:'hidden'});await page.waitForTimeout(200);
    const service=await page.evaluate(async(code)=>await apiRequest('/api/acceptance/services?q='+encodeURIComponent(code)),serviceCode);
    assert.equal(service.items[0].service_name,'浏览器验收测试服务');
    await page.reload();await page.locator('#appShell').waitFor({state:'visible'});await nav('services');
    await logout();
    await login('elder','Elder123!');for(const id of ['home','profile','book','my-orders','health','acceptance-medications','acceptance-visits','acceptance-thresholds','acceptance-records','acceptance-device','help'])await nav(id);
    await page.screenshot({path:path.join(out,'elder-help.png'),fullPage:true});
    await nav('book');await page.locator('#bookType').selectOption('紧急救援类');await page.locator('#bookService').selectOption('远程监护');
    await page.locator('[name=continuous_start]').fill('09:00');await page.locator('[name=continuous_end]').fill('09:00');await page.locator('[name=continuous_next_day]').selectOption('次日');
    await page.locator('[data-calendar-date]:not(:disabled)').last().click();
    const booked=page.waitForResponse(r=>r.request().method()==='POST'&&new URL(r.url()).pathname==='/api/orders');
    await page.locator('[data-action=submit-book]').click();assert.equal((await booked).status(),201);
    await page.waitForFunction(()=>document.querySelector('#toast').textContent.startsWith('预约已保存：'),{},{timeout:5000});
    const observer=await context.newPage();observer.on('pageerror',e=>errors.push(e.message));await observer.goto(base);
    await observer.locator('#loginRole').selectOption('admin');await observer.locator('#loginUsername').fill('admin');await observer.locator('#loginPassword').fill('Admin123!');await observer.locator('#loginForm button[type=submit]').click();
    await observer.locator('[data-page=notification-inbox]').waitFor();await observer.locator('[data-page=notification-inbox]').click();await observer.locator('#acceptance-inbox').waitFor();
    await nav('help');const notifiedAt=Date.now();await page.locator('[data-action=panic]').click();
    await page.locator('[data-ac=cancel-emergency]').waitFor();const emergency=await page.locator('[data-ac=cancel-emergency]').getAttribute('data-id');
    await observer.bringToFront();await observer.waitForFunction(no=>document.querySelector('#acceptance-inbox')?.textContent.includes(no),emergency,{timeout:10000});
    const notificationMs=Date.now()-notifiedAt;assert.ok(notificationMs<10000);await observer.close();await page.bringToFront();
    await page.locator('[data-ac=cancel-emergency]').click();await page.locator('#modalRoot').waitFor({state:'hidden'});
    await logout();
    await login('provider','Provider123!');for(const id of ['workbench','schedule','assigned','records','acceptance-credentials','acceptance-training','acceptance-records','acceptance-my-statistics','profile'])await nav(id);
    await page.screenshot({path:path.join(out,'provider-profile.png'),fullPage:true});
    const profileSaved=page.waitForResponse(r=>r.request().method()==='POST'&&r.url().includes('/api/profiles/provider/'));
    await page.locator('[data-action=edit-profile]').click();
    assert.equal((await profileSaved).status(),200,'Provider may save personal fields without submitting managed authority');
    await logout();await page.locator('#guestBrowse').click();await page.locator('#appShell').waitFor({state:'visible'});await nav('institutions');await nav('catalog');
    await context.close();
    const mobile=await browser.newContext({viewport:{width:390,height:844},isMobile:true,hasTouch:true});const phone=await mobile.newPage();phone.on('pageerror',e=>errors.push(e.message));await phone.goto(base);await phone.screenshot({path:path.join(out,'mobile-login.png'),fullPage:true});await mobile.close();
    assert.deepEqual(errors,[],'Browser JS must not throw');
    const report={pages:results.length,persisted_service:true,elder_booking:true,day_long_booking:true,provider_profile_save:true,logout_after_reload:true,notification_ms:notificationMs,emergency_cancel:true,desktop_and_mobile:true,errors,results};
    fs.writeFileSync(path.join(out,'browser-results.json'),JSON.stringify(report,null,2));console.log(JSON.stringify(report,null,2));
  }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
