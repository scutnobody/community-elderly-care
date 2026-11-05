PRAGMA foreign_keys = ON;

DROP TABLE IF EXISTS auth_sessions;
DROP TABLE IF EXISTS auth_accounts;
DROP TABLE IF EXISTS dashboard_baselines;
DROP TABLE IF EXISTS community_notices;
DROP TABLE IF EXISTS emergency_requests;
DROP TABLE IF EXISTS health_alerts;
DROP TABLE IF EXISTS exam_reports;
DROP TABLE IF EXISTS health_vitals;
DROP TABLE IF EXISTS service_complaints;
DROP TABLE IF EXISTS service_reviews;
DROP TABLE IF EXISTS service_records;
DROP TABLE IF EXISTS audit_logs;
DROP TABLE IF EXISTS service_assignments;
DROP TABLE IF EXISTS service_orders;
DROP TABLE IF EXISTS service_providers;
DROP TABLE IF EXISTS service_items;
DROP TABLE IF EXISTS elderly_profiles;


CREATE TABLE auth_accounts (
  account_id INTEGER PRIMARY KEY AUTOINCREMENT,
  username TEXT NOT NULL UNIQUE,
  password_hash TEXT NOT NULL,
  role TEXT NOT NULL CHECK(role IN ('admin','elder','provider','guest')),
  display_name TEXT NOT NULL,
  elder_code TEXT,
  provider_code TEXT,
  profile_completed INTEGER NOT NULL DEFAULT 0 CHECK(profile_completed IN (0,1)),
  enabled INTEGER NOT NULL DEFAULT 1 CHECK(enabled IN (0,1)),
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE auth_sessions (
  token TEXT PRIMARY KEY,
  username TEXT NOT NULL,
  expires_at TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY(username) REFERENCES auth_accounts(username) ON DELETE CASCADE
);

CREATE TABLE elderly_profiles (
  elder_id INTEGER PRIMARY KEY AUTOINCREMENT,
  elder_code TEXT NOT NULL UNIQUE,
  full_name TEXT NOT NULL,
  phone TEXT NOT NULL,
  address TEXT NOT NULL,
  community TEXT NOT NULL DEFAULT '青云社区',
  gender TEXT DEFAULT '女',
  id_card TEXT DEFAULT '',
  birth_date TEXT DEFAULT '',
  ethnicity TEXT DEFAULT '汉族',
  height_cm REAL,
  weight_kg REAL,
  blood_type TEXT DEFAULT '',
  allergy_history TEXT DEFAULT '',
  disease_history TEXT DEFAULT '',
  disability_status TEXT DEFAULT '无',
  self_care_level TEXT DEFAULT '完全自理',
  medical_type TEXT DEFAULT '',
  medical_card_no TEXT DEFAULT '',
  designated_hospital TEXT DEFAULT '',
  emergency_contact_name TEXT DEFAULT '',
  emergency_contact_relation TEXT DEFAULT '',
  emergency_contact_phone TEXT DEFAULT '',
  emergency_backup_phone TEXT DEFAULT '',
  living_situation TEXT DEFAULT '',
  language_preference TEXT DEFAULT '',
  diet_requirement TEXT DEFAULT '',
  visit_time_preference TEXT DEFAULT '',
  elder_tags TEXT NOT NULL DEFAULT '普通',
  service_priority TEXT NOT NULL DEFAULT '普通' CHECK (service_priority IN ('普通','优先')),
  is_deleted INTEGER NOT NULL DEFAULT 0 CHECK (is_deleted IN (0,1)),
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE service_items (
  service_item_id INTEGER PRIMARY KEY AUTOINCREMENT,
  service_code TEXT NOT NULL UNIQUE,
  service_name TEXT NOT NULL,
  service_type TEXT NOT NULL,
  price REAL NOT NULL CHECK (price >= 0),
  duration_minutes INTEGER NOT NULL CHECK (duration_minutes > 0),
  enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1))
);

