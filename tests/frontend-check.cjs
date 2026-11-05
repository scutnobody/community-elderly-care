const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const script = path.join(__dirname, '../static/acceptance.js');
const calls = [];
const nodes = new Map();
const node = () => ({innerHTML:'',textContent:'',dataset:{},classList:{add(){},remove(){},contains(){return false}},querySelector(){return null},querySelectorAll(){return []},addEventListener(){},appendChild(){}});
const ctx = {console,URLSearchParams,Date,JSON,Number,String,Array,Object,Math,Promise,Set,Map,
 window:{}, document:{hidden:false,querySelector(s){if(!nodes.has(s))nodes.set(s,node());return nodes.get(s)},querySelectorAll(){return []},addEventListener(){},createElement:node},
 AUTH:{token:'test',user:{role:'elder'}},API:{elderCode:'E1',providerCode:'',dashboardStats:{}},role:'elder',currentPage:'home',data:{orders:[],emergencies:[],services:[],elders:[],providers:[]},roleConfig:{admin:{nav:[]},elder:{nav:[]},provider:{nav:[]},guest:{nav:[]}},
 renderPage(){},handleAction(){},resetScopedState(){},enterSystem:async()=>{},renderNav(){},setHeader(){},bindActions(){},toast(){},showModal(){},closeModal(){},refreshOrders:async()=>{},refreshEmergencies:async()=>{},refreshDashboardStats:async()=>{},applyElderDirectory(){},applyProviderDirectory(){},getBrowserLocation:async()=>{calls.push('location');return {latitude:null,longitude:null}},
 apiRequest:async(p,o)=>{calls.push({path:p,body:o?.body});return {ok:true,emergency_no:'JQ1',created_at:'2026-10-26 12:00:00',items:[],total:0}},
 setInterval(fn,ms){calls.push({interval:ms});return ms},clearInterval(id){calls.push({clear:id})},setTimeout(){},navigator:{},location:{}, downloadApiFile:async()=>{},fileToBase64:async()=>''};
ctx.window=ctx; vm.createContext(ctx);vm.runInContext(fs.existsSync(script)?fs.readFileSync(script,'utf8'):'',ctx);
assert.ok(ctx.Acceptance, 'Acceptance frontend behavior must be loaded');
const A=ctx.Acceptance;
assert.equal(typeof A.reviewPayload,'function','Uploaded review evidence must be linked in the persisted review request');
const review=A.reviewPayload({attitude_score:'5',quality_score:'4',punctual_score:'3',review_text:'满意'},[17,18]);
assert.equal(JSON.stringify(review.attachments_json),'[17,18]');
assert.equal(review.quality_score,4);
assert.equal(typeof A.refreshDirectory,'function','Directory navigation must query the server for the requested page');
// Catches missing numeric conversion, unsafe output, and lost specialist record fields.
assert.equal(A.escape('<img src=x onerror="x">'), '&lt;img src=x onerror=&quot;x&quot;&gt;');
const threshold=A.payload('thresholds',{elder_code:'E1',metric:'glucose',lower_bound:'',upper_bound:'7.2',enabled:'1',action:'站内提醒'});
assert.equal(threshold.lower_bound,null);assert.equal(threshold.upper_bound,7.2);assert.equal(threshold.enabled,1);
assert.throws(()=>A.payload('thresholds',{lower_bound:'NaN',upper_bound:'7',enabled:'1'}),/数字/);
const record=A.recordPayload({start:'2026-10-26T08:00',end:'2026-10-26T09:00',completion:'全部完成',process:'护理',medical:'医嘱',rehab:'步行',material_name:'手套',material_quantity:'2',material_unit:'副'},[12]);
assert.equal(record.medical_details,'医嘱');assert.equal(record.rehab_details,'步行');
assert.equal(JSON.stringify(record.materials_json),'[{"name":"手套","quantity":2,"unit":"副"}]');
assert.equal(JSON.stringify(record.attachments_json),'[12]');
assert.ok(!A.listHTML('medications',[{id:1,name:'<img onerror=x>',status:'未服'}]).includes('<img'));
// Catches silently truncating an eight-hour service to the old two-hour slot.
assert.equal(typeof A.bookingInterval,'function','Long services require a validated continuous interval');
assert.equal(A.bookingInterval('上午 09:00-11:00','09:00','17:00',480),'09:00-17:00');
assert.equal(A.bookingInterval('','09:00','09:00',1440,true),'09:00-次日09:00');
assert.throws(()=>A.bookingInterval('上午 09:00-11:00','','',480),/覆盖/);
assert.throws(()=>A.bookingInterval('上午 09:00-11:00','17:00','09:00',60),/结束/);
assert.equal(A.bookingInterval('上午 09:00-11:00','','',60),'上午 09:00-11:00');
assert.equal(A.payload('institutions',{institution_code:'I1',name:'机构',bed_count:''}).bed_count,0);
// Catches stored profile data turning into live HTML in the original role views.
const legacyNodes=new Map();
const legacy={console,window:{},navigator:{userAgent:''},document:{querySelector(s){if(!legacyNodes.has(s))legacyNodes.set(s,node());return legacyNodes.get(s)},querySelectorAll(){return []}},setTimeout(){},setInterval(){},clearInterval(){}};
legacy.window=legacy;vm.createContext(legacy);
vm.runInContext(fs.readFileSync(path.join(__dirname,'../static/app.js'),'utf8').replace(/boot\(\);\s*$/,''),legacy);
vm.runInContext("role='elder';API.elderCode='E1';data.elders=[{id:'E1',name:'\" onfocus=alert(1) x=\"',disease:'<img src=x onerror=alert(1)>'}];renderElderProfile(document.querySelector('#viewRoot'));",legacy);
assert.ok(!legacyNodes.get('#viewRoot').innerHTML.includes('<img src=x'),'Stored profile text must be escaped');
assert.ok(!legacyNodes.get('#viewRoot').innerHTML.includes('value="" onfocus='),'Stored profile text must not escape attributes');
(async()=>{
 calls.length=0;await A.refreshDirectory('elders',2,'王');
 assert.equal(calls[0].path,'/api/elders?page=2&page_size=20&q=%E7%8E%8B');
 calls.length=0;await A.startEmergency('跌倒');
 assert.equal(calls[0].path,'/api/emergencies','Emergency must persist before requesting location');
 assert.ok(calls.indexOf('location')>0);
 calls.length=0;A.startTimers();assert.ok(calls.some(x=>x.interval===5000));assert.ok(calls.some(x=>x.interval===60000));
 calls.length=0;A.clearSession();assert.ok(calls.some(x=>x.clear===5000));assert.ok(calls.some(x=>x.clear===60000));assert.equal(A.state.notifications.length,0);
 console.log('frontend checks passed: safe rendering, typed payloads, specialist record persistence, immediate emergency, session timer cleanup');
})().catch(e=>{console.error(e);process.exitCode=1});
