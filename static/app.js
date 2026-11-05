function uiEscape(value) { return String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])); }

const SERVICE_TYPES = ['生活照料类', '医疗护理类', '康复保健类', '精神慰藉类', '紧急救援类'];
const ORDER_STATUS = ['待分配', '待服务', '服务中', '待评价', '已完成', '已取消'];
const EMERGENCY_TYPES = ['身体不适', '跌倒', '火灾', '煤气泄漏', '入室危险'];
const EMERGENCY_FLOW = ['待调度', '已派单', '服务人员已出发', '已到达', '已解决'];
const MEAL_TYPES = ['普通餐', '软食', '流食', '糖尿病餐', '低盐餐'];
const MEDICAL_NEEDS = ['上门巡诊', '陪同就医', '代取药品'];
const COMPLAINT_TYPES = ['服务态度差', '未按时上门', '服务不规范', '收费争议'];
const ELDER_TAGS = ['独居老人', '空巢老人', '失能老人', '高龄老人（80岁以上）', '低保老人', '优抚对象', '计划生育特殊家庭'];


const AUTH = { token: '', user: null };
const AUTH_STORAGE_KEY = 'elderly-care-auth';
const MOBILE_AUTH_TTL = 7 * 24 * 60 * 60 * 1000;

function isMobileClient() {
  const native = Boolean(window.Capacitor?.isNativePlatform?.());
  const touchMobile = /Android|iPhone|iPad|iPod/i.test(navigator.userAgent);
  return native || touchMobile;
}

function saveAuthSession(session) {
  const payload = JSON.stringify({ token: session.token, user: session.user, expiresAt: Date.now() + MOBILE_AUTH_TTL });
  clearAuthSession();
  if (isMobileClient() && session.user?.role !== 'guest') localStorage.setItem(AUTH_STORAGE_KEY, payload);
  else sessionStorage.setItem(AUTH_STORAGE_KEY, payload);
}

function loadAuthSession() {
  const raw = isMobileClient() ? localStorage.getItem(AUTH_STORAGE_KEY) || sessionStorage.getItem(AUTH_STORAGE_KEY) : sessionStorage.getItem(AUTH_STORAGE_KEY);
  if (!raw) return null;
  try {
    const session = JSON.parse(raw);
    if (!session?.token || !session?.user) return null;
    if (isMobileClient() && session.expiresAt && session.expiresAt < Date.now()) {
      clearAuthSession();
      return null;
    }
    return session;
  } catch (_) {
    clearAuthSession();
    return null;
  }
}

function clearAuthSession() {
  sessionStorage.removeItem(AUTH_STORAGE_KEY);
  localStorage.removeItem(AUTH_STORAGE_KEY);
}
const DEMO_ELDER_CODE = 'JX202600128';
const DEMO_PROVIDER_CODE = 'FW20260218';
let healthDays = 30;

const API = {
  elderCode: '',
  providerCode: '',
  options: null,
  providerReviews: [],
  providerReviewStats: { count:0, average:null, good_rate:0 },
  healthAlerts: [],
  healthAlertStats: { total:0, pending:0, handled:0, blood_pressure:0, glucose:0, heart_rate:0, temperature:0, sleep:0 },
  dashboardStats: { elder_count:0, order_count:0, provider_count:0, institution_count:0, pending_orders:0, active_emergencies:0 },
  examReports: [],
  complaints: [],
  healthSimulated: false,
  recordOrder: ''
};

async function apiRequest(path, options = {}) {
  const config = { ...options, headers: { ...(options.headers || {}) } };
  if (AUTH.token) config.headers.Authorization = `Bearer ${AUTH.token}`;
  if (config.body && typeof config.body !== 'string') {
    config.headers['Content-Type'] = 'application/json';
    config.body = JSON.stringify(config.body);
  }
  const response = await fetch(path, config);
  const payload = await response.json().catch(() => ({}));
  if (!response.ok || payload.ok === false) throw new Error(payload.error || `HTTP ${response.status}`);
  return payload;
}

async function downloadApiFile(path, fallbackName) {
  const headers = {};
  if (AUTH.token) headers.Authorization = `Bearer ${AUTH.token}`;
  const response = await fetch(path, { headers });
  if (!response.ok) {
    const payload = await response.json().catch(() => ({}));
    throw new Error(payload.error || `HTTP ${response.status}`);
  }
  const blob = await response.blob();
  const disposition = response.headers.get('Content-Disposition') || '';
  const match = disposition.match(/filename="?([^";]+)"?/i);
  let name = match?.[1] || fallbackName || 'download';
  const encoded = disposition.match(/filename\*=UTF-8''([^;]+)/i);
  if (encoded) { try { name = decodeURIComponent(encoded[1]); } catch (_) {} }
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement('a');
  anchor.href = url;
  anchor.download = name;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

function fileToBase64(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result || '').split(',')[1] || '');
    reader.onerror = () => reject(new Error('读取文件失败'));
    reader.readAsDataURL(file);
  });
}

function pickFile(accept) {
  return new Promise((resolve) => {
    const input = document.createElement('input');
    input.type = 'file';
    input.accept = accept;
    input.style.display = 'none';
    document.body.appendChild(input);
    input.addEventListener('change', () => {
      const file = input.files?.[0] || null;
      input.remove();
      resolve(file);
    }, { once: true });
    input.click();
  });
}

function apiOrderToUi(o) {
  return {
    id: o.order_no,
    dbId: o.order_id,
    elderCode: o.elder_code,
    elder: o.elder_name,
    type: o.service_type,
    serviceCode: o.service_code,
    service: o.service_name,
    date: o.service_date,
    slot: o.time_slot,
    duration: `${o.duration_minutes} 分钟`,
    durationMinutes: o.duration_minutes,
    address: o.service_address,
    latitude: o.service_latitude,
    longitude: o.service_longitude,
    phone: o.contact_phone,
    providerCode: o.provider_code || '',
    provider: o.provider_name || '待分配',
    status: o.status_label,
    statusCode: o.status,
    assignmentStatus: o.assignment_status || '',
    meal: o.meal_type || '',
    delivery: o.delivery_time || '',
    medicalNeed: o.medical_need || '',
    symptom: o.symptom_description || '',
    remark: o.special_requirements || '',
    auditNote: o.audit_remark || '',
    cancelReason: o.cancel_reason || '',
    assignedAt: o.assigned_at || '',
    departAt: o.depart_at || '',
    departLatitude: o.depart_latitude,
    departLongitude: o.depart_longitude,
    arriveAt: o.arrive_at || '',
    finishAt: o.finish_at || '',
    recordCount: o.record_count || 0,
    recordReviewStatus: o.record_review_status || '',
    conditionCheck: o.condition_check || '',
    startAt: o.start_at || '', endAt: o.end_at || ''
  };
}

async function refreshOrders() {
  const payload = await apiRequest('/api/orders');
  data.orders = payload.orders.map(apiOrderToUi);
}

function applyElderProfile(p) {
  let e = data.elders.find((x) => x.id === p.elder_code);
  if (!e) {
    e = { id:p.elder_code, name:p.full_name || '', status:'正常' };
    data.elders.unshift(e);
  }
  Object.assign(e, { id:p.elder_code, name:p.full_name || '', gender:p.gender || '', idCard:p.id_card || '', birth:p.birth_date || '', nation:p.ethnicity || '', phone:p.phone || '', community:p.community || '', address:p.address || '', height:p.height_cm != null ? `${p.height_cm} cm` : '', weight:p.weight_kg != null ? `${p.weight_kg} kg` : '', blood:p.blood_type || '', allergy:p.allergy_history || '', disease:p.disease_history || '', disability:p.disability_status || '', level:p.self_care_level || '', medicalType:p.medical_type || '', medicalCard:p.medical_card_no || '', hospital:p.designated_hospital || '', contact:p.emergency_contact_name || '', relation:p.emergency_contact_relation || '', contactPhone:p.emergency_contact_phone || '', backupPhone:p.emergency_backup_phone || '', living:p.living_situation || '', habit:p.language_preference || '', diet:p.diet_requirement || '', timePref:p.visit_time_preference || '', tag:p.elder_tags || '普通', priority:p.service_priority || '普通' });
}
function applyElderDirectory(rows) {
  const old = new Map(data.elders.map((e) => [e.id, e]));
  data.elders = rows.map((p) => {
    const e = { ...(old.get(p.elder_code) || {}) };
    Object.assign(e, { id:p.elder_code, name:p.full_name, phone:p.phone, address:p.address, community:p.community, gender:p.gender, idCard:p.id_card, birth:p.birth_date, nation:p.ethnicity, blood:p.blood_type, allergy:p.allergy_history, disease:p.disease_history, disability:p.disability_status, level:p.self_care_level, medicalType:p.medical_type, medicalCard:p.medical_card_no, hospital:p.designated_hospital, contact:p.emergency_contact_name, relation:p.emergency_contact_relation, contactPhone:p.emergency_contact_phone, backupPhone:p.emergency_backup_phone, living:p.living_situation, habit:p.language_preference, diet:p.diet_requirement, timePref:p.visit_time_preference, tag:p.elder_tags || '普通', priority:p.service_priority || '普通' });
    if (p.height_cm != null) e.height=`${p.height_cm} cm`;
    if (p.weight_kg != null) e.weight=`${p.weight_kg} kg`;
    if (p.age != null) e.age=p.age;
    return e;
  });
}
function applyProviderDirectory(rows) {
  const old = new Map(data.providers.map((p) => [p.id, p]));
  data.providers = rows.map((p) => {
    const x = old.get(p.provider_code) || {};
    return { ...x, id:p.provider_code, name:p.full_name, gender:p.gender || '', idCard:p.id_card || '', phone:p.phone || '', org:p.organization || '', skills:p.skills || '', certs:p.certificates || '', areas:p.service_areas || '', years:Number(p.service_years || 0), intro:p.profile_intro || '', health:p.health_certificate || '', training:p.training_records || '', status:p.employment_status || '在职', load:x.load || '今日 0 单 / 本周 0 单', rating:x.rating || '—', ratingGood:x.ratingGood || '—', complaintRate:x.complaintRate || '0%', orders:x.orders || 0, hours:x.hours || '0h' };
  });
}
function mapEmergency(e) {
  return { id:e.emergency_no, dbId:e.emergency_id, elderCode:e.elder_code, name:e.elder_name, type:e.emergency_type, source:e.trigger_source, address:e.address, location:e.longitude != null && e.latitude != null ? `${e.longitude},${e.latitude}` : '定位未提供', latitude:e.latitude, longitude:e.longitude, time:(e.created_at||'').slice(0,16), status:e.status, notify:e.notify_targets, providerCode:e.provider_code || '', provider:e.provider_name || '待指派', dispatchAt:e.dispatch_at || '', dispatchNote:e.dispatch_note || '', responseTime:e.response_time || '', departAt:e.depart_at || '', departLatitude:e.depart_latitude, departLongitude:e.depart_longitude, arriveTime:e.arrive_time || '', finishAt:e.finish_at || '', measure:e.handling_measure || '', result:e.result || '待处理', canCancel:e.status === '待调度' };
}
function applyProviderProfile(p) {
  let x = data.providers.find((v) => v.id === p.provider_code);
  if (!x) {
    x = { id:p.provider_code, name:p.full_name || '', load:'今日 0 单 / 本周 0 单', rating:'—', ratingGood:'—', complaintRate:'0%', orders:0, hours:'0h' };
    data.providers.unshift(x);
  }
  Object.assign(x, { id:p.provider_code, name:p.full_name || '', gender:p.gender || '', idCard:p.id_card || '', phone:p.phone || '', org:p.organization || '', skills:p.skills || '', certs:p.certificates || '', areas:p.service_areas || '', years:Number(p.service_years || 0), intro:p.profile_intro || '', health:p.health_certificate || '', training:p.training_records || '', status:p.employment_status || '在职' });
}
function currentElder() {
  return data.elders.find((x) => x.id === API.elderCode) || { id:API.elderCode, name:AUTH.user?.display_name || '', phone:'', address:'', community:'', contact:'', relation:'', contactPhone:'', hospital:'' };
}
function currentProvider() {
  return data.providers.find((x) => x.id === API.providerCode) || { id:API.providerCode, name:AUTH.user?.display_name || '', phone:'', org:'', skills:'', certs:'', areas:'', years:0, intro:'', health:'', training:'', status:'在职', rating:'—' };
}
function demoElderData(list) { return API.elderCode === DEMO_ELDER_CODE ? list : []; }
function emptyRow(columns, text) { return `<tr><td colspan="${columns}" class="empty">${text}</td></tr>`; }

function mapReview(r) { return { id:r.order_no, service:r.service_name, provider:r.provider_name || '待分配', date:r.service_date, attitude:r.attitude_score || 0, quality:r.quality_score || 0, punctual:r.punctual_score || 0, overall:r.overall ?? '—', text:r.review_text || (r.state === '待评价' ? '服务完成后可评价' : ''), photos:r.photo_note || '—', state:r.state }; }
function mapVital(v) { return { time:v.measured_at.slice(0,16), bp:v.systolic && v.diastolic ? `${v.systolic} / ${v.diastolic}` : '—', systolic:v.systolic, diastolic:v.diastolic, glucose:v.glucose ?? '—', heart:v.heart_rate ?? '—', temp:v.temperature ?? '—', weight:v.weight ?? '—', sleep:v.sleep_hours != null ? `${v.sleep_hours}h` : '—', sleepValue:v.sleep_hours, source:v.source }; }
async function refreshReviews() {
  if (!API.elderCode) { data.reviews=[]; return; }
  const p=await apiRequest('/api/reviews');
  data.reviews=(p.reviews || []).map(mapReview);
}
async function refreshProviderReviews() {
  if (!API.providerCode) { API.providerReviews=[]; API.providerReviewStats={count:0,average:null,good_rate:0}; return; }
  const p=await apiRequest('/api/reviews');
  API.providerReviews=p.reviews || [];
  API.providerReviewStats=p.stats || {count:0,average:null,good_rate:0};
}
async function refreshComplaints() {
  if (!['elder','provider','admin'].includes(role)) { API.complaints=[]; return; }
  const p=await apiRequest('/api/complaints');
  API.complaints=p.complaints || [];
}
async function refreshHealth(days=healthDays) {
  if (!API.elderCode) { API.healthRecords=[]; data.vitals=[]; return; }
  healthDays=Number(days);
  const p=await apiRequest(`/api/health-records?days=${healthDays}`);
  API.healthRecords=p.records || [];
  API.healthRange={ start:p.start_date, end:p.end_date, days:p.days };
  API.healthSimulated=Boolean(p.simulated);
  data.vitals=(p.records || []).map(mapVital);
}
async function refreshHealthAlerts() { const p=await apiRequest('/api/health-alerts'); API.healthAlerts=p.alerts || []; API.healthAlertStats=p.stats || {total:0,pending:0,handled:0,blood_pressure:0,glucose:0,heart_rate:0,temperature:0,sleep:0}; }
async function refreshElderDirectory() { const p=await apiRequest('/api/elders'); applyElderDirectory(p.elders || []); }
async function refreshProviderDirectory() { const p=await apiRequest('/api/providers'); applyProviderDirectory(p.providers || []); }
async function refreshEmergencies() { if (!['admin','elder','provider'].includes(role)) { data.emergencies=[]; return; } const p=await apiRequest('/api/emergencies'); data.emergencies=(p.emergencies || []).map(mapEmergency); }
async function refreshNotices() { const p=await apiRequest('/api/notices'); data.notices=p.notices || []; updateNoticeBadge(); }
async function refreshDashboardStats() { const p=await apiRequest('/api/dashboard-stats'); API.dashboardStats=p.stats || API.dashboardStats; }
async function refreshExamReports() { if (role !== 'elder' || !API.elderCode) { API.examReports=[]; return; } const p=await apiRequest('/api/exam-reports'); API.examReports=p.reports || []; }
function updateNoticeBadge() { const badge=document.querySelector('.notice-count'); if (badge) badge.textContent=String(data.notices.length); }
function resetScopedState() {
  data.orders=[];
  data.emergencies=[];
  data.reviews=[];
  data.serviceRecords=[];
  API.healthRecords=[];
  API.examReports=[];
  API.providerReviews=[];
  API.providerReviewStats={count:0,average:null,good_rate:0};
  API.complaints=[];
  API.recordOrder='';
}
async function initializeApiData() {
  try {
    resetScopedState();
    API.options=await apiRequest('/api/options');
    if (role === 'admin') {
      await Promise.all([refreshOrders(),refreshElderDirectory(),refreshProviderDirectory(),refreshEmergencies(),refreshNotices(),refreshDashboardStats(),refreshHealthAlerts(),refreshComplaints()]);
      return;
    }
    if (role === 'elder') {
      const elder=await apiRequest(`/api/profiles/elder/${encodeURIComponent(API.elderCode)}`);
      applyElderProfile(elder.profile);
      applyProviderDirectory(API.options?.providers || []);
      await Promise.all([refreshOrders(),refreshReviews(),refreshHealth(),refreshEmergencies(),refreshNotices(),refreshExamReports(),refreshComplaints()]);
      return;
    }
    if (role === 'provider') {
      const provider=await apiRequest(`/api/profiles/provider/${encodeURIComponent(API.providerCode)}`);
      applyProviderProfile(provider.profile);
      await Promise.all([refreshOrders(),refreshProviderReviews(),refreshEmergencies(),refreshNotices(),refreshComplaints()]);
      return;
    }
    await refreshNotices();
  } catch (error) { console.error('后端初始化失败：', error); toast(`后端连接失败：${error.message}`); }
}

function parseDurationMinutes(value, fallback = 60) {
  const match = String(value || '').match(/\d+/);
  return match ? Number(match[0]) : fallback;
}

function findServiceCodeByName(name) {
  const local = data.services.find((s) => s.name === name);
  if (local?.id) return local.id;
  const remote = API.options?.services?.find((s) => s.service_name === name);
  return remote?.service_code || '';
}

function getBrowserLocation() {
  return new Promise((resolve) => {
    const localHost = ['localhost', '127.0.0.1', '::1'].includes(location.hostname);
    if (!window.isSecureContext && !localHost) return resolve({ latitude: null, longitude: null, source: 'insecure' });
    if (!navigator.geolocation) return resolve({ latitude: null, longitude: null, source: 'unsupported' });
    navigator.geolocation.getCurrentPosition(
      (pos) => resolve({ latitude: pos.coords.latitude, longitude: pos.coords.longitude, source: 'browser' }),
      (error) => {
        const source = error.code === 1 ? 'denied' : error.code === 2 ? 'unavailable' : 'timeout';
        resolve({ latitude: null, longitude: null, source });
      },
      { enableHighAccuracy: true, timeout: 8000, maximumAge: 30000 }
    );
  });
}

function locationMessage(source) {
  if (source === 'denied') return '定位权限被浏览器拒绝，请点击地址栏左侧网站图标，在“网站设置/权限”中把“位置”改为“允许”后刷新页面';
  if (source === 'insecure') return '当前位置功能需要 HTTPS；本机 localhost 可用，局域网 IP 或公网访问请配置 HTTPS';
  if (source === 'timeout') return '定位请求超时，请确认系统定位服务已开启后重试';
  if (source === 'unavailable') return '设备暂时无法获取定位，请检查系统定位服务或网络';
  if (source === 'unsupported') return '当前浏览器不支持网页定位';
  return '定位未授权或不可用';
}

const roleConfig = {
  admin: {
    name: '陈雅 · 社区管理员',
    nav: [['dashboard','▦','运营总览'],['orders','☷','预约审核分配'],['elders','♙','老人档案'],['health-alerts','♡','健康预警'],['providers','♧','服务人员'],['services','◇','服务项目套餐'],['emergencies','!','紧急求助'],['notices','✎','社区公告'],['reports','◒','统计分析']],
    defaultPage: 'dashboard'
  },
  elder: {
    name: '李秀梅 · 老人 / 家属',
    nav: [['home','⌂','我的首页'],['profile','♙','个人信息'],['book','＋','预约服务'],['my-orders','☷','服务订单'],['health','♡','健康档案'],['help','!','紧急求助'],['reviews','☆','评价与投诉']],
    defaultPage: 'home'
  },
  provider: {
    name: '王建国 · 服务人员',
    nav: [['workbench','▦','工作台'],['schedule','▣','服务排班'],['assigned','☷','执行服务订单'],['emergency-tasks','!','紧急任务'],['records','✎','服务记录'],['profile','♙','人员信息'],['provider-reviews','☆','评价与申诉']],
    defaultPage: 'workbench'
  },
  guest: {
    name: '游客 · 访客模式',
    nav: [['guest-home','⌂','服务首页'],['institutions','▤','养老服务机构'],['catalog','◇','居家服务项目']],
    defaultPage: 'guest-home'
  }
};

const data = {services:[],packages:[],institutions:[],elders:[],providers:[],orders:[],emergencies:[],vitals:[],medications:[],exams:[],visits:[],thresholds:[],reviews:[],complaints:[],notices:[],reservations:[],serviceRecords:[]};

let role = 'admin';
let currentPage = 'dashboard';
let calendarViewDate = new Date();
let selectedAppointmentDate = '';
let emergencyCountdown = null;
let pendingEmergency = null;

const $ = (selector) => document.querySelector(selector);
const navIconFallback = '•';

function renderNav() {
  const nav = $('#mainNav');
  nav.innerHTML = roleConfig[role].nav.map(([id, icon, label]) => `<button class="nav-item ${id === currentPage ? 'active' : ''}" data-page="${id}"><span class="nav-icon">${icon || navIconFallback}</span><span>${label}</span></button>`).join('');
  nav.querySelectorAll('[data-page]').forEach((el) => el.addEventListener('click', () => navigate(el.dataset.page)));
}