CREATE TABLE service_providers (
  provider_id INTEGER PRIMARY KEY AUTOINCREMENT,
  provider_code TEXT NOT NULL UNIQUE,
  full_name TEXT NOT NULL,
  phone TEXT NOT NULL,
  organization TEXT NOT NULL,
  skills TEXT NOT NULL,
  service_areas TEXT NOT NULL,
  certificates TEXT,
  gender TEXT DEFAULT '男',
  id_card TEXT DEFAULT '',
  service_years INTEGER DEFAULT 0,
  health_certificate TEXT DEFAULT '',
  training_records TEXT DEFAULT '',
  profile_intro TEXT DEFAULT '',
  employment_status TEXT NOT NULL DEFAULT '在职' CHECK (employment_status IN ('在职','请假','离职')),
  is_deleted INTEGER NOT NULL DEFAULT 0 CHECK (is_deleted IN (0,1)),
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE service_orders (
  order_id INTEGER PRIMARY KEY AUTOINCREMENT,
  order_no TEXT NOT NULL UNIQUE,
  elder_id INTEGER NOT NULL,
  service_item_id INTEGER NOT NULL,
  service_date TEXT NOT NULL,
  time_slot TEXT NOT NULL,
  duration_minutes INTEGER NOT NULL,
  service_address TEXT NOT NULL,
  service_latitude REAL,
  service_longitude REAL,
  contact_phone TEXT NOT NULL,
  special_requirements TEXT,
  meal_type TEXT,
  delivery_time TEXT,
  medical_need TEXT,
  symptom_description TEXT,
  status TEXT NOT NULL DEFAULT 'PENDING_ASSIGN'
    CHECK (status IN ('PENDING_ASSIGN','PENDING_SERVICE','IN_SERVICE','PENDING_REVIEW','COMPLETED','CANCELLED')),
  audit_remark TEXT,
  cancel_reason TEXT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (elder_id) REFERENCES elderly_profiles(elder_id),
  FOREIGN KEY (service_item_id) REFERENCES service_items(service_item_id)
);

CREATE TABLE service_assignments (
  assignment_id INTEGER PRIMARY KEY AUTOINCREMENT,
  order_id INTEGER NOT NULL UNIQUE,
  provider_id INTEGER NOT NULL,
  assigned_by TEXT NOT NULL,
  assigned_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  assignment_status TEXT NOT NULL DEFAULT 'ASSIGNED'
    CHECK (assignment_status IN ('ASSIGNED','DEPARTED','ARRIVED','FINISHED','CANCELLED')),
  depart_at TEXT,
  depart_latitude REAL,
  depart_longitude REAL,
  arrive_at TEXT,
  finish_at TEXT,
  FOREIGN KEY (order_id) REFERENCES service_orders(order_id),
  FOREIGN KEY (provider_id) REFERENCES service_providers(provider_id)
);

CREATE TABLE audit_logs (
  audit_id INTEGER PRIMARY KEY AUTOINCREMENT,
  order_id INTEGER NOT NULL,
  action TEXT NOT NULL CHECK (action IN ('ASSIGN','WAIT','REJECT','CANCEL')),
  auditor TEXT NOT NULL,
  remark TEXT,
  operated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (order_id) REFERENCES service_orders(order_id)
);

CREATE TABLE service_records (
  record_id INTEGER PRIMARY KEY AUTOINCREMENT,
  order_id INTEGER NOT NULL,
  provider_id INTEGER NOT NULL,
  actual_start_time TEXT,
  actual_end_time TEXT,
  completion_status TEXT NOT NULL CHECK (completion_status IN ('全部完成','部分完成','未执行')),
  process_description TEXT,
  elder_observation TEXT,
  materials TEXT,
  submitted_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (order_id) REFERENCES service_orders(order_id),
  FOREIGN KEY (provider_id) REFERENCES service_providers(provider_id)
);


CREATE TABLE service_reviews (
  review_id INTEGER PRIMARY KEY AUTOINCREMENT,
  order_id INTEGER NOT NULL UNIQUE,
  elder_id INTEGER NOT NULL,
  provider_id INTEGER NOT NULL,
  attitude_score INTEGER NOT NULL CHECK (attitude_score BETWEEN 1 AND 5),
  quality_score INTEGER NOT NULL CHECK (quality_score BETWEEN 1 AND 5),
  punctual_score INTEGER NOT NULL CHECK (punctual_score BETWEEN 1 AND 5),
  review_text TEXT,
  photo_note TEXT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (order_id) REFERENCES service_orders(order_id),
  FOREIGN KEY (elder_id) REFERENCES elderly_profiles(elder_id),
  FOREIGN KEY (provider_id) REFERENCES service_providers(provider_id)
);


CREATE TABLE service_complaints (
  complaint_id INTEGER PRIMARY KEY AUTOINCREMENT,
  complaint_no TEXT NOT NULL UNIQUE,
  order_id INTEGER NOT NULL,
  elder_id INTEGER NOT NULL,
  provider_id INTEGER,
  complaint_type TEXT NOT NULL,
  complaint_content TEXT NOT NULL,
  evidence_note TEXT DEFAULT '',
  status TEXT NOT NULL DEFAULT '处理中' CHECK (status IN ('处理中','已处理')),
  admin_reply TEXT DEFAULT '',
  provider_appeal TEXT DEFAULT '',
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (order_id) REFERENCES service_orders(order_id),
  FOREIGN KEY (elder_id) REFERENCES elderly_profiles(elder_id),
  FOREIGN KEY (provider_id) REFERENCES service_providers(provider_id)
);

CREATE TABLE health_vitals (
  vital_id INTEGER PRIMARY KEY AUTOINCREMENT,
  elder_id INTEGER NOT NULL,
  measured_at TEXT NOT NULL,
  systolic INTEGER,
  diastolic INTEGER,
  glucose REAL,
  heart_rate INTEGER,
  temperature REAL,
  weight REAL,
  sleep_hours REAL,
  source TEXT NOT NULL DEFAULT '手动录入',
  FOREIGN KEY (elder_id) REFERENCES elderly_profiles(elder_id)
);

CREATE TABLE exam_reports (
  report_id INTEGER PRIMARY KEY AUTOINCREMENT,
  elder_id INTEGER NOT NULL,
  exam_date TEXT NOT NULL,
  organization TEXT NOT NULL,
  file_name TEXT NOT NULL,
  stored_name TEXT NOT NULL,
  mime_type TEXT NOT NULL DEFAULT 'application/pdf',
  file_size INTEGER NOT NULL DEFAULT 0,
  abnormal_summary TEXT NOT NULL DEFAULT '',
  uploaded_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (elder_id) REFERENCES elderly_profiles(elder_id)
);

CREATE TABLE health_alerts (
  alert_id INTEGER PRIMARY KEY AUTOINCREMENT,
  vital_id INTEGER,
  elder_id INTEGER NOT NULL,
  metric_type TEXT NOT NULL,
  current_value TEXT NOT NULL,
  threshold_value TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT '待处理' CHECK (status IN ('待处理','已处理')),
  handling_note TEXT DEFAULT '',
  handled_by TEXT,
  handled_at TEXT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (vital_id) REFERENCES health_vitals(vital_id),
  FOREIGN KEY (elder_id) REFERENCES elderly_profiles(elder_id)
);

CREATE TABLE community_notices (
  notice_id INTEGER PRIMARY KEY AUTOINCREMENT,
  title TEXT NOT NULL,
  content TEXT NOT NULL DEFAULT '',
  publisher TEXT NOT NULL DEFAULT '社区管理员',
  status TEXT NOT NULL DEFAULT '已发布' CHECK (status IN ('已发布','已撤回')),
  published_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE dashboard_baselines (
  baseline_id INTEGER PRIMARY KEY CHECK (baseline_id = 1),
  elder_base INTEGER NOT NULL,
  order_base INTEGER NOT NULL,
  provider_base INTEGER NOT NULL,
  institution_count INTEGER NOT NULL,
  elder_seed_count INTEGER NOT NULL,
  order_seed_count INTEGER NOT NULL,
  provider_seed_count INTEGER NOT NULL,
  trend_order_base TEXT NOT NULL DEFAULT '[14620,17280,19560,21840,23610,24860]',
  trend_completed_base TEXT NOT NULL DEFAULT '[13210,15890,17930,20120,22160,23980]'
);

CREATE TABLE emergency_requests (
  emergency_id INTEGER PRIMARY KEY AUTOINCREMENT,
  emergency_no TEXT NOT NULL UNIQUE,
  elder_id INTEGER NOT NULL,
  emergency_type TEXT NOT NULL,
  trigger_source TEXT NOT NULL DEFAULT 'APP一键呼叫',
  address TEXT NOT NULL,
  latitude REAL,
  longitude REAL,
  status TEXT NOT NULL DEFAULT '待调度' CHECK (status IN ('待调度','已派单','服务人员已出发','已到达','已解决','误报取消')),
  notify_targets TEXT NOT NULL DEFAULT '紧急联系人、社区管理员、家庭医生',
  assigned_provider_id INTEGER,
  dispatch_at TEXT,
  dispatch_note TEXT,
  response_time TEXT,
  depart_at TEXT,
  depart_latitude REAL,
  depart_longitude REAL,
  arrive_time TEXT,
  finish_at TEXT,
  handling_measure TEXT,
  result TEXT NOT NULL DEFAULT '待处理',
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (elder_id) REFERENCES elderly_profiles(elder_id),
  FOREIGN KEY (assigned_provider_id) REFERENCES service_providers(provider_id)
);

CREATE INDEX idx_auth_sessions_expiry ON auth_sessions(expires_at);
CREATE INDEX idx_reviews_elder ON service_reviews(elder_id, created_at);
CREATE INDEX idx_complaints_elder ON service_complaints(elder_id, created_at);
CREATE INDEX idx_complaints_provider ON service_complaints(provider_id, created_at);
CREATE INDEX idx_reviews_provider ON service_reviews(provider_id, created_at);
CREATE INDEX idx_vitals_elder_time ON health_vitals(elder_id, measured_at);
CREATE INDEX idx_exam_reports_elder_date ON exam_reports(elder_id, exam_date);
CREATE INDEX idx_health_alert_status ON health_alerts(status, created_at);
CREATE INDEX idx_health_alert_elder ON health_alerts(elder_id, created_at);
CREATE INDEX idx_emergency_elder_time ON emergency_requests(elder_id, created_at);
CREATE INDEX idx_emergency_status ON emergency_requests(status, created_at);
CREATE INDEX idx_notices_status_time ON community_notices(status, published_at);
CREATE INDEX idx_elders_deleted ON elderly_profiles(is_deleted, elder_id);
CREATE INDEX idx_providers_deleted ON service_providers(is_deleted, provider_id);

CREATE INDEX idx_orders_status_date ON service_orders(status, service_date);
CREATE INDEX idx_orders_elder_created ON service_orders(elder_id, created_at);
CREATE INDEX idx_assignments_provider ON service_assignments(provider_id, assignment_status);
CREATE INDEX idx_records_order ON service_records(order_id, submitted_at);

INSERT INTO elderly_profiles (elder_code, full_name, phone, address) VALUES
 ('JX202600128','李秀梅','13879202418','青云社区 12 栋 2 单元 301'),
 ('JX202600127','赵国强','13970008052','青云社区 8 栋 1 单元 202'),
 ('JX202600126','周桂芳','13600004720','青云社区 4 栋 3 单元 602'),
 ('JX202600125','孙德明','13700001184','青云社区 7 栋 2 单元 101'),
 ('JX202600124','胡秀兰','13500002231','青云社区 3 栋 1 单元 202');

INSERT INTO service_items (service_code, service_name, service_type, price, duration_minutes) VALUES
 ('FW-001','助餐服务','生活照料类',25,30),
 ('FW-002','助浴服务','生活照料类',100,90),
 ('FW-003','助洁服务','生活照料类',60,90),
 ('FW-004','助行服务','生活照料类',45,60),
 ('FW-005','代办服务','生活照料类',35,60),
 ('YL-001','助医服务','医疗护理类',80,60),
 ('YL-002','陪护服务','医疗护理类',180,480),
 ('YL-003','慢病用药指导','医疗护理类',80,60),
 ('KF-001','康复训练','康复保健类',120,60),
 ('JS-001','心理咨询','精神慰藉类',90,60),
 ('JS-002','陪伴聊天','精神慰藉类',50,60),
 ('JJ-001','远程监护','紧急救援类',30,1440),
 ('JJ-002','紧急呼叫响应','紧急救援类',20,1440),
 ('FW-009','居家保洁（限时促销）','生活照料类',45,90);

INSERT INTO service_providers (provider_code, full_name, phone, organization, skills, service_areas, certificates, employment_status) VALUES
 ('FW20260218','王建国','13900005820','青云社区养老服务中心','医疗护理 / 康复保健 / 生活照料','青云社区、翠湖社区、阳光社区','养老护理员证（高级）、急救培训证','在职'),
 ('FW20260106','刘敏','13800003391','青云社区卫生服务中心','医疗护理','青云社区、京山社区','护士执业证','在职'),
 ('FW20251242','陈小芳','13700007745','暖阳养老服务站','生活照料 / 精神慰藉','青云社区、锦绣社区','养老护理员证（中级）','在职'),
 ('FW20251023','张伟','13600009912','青云社区养老服务中心','康复保健','青云社区','康复师证','请假');

-- 初始演示订单：用于打开系统后立即看到不同阶段。
INSERT INTO service_orders (order_no, elder_id, service_item_id, service_date, time_slot, duration_minutes, service_address, contact_phone, special_requirements, meal_type, delivery_time, status, audit_remark, created_at, updated_at)
SELECT 'YY202609150001', e.elder_id, s.service_item_id, '2026-09-16','上午 11:00-12:00',30,e.address,e.phone,'请放在门口餐架','低盐餐','11:00','PENDING_ASSIGN','', '2026-09-15 08:00:00','2026-09-15 08:00:00' FROM elderly_profiles e, service_items s WHERE e.elder_code='JX202600128' AND s.service_code='FW-001';

INSERT INTO service_orders (order_no, elder_id, service_item_id, service_date, time_slot, duration_minutes, service_address, contact_phone, special_requirements, medical_need, symptom_description, status, audit_remark, created_at, updated_at)
SELECT 'YY202609150002', e.elder_id, s.service_item_id, '2026-09-16','上午 09:00-11:00',120,e.address,e.phone,'需搀扶，患有阿尔茨海默病请耐心沟通','陪同就医','近期头晕，需复查心电图','PENDING_SERVICE','已确认身体状况适合外出','2026-09-15 07:40:00','2026-09-15 08:20:00' FROM elderly_profiles e, service_items s WHERE e.elder_code='JX202600126' AND s.service_code='YL-001';

INSERT INTO service_orders (order_no, elder_id, service_item_id, service_date, time_slot, duration_minutes, service_address, contact_phone, special_requirements, status, audit_remark, created_at, updated_at)
SELECT 'YY202609140088', e.elder_id, s.service_item_id, '2026-09-15','上午 09:00-11:00',90,e.address,e.phone,'需要女护理员','IN_SERVICE','血压正常，适合洗浴','2026-09-14 16:30:00','2026-09-15 08:55:00' FROM elderly_profiles e, service_items s WHERE e.elder_code='JX202600128' AND s.service_code='FW-002';

INSERT INTO service_orders (order_no, elder_id, service_item_id, service_date, time_slot, duration_minutes, service_address, contact_phone, special_requirements, status, created_at, updated_at)
SELECT 'YY202609130081', e.elder_id, s.service_item_id, '2026-09-14','下午 14:00-16:00',60,e.address,e.phone,'需会本地方言','PENDING_REVIEW','2026-09-13 11:00:00','2026-09-14 15:05:00' FROM elderly_profiles e, service_items s WHERE e.elder_code='JX202600127' AND s.service_code='YL-003';

INSERT INTO service_orders (order_no, elder_id, service_item_id, service_date, time_slot, duration_minutes, service_address, contact_phone, special_requirements, status, created_at, updated_at)
SELECT 'YY202609120069', e.elder_id, s.service_item_id, '2026-09-12','晚上 18:00-20:00',90,e.address,e.phone,'无','COMPLETED','2026-09-11 09:00:00','2026-09-12 20:05:00' FROM elderly_profiles e, service_items s WHERE e.elder_code='JX202600125' AND s.service_code='FW-003';

INSERT INTO service_orders (order_no, elder_id, service_item_id, service_date, time_slot, duration_minutes, service_address, contact_phone, special_requirements, status, audit_remark, created_at, updated_at)
SELECT 'YY202609110052', e.elder_id, s.service_item_id, '2026-09-17','上午 09:00-11:00',60,e.address,e.phone,'','PENDING_ASSIGN','待调度：康复师排班已满，协调其他机构','2026-09-11 12:00:00','2026-09-11 12:10:00' FROM elderly_profiles e, service_items s WHERE e.elder_code='JX202600124' AND s.service_code='KF-001';

INSERT INTO service_orders (order_no, elder_id, service_item_id, service_date, time_slot, duration_minutes, service_address, contact_phone, special_requirements, status, cancel_reason, created_at, updated_at)
SELECT 'YY202609100041', e.elder_id, s.service_item_id, '2026-09-10','下午 14:00-16:00',60,e.address,e.phone,'家属临时取消','CANCELLED','家属临时取消','2026-09-09 16:00:00','2026-09-10 08:30:00' FROM elderly_profiles e, service_items s WHERE e.elder_code='JX202600126' AND s.service_code='JS-002';

INSERT INTO service_assignments (order_id, provider_id, assigned_by, assigned_at, assignment_status)
SELECT o.order_id,p.provider_id,'陈雅','2026-09-15 08:20:00','ASSIGNED' FROM service_orders o,service_providers p WHERE o.order_no='YY202609150002' AND p.provider_code='FW20260106';
INSERT INTO service_assignments (order_id, provider_id, assigned_by, assigned_at, assignment_status, depart_at, depart_latitude, depart_longitude)
SELECT o.order_id,p.provider_id,'陈雅','2026-09-14 17:00:00','DEPARTED','2026-09-15 08:55:00',28.65,115.92 FROM service_orders o,service_providers p WHERE o.order_no='YY202609140088' AND p.provider_code='FW20260218';
INSERT INTO service_assignments (order_id, provider_id, assigned_by, assigned_at, assignment_status, depart_at, arrive_at, finish_at)
SELECT o.order_id,p.provider_id,'陈雅','2026-09-13 12:00:00','FINISHED','2026-09-14 13:35:00','2026-09-14 13:55:00','2026-09-14 15:05:00' FROM service_orders o,service_providers p WHERE o.order_no='YY202609130081' AND p.provider_code='FW20260106';
INSERT INTO service_assignments (order_id, provider_id, assigned_by, assigned_at, assignment_status, depart_at, arrive_at, finish_at)
SELECT o.order_id,p.provider_id,'陈雅','2026-09-11 10:00:00','FINISHED','2026-09-12 17:40:00','2026-09-12 17:55:00','2026-09-12 19:35:00' FROM service_orders o,service_providers p WHERE o.order_no='YY202609120069' AND p.provider_code='FW20251242';



INSERT INTO service_orders (order_no, elder_id, service_item_id, service_date, time_slot, duration_minutes, service_address, contact_phone, special_requirements, status, created_at, updated_at)
SELECT 'YY202609080033',e.elder_id,s.service_item_id,'2026-09-08','下午 15:00-16:00',60,e.address,e.phone,'需会本地方言','COMPLETED','2026-09-07 10:00:00','2026-09-08 16:05:00' FROM elderly_profiles e,service_items s WHERE e.elder_code='JX202600128' AND s.service_code='JS-002';
INSERT INTO service_assignments (order_id, provider_id, assigned_by, assigned_at, assignment_status, depart_at, arrive_at, finish_at)
SELECT o.order_id,p.provider_id,'陈雅','2026-09-07 11:00:00','FINISHED','2026-09-08 14:35:00','2026-09-08 14:55:00','2026-09-08 16:00:00' FROM service_orders o,service_providers p WHERE o.order_no='YY202609080033' AND p.provider_code='FW20260218';
INSERT INTO service_reviews (order_id, elder_id, provider_id, attitude_score, quality_score, punctual_score, review_text, photo_note, created_at)
SELECT o.order_id,o.elder_id,p.provider_id,5,5,5,'服务很耐心，沟通清楚，按时上门。','1 张','2026-09-08 18:20:00' FROM service_orders o,service_providers p WHERE o.order_no='YY202609080033' AND p.provider_code='FW20260218';


UPDATE elderly_profiles SET gender='女', id_card='3601**********2846', birth_date='1948-03-12', ethnicity='汉族', community='青云社区', height_cm=158, weight_kg=58.2, blood_type='A 型', allergy_history='青霉素过敏', disease_history='高血压、骨质疏松', disability_status='无', self_care_level='轻度失能', medical_type='城镇职工医保', medical_card_no='JX36****8821', designated_hospital='青云社区卫生服务中心', emergency_contact_name='李晓华', emergency_contact_relation='女儿', emergency_contact_phone='138****6721', emergency_backup_phone='0791-8820****', living_situation='独居', language_preference='本地方言', diet_requirement='低盐饮食', visit_time_preference='上午 09:00-11:00', elder_tags='独居老人、高龄老人（80岁以上）', service_priority='优先' WHERE elder_code='JX202600128';
UPDATE elderly_profiles SET gender='男',birth_date='1954-07-08',self_care_level='完全自理',living_situation='与配偶同住',elder_tags='空巢老人',service_priority='普通' WHERE elder_code='JX202600127';
UPDATE elderly_profiles SET gender='女',birth_date='1945-11-23',self_care_level='中度失能',living_situation='与子女同住',elder_tags='高龄老人（80岁以上）',service_priority='优先' WHERE elder_code='JX202600126';
UPDATE elderly_profiles SET gender='男',birth_date='1957-02-19',self_care_level='完全自理',living_situation='与配偶同住',elder_tags='普通',service_priority='普通' WHERE elder_code='JX202600125';
UPDATE elderly_profiles SET gender='女',birth_date='1950-05-09',self_care_level='轻度失能',living_situation='独居',elder_tags='独居老人',service_priority='优先' WHERE elder_code='JX202600124';

UPDATE service_providers SET gender='男', id_card='3601**********1612', service_years=8, health_certificate='有效期至 2027-05-20', training_records='老年人跌倒预防 2026-09-18 合格
急救技能复训 2026-06-20 优秀', profile_intro='从事养老护理 8 年，擅长失能老人照护与康复协助。' WHERE provider_code='FW20260218';


INSERT INTO emergency_requests (emergency_no, elder_id, emergency_type, trigger_source, address, latitude, longitude, status, assigned_provider_id, result, created_at, updated_at)
SELECT 'JQ20260915001',e.elder_id,'身体不适','APP一键呼叫',e.address,28.65,115.92,'服务人员已出发',p.provider_id,'处理中','2026-09-15 16:42:00','2026-09-15 16:42:00' FROM elderly_profiles e,service_providers p WHERE e.elder_code='JX202600128' AND p.provider_code='FW20260218';
INSERT INTO emergency_requests (emergency_no, elder_id, emergency_type, trigger_source, address, latitude, longitude, status, assigned_provider_id, result, created_at, updated_at)
SELECT 'JQ20260915002',e.elder_id,'跌倒','智能手环',e.address,28.66,115.91,'已到达',p.provider_id,'转送医院','2026-09-15 10:18:00','2026-09-15 10:18:00' FROM elderly_profiles e,service_providers p WHERE e.elder_code='JX202600126' AND p.provider_code='FW20260106';
INSERT INTO emergency_requests (emergency_no, elder_id, emergency_type, trigger_source, address, latitude, longitude, status, result, created_at, updated_at)
SELECT 'JQ20260914003',e.elder_id,'煤气泄漏','呼叫器',e.address,28.64,115.90,'待调度','待处理','2026-09-14 19:06:00','2026-09-14 19:06:00' FROM elderly_profiles e WHERE e.elder_code='JX202600127';
INSERT INTO emergency_requests (emergency_no, elder_id, emergency_type, trigger_source, address, latitude, longitude, status, result, created_at, updated_at)
SELECT 'JQ20260913004',e.elder_id,'火灾','APP一键呼叫',e.address,28.63,115.89,'已解决','已解决','2026-09-13 08:30:00','2026-09-13 08:30:00' FROM elderly_profiles e WHERE e.elder_code='JX202600125';
INSERT INTO emergency_requests (emergency_no, elder_id, emergency_type, trigger_source, address, latitude, longitude, status, result, created_at, updated_at)
SELECT 'JQ20260912005',e.elder_id,'入室危险','呼叫器',e.address,28.62,115.88,'误报取消','误报','2026-09-12 21:44:00','2026-09-12 21:44:00' FROM elderly_profiles e WHERE e.elder_code='JX202600124';

INSERT INTO health_vitals (elder_id, measured_at, systolic, diastolic, glucose, heart_rate, temperature, weight, sleep_hours, source)
SELECT e.elder_id, printf('2026-08-%02d 08:10:00', d), 124 + (d % 8), 74 + (d % 6), 5.4 + (d % 7) * 0.12, 68 + (d % 9), 36.3 + (d % 4) * 0.1, 58.8 - (d % 5) * 0.1, 6.0 + (d % 6) * 0.25, CASE WHEN d % 2=0 THEN '智能手环' ELSE '手动录入' END
FROM elderly_profiles e, (SELECT 19 d UNION ALL SELECT 20 UNION ALL SELECT 21 UNION ALL SELECT 22 UNION ALL SELECT 23 UNION ALL SELECT 24 UNION ALL SELECT 25 UNION ALL SELECT 26 UNION ALL SELECT 27 UNION ALL SELECT 28 UNION ALL SELECT 29 UNION ALL SELECT 30 UNION ALL SELECT 31) x WHERE e.elder_code='JX202600128';
INSERT INTO health_vitals (elder_id, measured_at, systolic, diastolic, glucose, heart_rate, temperature, weight, sleep_hours, source)
SELECT e.elder_id, printf('2026-09-%02d 08:10:00', d), 126 + (d % 7), 75 + (d % 5), 5.5 + (d % 6) * 0.11, 69 + (d % 8), 36.4 + (d % 3) * 0.1, 58.5 - (d % 4) * 0.1, 6.2 + (d % 5) * 0.3, CASE WHEN d % 2=0 THEN '智能手环' ELSE '手动录入' END
FROM elderly_profiles e, (SELECT 1 d UNION ALL SELECT 2 UNION ALL SELECT 3 UNION ALL SELECT 4 UNION ALL SELECT 5 UNION ALL SELECT 6 UNION ALL SELECT 7 UNION ALL SELECT 8 UNION ALL SELECT 9 UNION ALL SELECT 10 UNION ALL SELECT 11 UNION ALL SELECT 12 UNION ALL SELECT 13 UNION ALL SELECT 14 UNION ALL SELECT 15 UNION ALL SELECT 16 UNION ALL SELECT 17) x WHERE e.elder_code='JX202600128';
INSERT INTO community_notices (title, content, publisher, published_at) VALUES
 ('高温天气关怀提醒','近期气温升高，请服务人员上门时关注老人饮水与室内通风。','陈雅','2026-09-13 09:00:00'),
 ('中秋节社区活动报名','9月25日社区长者中秋茶话会开放报名，欢迎老人参加。','陈雅','2026-09-12 09:00:00'),
 ('护理员培训通知','本月护理员急救技能复训安排已发布，请相关人员查看。','陈雅','2026-09-10 09:00:00');

INSERT INTO dashboard_baselines(
  baseline_id,elder_base,order_base,provider_base,institution_count,elder_seed_count,order_seed_count,provider_seed_count,trend_order_base,trend_completed_base)
SELECT 1,50286,248600,816,100,
  (SELECT COUNT(*) FROM elderly_profiles),
  (SELECT COUNT(*) FROM service_orders),
  (SELECT COUNT(*) FROM service_providers),
  '[14620,17280,19560,21840,23610,24860]',
  '[13210,15890,17930,20120,22160,23980]';