function isMobileLayout() {
  return window.matchMedia('(max-width: 760px)').matches;
}

function setMobileSidebar(open) {
  const sidebar=document.querySelector('#appShell .sidebar');
  const backdrop=document.querySelector('#appShell .mobile-sidebar-backdrop');
  if (!sidebar || !backdrop) return;
  sidebar.classList.toggle('mobile-open', Boolean(open));
  backdrop.classList.toggle('show', Boolean(open));
  document.body.classList.toggle('mobile-nav-open', Boolean(open) && isMobileLayout());
}

function initMobileSidebar() {
  const shell=$('#appShell');
  const sidebar=shell?.querySelector('.sidebar');
  const mark=sidebar?.querySelector('.brand-mark');
  if (!shell || !sidebar || !mark || mark.dataset.mobileToggleBound) return;
  const backdrop=document.createElement('button');
  backdrop.type='button';
  backdrop.className='mobile-sidebar-backdrop';
  backdrop.setAttribute('aria-label','关闭导航栏');
  shell.appendChild(backdrop);
  mark.dataset.mobileToggleBound='1';
  mark.setAttribute('role','button');
  mark.setAttribute('tabindex','0');
  mark.setAttribute('aria-label','展开或收起导航栏');
  mark.addEventListener('click',()=>{
    if (!isMobileLayout()) return;
    setMobileSidebar(!sidebar.classList.contains('mobile-open'));
  });
  mark.addEventListener('keydown',(event)=>{
    if (!isMobileLayout() || !['Enter',' '].includes(event.key)) return;
    event.preventDefault();
    setMobileSidebar(!sidebar.classList.contains('mobile-open'));
  });
  backdrop.addEventListener('click',()=>setMobileSidebar(false));
  window.addEventListener('resize',()=>{
    if (!isMobileLayout()) setMobileSidebar(false);
  });
}

function navigate(page) {
  currentPage = page;
  setMobileSidebar(false);
  renderNav();
  renderPage();
  window.scrollTo({ top: 0, behavior: 'smooth' });
}

function setHeader(title) { $('#pageTitle').textContent = title; $('#pageCrumb').textContent = title; }
function statusTag(text, cls='gray') { return `<span class="tag ${cls}">${uiEscape(text)}</span>`; }
function orderStatusClass(status) { return ({'待分配':'amber','待服务':'blue','服务中':'blue','待评价':'amber','已完成':'green','已取消':'gray'})[status] || 'gray'; }
function emergencyStatusClass(status) { return ({'待调度':'red','已派单':'amber','服务人员已出发':'amber','已到达':'blue','已解决':'green','误报取消':'gray'})[status] || 'gray'; }
function iconFor(type) { return ({'生活照料类':'⌂','医疗护理类':'＋','康复保健类':'◌','精神慰藉类':'♡','紧急救援类':'!'})[type] || '◇'; }
function panel(title, body, extra='') { return `<section class="panel ${extra}"><div class="panel-head"><div class="panel-title">${title}</div></div>${body}</section>`; }
function toolbar(searchPlaceholder, buttonText, action) { return `<div class="toolbar"><div class="search"><span>⌕</span><input data-filter-input placeholder="${searchPlaceholder}" /></div><button class="primary-button" data-action="${action}">＋ ${buttonText}</button></div>`; }
function field(label, control, full=false) { return `<div class="field ${full ? 'full' : ''}"><label>${label}</label>${control}</div>`; }
function pageShell(title, caption, body) { return `<div class="panel" style="max-width:960px"><div class="panel-head"><div><div class="panel-title">${title}</div><div class="panel-caption">${caption}</div></div></div>${body}</div>`; }

function renderPage() {
  const view = $('#viewRoot');
  if (role === 'admin') ({dashboard:renderAdminDashboard, orders:renderOrders, elders:renderElders, 'health-alerts':renderAdminHealthAlerts, providers:renderProviders, services:renderServices, emergencies:renderEmergencies, notices:renderNotices, reports:renderReports}[currentPage] || renderAdminDashboard)(view);
  if (role === 'elder') ({home:renderElderHome, profile:renderElderProfile, book:renderBook, 'my-orders':renderMyOrders, health:renderHealth, help:renderHelp, reviews:renderElderReviews}[currentPage] || renderElderHome)(view);
  if (role === 'provider') ({workbench:renderProviderHome, schedule:renderSchedule, assigned:renderAssigned, 'emergency-tasks':renderProviderEmergencyTasks, records:renderServiceRecords, profile:renderProviderProfile, 'provider-reviews':renderProviderReviews}[currentPage] || renderProviderHome)(view);
  if (role === 'guest') ({'guest-home':renderGuestHome, institutions:renderInstitutions, catalog:renderCatalog}[currentPage] || renderGuestHome)(view);
  bindActions();
  bindTrendChart();
  bindHealthCharts();
}

function renderOrderTrendChart() {
  const points = API.dashboardStats?.order_trend || [];
  if (!points.length) return '<div class="empty">暂无真实订单趋势</div>';
  const max = Math.max(1, ...points.map(p => Number(p.orders) || 0), ...points.map(p => Number(p.completed) || 0));
  const height = 210;
  const x = (index) => (points.length <= 1 ? 0.5 : index / (points.length - 1)) * 640;
  const y = (value) => height - (value / max) * height;
  const gridLines = [0, 0.25, 0.5, 0.75, 1].map((ratio) => `<line x1="0" y1="${height * ratio}" x2="640" y2="${height * ratio}" stroke="#e7ecee" stroke-width="1" />`).join('');
  const orderPath = points.map((p, i) => `${i === 0 ? 'M' : 'L'}${x(i)} ${y(p.orders)}`).join(' ');
  const donePath = points.map((p, i) => `${i === 0 ? 'M' : 'L'}${x(i)} ${y(p.completed)}`).join(' ');
  const dots = points.map((p, i) => `<circle class="chart-dot" cx="${x(i)}" cy="${uiEscape(y(p.orders))}" r="4" fill="#fff" stroke="#176b58" stroke-width="2" /><circle class="chart-dot" cx="${x(i)}" cy="${uiEscape(y(p.completed))}" r="3" fill="#fff" stroke="#3f6e9f" stroke-width="2" />`).join('');
  const hits = points.map((p, i) => `<rect x="${Math.max(0, Math.min(640, x(i) - 53))}" y="0" width="106" height="${height}" fill="transparent" class="chart-hit-area" data-chart-index="${i}" />`).join('');
  return `<div class="chart-wrap"><div class="chart-y"><span>${max}</span><span>${Math.round(max*.75)}</span><span>${Math.round(max*.5)}</span><span>${Math.round(max*.25)}</span><span>0</span></div><div class="chart-main"><div class="chart-canvas"><svg class="chart-svg" viewBox="0 0 640 ${height}" preserveAspectRatio="none">${gridLines}<path d="${orderPath}" fill="none" stroke="#176b58" stroke-width="2.5" /><path d="${donePath}" fill="none" stroke="#3f6e9f" stroke-width="2" stroke-dasharray="5 4" />${dots}<line class="chart-guide" x1="0" y1="0" x2="0" y2="${height}" stroke="#9aa7ab" stroke-width="1" stroke-dasharray="4 4" style="opacity:0" />${hits}</svg><div class="chart-tooltip" data-chart-tooltip></div></div><div class="chart-legend"><span><i class="legend-dot teal"></i>订单量</span><span><i class="legend-dot blue"></i>完成量</span></div><script type="application/json" data-chart-data>${JSON.stringify(points).replace(/</g, '\\u003c')}</script></div></div>`;
}

function bindTrendChart() {
  const canvas = document.querySelector('.chart-canvas');
  const dataScript = document.querySelector('[data-chart-data]');
  if (!canvas || !dataScript) return;
  const points = JSON.parse(dataScript.textContent);
  const svg = canvas.querySelector('.chart-svg');
  const tooltip = canvas.querySelector('[data-chart-tooltip]');
  const guide = canvas.querySelector('.chart-guide');
  const dots = Array.from(canvas.querySelectorAll('.chart-dot'));
  const height = 210;
  const max = Math.max(1, ...points.map(p => Number(p.orders) || 0), ...points.map(p => Number(p.completed) || 0));
  const x = (index) => (points.length <= 1 ? 0.5 : index / (points.length - 1)) * 640;
  const y = (value) => height - (value / max) * height;
  canvas.querySelectorAll('.chart-hit-area').forEach((area) => {
    area.addEventListener('mouseenter', () => {
      const index = Number(area.dataset.chartIndex);
      const point = points[index];
      tooltip.innerHTML = `<strong>${uiEscape(point.month)}</strong><span>订单量 ${uiEscape(point.orders.toLocaleString())} 单</span><span>完成量 ${point.completed.toLocaleString()} 单</span>`;
      tooltip.classList.add('show');
      tooltip.style.left = `${Math.min(78, Math.max(6, (index / (points.length - 1)) * 88))}%`;
      guide.setAttribute('x1', x(index));
      guide.setAttribute('x2', x(index));
      guide.style.opacity = '1';
      dots.forEach((dot) => dot.setAttribute('r', '3'));
      if (dots[index * 2]) { dots[index * 2].setAttribute('r', '6'); dots[index * 2 + 1].setAttribute('r', '5'); }
    });
    area.addEventListener('mouseleave', () => {
      tooltip.classList.remove('show');
      guide.style.opacity = '0';
      dots.forEach((dot, i) => dot.setAttribute('r', i % 2 === 0 ? '4' : '3'));
    });
  });
}

function renderAdminDashboard(view) {
  setHeader('运营总览');
  const stats=API.dashboardStats || {};
  const pending=stats.pending_orders ?? data.orders.filter((o)=>o.status==='待分配').length;
  const activeEmergency=stats.active_emergencies ?? data.emergencies.filter((e)=>!['已解决','误报取消'].includes(e.status)).length;
  view.innerHTML = `<div class="grid stats-grid">
    <div class="stat-card"><div class="stat-label">服务老人数</div><div class="stat-value">${uiEscape(Number(stats.elder_count || 50286).toLocaleString())}</div><div class="stat-meta">覆盖 <strong>${uiEscape(stats.institution_count || 100)}</strong> 家机构</div></div>
    <div class="stat-card"><div class="stat-label">年度服务订单</div><div class="stat-value">${uiEscape(Number(stats.order_count || 248600).toLocaleString())}</div><div class="stat-meta">待分配 <strong>${pending}</strong> 单</div></div>
    <div class="stat-card"><div class="stat-label">服务人员</div><div class="stat-value">${uiEscape(Number(stats.provider_count || 816).toLocaleString())}</div><div class="stat-meta"><strong>98.2%</strong> 资质有效</div></div>
    <div class="stat-card alert"><div class="stat-label">紧急求助处理中</div><div class="stat-value">${String(activeEmergency).padStart(2,'0')}</div><div class="stat-meta"><strong>需立即关注</strong></div></div>
  </div>
  <div class="grid content-grid">
    ${panel(`2026 年服务订单趋势（截至 ${stats.as_of || '当前日期'}，悬停查看数值）`, renderOrderTrendChart(), '')}
    ${panel('最新预约动态', `<div class="order-list">${data.orders.slice(0,4).map((o)=>`<div class="order-row"><div class="order-icon">${uiEscape(iconFor(o.type))}</div><div class="order-info"><div class="order-name">${uiEscape(o.service)} · ${uiEscape(o.elder)}</div><div class="order-sub">${uiEscape(o.date)} ${uiEscape(o.slot)} · ${uiEscape(o.duration)}</div></div>${statusTag(o.status,orderStatusClass(o.status))}${o.recordReviewStatus?`<br><small>记录：${uiEscape(o.recordReviewStatus)}</small>`:''}</div>`).join('')}</div>`, '')}
  </div>
  <div class="grid bottom-grid">
    ${panel('紧急求助实时提醒', `<div>${data.emergencies.slice(0,3).map((e)=>`<div class="alert-item"><div class="alert-mark">!</div><div class="alert-copy"><strong>${uiEscape(e.name)} · ${uiEscape(e.type)}</strong><span>${uiEscape(e.address)} · ${uiEscape(e.time)} · ${uiEscape(e.status)}</span></div>${statusTag(e.status,emergencyStatusClass(e.status))}</div>`).join('')}</div>`, 'alert-panel')}
    ${panel('服务类型订单分布', `<div class="progress-row"><span>生活照料类</span><div class="progress"><i style="width:50%"></i></div><b>50%</b></div><div class="progress-row"><span>医疗护理类</span><div class="progress"><i style="width:27%;background:#3f6e9f"></i></div><b>27%</b></div><div class="progress-row"><span>康复保健类</span><div class="progress"><i style="width:10%;background:#b4a46e"></i></div><b>10%</b></div><div class="progress-row"><span>精神慰藉类</span><div class="progress"><i style="width:7%;background:#b78d9c"></i></div><b>7%</b></div><div class="progress-row"><span>紧急救援类</span><div class="progress"><i style="width:6%;background:#c9584f"></i></div><b>6%</b></div>`)}
  </div>`;
}

function renderOrders(view) {
  setHeader('预约审核与分配');
  const recommend = (serviceType) => data.providers.filter((p) => p.skills.includes(serviceType.replace('类', '')) && p.status === '在职').slice(0, 3);
  view.innerHTML = toolbar('搜索预约编号、老人姓名或服务项目', '新建预约', 'new-order') + `<div class="table-panel"><div class="table-scroll"><table><thead><tr><th>预约编号</th><th>服务对象</th><th>服务类型 / 项目</th><th>预约日期时段</th><th>时长</th><th>服务地址</th><th>服务人员</th><th>订单状态</th><th>操作</th></tr></thead><tbody>${data.orders.map((o) => `<tr data-search="${uiEscape(o.id)}${uiEscape(o.elder)}${uiEscape(o.service)}"><td><strong>${uiEscape(o.id)}</strong></td><td>${uiEscape(o.elder)}</td><td>${uiEscape(o.service)}<br><span class="panel-caption">${uiEscape(o.type)}</span></td><td>${uiEscape(o.date)}<br><span class="panel-caption">${uiEscape(o.slot)}</span></td><td>${uiEscape(o.duration)}</td><td>${uiEscape(o.address)}</td><td>${uiEscape(o.provider)}</td><td>${statusTag(o.status, orderStatusClass(o.status))}${o.recordReviewStatus?`<br><small>记录：${uiEscape(o.recordReviewStatus)}</small>`:''}</td><td><div class="table-actions"><button class="text-button" data-action="view-order" data-id="${uiEscape(o.id)}">详情</button>${o.status === '待分配' ? `<button class="text-button" data-action="audit-order" data-id="${uiEscape(o.id)}">审核分配</button>` : ''}</div></td></tr>`).join('')}</tbody></table></div></div>
  <div class="grid content-grid" style="margin-top:18px">${panel('智能推荐规则', `<div class="alert-item"><div class="alert-mark" style="background:var(--blue-pale);color:var(--blue)">i</div><div class="alert-copy"><strong>分配时自动校验四项条件</strong><span>服务区域是否覆盖老人住址、是否具备对应资质、当日排班是否冲突、老人是否有指定服务人员偏好。</span></div></div>`)}${panel('可分配人员（按服务类型匹配）', `<div class="order-list">${recommend('生活照料').map((p) => `<div class="order-row"><div class="order-icon">♧</div><div class="order-info"><div class="order-name">${uiEscape(p.name)} · ${uiEscape(p.skills)}</div><div class="order-sub">${uiEscape(p.areas)} · 今日负荷 ${uiEscape(p.load.split(' / ')[0])}</div></div>${statusTag(p.status, 'green')}</div>`).join('')}</div>`)}</div>`;
}

function renderElders(view) {
  setHeader('老人档案管理');
  view.innerHTML = `<div class="toolbar"><div class="search"><span>⌕</span><input data-filter-input placeholder="搜索姓名、档案编号、标签或社区" /></div><div class="table-actions"><button class="outline-button" data-action="download-elder-template">下载导入模板</button><button class="outline-button" data-action="import-elders-excel">导入 Excel</button><button class="primary-button" data-action="new-elder">＋ 新增老人档案</button></div></div><div class="table-panel"><div class="table-scroll"><table><thead><tr><th>档案编号</th><th>姓名 / 性别</th><th>年龄</th><th>自理能力</th><th>居住情况</th><th>联系电话</th><th>老人标签</th><th>服务优先级</th><th>操作</th></tr></thead><tbody>${data.elders.map((e)=>`<tr data-search="${uiEscape(e.id)}${uiEscape(e.name)}${uiEscape(e.tag)}${uiEscape(e.community)}"><td><strong>${uiEscape(e.id)}</strong></td><td><strong>${uiEscape(e.name)}</strong> / ${uiEscape(e.gender)}<br><span class="panel-caption">${uiEscape(e.community)}</span></td><td>${uiEscape(e.age)} 岁</td><td>${uiEscape(e.level)}</td><td>${uiEscape(e.living)}</td><td>${uiEscape(e.phone)}</td><td>${statusTag(e.tag,e.tag.includes('高龄') || e.tag.includes('失能') ? 'red' : 'blue')}</td><td>${statusTag(e.priority,e.priority==='优先'?'amber':'green')}</td><td><div class="table-actions"><button class="text-button" data-action="edit-elder" data-id="${uiEscape(e.id)}">编辑</button><button class="text-button" data-action="delete-elder" data-id="${uiEscape(e.id)}">注销</button></div></td></tr>`).join('')}</tbody></table></div></div>`;
}

function renderAdminHealthAlerts(view) {
  setHeader('健康预警');
  const alerts=API.healthAlerts || [];
  const stats=API.healthAlertStats || {};
  const cards=[['预警总数',stats.total || 0,'全部异常记录'],['待处理',stats.pending || 0,'需要管理员关注'],['血压',stats.blood_pressure || 0,'≥ 140/90 mmHg'],['空腹血糖',stats.glucose || 0,'≥ 7.0 mmol/L'],['心率',stats.heart_rate || 0,'< 60 或 > 100 次/分'],['体温',stats.temperature || 0,'≥ 38.0 ℃'],['睡眠',stats.sleep || 0,'建议 7-8 小时']];
  view.innerHTML = `<div class="grid kpi-grid">${cards.map(([name,value,caption])=>`<div class="kpi"><h3>${name}</h3><p>${String(value).padStart(2,'0')}</p><small>${caption}</small></div>`).join('')}</div>
  <div class="table-panel"><div class="table-scroll"><table><thead><tr><th>预警编号</th><th>老人</th><th>异常指标</th><th>当前值</th><th>预警阈值</th><th>产生时间</th><th>状态</th><th>处理说明</th><th>操作</th></tr></thead><tbody>${alerts.length ? alerts.map((a)=>`<tr><td><strong>JK${uiEscape(String(a.alert_id).padStart(5,'0'))}</strong></td><td>${uiEscape(a.elder_name)}<br><span class="panel-caption">${uiEscape(a.elder_code)}</span></td><td>${uiEscape(a.metric_type)}</td><td><strong>${uiEscape(a.current_value)}</strong></td><td>${uiEscape(a.threshold_value)}</td><td>${uiEscape((a.created_at || '').slice(0,16))}</td><td>${statusTag(a.status,a.status==='已处理'?'green':'red')}</td><td>${uiEscape(a.handling_note || '—')}${a.handled_by ? `<br><span class="panel-caption">${uiEscape(a.handled_by)} · ${uiEscape((a.handled_at || '').slice(0,16))}</span>` : ''}</td><td>${a.status==='待处理' ? `<button class="text-button" data-action="handle-health-alert" data-id="${uiEscape(a.alert_id)}">处理</button>` : '—'}</td></tr>`).join('') : '<tr><td colspan="9" class="empty">暂无异常预警。</td></tr>'}</tbody></table></div></div>`;
}

function renderProviders(view) {
  setHeader('服务人员管理');
  view.innerHTML = toolbar('搜索姓名、工号、服务类型或区域', '录入服务人员', 'new-provider') + `<div class="table-panel"><div class="table-scroll"><table><thead><tr><th>工号</th><th>姓名 / 岗位</th><th>所属机构</th><th>服务类型权限</th><th>服务年限</th><th>服务区域</th><th>账号状态</th><th>工作负荷</th><th>评分 / 投诉率</th><th>操作</th></tr></thead><tbody>${data.providers.map((p) => `<tr data-search="${uiEscape(p.id)}${uiEscape(p.name)}${uiEscape(p.skills)}${uiEscape(p.areas)}"><td><strong>${uiEscape(p.id)}</strong></td><td><strong>${uiEscape(p.name)}</strong><br><span class="panel-caption">${uiEscape(p.gender)} · ${uiEscape(p.years)} 年</span></td><td>${uiEscape(p.org)}</td><td>${uiEscape(p.skills)}</td><td>${uiEscape(p.years)} 年</td><td>${uiEscape(p.areas)}</td><td>${statusTag(p.status, p.status === '在职' ? 'green' : p.status === '请假' ? 'amber' : 'gray')}</td><td>${uiEscape(p.load)}</td><td>★ ${uiEscape(p.rating)}<br><span class="panel-caption">投诉率 ${uiEscape(p.complaintRate)}</span></td><td><div class="table-actions"><button class="text-button" data-action="view-provider" data-id="${uiEscape(p.id)}">查看</button><button class="text-button" data-action="delete-provider" data-id="${uiEscape(p.id)}">删除</button></div></td></tr>`).join('')}</tbody></table></div></div>`;
}

function renderServices(view) {
  setHeader('服务项目与套餐');
  view.innerHTML = toolbar('搜索服务名称、编号、类型或方式', '新增服务项目', 'new-service') + `<div class="table-panel"><div class="table-scroll"><table><thead><tr><th>服务编号</th><th>服务名称</th><th>服务类型</th><th>服务方式</th><th>内容说明</th><th>收费标准</th><th>时长</th><th>资质要求</th><th>适用人群</th><th>预约须知</th><th>状态</th><th>操作</th></tr></thead><tbody>${data.services.map((s) => `<tr data-search="${uiEscape(s.id)}${uiEscape(s.name)}${uiEscape(s.type)}${uiEscape(s.mode)}${uiEscape(s.target)}"><td><strong>${uiEscape(s.id)}</strong></td><td><strong>${uiEscape(s.name)}</strong></td><td>${uiEscape(s.type)}</td><td>${uiEscape(s.mode)}</td><td>${uiEscape(s.desc)}</td><td>${uiEscape(s.price)}</td><td>${uiEscape(s.duration)}</td><td>${uiEscape(s.qualification)}</td><td>${uiEscape(s.target)}</td><td>${uiEscape(s.notes)}</td><td>${statusTag(s.status, s.status === '正常提供' ? 'green' : s.status === '限时促销' ? 'amber' : 'gray')}</td><td><div class="table-actions"><button class="text-button" data-action="edit-service" data-id="${uiEscape(s.id)}">编辑</button></div></td></tr>`).join('')}</tbody></table></div></div>
  ${panel('服务套餐管理', `<div class="table-panel"><div class="table-scroll"><table><thead><tr><th>套餐编号</th><th>套餐名称</th><th>包含内容</th><th>套餐价格</th><th>有效期</th><th>状态</th></tr></thead><tbody>${data.packages.map((p) => `<tr><td><strong>${uiEscape(p.id)}</strong></td><td>${uiEscape(p.name)}</td><td>${uiEscape(p.content)}</td><td>${uiEscape(p.price)}</td><td>至 ${uiEscape(p.valid)}</td><td>${statusTag(p.status, p.status === '正常提供' ? 'green' : 'gray')}</td></tr>`).join('')}</tbody></table></div></div>`)}`;
}

function renderEmergencies(view) {
  setHeader('紧急求助调度');
  const total=data.emergencies.length;
  const waiting=data.emergencies.filter((e)=>e.status==='待调度').length;
  const assigned=data.emergencies.filter((e)=>['已派单','服务人员已出发','已到达'].includes(e.status)).length;
  const closed=data.emergencies.filter((e)=>['已解决','误报取消'].includes(e.status)).length;
  view.innerHTML = `<div class="grid kpi-grid"><div class="kpi"><h3>求助记录</h3><p>${String(total).padStart(2,'0')}</p><small>emergency_requests</small></div><div class="kpi"><h3>待调度</h3><p style="color:var(--red)">${String(waiting).padStart(2,'0')}</p><small>管理员需要指派人员</small></div><div class="kpi"><h3>正在响应</h3><p>${String(assigned).padStart(2,'0')}</p><small>已派单 / 出发 / 到达</small></div><div class="kpi"><h3>已结束</h3><p>${String(closed).padStart(2,'0')}</p><small>已解决或误报取消</small></div></div>
  <div class="table-panel"><div class="table-scroll"><table><thead><tr><th>求助编号</th><th>求助人</th><th>类型 / 触发方式</th><th>地址 / 定位</th><th>发起时间</th><th>事件状态</th><th>指派人员</th><th>处理结果</th><th>操作</th></tr></thead><tbody>${data.emergencies.map((e)=>`<tr><td><strong>${uiEscape(e.id)}</strong></td><td>${uiEscape(e.name)}</td><td>${uiEscape(e.type)}<br><span class="panel-caption">${uiEscape(e.source)}</span></td><td>${uiEscape(e.address)}<br><span class="panel-caption">${uiEscape(e.location)}</span></td><td>${uiEscape(e.time)}</td><td>${statusTag(e.status,emergencyStatusClass(e.status))}</td><td>${uiEscape(e.provider)}</td><td>${uiEscape(e.result)}</td><td><div class="table-actions">${['待调度','已派单'].includes(e.status) ? `<button class="text-button" data-action="handle-emergency" data-id="${uiEscape(e.id)}">${e.status==='待调度'?'调度':'调整派单'}</button>` : `<button class="text-button" data-action="view-emergency" data-id="${uiEscape(e.id)}">查看进度</button>`}</div></td></tr>`).join('')}</tbody></table></div></div>`;
}

function renderNotices(view) {
  setHeader('社区公告');
  view.innerHTML = toolbar('搜索公告标题或内容', '发布公告', 'new-notice') + `<div class="table-panel"><div class="table-scroll"><table><thead><tr><th>公告标题</th><th>公告内容</th><th>发布日期</th><th>发布人</th><th>操作</th></tr></thead><tbody>${data.notices.map((n)=>`<tr data-search="${uiEscape(n.title)}${uiEscape(n.content)}"><td><strong>${uiEscape(n.title)}</strong></td><td>${uiEscape(n.content)}</td><td>${uiEscape((n.published_at || '').slice(0,10))}</td><td>${uiEscape(n.publisher)}</td><td><div class="table-actions"><button class="text-button" data-action="view-notice" data-id="${uiEscape(n.notice_id)}">查看</button><button class="text-button" data-action="delete-notice" data-id="${uiEscape(n.notice_id)}">删除</button></div></td></tr>`).join('')}</tbody></table></div></div>`;
}

function renderReports(view) {
  setHeader('数据统计分析');
  const stats=API.dashboardStats || {};
  const typeCounts=stats.service_type_counts || {};
  view.innerHTML = `<div class="toolbar"><div class="search"><span>⌕</span><input data-filter-input placeholder="多维度筛选：社区、服务类型、时段" /></div><div class="table-actions"><button class="outline-button" data-action="export-pdf">导出 PDF</button><button class="outline-button" data-action="export-excel">导出 Excel</button></div></div>
  <div class="panel-caption" style="margin-bottom:14px">演示统计数据按系统日期更新，当前截止 ${uiEscape(stats.as_of || '—')}；真实新增业务数据会叠加到数据库统计中。</div>
  <div class="grid stats-grid"><div class="stat-card"><div class="stat-label">年度服务订单</div><div class="stat-value">${uiEscape(Number(stats.order_count || 0).toLocaleString())}</div><div class="stat-meta">模拟目标约 <strong>30 万单</strong></div></div><div class="stat-card"><div class="stat-label">订单完成率</div><div class="stat-value">96.8%</div><div class="stat-meta"><strong>+1.2%</strong> 较去年</div></div><div class="stat-card"><div class="stat-label">服务好评率</div><div class="stat-value">92.4%</div><div class="stat-meta">中评 5% · 差评 3%</div></div><div class="stat-card"><div class="stat-label">求助平均响应</div><div class="stat-value">07:58</div><div class="stat-meta"><strong>优于 8 分钟目标</strong></div></div></div>
  <div class="grid content-grid"><div>${panel('老人结构分析（年龄 / 性别 / 失能等级 / 居住情况）', '<div class="progress-row"><span>60-69 岁</span><div class="progress"><i style="width:38%"></i></div><b>38%</b></div><div class="progress-row"><span>70-79 岁</span><div class="progress"><i style="width:34%;background:#3f6e9f"></i></div><b>34%</b></div><div class="progress-row"><span>80 岁以上</span><div class="progress"><i style="width:28%;background:#b4a46e"></i></div><b>28%</b></div><div class="progress-row"><span>独居 / 空巢</span><div class="progress"><i style="width:41%;background:#b78d9c"></i></div><b>41%</b></div><div class="progress-row"><span>失能老人</span><div class="progress"><i style="width:36%;background:#c9584f"></i></div><b>36%</b></div>')}</div><div>${panel('服务订单统计（类型 / 时段 / 社区）', `<div class="progress-row"><span>生活照料类</span><div class="progress"><i style="width:50%"></i></div><b>${Number(typeCounts['生活照料类'] || 0).toLocaleString()} 单</b></div><div class="progress-row"><span>医疗护理类</span><div class="progress"><i style="width:27%;background:#3f6e9f"></i></div><b>${Number(typeCounts['医疗护理类'] || 0).toLocaleString()} 单</b></div><div class="progress-row"><span>康复保健类</span><div class="progress"><i style="width:10%;background:#b4a46e"></i></div><b>${Number(typeCounts['康复保健类'] || 0).toLocaleString()} 单</b></div><div class="progress-row"><span>精神慰藉类</span><div class="progress"><i style="width:7%;background:#b78d9c"></i></div><b>${Number(typeCounts['精神慰藉类'] || 0).toLocaleString()} 单</b></div><div class="progress-row"><span>紧急救援类</span><div class="progress"><i style="width:6%;background:#c9584f"></i></div><b>${Number(typeCounts['紧急救援类'] || 0).toLocaleString()} 单</b></div>`)}</div></div>
  <div class="grid content-grid" style="margin-top:18px"><div>${panel('服务人员绩效', '<div class="table-panel"><table><thead><tr><th>人员</th><th>服务单数</th><th>服务时长</th><th>好评率</th><th>投诉率</th></tr></thead><tbody>' + data.providers.map((p) => `<tr><td>${uiEscape(p.name)}</td><td>${uiEscape(p.orders)}</td><td>${uiEscape(p.hours)}</td><td>${uiEscape(p.ratingGood)}</td><td>${uiEscape(p.complaintRate)}</td></tr>`).join('') + '</tbody></table></div>')}</div><div>${panel('健康数据监测与紧急求助分析', '<div class="progress-row"><span>异常指标预警</span><div class="progress"><i style="width:18%;background:#c9584f"></i></div><b>1,842 次</b></div><div class="progress-row"><span>慢病管理覆盖率</span><div class="progress"><i style="width:86%"></i></div><b>86%</b></div><div class="progress-row"><span>求助：身体不适</span><div class="progress"><i style="width:45%;background:#3f6e9f"></i></div><b>45%</b></div><div class="progress-row"><span>求助：跌倒</span><div class="progress"><i style="width:32%;background:#b4a46e"></i></div><b>32%</b></div><div class="progress-row"><span>处理结果：已解决</span><div class="progress"><i style="width:78%"></i></div><b>78%</b></div>')}</div></div>`;
}

function renderElderHome(view) {
  setHeader('我的首页');
  const e=currentElder();
  const mine=data.orders.filter((o)=>o.elderCode===API.elderCode && o.status!=='已取消');
  const now=new Date();
  const monthKey=`${now.getFullYear()}-${String(now.getMonth()+1).padStart(2,'0')}`;
  const monthCount=mine.filter((o)=>String(o.date || '').startsWith(monthKey)).length;
  const pending=mine.filter((o)=>['待分配','待服务','服务中'].includes(o.status));
  const next=pending.slice().sort((a,b)=>(a.date+a.slot).localeCompare(b.date+b.slot))[0];
  const healthRows=API.healthRecords || [];
  const latest=healthRows[0];
  const contactCount=e.contact && e.contactPhone ? 1 : 0;
  const healthReminder=latest ? `<div class="alert-item"><div class="alert-mark" style="background:var(--mint);color:var(--teal)">♡</div><div class="alert-copy"><strong>最近健康记录</strong><span>${uiEscape(latest.measured_at.slice(0,16))} · 血压 ${uiEscape(latest.systolic ?? '—')}/${uiEscape(latest.diastolic ?? '—')} mmHg · 血糖 ${uiEscape(latest.glucose ?? '—')} mmol/L</span></div></div>` : '<div class="empty">当前账号还没有健康记录，可点击“记录健康数据”开始建立档案。</div>';
  view.innerHTML = `<div class="grid stats-grid"><div class="stat-card"><div class="stat-label">本月服务次数</div><div class="stat-value">${String(monthCount).padStart(2,'0')}</div><div class="stat-meta">累计预约 <strong>${uiEscape(mine.length)}</strong> 次</div></div><div class="stat-card"><div class="stat-label">待处理订单</div><div class="stat-value">${String(pending.length).padStart(2,'0')}</div><div class="stat-meta">${next?`最近 <strong>${uiEscape(next.date)} ${uiEscape(next.slot)}</strong>`:'暂无待处理预约'}</div></div><div class="stat-card"><div class="stat-label">健康档案</div><div class="stat-value">${uiEscape(healthRows.length)}</div><div class="stat-meta">${latest?`最近更新 <strong>${uiEscape(latest.measured_at.slice(0,16))}</strong>`:'尚未录入健康数据'}</div></div><div class="stat-card alert"><div class="stat-label">紧急联系人</div><div class="stat-value">${String(contactCount).padStart(2,'0')}</div><div class="stat-meta"><strong>${contactCount?`${uiEscape(e.contact)} · ${uiEscape(e.relation || '联系人')}`:'尚未绑定'}</strong></div></div></div>
  <div class="grid content-grid"><div>${panel('快捷服务', '<div class="grid" style="grid-template-columns:repeat(2,1fr);gap:12px"><button class="outline-button" data-action="quick-book">＋ 预约居家服务</button><button class="outline-button" data-action="quick-health">♡ 记录健康数据</button><button class="outline-button" data-action="quick-help" style="color:var(--red);border-color:#f0d7d4">! 一键紧急求助</button><button class="outline-button" data-action="quick-orders">☷ 查看服务订单</button></div>')}</div>${panel('即将到来的服务', `<div class="order-list">${pending.length?pending.slice(0,3).map((o)=>`<div class="order-row"><div class="order-icon">${uiEscape(iconFor(o.type))}</div><div class="order-info"><div class="order-name">${uiEscape(o.service)}</div><div class="order-sub">${uiEscape(o.date)} ${uiEscape(o.slot)} · ${uiEscape(o.duration)} · ${uiEscape(o.provider)}</div></div>${statusTag(o.status,orderStatusClass(o.status))}${o.recordReviewStatus?`<br><small>记录：${uiEscape(o.recordReviewStatus)}</small>`:''}</div>`).join(''):'<div class="empty">当前账号还没有即将到来的服务</div>'}</div>`)}</div>
  <div class="grid bottom-grid">${panel('健康提醒', healthReminder)}${panel('社区公告', data.notices.length?data.notices.map((n)=>`<div class="alert-item" data-action="view-notice" data-id="${uiEscape(n.notice_id)}"><div class="alert-copy"><strong>${uiEscape(n.title)}</strong><span>${uiEscape(n.content)} · ${uiEscape((n.published_at || '').slice(0,10))}</span></div></div>`).join(''):'<div class="empty">暂无社区公告</div>')}</div>`;
}

function renderElderProfile(view) {
  setHeader('个人信息');
  const e=currentElder();
  view.innerHTML=pageShell('老人个人信息管理','修改后点击保存，数据会 POST 到后端并持久化到 SQLite',`
    ${panel('基本信息',`<div class="form-grid"><div class="field"><label>姓名</label><input name="full_name" value="${uiEscape(e.name || '')}" /></div><div class="field"><label>性别</label><input name="gender" value="${uiEscape(e.gender || '')}" /></div><div class="field"><label>身份证号</label><input name="id_card" value="${uiEscape(e.idCard || '')}" /></div><div class="field"><label>出生日期</label><input name="birth_date" value="${uiEscape(e.birth || '')}" /></div><div class="field"><label>民族</label><input name="ethnicity" value="${uiEscape(e.nation || '')}" /></div><div class="field"><label>联系电话</label><input name="phone" value="${uiEscape(e.phone || '')}" /></div><div class="field"><label>所属社区</label><input name="community" value="${uiEscape(e.community || '')}" /></div><div class="field"><label>居住地址</label><input name="address" value="${uiEscape(e.address || '')}" /></div></div>`)}
    ${panel('身体状况',`<div class="form-grid"><div class="field"><label>身高（cm）</label><input name="height_cm" value="${uiEscape(parseFloat(e.height) || '')}" /></div><div class="field"><label>体重（kg）</label><input name="weight_kg" value="${uiEscape(parseFloat(e.weight) || '')}" /></div><div class="field"><label>血型</label><input name="blood_type" value="${uiEscape(e.blood || '')}" /></div><div class="field"><label>过敏史</label><input name="allergy_history" value="${uiEscape(e.allergy || '')}" /></div><div class="field full"><label>既往病史</label><input name="disease_history" value="${uiEscape(e.disease || '')}" /></div><div class="field"><label>残疾情况</label><input name="disability_status" value="${uiEscape(e.disability || '')}" /></div><div class="field"><label>自理能力评估等级</label><input name="self_care_level" value="${uiEscape(e.level || '')}" /></div></div>`)}
    ${panel('医保信息',`<div class="form-grid"><div class="field"><label>医保类型</label><input name="medical_type" value="${uiEscape(e.medicalType || '')}" /></div><div class="field"><label>医保卡号</label><input name="medical_card_no" value="${uiEscape(e.medicalCard || '')}" /></div><div class="field full"><label>定点医疗机构</label><input name="designated_hospital" value="${uiEscape(e.hospital || '')}" /></div></div>`)}
    ${panel('紧急联系人',`<div class="form-grid"><div class="field"><label>姓名</label><input name="emergency_contact_name" value="${uiEscape(e.contact || '')}" /></div><div class="field"><label>关系</label><input name="emergency_contact_relation" value="${uiEscape(e.relation || '')}" /></div><div class="field"><label>联系电话</label><input name="emergency_contact_phone" value="${uiEscape(e.contactPhone || '')}" /></div><div class="field"><label>备用电话</label><input name="emergency_backup_phone" value="${uiEscape(e.backupPhone || '')}" /></div></div>`)}
    ${panel('居住情况与服务偏好',`<div class="form-grid"><div class="field"><label>居住情况</label><input name="living_situation" value="${uiEscape(e.living || '')}" /></div><div class="field"><label>习惯用语</label><input name="language_preference" value="${uiEscape(e.habit || '')}" /></div><div class="field"><label>特殊饮食要求</label><input name="diet_requirement" value="${uiEscape(e.diet || '')}" /></div><div class="field"><label>上门时间偏好</label><input name="visit_time_preference" value="${uiEscape(e.timePref || '')}" /></div></div>`)}
    <div class="modal-foot"><button class="primary-button" data-action="save-profile">保存个人信息</button></div>`);
}

function renderBook(view) {
  setHeader('预约服务');
  const e=currentElder();
  const options=(type)=>data.services.filter((s)=>s.type===type).map((s)=>`<option value="${uiEscape(s.name)}" data-special="${uiEscape(s.special || '')}">${uiEscape(s.name)}（${uiEscape(s.price)}）</option>`).join('');
  const providers=(API.options?.providers || []).filter((x)=>x.employment_status==='在职').map((x)=>`<option value="${uiEscape(x.provider_code)}">${uiEscape(x.full_name)} · ${uiEscape(x.skills || '服务人员')}</option>`).join('');
  view.innerHTML=pageShell('预约居家服务','先选择服务类型与具体服务项目，每张预约单只能对应一个服务项目',`<div class="form-grid">
    <div class="field"><label>服务类型 *</label><select id="bookType" name="serviceType">${SERVICE_TYPES.map((t)=>`<option>${t}</option>`).join('')}</select></div>
    <div class="field"><label>具体服务项目 *</label><select id="bookService" name="service">${options('生活照料类')}</select></div>
    <div class="field"><label>服务对象（关联老人）*</label><select name="elder"><option value="${uiEscape(e.id || API.elderCode)}">${uiEscape(e.name || AUTH.user?.display_name || '当前老人')}（当前档案）</option></select></div>
    <div class="field"><label>意向服务人员（可选）</label><select name="preferredProvider"><option value="">无指定，由系统推荐</option>${providers}</select></div>
    <div class="field full"><label>服务地址</label><div class="location-row"><input name="address" value="${uiEscape(e.address || '')}" placeholder="默认使用个人档案居住地址，可手动修改" /><button type="button" class="outline-button" data-action="capture-book-location">获取定位</button></div><input name="serviceLatitude" type="hidden" /><input name="serviceLongitude" type="hidden" /><div id="bookLocationText" class="panel-caption">未获取坐标</div></div>
    <div class="field full"><label>预约日期 *</label><input id="appointmentDate" name="date" type="text" placeholder="请在下方日历选择日期" readonly /><div id="appointmentCalendar" class="appointment-calendar"></div></div>
    <div class="field"><label>预约时段 *</label><select name="slot"><option>上午 09:00-11:00</option><option>上午 10:00-12:00</option><option>下午 13:00-15:00</option><option>下午 15:00-17:00</option><option>晚上 18:00-20:00</option><option>晚上 19:00-21:00</option></select></div>
    <div class="field"><label>服务时长</label><input name="duration" value="60 分钟" /></div>
    <div class="field full"><label>特殊需求说明</label><textarea name="remark" placeholder="如需要男护理员、需要会方言的服务人员、携带特定设备等"></textarea></div>
    <div id="mealExtra" class="field full" style="display:none"><label>助餐服务：餐食类型与配送时间</label><div class="form-grid"><div class="field"><label>餐食类型</label><select name="meal">${MEAL_TYPES.map((m)=>`<option>${m}</option>`).join('')}</select></div><div class="field"><label>配送时间</label><select name="delivery"><option>11:00-11:30</option><option>11:30-12:00</option><option>17:00-17:30</option></select></div></div></div>
    <div id="medicalExtra" class="field full" style="display:none"><label>助医服务：就诊需求与症状描述</label><div class="form-grid"><div class="field"><label>就诊需求</label><select name="medicalNeed">${MEDICAL_NEEDS.map((m)=>`<option>${m}</option>`).join('')}</select></div><div class="field"><label>症状描述</label><input name="symptom" placeholder="请简要描述症状" /></div></div></div>
  </div><div class="modal-foot"><button class="outline-button" data-action="cancel-book">取消</button><button class="primary-button" data-action="submit-book">提交预约</button></div>`);
  const typeSelect=$('#bookType');
  const serviceSelect=$('#bookService');
  const syncExtras=()=>{ const selected=data.services.find((s)=>s.name===serviceSelect.value); const special=selected?selected.special:''; $('#mealExtra').style.display=special==='meal'?'block':'none'; $('#medicalExtra').style.display=special==='medical'?'block':'none'; };
  typeSelect.addEventListener('change',()=>{ serviceSelect.innerHTML=options(typeSelect.value); syncExtras(); });
  serviceSelect.addEventListener('change',syncExtras);
  syncExtras();
  calendarViewDate=new Date();
  selectedAppointmentDate='';
  renderAppointmentCalendar();
}

function renderMyOrders(view) {
  setHeader('服务订单');
  const mine=data.orders.filter((o)=>o.elderCode===API.elderCode);
  const rows=mine.length?mine.map((o)=>`<tr><td><strong>${uiEscape(o.id)}</strong></td><td>${uiEscape(o.service)}<br><span class="panel-caption">${uiEscape(o.type)}</span></td><td>${uiEscape(o.date)}<br><span class="panel-caption">${uiEscape(o.slot)}</span></td><td>${uiEscape(o.address)}</td><td>${uiEscape(o.provider)}</td><td>${statusTag(o.status,orderStatusClass(o.status))}${o.recordReviewStatus?`<br><small>记录：${uiEscape(o.recordReviewStatus)}</small>`:''}</td><td><div class="table-actions"><button class="text-button" data-action="view-order" data-id="${uiEscape(o.id)}">详情</button>${o.status==='待分配'||o.status==='待服务'?`<button class="text-button" data-action="cancel-order" data-id="${uiEscape(o.id)}">取消</button>`:''}${o.status==='待评价'?`<button class="primary-button compact-button" data-action="write-review" data-id="${uiEscape(o.id)}">去评价</button>`:''}</div></td></tr>`).join(''):emptyRow(7,'当前账号还没有服务订单');
  view.innerHTML=`<div class="table-panel"><div class="table-scroll"><table><thead><tr><th>预约编号</th><th>服务名称</th><th>预约日期 / 时段</th><th>服务地址</th><th>服务人员</th><th>订单状态</th><th>操作</th></tr></thead><tbody>${rows}</tbody></table></div></div>
  ${panel('订单状态说明','<div class="order-list"><div class="order-row"><div class="order-info"><div class="order-name">待分配 → 待服务 → 服务中 → 待评价 → 已完成</div><div class="order-sub">服务完成后提交服务记录，管理员审核通过后才可评价。退回记录可由人员修改重提。</div></div></div></div>')}`;
}

function healthMiniChart(title, records, key, suffix, minPad=1) {
  const points=[...records].reverse().filter((r)=>r[key]!=null && r[key] !== '—');
  if (!points.length) return `<div class="health-mini-chart"><strong>${title}</strong><div class="empty">暂无数据</div></div>`;
  const values=points.map((p)=>Number(p[key]));
  const min=Math.min(...values)-minPad;
  const max=Math.max(...values)+minPad;
  const range=Math.max(1,max-min);
  const width=640;
  const height=150;
  const x=(i)=>(i/(Math.max(1,values.length-1)))*width;
  const y=(v)=>height-12-((v-min)/range)*(height-30);
  const coords=values.map((v,i)=>`${x(i)},${y(v)}`).join(' ');
  const first=points[0].measured_at.slice(5,10);
  const last=points[points.length-1].measured_at.slice(5,10);
  const latest=values[values.length-1];
  const dots=values.map((v,i)=>`<circle class="health-chart-dot" cx="${x(i)}" cy="${y(v)}" r="2.4"/>`).join('');
  const hitWidth=Math.max(6,width/Math.max(1,values.length));
  const hits=values.map((v,i)=>`<rect class="health-chart-hit" data-index="${i}" x="${Math.max(0,x(i)-hitWidth/2)}" y="0" width="${hitWidth}" height="${height}" />`).join('');
  const chartData=points.map((p,i)=>({date:p.measured_at.slice(0,10),value:values[i],source:p.source || '—'}));
  return `<div class="health-mini-chart" data-health-chart><div class="health-chart-head"><strong>${title}</strong><span>最新 ${latest} ${suffix}</span></div><div class="health-chart-canvas"><svg viewBox="0 0 ${width} ${height}" preserveAspectRatio="none" aria-label="${title}${healthDays}天趋势"><line x1="0" y1="${height-12}" x2="${width}" y2="${height-12}" class="health-grid-line"/><polyline points="${coords}" class="health-trend-line"/>${dots}<line class="health-chart-guide" x1="0" y1="0" x2="0" y2="${height}" style="opacity:0"/>${hits}</svg><div class="health-chart-tooltip"></div><script type="application/json" data-health-chart-data>${JSON.stringify(chartData).replace(/</g, '\\u003c')}</script></div><div class="health-axis"><span>${first}</span><span>近 ${healthDays} 天</span><span>${last}</span></div></div>`;
}

function bindHealthCharts() {
  document.querySelectorAll('[data-health-chart]').forEach((chart)=>{
    const canvas=chart.querySelector('.health-chart-canvas');
    const script=chart.querySelector('[data-health-chart-data]');
    if (!canvas || !script) return;
    const points=JSON.parse(script.textContent);
    const tooltip=chart.querySelector('.health-chart-tooltip');
    const guide=chart.querySelector('.health-chart-guide');
    const dots=Array.from(chart.querySelectorAll('.health-chart-dot'));
    chart.querySelectorAll('.health-chart-hit').forEach((hit)=>{
      hit.addEventListener('mouseenter',()=>{
        const index=Number(hit.dataset.index);
        const point=points[index];
        const dot=dots[index];
        tooltip.innerHTML=`<strong>${uiEscape(point.date)}</strong><span>${uiEscape(point.value)}</span><small>${uiEscape(point.source)}</small>`;
        tooltip.classList.add('show');
        const ratio=points.length<=1?0.5:index/(points.length-1);
        tooltip.style.left=`${Math.min(88,Math.max(12,ratio*100))}%`;
        if (dot) {
          guide.setAttribute('x1',dot.getAttribute('cx'));
          guide.setAttribute('x2',dot.getAttribute('cx'));
          dot.classList.add('active');
        }
        guide.style.opacity='1';
      });
      hit.addEventListener('mouseleave',()=>{
        tooltip.classList.remove('show');
        guide.style.opacity='0';
        dots.forEach((dot)=>dot.classList.remove('active'));
      });
    });
  });
}
function renderHealth(view) {
  setHeader('健康档案');
  const rows=API.healthRecords || [];
  const latest=rows[0] || {};
  const range=API.healthRange || {};
  const rangeText=range.start && range.end ? `${range.start.slice(5)} 至 ${range.end.slice(5)}` : `近 ${healthDays} 天`;
  const rangeButtons=[7,30,90].map((d)=>`<button class="health-range-button ${healthDays===d?'active':''}" data-action="health-range" data-days="${d}">${d} 天</button>`).join('');
  const medications=API.acceptanceMedications || [];
  const demoExams=[];
  const visits=API.acceptanceVisits || [];
  const trendCaption=`${rangeText}，仅显示实际登记测量；缺测日期不补造数据，同日多次测量分别保留。`;
  const healthCards=rows.length?rows.slice(0,8).map((v)=>`<div class="health-record-card"><strong>${uiEscape(v.measured_at.slice(0,16))}</strong><span>血压 ${uiEscape(v.systolic || '—')}/${uiEscape(v.diastolic || '—')} mmHg</span><span>血糖 ${uiEscape(v.glucose ?? '—')} mmol/L · 心率 ${uiEscape(v.heart_rate ?? '—')}</span><span>体温 ${uiEscape(v.temperature ?? '—')}℃ · 体重 ${uiEscape(v.weight ?? '—')}kg · 睡眠 ${uiEscape(v.sleep_hours ?? '—')}h</span><small>${uiEscape(v.source || '手动录入')}</small></div>`).join(''):'<div class="empty">当前账号还没有健康记录</div>';
  const medicationRows=medications.length?medications.map((m)=>`<tr><td>${uiEscape(m.name)}</td><td>${uiEscape(m.spec)}</td><td>${uiEscape(m.usage)}</td><td>${uiEscape(m.time)}</td><td>${statusTag(m.state,m.state==='已服'?'green':m.state==='未服'?'amber':'red')}</td></tr>`).join(''):emptyRow(5,'当前账号还没有用药记录');
  const exams=[...API.examReports.map((x)=>({id:x.report_id,date:x.exam_date,org:x.organization,file:x.file_name,abnormal:x.abnormal_summary,uploaded:true})),...demoExams];
  const examCards=exams.length?exams.map((x)=>`<div class="health-list-card"><strong>${uiEscape(x.date)} · ${uiEscape(x.org)}</strong><span>${uiEscape(x.file)}</span><small>${uiEscape(x.abnormal || '未记录异常指标')}</small>${x.uploaded?`<div style="margin-top:8px"><button class="text-button" data-action="download-exam" data-id="${uiEscape(x.id)}">下载报告</button></div>`:''}</div>`).join(''):'<div class="empty">当前账号还没有体检报告</div>';
  const visitCards=visits.length?visits.map((v)=>`<div class="health-list-card"><strong>${uiEscape(v.date)} · ${uiEscape(v.hospital)} / ${uiEscape(v.dept)}</strong><span>${uiEscape(v.result)}</span><small>医嘱：${uiEscape(v.advice)}；处方：${uiEscape(v.drug)}；复诊：${uiEscape(v.review)}</small></div>`).join(''):'<div class="empty">当前账号还没有就诊记录</div>';
  view.innerHTML=`<div class="health-page"><div class="grid kpi-grid"><div class="kpi"><h3>最近血压</h3><p>${uiEscape(latest.systolic || '—')} / ${uiEscape(latest.diastolic || '—')}</p><small>${latest.measured_at ? latest.measured_at.slice(0,16) : '暂无'} · mmHg</small></div><div class="kpi"><h3>最近血糖</h3><p>${uiEscape(latest.glucose ?? '—')}</p><small>mmol/L</small></div><div class="kpi"><h3>最近睡眠</h3><p>${uiEscape(latest.sleep_hours ?? '—')}h</p><small>${uiEscape(latest.source || '暂无数据')}</small></div><div class="kpi"><h3>${healthDays} 天记录</h3><p>${uiEscape(rows.length)}</p><small>${rangeText}</small></div></div>
  <div class="health-stack">${panel(`健康数据趋势 <span class="health-range-switch">${rangeButtons}</span>`,`<div class="health-range-caption">${trendCaption}</div><div class="health-chart-grid">${healthMiniChart('收缩压',rows,'systolic','mmHg',5)}${healthMiniChart('空腹血糖',rows,'glucose','mmol/L',.4)}${healthMiniChart('心率',rows,'heart_rate','次/分',5)}${healthMiniChart('睡眠时长',rows,'sleep_hours','h',.6)}</div>`)}
  ${panel('最近健康记录',`<div class="health-record-grid">${healthCards}</div><div class="modal-foot"><button class="outline-button" data-action="add-vital">＋ 手动录入健康数据</button></div>`)}
  ${panel('用药记录',`<div class="table-panel health-table"><table><thead><tr><th>药品名称</th><th>规格</th><th>用法用量</th><th>服药时间</th><th>状态</th></tr></thead><tbody>${medicationRows}</tbody></table></div>`)}
  ${panel('体检报告',`<div class="health-card-list">${examCards}</div><div class="modal-foot"><button class="outline-button" data-action="upload-exam">上传 PDF / 图片体检报告</button></div>`)}
  ${panel('就诊记录',`<div class="health-card-list">${visitCards}</div>`)}
  ${panel('异常指标预警阈值',`<div class="health-card-list">${(API.acceptanceThresholds || []).map((t)=>`<div class="health-list-card"><strong>${t.name}　${t.value}</strong><small>${t.action}</small></div>`).join('')}</div><div class="modal-foot"><button class="outline-button" data-action="set-threshold">设置预警阈值</button></div>`)}</div></div>`;
}

function renderHelp(view) {
  setHeader('紧急求助');
  const e=currentElder();
  const mine=data.emergencies.filter((x)=>x.elderCode===API.elderCode);
  const emergencyContact=e.contact && e.contactPhone ? `<p style="font-size:16px">${uiEscape(e.contact)}</p><small>${uiEscape(e.relation || '紧急联系人')} · ${uiEscape(e.contactPhone)}</small>` : '<p style="font-size:16px">未绑定</p><small>请先在个人信息中完善紧急联系人</small>';
  const medicalContact=e.hospital ? `<p style="font-size:16px">${uiEscape(e.hospital)}</p><small>个人档案中的定点医疗机构</small>` : '<p style="font-size:16px">未绑定</p><small>请在个人信息中完善定点医疗机构</small>';
  const rows=mine.length?mine.map((x)=>`<tr><td>${uiEscape(x.id)}</td><td>${uiEscape(x.type)}</td><td>${uiEscape(x.source)}</td><td>${uiEscape(x.time)}</td><td>${statusTag(x.status,emergencyStatusClass(x.status))}</td><td>${uiEscape(x.result)}</td><td>${x.canCancel?`<button class="text-button" data-action="cancel-emergency" data-id="${uiEscape(x.id)}">取消求助</button>`:'—'}</td></tr>`).join(''):emptyRow(7,'当前账号还没有紧急求助记录');
  view.innerHTML=pageShell('紧急求助','点击后立即登记并生成站内通知；外部渠道未配置时不发送。登记后 30 秒内且未派单可申请取消',`
    <div class="grid" style="grid-template-columns:repeat(3,1fr);gap:12px;margin-bottom:18px">
      <div class="kpi"><h3>紧急联系人</h3>${emergencyContact}</div>
      <div class="kpi"><h3>社区管理员</h3><p style="font-size:16px">陈雅</p><small>${uiEscape(e.community || '所属社区')} · 社区值班管理员</small></div>
      <div class="kpi"><h3>医疗联系人</h3>${medicalContact}</div>
    </div>
    <div class="field" style="margin-bottom:18px"><label>预设紧急类型 *</label><select id="emergencyType">${EMERGENCY_TYPES.map((t)=>`<option>${t}</option>`).join('')}</select></div>
    <button class="primary-button" style="background:var(--red);width:100%;height:66px;font-size:18px" data-action="panic">! 一键紧急求助</button>
    ${panel('我的求助记录与响应状态',`<div class="table-panel emergency-table-panel"><div class="table-scroll emergency-table-scroll"><table><thead><tr><th>求助编号</th><th>求助类型</th><th>触发方式</th><th>求助时间</th><th>响应状态</th><th>处理结果</th><th>操作</th></tr></thead><tbody>${rows}</tbody></table></div></div><div class="panel-caption" style="margin-top:10px">响应状态流程：待调度 → 已派单 → 服务人员已出发 → 已到达 → 已解决</div>`)}
    ${panel('求助信息内容','<div class="alert-item"><div class="alert-copy"><strong>站内求助登记内容</strong><span>求助人姓名、居住地址、定位信息、求助时间、预设紧急类型（身体不适 / 跌倒 / 火灾 / 煤气泄漏 / 入室危险）。</span></div></div>')}`);
}

function renderElderReviews(view) {
  setHeader('评价与投诉');
  const complaints=API.complaints || [];
  const complaintCards=complaints.length?complaints.map((c)=>`<div class="health-list-card"><strong>${uiEscape(c.complaint_no)} · ${uiEscape(c.complaint_type)} · ${uiEscape(c.status)}</strong><span>关联订单 ${uiEscape(c.order_no)}：${uiEscape(c.complaint_content)}</span><small>${uiEscape(c.admin_reply || '等待管理员处理')}</small></div>`).join(''):'<div class="empty">当前账号还没有投诉记录</div>';
  view.innerHTML=`${panel('待评价与历史评价',`<div class="review-list">${data.reviews.length?data.reviews.map((r)=>`<div class="review-card"><div><strong>${uiEscape(r.service)} · ${uiEscape(r.provider)}</strong><span>${uiEscape(r.date)} · ${r.state==='已评价'?`综合 ${uiEscape(r.overall)} 星`:'等待评价'}</span></div><div class="review-copy">${r.state==='已评价'?`态度 ${uiEscape(r.attitude)} / 质量 ${uiEscape(r.quality)} / 准时 ${uiEscape(r.punctual)}；${uiEscape(r.text || '无文字评价')}`:'服务已完成，现在可以提交评价，提交后订单会进入“已完成”。'}</div><div>${r.state==='待评价'?`<button class="primary-button compact-button" data-action="write-review" data-id="${uiEscape(r.id)}">去评价</button>`:`<button class="text-button" data-action="view-review" data-id="${uiEscape(r.id)}">查看</button>`}</div></div>`).join(''):'<div class="empty">当前账号没有待评价或历史评价订单</div>'}</div>`)}
  ${panel('我的投诉记录',`<div class="health-card-list">${complaintCards}</div><div class="modal-foot"><button class="outline-button" data-action="new-complaint">＋ 提交投诉</button></div>`)}`;
}

function renderGuestHome(view) {
  setHeader('服务首页');
  const activeServices=data.services.filter((s)=>s.status!=='暂停提供');
  view.innerHTML = `<div class="grid stats-grid"><div class="stat-card"><div class="stat-label">养老服务机构</div><div class="stat-value">${uiEscape(data.institutions.length)}</div><div class="stat-meta">社区内可查询机构</div></div><div class="stat-card"><div class="stat-label">居家服务项目</div><div class="stat-value">${uiEscape(activeServices.length)}</div><div class="stat-meta">覆盖五类养老服务</div></div><div class="stat-card"><div class="stat-label">上门服务</div><div class="stat-value">${activeServices.filter((s)=>s.mode==='上门').length}</div><div class="stat-meta">生活照料与医疗护理</div></div><div class="stat-card"><div class="stat-label">紧急救援</div><div class="stat-value">${activeServices.filter((s)=>s.type==='紧急救援类').length}</div><div class="stat-meta">远程监护与紧急响应</div></div></div>
  <div class="grid content-grid"><div>${panel('推荐养老服务机构',`<div class="order-list">${data.institutions.slice(0,3).map((x)=>`<div class="order-row"><div class="order-icon">⌂</div><div class="order-info"><div class="order-name">${uiEscape(x.name)}</div><div class="order-sub">${uiEscape(x.type)} · ${uiEscape(x.address)} · ${uiEscape(x.services)}</div></div>${statusTag(x.status,'green')}</div>`).join('')}</div>`)}</div><div>${panel('常用居家服务',`<div class="order-list">${activeServices.slice(0,5).map((x)=>`<div class="order-row"><div class="order-icon">${uiEscape(iconFor(x.type))}</div><div class="order-info"><div class="order-name">${uiEscape(x.name)}</div><div class="order-sub">${uiEscape(x.type)} · ${uiEscape(x.mode)} · ${uiEscape(x.price)}</div></div></div>`).join('')}</div>`)}</div></div>`;
}

function renderInstitutions(view) {
  setHeader('养老服务机构');
  view.innerHTML = `<div class="toolbar"><div class="search"><span>⌕</span><input data-filter-input placeholder="搜索机构名称、类型、地区或服务项目" /></div></div><div class="table-panel"><div class="table-scroll"><table><thead><tr><th>机构编号</th><th>机构名称</th><th>机构类型</th><th>所在地区</th><th>详细地址</th><th>联系电话</th><th>服务项目</th><th>床位</th><th>机构简介</th></tr></thead><tbody>${data.institutions.map((x)=>`<tr data-search="${uiEscape(x.id)}${uiEscape(x.name)}${uiEscape(x.type)}${uiEscape(x.region)}${uiEscape(x.services)}"><td><strong>${uiEscape(x.id)}</strong></td><td>${uiEscape(x.name)}</td><td>${uiEscape(x.type)}</td><td>${uiEscape(x.region)}</td><td>${uiEscape(x.address)}</td><td>${uiEscape(x.phone)}</td><td>${uiEscape(x.services)}</td><td>${uiEscape(x.beds || '—')}</td><td>${uiEscape(x.intro)}</td></tr>`).join('')}</tbody></table></div></div>`;
}

function renderCatalog(view) {
  setHeader('居家服务项目');
  view.innerHTML = `<div class="toolbar"><div class="search"><span>⌕</span><input data-filter-input placeholder="搜索服务名称、编号、类型或服务方式" /></div></div><div class="table-panel"><div class="table-scroll"><table><thead><tr><th>服务编号</th><th>服务名称</th><th>服务类型</th><th>服务内容</th><th>时长</th><th>收费标准</th><th>服务方式</th><th>适用人群</th><th>预约须知</th></tr></thead><tbody>${data.services.filter((x)=>x.status!=='暂停提供').map((x)=>`<tr data-search="${uiEscape(x.id)}${uiEscape(x.name)}${uiEscape(x.type)}${uiEscape(x.mode)}"><td><strong>${uiEscape(x.id)}</strong></td><td>${uiEscape(x.name)}</td><td>${uiEscape(x.type)}</td><td>${uiEscape(x.desc)}</td><td>${uiEscape(x.duration)}</td><td>${uiEscape(x.price)}</td><td>${uiEscape(x.mode)}</td><td>${uiEscape(x.target)}</td><td>${uiEscape(x.notes)}</td></tr>`).join('')}</tbody></table></div></div>`;
}

function renderProviderHome(view) {
  setHeader('工作台');
  const p=currentProvider();
  const mine=data.orders.filter((o)=>o.providerCode===API.providerCode && o.status!=='已取消');
  const today=new Date().toISOString().slice(0,10);
  const todayOrders=mine.filter((o)=>o.date===today);
  const pending=mine.filter((o)=>['待服务','服务中'].includes(o.status));
  const finished=mine.filter((o)=>['待评价','已完成'].includes(o.status));
  const next=pending.slice().sort((a,b)=>(a.date+a.slot).localeCompare(b.date+b.slot))[0];
  const rating=API.providerReviewStats.average ?? '—';
  view.innerHTML=`<div class="grid stats-grid"><div class="stat-card"><div class="stat-label">已完成 / 待评价</div><div class="stat-value">${String(finished.length).padStart(2,'0')}</div><div class="stat-meta">仅统计当前账号被分配的订单</div></div><div class="stat-card"><div class="stat-label">当前待执行</div><div class="stat-value">${String(pending.length).padStart(2,'0')}</div><div class="stat-meta">待服务 + 服务中</div></div><div class="stat-card"><div class="stat-label">服务对象满意度</div><div class="stat-value">${rating}</div><div class="stat-meta">${uiEscape(API.providerReviewStats.count || 0)} 条当前账号评价</div></div><div class="stat-card alert"><div class="stat-label">今日排班</div><div class="stat-value">${uiEscape(String(todayOrders.length).padStart(2,'0'))}</div><div class="stat-meta">${next?`下一单 <strong>${uiEscape(next.date)} ${uiEscape(next.slot)}</strong>`:'暂无待执行订单'}</div></div></div>
  <div class="grid content-grid"><div>${panel('当前待执行服务',`<div class="order-list">${pending.length?pending.map((o)=>`<div class="order-row"><div class="order-icon">${uiEscape(iconFor(o.type))}</div><div class="order-info"><div class="order-name">${uiEscape(o.service)} · ${uiEscape(o.elder)}</div><div class="order-sub">${uiEscape(o.date)} ${uiEscape(o.slot)} · ${uiEscape(o.address)} · ${uiEscape(o.duration)}</div></div>${statusTag(o.status,orderStatusClass(o.status))}${o.recordReviewStatus?`<br><small>记录：${uiEscape(o.recordReviewStatus)}</small>`:''}</div>`).join(''):'<div class="empty">当前账号没有待执行订单</div>'}</div>`)}</div>
  ${panel('当前服务人员',`<div class="alert-item"><div class="alert-copy"><strong>${uiEscape(p.name || AUTH.user?.display_name || '当前账号')} · ${uiEscape(p.id || API.providerCode)}</strong><span>${uiEscape(p.org || '尚未填写所属机构')} · ${uiEscape(p.skills || '尚未设置服务类型')}。所有订单、评价、投诉和紧急任务均按当前账号隔离。</span></div></div>`)}</div>`;
}

function renderSchedule(view) {
  setHeader('服务排班');
  const mine=data.orders.filter((o)=>o.providerCode===API.providerCode && o.status!=='已取消').sort((a,b)=>a.date.localeCompare(b.date));
  const base=mine.length?new Date(`${mine[0].date}T00:00:00`):new Date();
  const year=base.getFullYear(), month=base.getMonth();
  const daysInMonth=new Date(year,month+1,0).getDate();
  const mondayOffset=(new Date(year,month,1).getDay()+6)%7;
  const cells=Array.from({length:mondayOffset},()=>'<div class="schedule-day blank"></div>');
  for(let d=1;d<=daysInMonth;d++){ const date=`${year}-${String(month+1).padStart(2,'0')}-${String(d).padStart(2,'0')}`; const orders=mine.filter((o)=>o.date===date); cells.push(`<div class="schedule-day ${orders.length?'busy':''}"><strong>${d}</strong>${orders.map((o)=>`<div class="schedule-order"><span>${uiEscape(o.slot.replace(/^(上午|下午|晚上) /,''))}</span><b>${uiEscape(o.service)} · ${uiEscape(o.elder)}</b><small>${uiEscape(o.status)}</small></div>`).join('')}</div>`); }
  const provider=currentProvider();
  view.innerHTML=`<div class="panel"><div class="panel-head"><div class="panel-title">${year} 年 ${month+1} 月服务排班日历</div><div class="panel-caption">仅显示分配给 ${uiEscape(provider.name || AUTH.user?.display_name || API.providerCode)} 的订单</div></div><div class="schedule-week">${['周一','周二','周三','周四','周五','周六','周日'].map((x)=>`<span>${x}</span>`).join('')}</div><div class="schedule-grid">${cells.join('')}</div></div>
  ${panel('排班明细',`<div class="table-panel"><div class="table-scroll"><table><thead><tr><th>日期</th><th>预约时间</th><th>预约地址</th><th>服务对象</th><th>服务内容</th><th>预计时长</th><th>状态</th></tr></thead><tbody>${mine.length?mine.map((o)=>`<tr><td>${uiEscape(o.date)}</td><td>${uiEscape(o.slot)}</td><td>${uiEscape(o.address)}</td><td>${uiEscape(o.elder)}</td><td>${uiEscape(o.service)}</td><td>${uiEscape(o.duration)}</td><td>${statusTag(o.status,orderStatusClass(o.status))}${o.recordReviewStatus?`<br><small>记录：${uiEscape(o.recordReviewStatus)}</small>`:''}</td></tr>`).join(''):emptyRow(7,'当前账号没有已分配订单')}</tbody></table></div></div>`)}`;
}

function renderAssigned(view) {
  setHeader('执行服务订单');
  const mine=data.orders.filter((o)=>o.providerCode===API.providerCode);
  const orderRows=mine.length?mine.map((o)=>`<tr><td><strong>${uiEscape(o.id)}</strong></td><td>${uiEscape(o.elder)}</td><td>${uiEscape(o.service)}<br><span class="panel-caption">${uiEscape(o.type)}</span></td><td>${uiEscape(o.date)}<br><span class="panel-caption">${uiEscape(o.slot)}</span></td><td>${uiEscape(o.address)}</td><td>${uiEscape(o.remark || '无')}</td><td>${statusTag(o.status,orderStatusClass(o.status))}${o.recordReviewStatus?`<br><small>记录：${uiEscape(o.recordReviewStatus)}</small>`:''}<br><span class="panel-caption">${o.assignmentStatus==='DEPARTED'?'已出发':o.assignmentStatus==='ARRIVED'?'已到达':o.assignmentStatus==='FINISHED'?'服务已结束':''}</span></td><td><div class="table-actions">${o.status==='待服务'?`<button class="text-button" data-action="start-service" data-id="${uiEscape(o.id)}">出发</button>`:''}${o.status==='服务中'&&o.assignmentStatus==='DEPARTED'?`<button class="text-button" data-action="arrive-service" data-id="${uiEscape(o.id)}">到达</button>`:''}${o.status==='服务中'?`<button class="text-button" data-action="finish-service" data-id="${uiEscape(o.id)}">完成</button>`:''}${o.assignmentStatus==='FINISHED'&&!o.recordCount?`<button class="text-button" data-action="record-order" data-id="${uiEscape(o.id)}">填写记录</button>`:''}<button class="text-button" data-action="view-order" data-id="${uiEscape(o.id)}">详情</button></div></td></tr>`).join(''):emptyRow(8,'当前账号没有被分配的服务订单');
  view.innerHTML=`<div class="table-panel"><div class="table-scroll"><table><thead><tr><th>订单编号</th><th>服务对象</th><th>服务类型 / 内容</th><th>预约时段</th><th>居住地址</th><th>特殊需求</th><th>订单状态</th><th>操作</th></tr></thead><tbody>${orderRows}</tbody></table></div></div>
  ${panel('订单详情与老人注意事项','<div class="alert-item"><div class="alert-mark" style="background:var(--amber-pale);color:var(--amber)">i</div><div class="alert-copy"><strong>当前页面只显示分配给本账号的订单</strong><span>点击“出发”会记录出发时间和定位；“到达”记录到达时间；“完成”记录结束时间并推进到待评价。</span></div></div>')}
  ${panel('服务过程记录（完成后填写）',`<div class="table-panel"><table><thead><tr><th>订单编号</th><th>实际开始</th><th>实际结束</th><th>完成情况</th><th>耗材清单</th><th>保存状态</th></tr></thead><tbody>${data.serviceRecords.length?data.serviceRecords.map((r)=>`<tr><td>${uiEscape(r.order)}</td><td>${uiEscape(r.start)}</td><td>${uiEscape(r.end)}</td><td>${uiEscape(r.completion)}</td><td>${uiEscape(r.materials)}</td><td>${statusTag('已写入后端','green')}</td></tr>`).join(''):emptyRow(6,'当前账号还没有提交服务记录')}</tbody></table></div>`)}`;
}

function renderProviderEmergencyTasks(view) {
  setHeader('紧急任务');
  const tasks=data.emergencies.filter((e)=>e.providerCode===API.providerCode);
  const active=tasks.filter((e)=>!['已解决','误报取消'].includes(e.status));
  view.innerHTML = `<div class="grid kpi-grid"><div class="kpi"><h3>当前紧急任务</h3><p>${uiEscape(String(active.length).padStart(2,'0'))}</p><small>管理员派给当前服务人员</small></div><div class="kpi"><h3>已派单</h3><p>${tasks.filter((e)=>e.status==='已派单').length}</p><small>等待出发</small></div><div class="kpi"><h3>响应途中</h3><p>${tasks.filter((e)=>e.status==='服务人员已出发').length}</p><small>已记录出发位置</small></div><div class="kpi"><h3>已到达</h3><p>${tasks.filter((e)=>e.status==='已到达').length}</p><small>等待填写处理结果</small></div></div>
  <div class="table-panel"><div class="table-scroll"><table><thead><tr><th>求助编号</th><th>老人</th><th>紧急类型</th><th>地址 / 定位</th><th>发起时间</th><th>当前状态</th><th>调度说明</th><th>操作</th></tr></thead><tbody>${tasks.length ? tasks.map((e)=>`<tr><td><strong>${uiEscape(e.id)}</strong></td><td>${uiEscape(e.name)}</td><td>${uiEscape(e.type)}</td><td>${uiEscape(e.address)}<br><span class="panel-caption">${uiEscape(e.location)}</span></td><td>${uiEscape(e.time)}</td><td>${statusTag(e.status,emergencyStatusClass(e.status))}</td><td>${uiEscape(e.dispatchNote || '—')}</td><td><div class="table-actions">${e.status==='已派单' ? `<button class="text-button" data-action="depart-emergency" data-id="${uiEscape(e.id)}">出发</button>` : ''}${e.status==='服务人员已出发' ? `<button class="text-button" data-action="arrive-emergency" data-id="${uiEscape(e.id)}">到达</button>` : ''}${e.status==='已到达' ? `<button class="text-button" data-action="finish-emergency" data-id="${uiEscape(e.id)}">完成处理</button>` : ''}${['已解决','误报取消'].includes(e.status) ? '—' : ''}</div></td></tr>`).join('') : '<tr><td colspan="8" class="empty">当前没有分配给你的紧急任务</td></tr>'}</tbody></table></div></div>`;
}

function renderServiceRecords(view) {
  setHeader('服务记录');
  const orders=data.orders.filter((o)=>o.providerCode===API.providerCode && o.assignmentStatus==='FINISHED' && !o.recordCount);
  const selected=orders.find((o)=>o.id===API.recordOrder) || orders[0];
  if (selected) API.recordOrder=selected.id;
  const startValue=(selected?.arriveAt || selected?.departAt || '').replace(' ','T').slice(0,16);
  const endValue=(selected?.finishAt || '').replace(' ','T').slice(0,16);
  view.innerHTML = pageShell('服务记录填写', '服务完成后选择对应订单填写服务过程，提交后写入数据库', `<div class="form-grid">
    <div class="field"><label>关联订单</label><select name="recordOrder">${orders.length?orders.map((o)=>`<option value="${uiEscape(o.id)}" ${o.id===API.recordOrder?'selected':''}>${uiEscape(o.id)} · ${uiEscape(o.service)} · ${uiEscape(o.elder)}</option>`).join(''):'<option value="">暂无待填写记录的已完成订单</option>'}</select></div>
    <div class="field"><label>完成情况</label><select name="completion"><option>全部完成</option><option>部分完成</option><option>未执行</option></select></div>
    <div class="field"><label>实际开始时间</label><input name="start" type="datetime-local" value="${startValue}" /></div>
    <div class="field"><label>实际结束时间</label><input name="end" type="datetime-local" value="${endValue}" /></div>
    <div class="field full"><label>服务过程描述</label><textarea name="process" placeholder="填写服务过程、老人反馈及注意事项"></textarea></div>
    <div class="field full"><label>老人身体状况观察</label><textarea name="observation" placeholder="如：血压 132/80mmHg，精神状态良好，午餐进食正常"></textarea></div>
    <div class="field full"><label>使用耗材清单</label><input name="materials" placeholder="如：一次性手套 2 副、消毒湿巾 1 包" /></div>
    <div class="field full"><label>现场照片说明</label><input name="photos" placeholder="如：服务前、服务后、签字单共 3 张" /></div>
    <div class="field full"><label>助医专项</label><textarea name="medical" placeholder="诊断建议、用药调整、复诊提醒"></textarea></div>
    <div class="field full"><label>康复专项</label><textarea name="rehab" placeholder="训练项目、训练强度、老人耐受情况"></textarea></div>
  </div><div class="modal-foot"><button class="outline-button" data-action="cancel-book">取消</button><button class="primary-button" data-action="submit-record" ${orders.length?'':'disabled'}>提交服务记录</button></div>`);
}

function renderProviderProfile(view) {
  setHeader('人员信息');
  const p=currentProvider();
  view.innerHTML=pageShell('服务人员信息管理','修改后点击保存，资料会 POST 到后端数据库',`
    ${panel('基本信息',`<div class="form-grid"><div class="field"><label>姓名</label><input name="full_name" value="${uiEscape(p.name || '')}" /></div><div class="field"><label>性别</label><input name="gender" value="${uiEscape(p.gender || '')}" /></div><div class="field"><label>身份证号</label><input name="id_card" value="${uiEscape(p.idCard || '')}" /></div><div class="field"><label>联系电话</label><input name="phone" value="${uiEscape(p.phone || '')}" /></div><div class="field"><label>工号</label><input value="${uiEscape(p.id || API.providerCode)}" disabled /></div><div class="field"><label>所属服务机构</label><input name="organization" value="${uiEscape(p.org || '')}" /></div><div class="field"><label>服务年限</label><input name="service_years" type="number" min="0" value="${uiEscape(p.years || 0)}" /></div><div class="field"><label>账号状态</label><select name="employment_status">${['在职','请假','离职'].map((x)=>`<option ${x===(p.status || '在职')?'selected':''}>${x}</option>`).join('')}</select></div></div>`)}
    ${panel('服务类型与资质证书',`<div class="form-grid"><div class="field full"><label>服务类型权限</label><input name="skills" value="${uiEscape(p.skills || '')}" /></div><div class="field full"><label>资质证书</label><input name="certificates" value="${uiEscape(p.certs || '')}" /></div><div class="field full"><label>健康证明有效期</label><input name="health_certificate" value="${uiEscape(p.health || '')}" /></div></div>`)}
    ${panel('服务区域与个人简介',`<div class="form-grid"><div class="field full"><label>可服务社区范围</label><input name="service_areas" value="${uiEscape(p.areas || '')}" /></div><div class="field full"><label>个人简介</label><textarea name="profile_intro">${uiEscape(p.intro || '')}</textarea></div></div>`)}
    ${panel('培训记录',`<div class="field full"><label>培训名称、时间、考核结果（每行一条）</label><textarea name="training_records">${uiEscape(p.training || '')}</textarea></div><div class="modal-foot"><button class="primary-button" data-action="edit-profile">保存资料</button></div>`)}
  `);
}

function renderProviderReviews(view) {
  setHeader('评价与申诉');
  const p=currentProvider();
  const reviews=API.providerReviews || [];
  const complaints=API.complaints || [];
  const stats=API.providerReviewStats || {};
  const goodRate=stats.count?`${stats.good_rate}%`:'—';
  const avg=stats.average ?? '—';
  const served=data.orders.filter((o)=>o.providerCode===API.providerCode && ['待评价','已完成'].includes(o.status)).length;
  const complaintRows=complaints.length?complaints.map((c)=>`<tr><td>${uiEscape(c.complaint_no)}</td><td>${uiEscape(c.complaint_type)}</td><td>${uiEscape(c.complaint_content)}</td><td>${statusTag(c.status,c.status==='已处理'?'green':'amber')}</td><td>${c.provider_appeal?`<span class="panel-caption">已申诉：${uiEscape(c.provider_appeal)}</span>`:`<button class="text-button" data-action="appeal-complaint" data-id="${uiEscape(c.complaint_no)}">提交申诉说明</button>`}</td></tr>`).join(''):emptyRow(5,'当前账号没有相关投诉工单');
  view.innerHTML=`<div class="grid kpi-grid"><div class="kpi"><h3>综合评分</h3><p>${avg}</p><small>${uiEscape(stats.count || 0)} 条当前账号真实评价</small></div><div class="kpi"><h3>已服务订单</h3><p>${String(served).padStart(2,'0')} 单</p><small>待评价 + 已完成</small></div><div class="kpi"><h3>好评率</h3><p>${goodRate}</p><small>综合评分 ≥ 4 星</small></div><div class="kpi"><h3>当前账号</h3><p style="font-size:20px">${uiEscape(p.name || AUTH.user?.display_name || '—')}</p><small>${uiEscape(p.id || API.providerCode)}</small></div></div>
  ${panel('服务对象真实评价',`<div class="review-list">${reviews.length?reviews.map((r)=>`<div class="review-card"><div><strong>${uiEscape(r.elder_name)} · ${uiEscape(r.service_name)}</strong><span>${uiEscape((r.created_at || r.service_date || '').slice(0,16))} · 综合 ${uiEscape(r.overall ?? '—')} 星</span></div><div class="review-copy">态度 ${uiEscape(r.attitude_score)} / 质量 ${uiEscape(r.quality_score)} / 准时 ${uiEscape(r.punctual_score)}；${uiEscape(r.review_text || '无文字评价')}</div><div><span class="panel-caption">订单 ${uiEscape(r.order_no)}</span></div></div>`).join(''):'<div class="empty">当前账号还没有服务对象评价</div>'}</div>`)}
  ${panel('投诉工单与申诉',`<div class="table-panel"><table><thead><tr><th>投诉编号</th><th>投诉类型</th><th>投诉内容</th><th>处理状态</th><th>操作</th></tr></thead><tbody>${complaintRows}</tbody></table></div>`)}`;
}

function showModal(title, body, onSubmit) {
  if (emergencyCountdown) { clearInterval(emergencyCountdown); emergencyCountdown = null; }
  $('#modalRoot').innerHTML = `<div class="modal"><div class="modal-head"><h2>${uiEscape(title)}</h2><button class="close-button" data-action="close-modal" type="button">×</button></div><div class="modal-form">${body}</div><div class="modal-foot"><button class="outline-button" data-action="close-modal" type="button">取消</button><button class="primary-button" data-action="modal-submit" type="button">确认</button></div></div>`;
  $('#modalRoot').classList.add('show');
  $('#modalRoot').dataset.submit = onSubmit || '';
  bindActions();
  renderAppointmentCalendar();
  if (onSubmit === 'new-order') bindAdminOrderForm();
}

function closeModal() {
  if (emergencyCountdown) { clearInterval(emergencyCountdown); emergencyCountdown = null; }
  $('#modalRoot').classList.remove('show');
  $('#modalRoot').innerHTML = '';
  delete $('#modalRoot').dataset.submit;
}

function toast(message) {
  const el = $('#toast');
  el.textContent = message;
  el.classList.add('show');
  setTimeout(() => el.classList.remove('show'), 2400);
}

function readModalForm() {
  const values = {};
  $('#modalRoot').querySelectorAll('[name]').forEach((f) => { values[f.name] = f.value.trim(); });
  return values;
}

async function submitNewEntity(type) {
  const values = readModalForm();
  if (type === 'new-order') {
    if (!values.elderCode || !values.serviceCode || !values.date) { toast('请选择服务对象、服务项目并选择预约日期'); return; }
    const elder = API.options?.elders?.find((e) => e.elder_code === values.elderCode);
    const serviceCode = values.serviceCode;
    if (!elder) return toast('服务对象不存在');
    try {
      const result = await apiRequest('/api/orders', { method: 'POST', body: {
        elder_code: elder.elder_code, service_code: serviceCode, service_date: values.date,
        service_latitude: values.serviceLatitude ? Number(values.serviceLatitude) : null, service_longitude: values.serviceLongitude ? Number(values.serviceLongitude) : null,
        time_slot: values.slot || '上午 09:00-11:00', duration_minutes: parseDurationMinutes(values.duration),
        service_address: values.address || elder.address, special_requirements: [values.remark, values.preferredProvider ? `意向服务人员：${values.preferredProvider}` : ''].filter(Boolean).join('；'),
        meal_type: values.meal || '', delivery_time: values.delivery || '', medical_need: values.medicalNeed || '', symptom_description: values.symptom || ''
      }});
      closeModal();
      await Promise.all([refreshOrders(),refreshDashboardStats()]);
      renderPage();
      return toast(`预约已写入数据库（${result.order_no}）`);
    } catch (error) { return toast(`新建预约失败：${error.message}`); }
  }
  if (type === 'new-elder') {
    const required=[['name','姓名'],['gender','性别'],['age','年龄'],['phone','联系电话'],['level','自理能力'],['living','居住情况']];
    const missing=required.filter(([key])=>!String(values[key] || '').trim()).map(([,label])=>label);
    if (missing.length) return toast(`请填写必填项：${missing.join('、')}`);
    try {
      const result=await apiRequest('/api/elders',{method:'POST',body:{
        full_name:values.name,gender:values.gender,age:Number(values.age),phone:values.phone,community:values.community,address:values.address,
        id_card:values.idCard,self_care_level:values.level,living_situation:values.living,disease_history:values.disease,allergy_history:values.allergy,
        medical_type:values.medicalType,medical_card_no:values.medicalCard,emergency_contact_name:values.contact,emergency_contact_relation:values.relation,
        emergency_contact_phone:values.contactPhone,emergency_backup_phone:values.backupPhone,language_preference:values.habit,diet_requirement:values.diet,
        visit_time_preference:values.timePref,elder_tags:values.tag
      }});
      closeModal();
      await Promise.all([refreshElderDirectory(),refreshDashboardStats()]);
      API.options=await apiRequest('/api/options');
      renderPage();
      return toast(`${result.message}（${result.profile.elder_code}，${result.profile.service_priority}）`);
    } catch(error) { return toast(`新增档案失败：${error.message}`); }
  }
  if (type === 'new-provider') {
    if (!values.name) return toast('请先填写服务人员姓名');
    try {
      const result=await apiRequest('/api/providers',{method:'POST',body:{full_name:values.name,gender:values.gender,id_card:values.idCard,phone:values.phone,organization:values.org,skills:values.skills,service_areas:values.areas,service_years:Number(values.years||0),health_certificate:values.health,certificates:values.certs,profile_intro:values.intro}});
      closeModal();
      await Promise.all([refreshProviderDirectory(),refreshDashboardStats()]);
      API.options=await apiRequest('/api/options');
      renderPage();
      return toast(`${result.message}（${result.profile.provider_code}）`);
    } catch(error) { return toast(`录入失败：${error.message}`); }
  }
  if (type === 'new-service') {
    if (!values.name) { toast('请先填写服务名称'); return; }
    data.services.unshift({ id: `FW-${Date.now().toString().slice(-3)}`, name: values.name, type: values.type || '生活照料类', mode: values.mode || '上门', desc: values.desc || '暂无内容说明', price: values.price || '待定', duration: values.duration || '待定', qualification: values.qualification || '无', target: values.target || '全体老人', notes: values.notes || '无', status: values.status || '正常提供' });
  }
  if (type === 'new-notice') {
    if (!values.title) return toast('请填写公告标题');
    try {
      const result=await apiRequest('/api/notices',{method:'POST',body:{title:values.title,content:values.content || '',publisher:'陈雅'}});
      closeModal();
      await refreshNotices();
      renderPage();
      return toast(result.message);
    } catch(error) { return toast(`公告发布失败：${error.message}`); }
  }
  closeModal();
  renderPage();
  toast('新增成功（当前模块为前端演示数据）');
}


function bindAdminOrderForm() {
  const elder=$('#adminOrderElder');
  const type=$('#adminOrderType');
  const service=$('#adminOrderService');
  if (!elder || !type || !service) return;
  const renderServices=()=>{
    const options=(API.options?.services || []).filter((x)=>x.service_type===type.value);
    service.innerHTML=options.map((x)=>`<option value="${uiEscape(x.service_code)}" data-type="${uiEscape(x.service_type)}" data-name="${uiEscape(x.service_name)}" data-duration="${uiEscape(x.duration_minutes)}">${uiEscape(x.service_name)}</option>`).join('');
  };
  const sync=()=>{
    const eo=elder.selectedOptions[0];
    const so=service.selectedOptions[0];
    if ($('#adminOrderAddress') && !$('#adminOrderAddress').dataset.touched) $('#adminOrderAddress').value=eo?.dataset.address || '';
    $('#adminOrderDuration').value=so?.dataset.duration ? `${so.dataset.duration} 分钟` : '';
    const name=so?.dataset.name || '';
    $('#adminMealExtra').style.display=name==='助餐服务'?'block':'none';
    $('#adminMedicalExtra').style.display=name==='助医服务'?'block':'none';
  };
  elder.addEventListener('change',sync);
  type.addEventListener('change',()=>{
    renderServices();
    sync();
  });
  service.addEventListener('change',sync);
  $('#adminOrderAddress').addEventListener('input',()=>{$('#adminOrderAddress').dataset.touched='1';});
  renderServices();
  sync();
}
function formatDate(year, month, day) {
  return `${year}-${String(month + 1).padStart(2, '0')}-${String(day).padStart(2, '0')}`;
}

function renderAppointmentCalendar() {
  const calendar = $('#appointmentCalendar');
  if (!calendar) return;
  const year = calendarViewDate.getFullYear();
  const month = calendarViewDate.getMonth();
  const firstDay = new Date(year, month, 1).getDay();
  const leading = (firstDay + 6) % 7;
  const days = new Date(year, month + 1, 0).getDate();
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  const cells = Array.from({ length: leading }, () => '<span class="calendar-day empty-day"></span>');
  for (let day = 1; day <= days; day += 1) {
    const dateValue = formatDate(year, month, day);
    const cellDate = new Date(year, month, day);
    const disabled = cellDate < today;
    cells.push(`<button type="button" class="calendar-day ${selectedAppointmentDate === dateValue ? 'selected' : ''} ${disabled ? 'disabled' : ''}" data-calendar-date="${dateValue}" ${disabled ? 'disabled' : ''}>${day}</button>`);
  }
  calendar.innerHTML = `<div class="calendar-head"><button type="button" class="calendar-nav" data-calendar-move="-1" title="上个月">‹</button><strong>${year} 年 ${month + 1} 月</strong><button type="button" class="calendar-nav" data-calendar-move="1" title="下个月">›</button></div><div class="calendar-week">${['一','二','三','四','五','六','日'].map((day) => `<span>${day}</span>`).join('')}</div><div class="calendar-grid">${cells.join('')}</div>`;
  bindCalendarActions();
}

function bindCalendarActions() {
  document.querySelectorAll('[data-calendar-move]').forEach((button) => button.addEventListener('click', () => {
    calendarViewDate = new Date(calendarViewDate.getFullYear(), calendarViewDate.getMonth() + Number(button.dataset.calendarMove), 1);
    renderAppointmentCalendar();
  }));
  document.querySelectorAll('[data-calendar-date]').forEach((button) => button.addEventListener('click', () => {
    selectedAppointmentDate = button.dataset.calendarDate;
    const input = $('#appointmentDate');
    if (input) input.value = selectedAppointmentDate;
    renderAppointmentCalendar();
  }));
}

function newEntityForm(type) {
  if (type === 'new-order') {
    selectedAppointmentDate='';
    calendarViewDate=new Date();
    const elders=(API.options?.elders || []).map((e)=>`<option value="${uiEscape(e.elder_code)}" data-address="${uiEscape(e.address)}">${uiEscape(e.full_name)}（${uiEscape(e.elder_code)}）</option>`).join('');
    const firstType=SERVICE_TYPES[0];
    const services=(API.options?.services || []).filter((x)=>x.service_type===firstType).map((x)=>`<option value="${uiEscape(x.service_code)}" data-type="${uiEscape(x.service_type)}" data-name="${uiEscape(x.service_name)}" data-duration="${uiEscape(x.duration_minutes)}">${uiEscape(x.service_name)}</option>`).join('');
    const providers=data.providers.filter((p)=>p.status==='在职').map((p)=>`<option value="${uiEscape(p.id)}">${uiEscape(p.name)} · ${uiEscape(p.skills)}</option>`).join('');
    return `<div class="form-grid"><div class="field"><label>服务对象 *</label><select id="adminOrderElder" name="elderCode">${elders}</select></div><div class="field"><label>服务类型 *</label><select id="adminOrderType" name="serviceType">${SERVICE_TYPES.map((t)=>`<option>${t}</option>`).join('')}</select></div><div class="field full"><label>服务项目 *</label><select id="adminOrderService" name="serviceCode">${services}</select></div><div class="field"><label>意向服务人员（可选）</label><select name="preferredProvider"><option value="">无指定，由系统推荐</option>${providers}</select></div><div class="field"><label>服务时长</label><input id="adminOrderDuration" name="duration" /></div><div class="field full"><label>预约日期 *</label><input id="appointmentDate" name="date" type="text" placeholder="请在下方日历选择日期" readonly /><div id="appointmentCalendar" class="appointment-calendar"></div></div><div class="field"><label>预约时段</label><select name="slot"><option>上午 09:00-11:00</option><option>上午 10:00-12:00</option><option>下午 13:00-15:00</option><option>下午 15:00-17:00</option><option>晚上 18:00-20:00</option><option>晚上 19:00-21:00</option></select></div><div class="field full"><label>服务地址</label><div class="location-row"><input id="adminOrderAddress" name="address" placeholder="默认老人居住地址，可手动填写" /><button type="button" class="outline-button" data-action="capture-book-location">获取定位</button></div><input name="serviceLatitude" type="hidden" /><input name="serviceLongitude" type="hidden" /><div id="bookLocationText" class="panel-caption">未获取坐标</div></div><div class="field full"><label>特殊需求说明</label><textarea name="remark" placeholder="如需要男护理员、会方言的服务人员、携带特定设备"></textarea></div><div id="adminMealExtra" class="field full" style="display:none"><label>助餐服务：餐食类型与配送时间</label><div class="form-grid"><div class="field"><label>餐食类型</label><select name="meal">${MEAL_TYPES.map((m)=>`<option>${m}</option>`).join('')}</select></div><div class="field"><label>配送时间</label><select name="delivery"><option>11:00-11:30</option><option>11:30-12:00</option><option>17:00-17:30</option></select></div></div></div><div id="adminMedicalExtra" class="field full" style="display:none"><label>助医服务：就诊需求与症状描述</label><div class="form-grid"><div class="field"><label>就诊需求</label><select name="medicalNeed">${MEDICAL_NEEDS.map((m)=>`<option>${m}</option>`).join('')}</select></div><div class="field"><label>症状描述</label><input name="symptom" placeholder="请简要描述症状" /></div></div></div></div>`;
  }
  if (type === 'new-notice') return `<div class="form-grid"><div class="field full"><label>公告标题 *</label><input name="title" placeholder="请输入公告标题" /></div><div class="field full"><label>公告内容</label><textarea name="content" placeholder="请输入公告正文"></textarea></div></div>`;
  if (type === 'new-elder') return `<div class="form-grid"><div class="field"><label>姓名 *</label><input name="name" placeholder="请输入老人姓名" /></div><div class="field"><label>性别 *</label><select name="gender"><option>女</option><option>男</option></select></div><div class="field"><label>年龄 *</label><input name="age" type="number" min="60" max="120" placeholder="请输入年龄" /></div><div class="field"><label>联系电话 *</label><input name="phone" placeholder="请输入联系电话" /></div><div class="field"><label>自理能力 *</label><select name="level"><option>完全自理</option><option>轻度失能</option><option>中度失能</option><option>重度失能</option></select></div><div class="field"><label>居住情况 *</label><select name="living"><option>独居</option><option>与配偶同住</option><option>与子女同住</option><option>养老机构居住</option></select></div><div class="field"><label>身份证号</label><input name="idCard" placeholder="请输入身份证号" /></div><div class="field"><label>所属社区</label><input name="community" value="青云社区" /></div><div class="field full"><label>居住地址</label><input name="address" placeholder="请输入居住地址" /></div><div class="field"><label>既往病史</label><input name="disease" placeholder="如 高血压、糖尿病" /></div><div class="field"><label>过敏史</label><input name="allergy" placeholder="如 青霉素过敏" /></div><div class="field"><label>医保类型</label><input name="medicalType" placeholder="如 城镇职工医保" /></div><div class="field"><label>医保卡号</label><input name="medicalCard" placeholder="请输入医保卡号" /></div><div class="field"><label>紧急联系人</label><input name="contact" placeholder="请输入联系人姓名" /></div><div class="field"><label>关系</label><input name="relation" placeholder="如 女儿" /></div><div class="field"><label>紧急联系电话</label><input name="contactPhone" placeholder="请输入联系电话" /></div><div class="field"><label>备用电话</label><input name="backupPhone" placeholder="请输入备用电话" /></div><div class="field"><label>习惯用语</label><input name="habit" placeholder="如 本地方言" /></div><div class="field"><label>特殊饮食要求</label><input name="diet" placeholder="如 低盐饮食" /></div><div class="field"><label>上门时间偏好</label><input name="timePref" placeholder="如 上午 09:00-11:00" /></div><div class="field"><label>老人标签</label><select name="tag">${ELDER_TAGS.map((t)=>`<option>${t}</option>`).join('')}<option>普通</option></select></div><div class="field"><label>服务优先级</label><input value="保存后根据年龄和自理能力自动计算" disabled /></div></div>`;
  if (type === 'new-provider') return `<div class="form-grid"><div class="field"><label>姓名 *</label><input name="name" placeholder="请输入服务人员姓名" /></div><div class="field"><label>性别</label><select name="gender"><option>女</option><option>男</option></select></div><div class="field"><label>工号</label><input name="id" placeholder="系统可自动生成" /></div><div class="field"><label>身份证号</label><input name="idCard" placeholder="请输入身份证号" /></div><div class="field"><label>联系电话</label><input name="phone" placeholder="请输入联系电话" /></div><div class="field"><label>所属服务机构</label><input name="org" placeholder="请输入服务机构" /></div><div class="field full"><label>服务类型权限</label><input name="skills" placeholder="如 生活照料 / 医疗护理" /></div><div class="field full"><label>服务区域（可多个）</label><input name="areas" placeholder="如 青云社区、翠湖社区" /></div><div class="field"><label>服务年限</label><input name="years" type="number" min="0" placeholder="请输入服务年限" /></div><div class="field"><label>健康证明有效期</label><input name="health" placeholder="如 有效期至 2027-05-20" /></div><div class="field full"><label>资质证书（编号及扫描件）</label><input name="certs" placeholder="如 养老护理员证（高级）" /></div><div class="field full"><label>个人简介</label><textarea name="intro" placeholder="请输入个人简介"></textarea></div></div>`;
  return `<div class="form-grid"><div class="field"><label>服务名称 *</label><input name="name" placeholder="请输入服务名称" /></div><div class="field"><label>服务类型</label><select name="type">${SERVICE_TYPES.map((t) => `<option>${t}</option>`).join('')}</select></div><div class="field"><label>服务方式</label><select name="mode"><option>上门</option><option>站点</option><option>远程</option></select></div><div class="field"><label>服务状态</label><select name="status"><option>正常提供</option><option>暂停提供</option><option>限时促销</option></select></div><div class="field"><label>收费标准</label><input name="price" placeholder="例如 ¥60 / 次" /></div><div class="field"><label>服务时长</label><input name="duration" placeholder="例如 90 分钟" /></div><div class="field"><label>资质要求</label><input name="qualification" placeholder="例如 护理员证" /></div><div class="field"><label>适用人群</label><input name="target" placeholder="例如 失能老人" /></div><div class="field full"><label>预约须知</label><input name="notes" placeholder="例如 需提前 1 天预约" /></div><div class="field full"><label>内容说明</label><textarea name="desc" placeholder="请输入服务内容说明"></textarea></div></div>`;
}

async function startEmergencyCountdown(emergencyType) {
  const elder=currentElder();
  const loc=await getBrowserLocation();
  pendingEmergency = {
    name: elder.name, type: emergencyType, source: 'APP一键呼叫', address: elder.address,
    latitude: loc.latitude, longitude: loc.longitude, location: loc.latitude==null ? '定位未授权/不可用' : `${loc.longitude.toFixed(6)},${loc.latitude.toFixed(6)}`,
    time: new Date().toLocaleString('zh-CN', { hour12: false }).replace(/\//g, '-'),
    status: '待调度', notify: '紧急联系人、社区管理员、家庭医生', provider: '待指派', result: '待处理'
  };
  let remaining = 30;
  const body = `<p style="font-size:13px;color:#667570;line-height:1.8">求助类型：<strong>${emergencyType}</strong><br>系统将通知紧急联系人、社区管理员和签约家庭医生，求助信息包含求助人姓名、居住地址、定位信息、求助时间与紧急类型。<br>定位：<strong>${pendingEmergency.location}</strong></p><div style="text-align:center;margin:18px 0"><div style="font-size:44px;font-weight:700;color:var(--red)" id="countdownText">30</div><div class="panel-caption">秒后可取消本次求助</div></div><div class="alert-item"><div class="alert-mark" style="background:var(--amber-pale);color:var(--amber)">i</div><div class="alert-copy"><strong>误触发请点击“立即取消求助”</strong><span>取消后本次求助不会计入统计，也不会继续通知救援人员。</span></div></div>`;
  $('#modalRoot').innerHTML = `<div class="modal"><div class="modal-head"><h2>紧急求助已发起</h2><button class="close-button" data-action="close-modal" type="button">×</button></div><div class="modal-form">${body}</div><div class="modal-foot"><button class="outline-button" data-action="cancel-emergency-now" style="color:var(--red);border-color:#f0d7d4">立即取消求助</button><button class="primary-button" data-action="confirm-emergency-now">确认求助</button></div></div>`;
  $('#modalRoot').classList.add('show');
  bindActions();
  emergencyCountdown = setInterval(() => {
    remaining -= 1;
    const text = $('#countdownText');
    if (text) text.textContent = String(remaining);
    if (remaining <= 0) {
      clearInterval(emergencyCountdown);
      emergencyCountdown = null;
      confirmEmergency();
    }
  }, 1000);
}

async function confirmEmergency() {
  if (emergencyCountdown) { clearInterval(emergencyCountdown); emergencyCountdown = null; }
  if (!pendingEmergency) { closeModal(); return; }
  const pending={...pendingEmergency}; pendingEmergency=null;
  try {
    const result=await apiRequest('/api/emergencies',{method:'POST',body:{elder_code:API.elderCode,emergency_type:pending.type,source:pending.source,address:pending.address,latitude:pending.latitude,longitude:pending.longitude,notify_targets:pending.notify}});
    await refreshEmergencies(); closeModal(); renderPage(); toast(`${result.message}（${result.emergency_no}）`);
  } catch(error) { closeModal(); toast(`求助发送失败：${error.message}`); }
}

async function handleAction(action, el) {
  const id = el?.dataset.id;
  if (action === 'close-modal' || action === 'cancel-book') return closeModal();
  if (action === 'health-range') {
    const days=Number(el?.dataset.days || 30);
    await refreshHealth(days);
    renderPage();
    return;
  }
  if (action === 'capture-book-location') { const loc=await getBrowserLocation(); const root=$('#modalRoot').classList.contains('show')?$('#modalRoot'):$('#viewRoot'); const lat=root.querySelector('[name=serviceLatitude]'); const lon=root.querySelector('[name=serviceLongitude]'); if (lat) lat.value=loc.latitude ?? ''; if (lon) lon.value=loc.longitude ?? ''; const text=root.querySelector('#bookLocationText'); const message=loc.latitude==null?locationMessage(loc.source):`已定位：${loc.latitude.toFixed(6)}, ${loc.longitude.toFixed(6)}`; if (text) text.textContent=message; return toast(loc.latitude==null?message:'定位已获取'); }
  if (action === 'logout') { try { await apiRequest('/api/auth/logout',{method:'POST'}); } catch (_) {} clearAuthSession(); AUTH.token=''; AUTH.user=null; API.elderCode=''; API.providerCode=''; resetScopedState(); $('#appShell').classList.add('hidden'); $('#loginRoot').classList.remove('hidden'); showLoginPanel('login'); return; }
  if (action === 'modal-submit') {
    const submit = $('#modalRoot').dataset.submit;
    if (['new-order','new-elder','new-provider','new-service','new-notice'].includes(submit)) return submitNewEntity(submit);
    if (submit === 'audit') {
      const orderNo = $('#modalRoot').dataset.orderId;
      const values = readModalForm();
      const actionCode = values.auditResult === '驳回' ? 'REJECT' : values.assignMode === '标记为待调度' ? 'WAIT' : 'ASSIGN';
      try {
        const result = await apiRequest(`/api/orders/${encodeURIComponent(orderNo)}/assign`, {
          method: 'POST',
          body: { action: actionCode, provider_code: values.assignProvider, assign_mode: values.assignMode.startsWith('智能推荐') ? 'SMART' : 'MANUAL', auditor: AUTH.user?.display_name || '管理员', remark: values.auditNote || '' }
        });
        closeModal();
        await Promise.all([refreshOrders(),refreshDashboardStats()]);
        renderPage();
        return toast(result.message);
      } catch (error) { return toast(`分配失败：${error.message}`); }
    }
    if (submit === 'review') {
      const orderNo=$('#modalRoot').dataset.orderId; const values=readModalForm();
      try { const result=await apiRequest(`/api/orders/${encodeURIComponent(orderNo)}/review`,{method:'POST',body:{attitude_score:parseInt(values.attitude),quality_score:parseInt(values.quality),punctual_score:parseInt(values.punctual),review_text:values.reviewText||'',photo_note:values.reviewPhotos||''}}); closeModal(); await Promise.all([refreshOrders(),refreshReviews()]); renderPage(); return toast(result.message); } catch(error){ return toast(`评价提交失败：${error.message}`); }
    }
    if (submit === 'vital') {
      const values=readModalForm();
      try { const result=await apiRequest('/api/health-records',{method:'POST',body:{elder_code:API.elderCode,measured_at:values.time,source:values.source,bp:values.bp,glucose:values.glucose,heart_rate:values.heart,temperature:values.temp,weight:values.weight,sleep_hours:values.sleep}}); closeModal(); await refreshHealth(); renderPage(); return toast(result.message); } catch(error){ return toast(`健康数据保存失败：${error.message}`); }
    }
    if (submit === 'exam') {
      const values=readModalForm();
      const file=$('#examFileInput')?.files?.[0];
      if (!values.examDate || !values.examOrg || !file) return toast('请填写体检日期、体检机构并选择报告文件');
      if (file.size > 12*1024*1024) return toast('体检报告不能超过 12MB');
      try {
        const fileData=await fileToBase64(file);
        const result=await apiRequest('/api/exam-reports',{method:'POST',body:{elder_code:API.elderCode,exam_date:values.examDate,organization:values.examOrg,abnormal_summary:values.examAbnormal||'',file_name:file.name,mime_type:file.type,file_data:fileData}});
        closeModal();
        await refreshExamReports();
        renderPage();
        return toast(result.message);
      } catch(error) { return toast(`体检报告上传失败：${error.message}`); }
    }
    if (submit === 'admin-elder') {
      const elderId=$('#modalRoot').dataset.elderId; const values=readModalForm();
      try { const result=await apiRequest(`/api/profiles/elder/${encodeURIComponent(elderId)}`,{method:'POST',body:values}); closeModal(); await Promise.all([refreshElderDirectory(),refreshDashboardStats()]); renderPage(); return toast(`档案已保存：${result.profile.full_name}（${result.profile.service_priority}）`); } catch(error){ return toast(`档案保存失败：${error.message}`); }
    }
    if (submit === 'emergency-dispatch') {
      const emergencyNo=$('#modalRoot').dataset.emergencyId; const values=readModalForm();
      try { const result=await apiRequest(`/api/emergencies/${encodeURIComponent(emergencyNo)}/dispatch`,{method:'POST',body:{provider_code:values.providerCode,dispatch_note:values.dispatchNote||''}}); closeModal(); await refreshEmergencies(); renderPage(); return toast(result.message); } catch(error){ return toast(`调度失败：${error.message}`); }
    }
    if (submit === 'emergency-finish') {
      const emergencyNo=$('#modalRoot').dataset.emergencyId; const values=readModalForm();
      try { const result=await apiRequest(`/api/emergencies/${encodeURIComponent(emergencyNo)}/complete`,{method:'POST',body:{provider_code:API.providerCode,result:values.emergencyResult,handling_measure:values.emergencyMeasure||''}}); closeModal(); await refreshEmergencies(); renderPage(); return toast(result.message); } catch(error){ return toast(`处理完成失败：${error.message}`); }
    }
    if (submit === 'health-alert') {
      const alertId=$('#modalRoot').dataset.alertId; const values=readModalForm();
      try { const result=await apiRequest(`/api/health-alerts/${encodeURIComponent(alertId)}/handle`,{method:'POST',body:{handled_by:'陈雅',handling_note:values.handlingNote||''}}); closeModal(); await refreshHealthAlerts(); renderPage(); return toast(result.message); } catch(error){ return toast(`预警处理失败：${error.message}`); }
    }
    if (submit === 'complaint') {
      const values=readModalForm();
      if (!values.complaintOrder) return toast('当前账号没有可关联的服务订单');
      if (!String(values.complaintContent || '').trim()) return toast('请填写投诉内容');
      try { const result=await apiRequest('/api/complaints',{method:'POST',body:{order_no:values.complaintOrder,complaint_type:values.complaintType,complaint_content:values.complaintContent,evidence_note:values.complaintEvidence||''}}); closeModal(); await refreshComplaints(); renderPage(); return toast(result.message); } catch(error){ return toast(`投诉提交失败：${error.message}`); }
    }
    if (submit === 'appeal') {
      const complaintNo=$('#modalRoot').dataset.complaintId; const values=readModalForm();
      if (!String(values.appeal || '').trim()) return toast('请填写申诉说明');
      try { const result=await apiRequest(`/api/complaints/${encodeURIComponent(complaintNo)}/appeal`,{method:'POST',body:{appeal:values.appeal}}); closeModal(); await refreshComplaints(); renderPage(); return toast(result.message); } catch(error){ return toast(`申诉提交失败：${error.message}`); }
    }
    closeModal();
    if (submit === 'record') return toast('服务记录已提交，等待管理员审核');
    toast('操作已提交，系统已更新');
    return;
  }
  if (['new-order','new-elder','new-provider','new-service','new-notice'].includes(action)) {
    const titles = { 'new-order':'新建预约','new-elder':'新增老人档案','new-provider':'录入服务人员','new-service':'新增服务项目','new-notice':'发布社区公告' };
    return showModal(titles[action], newEntityForm(action), action);
  }
  if (action === 'panic') {
    const type = ($('#emergencyType')?.value) || '身体不适';
    return startEmergencyCountdown(type);
  }
  if (action === 'cancel-emergency-now') {
    if (emergencyCountdown) { clearInterval(emergencyCountdown); emergencyCountdown = null; }
    pendingEmergency = null;
    closeModal();
    return toast('求助已在 30 秒内取消，未通知救援人员');
  }
  if (action === 'confirm-emergency-now') return confirmEmergency();
  if (action === 'cancel-emergency') {
    try { const result=await apiRequest(`/api/emergencies/${encodeURIComponent(id)}/cancel`,{method:'POST',body:{}}); await refreshEmergencies(); renderPage(); return toast(result.message); } catch(error){ return toast(`取消求助失败：${error.message}`); }
  }
  if (action === 'delete-notice') {
    if (!confirm('确认删除这条公告？')) return;
    try { const result=await apiRequest(`/api/notices/${encodeURIComponent(id)}`,{method:'DELETE'}); await refreshNotices(); renderPage(); return toast(result.message); } catch(error){ return toast(`删除失败：${error.message}`); }
  }
  if (action === 'delete-elder') {
    if (!confirm('确认删除该老人档案？存在未结束订单或求助时将禁止删除。')) return;
    try { const result=await apiRequest(`/api/elders/${encodeURIComponent(id)}`,{method:'DELETE'}); await Promise.all([refreshElderDirectory(),refreshDashboardStats()]); API.options=await apiRequest('/api/options'); renderPage(); return toast(result.message); } catch(error){ return toast(`删除失败：${error.message}`); }
  }
  if (action === 'delete-provider') {
    if (!confirm('确认删除该服务人员档案？存在未结束任务时将禁止删除。')) return;
    try { const result=await apiRequest(`/api/providers/${encodeURIComponent(id)}`,{method:'DELETE'}); await Promise.all([refreshProviderDirectory(),refreshDashboardStats()]); API.options=await apiRequest('/api/options'); renderPage(); return toast(result.message); } catch(error){ return toast(`删除失败：${error.message}`); }
  }
  if (action === 'record-order') { API.recordOrder=id; return navigate('records'); }
  if (action === 'audit-order') {
    const o=data.orders.find((x)=>x.id===id);
    if (!o) return;
    let recommendations=[];
    try { recommendations=(await apiRequest(`/api/orders/${encodeURIComponent(id)}/recommend`)).recommendations || []; } catch (_) {}
    const fallback=data.providers.filter((p)=>p.status==='在职').map((p)=>({provider_code:p.id,provider_name:p.name,priority_level:1,eligible:true,reasons:['可手动指定']}));
    const list=recommendations.length?recommendations:fallback;
    const best=list.find((r)=>r.eligible) || list[0];
    const candidates=list.map((r)=>`<option value="${uiEscape(r.provider_code)}" ${best && r.provider_code===best.provider_code?'selected':''}>${uiEscape(r.provider_name)} · 优先级 ${uiEscape(r.priority_level || 1)}${r.distance_km!=null?` · ${uiEscape(r.distance_km)}km`:''}</option>`).join('');
    const details=list.slice(0,5).map((r)=>`<div class="recommend-item"><strong><span>${uiEscape(r.provider_name)}</span><b class="priority-level level-${uiEscape(r.priority_level || 1)}">优先级 ${uiEscape(r.priority_level || 1)}</b></strong><span>${r.reasons.join('、')}</span></div>`).join('');
    showModal('审核预约与分配服务人员', `<div class="alert-item"><div class="alert-copy"><strong>${uiEscape(o.id)} · ${uiEscape(o.service)} · ${uiEscape(o.elder)}</strong><span>${uiEscape(o.date)} ${uiEscape(o.slot)} · ${uiEscape(o.duration)} · ${uiEscape(o.address)}</span></div></div><div class="form-grid" style="margin-top:15px"><div class="field"><label>审核结果</label><select name="auditResult"><option>审核通过</option><option>驳回</option></select></div><div class="field"><label>分配方式</label><select name="assignMode"><option>智能推荐分配（可改选）</option><option>手动指定分配</option><option>标记为待调度</option></select></div><div class="field full"><label>分配服务人员</label><select name="assignProvider">${candidates}</select><div class="panel-caption">默认选中优先级最高人员；老人有意向服务人员时会提高推荐优先级，也可以在这里手动改选。</div></div><div class="field full"><label>推荐依据</label><div class="recommend-list">${details || '<div class="panel-caption">暂无可计算推荐数据</div>'}</div></div><div class="field full"><label>审核备注 / 分配说明</label><textarea name="auditNote"></textarea></div></div>`, 'audit');
    $('#modalRoot').dataset.orderId=id;
    const mode=$('#modalRoot [name=assignMode]');
    const provider=$('#modalRoot [name=assignProvider]');
    const smartProvider=best?.provider_code || '';
    mode?.addEventListener('change',()=>{
      if (mode.value.startsWith('智能推荐') && smartProvider) provider.value=smartProvider;
    });
    provider?.addEventListener('change',()=>{
      if (mode.value.startsWith('智能推荐') && provider.value!==smartProvider) mode.value='手动指定分配';
    });
    return;
  }
  if (action === 'start-service') {
    try {
      const location = await getBrowserLocation();
      const result = await apiRequest(`/api/orders/${encodeURIComponent(id)}/depart`, { method: 'POST', body: { provider_code: API.providerCode, latitude: location.latitude, longitude: location.longitude } });
      await refreshOrders();
      renderPage();
      return toast(location.latitude == null ? `${result.message}（定位未授权/不可用，时间已保存）` : result.message);
    } catch (error) { return toast(`出发失败：${error.message}`); }
  }
  if (action === 'arrive-service') {
    try {
      const result = await apiRequest(`/api/orders/${encodeURIComponent(id)}/arrive`, { method: 'POST', body: { provider_code: API.providerCode } });
      await refreshOrders();
      renderPage();
      return toast(result.message);
    } catch (error) { return toast(`到达失败：${error.message}`); }
  }
  if (action === 'finish-service') {
    try {
      const result = await apiRequest(`/api/orders/${encodeURIComponent(id)}/complete`, { method: 'POST', body: { provider_code: API.providerCode } });
      await refreshOrders();
      renderPage();
      return toast(result.message);
    } catch (error) { return toast(`完成失败：${error.message}`); }
  }
  if (action === 'submit-record') {
    const values = Object.fromEntries(Array.from(document.querySelectorAll('#viewRoot [name]')).map((f) => [f.name, f.value]));
    const orderNo = values.recordOrder || '';
    if (!orderNo) return toast('当前没有可填写服务记录的待评价订单');
    try {
      const result = await apiRequest(`/api/orders/${encodeURIComponent(orderNo)}/records`, {
        method: 'POST',
        body: {
          actual_start_time: values.start || null,
          actual_end_time: values.end || null,
          provider_code: API.providerCode,
          completion_status: values.completion,
          process_description: values.process || '',
          elder_observation: values.observation || '',
          materials: values.materials || ''
        }
      });
      data.serviceRecords.unshift({ order: orderNo, start: values.start, end: values.end, completion: values.completion, materials: values.materials || '无' });
      API.recordOrder='';
      await refreshOrders();
      navigate('assigned');
      return toast(result.message);
    } catch (error) { return toast(`服务记录提交失败：${error.message}`); }
  }
  if (action === 'submit-book') {
    const values = Object.fromEntries(Array.from(document.querySelectorAll('#viewRoot [name]')).map((f) => [f.name, f.value]));
    if (!values.date) return toast('请先在日历中选择预约日期');
    const serviceCode = findServiceCodeByName(values.service);
    if (!serviceCode) return toast('未找到该服务项目对应的后端 service_code');
    try {
      const result = await apiRequest('/api/orders', {
        method: 'POST',
        body: {
          elder_code: API.elderCode,
          service_code: serviceCode,
          service_date: values.date,
          time_slot: values.slot,
          duration_minutes: parseDurationMinutes(values.duration),
          service_address: values.address,
          service_latitude: values.serviceLatitude ? Number(values.serviceLatitude) : null,
          service_longitude: values.serviceLongitude ? Number(values.serviceLongitude) : null,
          special_requirements: [values.remark, values.preferredProvider ? `意向服务人员：${values.preferredProvider}` : ''].filter(Boolean).join('；'),
          meal_type: values.meal || '',
          delivery_time: values.delivery || '',
          medical_need: values.medicalNeed || '',
          symptom_description: values.symptom || ''
        }
      });
      await refreshOrders();
      navigate('my-orders');
      return toast(`${result.message}（${result.order_no}）`);
    } catch (error) { return toast(`预约提交失败：${error.message}`); }
  }
  if (action === 'cancel-order') {
    try {
      const result = await apiRequest(`/api/orders/${encodeURIComponent(id)}/cancel`, { method: 'POST', body: { reason: '老人/家属在前端取消预约' } });
      await refreshOrders();
      renderPage();
      return toast(result.message);
    } catch (error) { return toast(`取消失败：${error.message}`); }
  }
  if (action === 'write-review') { showModal('服务评价', `<div class="form-grid"><div class="field"><label>服务态度评分（1-5 星）</label><select name="attitude">${[5,4,3,2,1].map(x=>`<option value="${x}">${x} 星</option>`).join('')}</select></div><div class="field"><label>服务质量评分（1-5 星）</label><select name="quality">${[5,4,3,2,1].map(x=>`<option value="${x}">${x} 星</option>`).join('')}</select></div><div class="field"><label>准时程度评分（1-5 星）</label><select name="punctual">${[5,4,3,2,1].map(x=>`<option value="${x}">${x} 星</option>`).join('')}</select></div><div class="field full"><label>文字评价</label><textarea name="reviewText" placeholder="请描述本次服务体验"></textarea></div><div class="field full"><label>现场照片说明</label><input name="reviewPhotos" placeholder="例如：服务完成照片 2 张" /></div></div>`, 'review'); $('#modalRoot').dataset.orderId=id; return; }
  if (action === 'view-review') return showModal('评价详情', `<div class="alert-item"><div class="alert-copy"><strong>服务评价记录</strong><span>综合评分由服务态度、服务质量、准时程度三项平均计算。</span></div></div>`);
  if (action === 'new-complaint') {
    const ownOrders=data.orders.filter((o)=>o.elderCode===API.elderCode && o.status!=='已取消');
    const orderOptions=ownOrders.length?ownOrders.map((o)=>`<option value="${uiEscape(o.id)}">${uiEscape(o.id)} · ${uiEscape(o.service)} · ${uiEscape(o.date)}</option>`).join(''):'<option value="">当前账号暂无可关联订单</option>';
    return showModal('提交投诉', `<div class="form-grid"><div class="field"><label>投诉类型</label><select name="complaintType">${COMPLAINT_TYPES.map((c)=>`<option>${c}</option>`).join('')}</select></div><div class="field"><label>关联订单</label><select name="complaintOrder">${orderOptions}</select></div><div class="field full"><label>投诉内容</label><textarea name="complaintContent" placeholder="请详细描述问题经过"></textarea></div><div class="field full"><label>相关证据说明</label><input name="complaintEvidence" placeholder="可填写照片、录音或通话记录说明" /></div></div>`, 'complaint');
  }
  if (action === 'appeal-complaint') {
    showModal('提交申诉说明', `<div class="field full"><label>申诉说明</label><textarea name="appeal" placeholder="请说明实际情况并提供相关证明"></textarea></div>`, 'appeal');
    $('#modalRoot').dataset.complaintId=id;
    return;
  }
  if (action === 'open-notices') {
    const body=data.notices.length ? data.notices.map((n)=>`<div class="alert-item" data-action="view-notice" data-id="${uiEscape(n.notice_id)}"><div class="alert-copy"><strong>${uiEscape(n.title)}</strong><span>${uiEscape((n.published_at || '').slice(0,10))} · ${uiEscape(n.publisher)}</span></div></div>`).join('') : '<div class="empty">暂无公告</div>';
    return showModal(`社区公告（${data.notices.length}）`, body);
  }
  if (action === 'view-notice') {
    const n=data.notices.find((x)=>String(x.notice_id)===String(id));
    if (!n) return;
    return showModal(n.title, `<div class="field full"><label>公告正文</label><textarea disabled>${uiEscape(n.content || '暂无内容')}</textarea></div><div class="form-grid" style="margin-top:12px"><div class="field"><label>发布人</label><input value="${uiEscape(n.publisher)}" disabled /></div><div class="field"><label>发布时间</label><input value="${uiEscape((n.published_at || '').slice(0,16))}" disabled /></div></div>`);
  }
  if (action === 'handle-health-alert') {
    const a=(API.healthAlerts || []).find((x)=>String(x.alert_id)===String(id));
    if (!a) return;
    showModal('处理健康预警', `<div class="alert-item"><div class="alert-mark">!</div><div class="alert-copy"><strong>${uiEscape(a.elder_name)} · ${uiEscape(a.metric_type)}</strong><span>当前值 ${uiEscape(a.current_value)}，预警阈值 ${uiEscape(a.threshold_value)}<br>产生时间 ${uiEscape((a.created_at || '').slice(0,16))}</span></div></div><div class="field full" style="margin-top:15px"><label>处理说明</label><textarea name="handlingNote" placeholder="例如：已电话联系老人及家属，建议复测并联系家庭医生"></textarea></div>`, 'health-alert');
    $('#modalRoot').dataset.alertId=id;
    return;
  }
  if (action === 'handle-emergency') {
    const e=data.emergencies.find((x)=>x.id===id);
    if (!e) return;
    const candidates=(API.options?.providers || []).filter((p)=>p.employment_status==='在职').map((p)=>`<option value="${uiEscape(p.provider_code)}" ${p.provider_code===e.providerCode?'selected':''}>${uiEscape(p.full_name)} · ${uiEscape(p.provider_code)} · ${uiEscape(p.skills)}</option>`).join('');
    showModal('紧急求助调度', `<div class="alert-item"><div class="alert-mark">!</div><div class="alert-copy"><strong>${uiEscape(e.name)} · ${uiEscape(e.type)}</strong><span>${uiEscape(e.address)} · ${uiEscape(e.location)} · ${uiEscape(e.time)}<br>当前状态：${uiEscape(e.status)}</span></div></div><div class="form-grid" style="margin-top:15px"><div class="field full"><label>指派服务人员</label><select name="providerCode">${candidates}</select></div><div class="field full"><label>调度说明</label><textarea name="dispatchNote" placeholder="例如：身体不适，携带血压计和急救包，优先到场"></textarea></div></div>`, 'emergency-dispatch');
    $('#modalRoot').dataset.emergencyId=id;
    return;
  }
  if (action === 'view-emergency') {
    const e=data.emergencies.find((x)=>x.id===id);
    if (!e) return;
    return showModal('紧急求助进度', `<div class="form-grid"><div class="field"><label>求助编号</label><input value="${uiEscape(e.id)}" disabled /></div><div class="field"><label>当前状态</label><input value="${uiEscape(e.status)}" disabled /></div><div class="field"><label>指派人员</label><input value="${uiEscape(e.provider)}" disabled /></div><div class="field"><label>调度时间</label><input value="${uiEscape(e.dispatchAt || '—')}" disabled /></div><div class="field"><label>出发时间</label><input value="${uiEscape(e.departAt || '—')}" disabled /></div><div class="field"><label>到达时间</label><input value="${uiEscape(e.arriveTime || '—')}" disabled /></div><div class="field"><label>结束时间</label><input value="${uiEscape(e.finishAt || '—')}" disabled /></div><div class="field"><label>处理结果</label><input value="${uiEscape(e.result)}" disabled /></div><div class="field full"><label>处理措施</label><textarea disabled>${uiEscape(e.measure || '—')}</textarea></div></div>`);
  }
  if (action === 'depart-emergency') {
    try { const location=await getBrowserLocation(); const result=await apiRequest(`/api/emergencies/${encodeURIComponent(id)}/depart`,{method:'POST',body:{provider_code:API.providerCode,latitude:location.latitude,longitude:location.longitude}}); await refreshEmergencies(); renderPage(); return toast(result.message); } catch(error){ return toast(`出发失败：${error.message}`); }
  }
  if (action === 'arrive-emergency') {
    try { const result=await apiRequest(`/api/emergencies/${encodeURIComponent(id)}/arrive`,{method:'POST',body:{provider_code:API.providerCode}}); await refreshEmergencies(); renderPage(); return toast(result.message); } catch(error){ return toast(`到达失败：${error.message}`); }
  }
  if (action === 'finish-emergency') {
    const e=data.emergencies.find((x)=>x.id===id);
    if (!e) return;
    showModal('完成紧急任务', `<div class="alert-item"><div class="alert-copy"><strong>${uiEscape(e.name)} · ${uiEscape(e.type)}</strong><span>${uiEscape(e.address)} · 当前状态 ${uiEscape(e.status)}</span></div></div><div class="form-grid" style="margin-top:15px"><div class="field"><label>处理结果</label><select name="emergencyResult"><option>已解决</option><option>转送医院</option><option>联系家属</option></select></div><div class="field full"><label>现场处理措施</label><textarea name="emergencyMeasure" placeholder="记录现场处置、老人状态及后续安排"></textarea></div></div>`, 'emergency-finish');
    $('#modalRoot').dataset.emergencyId=id;
    return;
  }
  if (action === 'view-order') {
    const o = data.orders.find((x) => x.id === id);
    if (!o) return;
    return showModal('预约订单详情', `<div class="form-grid"><div class="field"><label>预约编号</label><input value="${uiEscape(o.id)}" disabled /></div><div class="field"><label>订单状态</label><input value="${uiEscape(o.status)}" disabled /></div><div class="field"><label>服务对象</label><input value="${uiEscape(o.elder)}" disabled /></div><div class="field"><label>服务类型</label><input value="${uiEscape(o.type)}" disabled /></div><div class="field"><label>服务项目</label><input value="${uiEscape(o.service)}" disabled /></div><div class="field"><label>服务人员</label><input value="${uiEscape(o.provider)}" disabled /></div><div class="field"><label>预约日期</label><input value="${uiEscape(o.date)}" disabled /></div><div class="field"><label>预约时段</label><input value="${uiEscape(o.slot)}" disabled /></div><div class="field"><label>服务时长</label><input value="${uiEscape(o.duration)}" disabled /></div><div class="field"><label>服务地址</label><input value="${uiEscape(o.address)}" disabled /></div>${o.meal ? `<div class="field"><label>餐食类型</label><input value="${uiEscape(o.meal)}" disabled /></div><div class="field"><label>配送时间</label><input value="${uiEscape(o.delivery)}" disabled /></div>` : ''}${o.medicalNeed ? `<div class="field"><label>就诊需求</label><input value="${uiEscape(o.medicalNeed)}" disabled /></div><div class="field"><label>症状描述</label><input value="${uiEscape(o.symptom)}" disabled /></div>` : ''}<div class="field full"><label>特殊需求说明</label><textarea disabled>${uiEscape(o.remark || '无')}</textarea></div><div class="field full"><label>审核备注</label><textarea disabled>${uiEscape(o.auditNote || '无')}</textarea></div><div class="field"><label>出发时间</label><input value="${uiEscape(o.departAt || '未出发')}" disabled /></div><div class="field"><label>到达时间</label><input value="${uiEscape(o.arriveAt || '未到达')}" disabled /></div><div class="field"><label>完成时间</label><input value="${uiEscape(o.finishAt || '未完成')}" disabled /></div><div class="field"><label>服务阶段</label><input value="${uiEscape(o.assignmentStatus || '未分配')}" disabled /></div></div>`);
  }
  if (action === 'edit-elder') {
    try {
      const r=await apiRequest(`/api/profiles/elder/${encodeURIComponent(id)}`);
      const p=r.profile;
      showModal('编辑老人档案', `<div class="form-grid"><div class="field"><label>姓名 *</label><input name="full_name" value="${uiEscape(p.full_name || '')}" /></div><div class="field"><label>性别 *</label><select name="gender">${['女','男'].map(x=>`<option ${x===p.gender?'selected':''}>${x}</option>`).join('')}</select></div><div class="field"><label>年龄</label><input value="${uiEscape(p.age || '—')} 岁" disabled /></div><div class="field"><label>联系电话 *</label><input name="phone" value="${uiEscape(p.phone || '')}" /></div><div class="field"><label>自理能力 *</label><select name="self_care_level">${['完全自理','轻度失能','中度失能','重度失能'].map(x=>`<option ${x===p.self_care_level?'selected':''}>${x}</option>`).join('')}</select></div><div class="field"><label>居住情况 *</label><select name="living_situation">${['独居','与配偶同住','与子女同住','养老机构居住'].map(x=>`<option ${x===p.living_situation?'selected':''}>${x}</option>`).join('')}</select></div><div class="field"><label>服务优先级</label><input value="${uiEscape(p.service_priority || '普通')}（保存后自动重算）" disabled /></div><div class="field"><label>身份证号</label><input name="id_card" value="${uiEscape(p.id_card || '')}" /></div><div class="field"><label>所属社区</label><input name="community" value="${uiEscape(p.community || '')}" /></div><div class="field full"><label>居住地址</label><input name="address" value="${uiEscape(p.address || '')}" /></div><div class="field"><label>既往病史</label><input name="disease_history" value="${uiEscape(p.disease_history || '')}" /></div><div class="field"><label>过敏史</label><input name="allergy_history" value="${uiEscape(p.allergy_history || '')}" /></div><div class="field"><label>紧急联系人</label><input name="emergency_contact_name" value="${uiEscape(p.emergency_contact_name || '')}" /></div><div class="field"><label>紧急联系电话</label><input name="emergency_contact_phone" value="${uiEscape(p.emergency_contact_phone || '')}" /></div><div class="field"><label>老人标签</label><input name="elder_tags" value="${uiEscape(p.elder_tags || '普通')}" /></div><div class="field"><label>特殊饮食</label><input name="diet_requirement" value="${uiEscape(p.diet_requirement || '')}" /></div><div class="field"><label>上门时间偏好</label><input name="visit_time_preference" value="${uiEscape(p.visit_time_preference || '')}" /></div></div>`, 'admin-elder');
      $('#modalRoot').dataset.elderId=id;
      return;
    } catch(error) { return toast(`读取档案失败：${error.message}`); }
  }
  if (action === 'view-provider') {
    const p = data.providers.find((x) => x.id === id) || data.providers[0];
    return showModal('服务人员资料', `<div class="form-grid"><div class="field"><label>姓名</label><input value="${uiEscape(p.name)}" disabled /></div><div class="field"><label>工号</label><input value="${uiEscape(p.id)}" disabled /></div><div class="field"><label>服务年限</label><input value="${uiEscape(p.years)} 年" disabled /></div><div class="field"><label>账号状态</label><input value="${uiEscape(p.status)}" disabled /></div><div class="field full"><label>资质证书</label><input value="${uiEscape(p.certs)}" disabled /></div><div class="field full"><label>健康证明</label><input value="${uiEscape(p.health)}" disabled /></div><div class="field full"><label>服务区域</label><input value="${uiEscape(p.areas)}" disabled /></div><div class="field full"><label>培训记录</label><textarea disabled>${uiEscape(p.training)}</textarea></div></div>`);
  }
  if (action === 'edit-service') return showModal('编辑服务项目', `<div class="form-grid"><div class="field"><label>服务名称</label><input name="name" value="助餐服务" /></div><div class="field"><label>服务类型</label><select name="type">${SERVICE_TYPES.map((t) => `<option>${t}</option>`).join('')}</select></div><div class="field"><label>服务方式</label><select name="mode"><option>上门</option><option>站点</option><option>远程</option></select></div><div class="field"><label>服务状态</label><select name="status"><option>正常提供</option><option>暂停提供</option><option>限时促销</option></select></div><div class="field"><label>收费标准</label><input name="price" value="¥25 / 餐" /></div><div class="field"><label>服务时长</label><input name="duration" value="30 分钟" /></div><div class="field full"><label>适用人群</label><input name="target" value="全体老人" /></div><div class="field full"><label>预约须知</label><input name="notes" value="需提前 1 天预约" /></div><div class="field full"><label>内容说明</label><textarea name="desc">营养配餐与送餐上门</textarea></div></div>`, 'edit-service');
  if (action === 'edit-profile') {
    const fields=Object.fromEntries(Array.from(document.querySelectorAll('#viewRoot [name]:not(:disabled)')).map((f)=>[f.name,f.value]));
    if ('service_years' in fields) fields.service_years=Number(fields.service_years);
    try { const result=await apiRequest(`/api/profiles/provider/${API.providerCode}`,{method:'POST',body:fields}); applyProviderProfile(result.profile); roleConfig.provider.name=`${result.profile.full_name} · 服务人员`; $('#userName').textContent=roleConfig.provider.name; return toast(result.message); } catch(error){ return toast(`保存失败：${error.message}`); }
  }
  if (action === 'save-profile') {
    const fields=Object.fromEntries(Array.from(document.querySelectorAll('#viewRoot [name]')).map((f)=>[f.name,f.value])); fields.height_cm=Number(fields.height_cm)||null; fields.weight_kg=Number(fields.weight_kg)||null;
    try { const result=await apiRequest(`/api/profiles/elder/${API.elderCode}`,{method:'POST',body:fields}); applyElderProfile(result.profile); roleConfig.elder.name=`${result.profile.full_name} · 老人 / 家属`; $('#userName').textContent=roleConfig.elder.name; return toast(result.message); } catch(error){ return toast(`保存失败：${error.message}`); }
  }
  if (action === 'add-vital') return showModal('录入健康数据', `<div class="form-grid"><div class="field"><label>记录时间</label><input name="time" type="datetime-local" /></div><div class="field"><label>数据来源</label><select name="source"><option>手动录入</option><option>课程手动登记</option></select></div><div class="field"><label>血压（收缩压 / 舒张压）</label><input name="bp" placeholder="例如 128 / 76" /></div><div class="field"><label>血糖（mmol/L）</label><input name="glucose" placeholder="例如 5.8" /></div><div class="field"><label>心率（次/分）</label><input name="heart" placeholder="例如 72" /></div><div class="field"><label>体温（℃）</label><input name="temp" placeholder="例如 36.5" /></div><div class="field"><label>体重（kg）</label><input name="weight" placeholder="例如 58.2" /></div><div class="field"><label>睡眠时长（小时）</label><input name="sleep" placeholder="例如 6.5" /></div></div>`, 'vital');
  if (action === 'upload-exam') return showModal('上传体检报告', `<div class="form-grid"><div class="field"><label>体检日期</label><input name="examDate" type="date" /></div><div class="field"><label>体检机构</label><input name="examOrg" placeholder="请输入体检机构" /></div><div class="field full"><label>报告文件（PDF 或图片，最大 12MB）</label><input id="examFileInput" name="examFile" type="file" accept=".pdf,image/png,image/jpeg,image/webp" /></div><div class="field full"><label>主要异常指标</label><textarea name="examAbnormal" placeholder="请填写主要异常指标"></textarea></div></div>`, 'exam');
  if (action === 'set-threshold') return showModal('设置异常指标预警阈值', `<div class="form-grid"><div class="field"><label>指标名称</label><select name="thresholdName"><option>血压（收缩压）</option><option>血压（舒张压）</option><option>空腹血糖</option><option>心率</option><option>体温</option></select></div><div class="field"><label>预警阈值</label><input name="thresholdValue" placeholder="例如 ≥ 140 mmHg" /></div><div class="field full"><label>触发动作</label><input name="thresholdAction" placeholder="例如 提醒家属与家庭医生" /></div></div>`, 'threshold');
  if (action === 'add-training') return showModal('新增培训记录', `<div class="form-grid"><div class="field"><label>培训名称</label><input name="trainingName" placeholder="请输入培训名称" /></div><div class="field"><label>培训时间</label><input name="trainingTime" type="date" /></div><div class="field"><label>考核结果</label><select name="trainingResult"><option>合格</option><option>优秀</option><option>待补考</option></select></div></div>`, 'training');
  if (action === 'export-pdf') { try { await downloadApiFile('/api/reports/export?format=pdf', `统计报表_${API.dashboardStats?.as_of || ''}.pdf`); return toast('PDF 报表已下载'); } catch(error) { return toast(`PDF 导出失败：${error.message}`); } }
  if (action === 'export-excel') { try { await downloadApiFile('/api/reports/export?format=excel', `统计报表_${API.dashboardStats?.as_of || ''}.xlsx`); return toast('Excel 报表已下载'); } catch(error) { return toast(`Excel 导出失败：${error.message}`); } }
  if (action === 'download-elder-template') { try { await downloadApiFile('/api/elders/import-template', '老人档案导入模板.xlsx'); return toast('导入模板已下载'); } catch(error) { return toast(`模板下载失败：${error.message}`); } }
  if (action === 'import-elders-excel') {
    const file=await pickFile('.xlsx,.xlsm');
    if (!file) return;
    if (file.size > 8*1024*1024) return toast('Excel 文件不能超过 8MB');
    try {
      const fileData=await fileToBase64(file);
      const result=await apiRequest('/api/elders/import',{method:'POST',body:{file_name:file.name,file_data:fileData}});
      await Promise.all([refreshElderDirectory(),refreshDashboardStats()]);
      API.options=await apiRequest('/api/options');
      renderPage();
      const skipped=result.skipped?.length?`；跳过 ${result.skipped.length} 行`:'';
      return toast(`${result.message}${skipped}`);
    } catch(error) { return toast(`Excel 导入失败：${error.message}`); }
  }
  if (action === 'download-exam') { try { await downloadApiFile(`/api/exam-reports/${encodeURIComponent(id)}/download`, '体检报告'); return toast('体检报告已下载'); } catch(error) { return toast(`报告下载失败：${error.message}`); } }
  if (action === 'quick-book') return navigate('book');
  if (action === 'quick-health') return navigate('health');
  if (action === 'quick-help') return navigate('help');
  if (action === 'quick-orders') return navigate('my-orders');
}

function bindActions() {
  document.querySelectorAll('[data-action]').forEach((el) => {
    if (!el.dataset.bound) {
      el.dataset.bound = '1';
      el.addEventListener('click', () => { void handleAction(el.dataset.action, el).catch((error) => { console.error(error); toast(`操作失败：${error.message}`); }); });
    }
  });
  document.querySelectorAll('[data-page]').forEach((el) => {
    if (!el.dataset.navbound) {
      el.dataset.navbound = '1';
      el.addEventListener('click', () => navigate(el.dataset.page));
    }
  });
  document.querySelectorAll('[data-filter-input]').forEach((input) => {
    if (input.dataset.filterbound) return;
    input.dataset.filterbound = '1';
    input.addEventListener('input', () => {
      const term = input.value.toLowerCase();
      document.querySelectorAll('tbody tr').forEach((row) => {
        if (row.dataset.search) row.style.display = row.dataset.search.toLowerCase().includes(term) ? '' : 'none';
      });
    });
  });
}

function roleLabel(value){ return {admin:'社区管理员',elder:'老人 / 家属',provider:'服务人员',guest:'游客'}[value] || value; }

function showLoginPanel(panel='login') {
  $('#loginForm').classList.toggle('hidden', panel !== 'login');
  $('#registerForm').classList.toggle('hidden', panel !== 'register');
  $('#profileSetupForm').classList.toggle('hidden', panel !== 'profile');
  $('#loginError').textContent='';
  $('#registerError').textContent='';
  $('#profileSetupError').textContent='';
}

function renderProfileSetup(roleName) {
  const elderFields=`
    <div class="profile-setup-grid">
      <label>老人姓名 *<input name="full_name" required /></label>
      <label>性别 *<select name="gender" required><option value="女">女</option><option value="男">男</option></select></label>
      <label>出生日期 *<input name="birth_date" type="date" required /></label>
      <label>联系电话 *<input name="phone" required /></label>
      <label class="wide">居住地址 *<input name="address" required /></label>
      <label>所属社区 *<input name="community" value="青云社区" required /></label>
      <label>自理能力 *<select name="self_care_level" required><option>完全自理</option><option>轻度失能</option><option>中度失能</option><option>重度失能</option></select></label>
      <label>居住情况 *<select name="living_situation" required><option value="">请选择</option><option>独居</option><option>与配偶同住</option><option>与子女同住</option><option>养老机构居住</option></select></label>
      <label>紧急联系人<input name="emergency_contact_name" /></label>
      <label>联系人关系<input name="emergency_contact_relation" /></label>
      <label class="wide">紧急联系电话<input name="emergency_contact_phone" /></label>
    </div>`;
  const providerFields=`
    <div class="profile-setup-grid">
      <label>姓名 *<input name="full_name" required /></label>
      <label>性别 *<select name="gender" required><option value="男">男</option><option value="女">女</option></select></label>
      <label>联系电话 *<input name="phone" required /></label>
      <label>所属机构 *<input name="organization" placeholder="如 青云社区养老服务中心" required /></label>
      <label>服务类型 *<select name="skills" required><option>生活照料</option><option>医疗护理</option><option>康复保健</option><option>精神慰藉</option><option>紧急救援</option></select></label>
      <label>服务年限<input name="service_years" type="number" min="0" value="0" /></label>
      <label class="wide">服务区域 *<input name="service_areas" placeholder="如 青云社区、朝阳社区" required /></label>
      <label class="wide">资质证书 *<input name="certificates" placeholder="如 养老护理员证 A123456" required /></label>
      <label class="wide">健康证明<input name="health_certificate" /></label>
      <label class="wide">个人简介<textarea name="profile_intro" rows="3"></textarea></label>
    </div>`;
  $('#profileSetupHint').textContent=`${roleLabel(roleName)}首次登录需先完成个人档案，保存后才能使用系统功能。`;
  $('#profileSetupFields').innerHTML=roleName === 'elder' ? elderFields : providerFields;
}

async function enterSystem(session, persist=true) {
  AUTH.token=session.token; AUTH.user=session.user; role=session.user.role;
  if (persist) saveAuthSession(session);
  if (['elder','provider'].includes(role) && !session.user.profile_completed) {
    renderProfileSetup(role);
    showLoginPanel('profile');
    return;
  }
  API.elderCode=session.user.elder_code || '';
  API.providerCode=session.user.provider_code || '';
  resetScopedState();
  roleConfig[role].name=`${session.user.display_name} · ${roleLabel(role)}`;
  currentPage=roleConfig[role].defaultPage;
  $('#userName').textContent=roleConfig[role].name; $('#roleLabel').textContent=roleLabel(role);
  $('#loginRoot').classList.add('hidden'); $('#appShell').classList.remove('hidden');
  if (role === 'guest') {
    try { await refreshNotices(); } catch (_) {}
  } else {
    await initializeApiData();
  }
  renderNav(); renderPage();
}

$('#loginForm').addEventListener('submit', async (event)=>{
  event.preventDefault(); $('#loginError').textContent='';
  try { const session=await apiRequest('/api/auth/login',{method:'POST',body:{role:$('#loginRole').value,username:$('#loginUsername').value,password:$('#loginPassword').value,remember:isMobileClient()}}); await enterSystem(session); }
  catch(error){ $('#loginError').textContent=error.message; }
});

$('#openRegister').addEventListener('click',()=>showLoginPanel('register'));
$('#backToLogin').addEventListener('click',()=>showLoginPanel('login'));
$('#guestBrowse').addEventListener('click',async()=>{
  $('#loginError').textContent='';
  try { const session=await apiRequest('/api/auth/guest',{method:'POST'}); await enterSystem(session); }
  catch(error){ $('#loginError').textContent=error.message; }
});

$('#registerForm').addEventListener('submit',async(event)=>{
  event.preventDefault();
  const password=$('#registerPassword').value;
  const confirm=$('#registerPasswordConfirm').value;
  $('#registerError').textContent='';
  if (password !== confirm) { $('#registerError').textContent='两次输入的密码不一致'; return; }
  try {
    const result=await apiRequest('/api/auth/register',{method:'POST',body:{role:$('#registerRole').value,username:$('#registerUsername').value.trim(),password}});
    $('#loginRole').value=result.role;
    $('#loginUsername').value=result.username;
    $('#loginPassword').value='';
    showLoginPanel('login');
    toast(result.message);
  } catch(error) { $('#registerError').textContent=error.message; }
});

$('#profileSetupForm').addEventListener('submit',async(event)=>{
  event.preventDefault();
  $('#profileSetupError').textContent='';
  const body=Object.fromEntries(new FormData(event.currentTarget).entries());
  try {
    const result=await apiRequest('/api/auth/complete-profile',{method:'POST',body});
    AUTH.user=result.user;
    const session={token:AUTH.token,user:result.user};
    await enterSystem(session);
  } catch(error) { $('#profileSetupError').textContent=error.message; }
});

$('#cancelProfileSetup').addEventListener('click',async()=>{
  try { await apiRequest('/api/auth/logout',{method:'POST'}); } catch (_) {}
  clearAuthSession(); AUTH.token=''; AUTH.user=null; API.elderCode=''; API.providerCode=''; resetScopedState();
  showLoginPanel('login');
});

async function refreshVisibleData(silent=false) {
  if (!AUTH.token || !AUTH.user) return;
  try {
    await initializeApiData();
    renderNav();
    renderPage();
    if (!silent) toast('已刷新');
  } catch (error) {
    if (!silent) toast(`刷新失败：${error.message}`);
  }
}

function initPullRefresh() {
  if (!isMobileClient()) return;
  const indicator=document.createElement('div');
  indicator.className='pull-refresh';
  indicator.textContent='下拉刷新';
  document.body.appendChild(indicator);
  let startY=0;
  let distance=0;
  let tracking=false;
  document.addEventListener('touchstart',(event)=>{
    if (!AUTH.token || window.scrollY>0 || event.touches.length!==1 || event.target.closest('.modal-root,.sidebar,.table-scroll,.emergency-table-scroll')) return;
    startY=event.touches[0].clientY;
    distance=0;
    tracking=true;
  },{passive:true});
  document.addEventListener('touchmove',(event)=>{
    if (!tracking) return;
    distance=Math.max(0,Math.min(100,(event.touches[0].clientY-startY)*.55));
    if (!distance) return;
    event.preventDefault();
    indicator.classList.add('show');
    indicator.textContent=distance>=64?'松开刷新':'下拉刷新';
    indicator.style.transform=`translate(-50%, ${distance-48}px)`;
  },{passive:false});
  document.addEventListener('touchend',async()=>{
    if (!tracking) return;
    tracking=false;
    const shouldRefresh=distance>=64;
    distance=0;
    if (!shouldRefresh) {
      indicator.classList.remove('show');
      indicator.style.transform='translate(-50%, -48px)';
      return;
    }
    indicator.classList.add('refreshing');
    indicator.textContent='正在刷新';
    indicator.style.transform='translate(-50%, 14px)';
    await refreshVisibleData(true);
    indicator.textContent='刷新完成';
    setTimeout(()=>{
      indicator.classList.remove('show','refreshing');
      indicator.style.transform='translate(-50%, -48px)';
    },450);
  },{passive:true});
  document.addEventListener('visibilitychange',()=>{
    if (!document.hidden && AUTH.token) refreshVisibleData(true);
  });
}

async function restoreAuthSession() {
  const stored=loadAuthSession();
  if (!stored) return false;
  AUTH.token=stored.token;
  AUTH.user=stored.user;
  try {
    const result=await apiRequest('/api/auth/me');
    await enterSystem({token:stored.token,user:result.user},false);
    return true;
  } catch (_) {
    clearAuthSession();
    AUTH.token='';
    AUTH.user=null;
    return false;
  }
}

async function boot() {
  $('#loginRoot').classList.remove('hidden');
  $('#appShell').classList.add('hidden');
  showLoginPanel('login');
  initMobileSidebar();
  initPullRefresh();
  await restoreAuthSession();
}

boot();
