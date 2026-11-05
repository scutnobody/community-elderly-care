import base64
import hashlib
import hmac
import io
import json
import math
import mimetypes
import re
import secrets
import sqlite3
import sys
from calendar import monthrange
from datetime import date, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "elderly_care.db"
SCHEMA_PATH = BASE_DIR / "schema.sql"
STATIC_DIR = BASE_DIR / "static"
UPLOAD_DIR = BASE_DIR / "uploads" / "exams"
SESSIONS = {}

STATUS_ZH = {
    "PENDING_ASSIGN": "待分配",
    "PENDING_SERVICE": "待服务",
    "IN_SERVICE": "服务中",
    "PENDING_REVIEW": "待评价",
    "COMPLETED": "已完成",
    "CANCELLED": "已取消",
}



def hash_password(password):
    return hashlib.sha256(password.encode("utf-8")).hexdigest()


def haversine_km(lat1, lon1, lat2, lon2):
    if None in {lat1, lon1, lat2, lon2}:
        return None
    radius = 6371.0
    phi1, phi2 = math.radians(float(lat1)), math.radians(float(lat2))
    dphi = math.radians(float(lat2) - float(lat1))
    dlambda = math.radians(float(lon2) - float(lon1))
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return radius * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


DEMO_DATA_START = date(2026, 1, 1)
DEMO_DATA_END = date(2026, 12, 20)
DEMO_ELDER_CODE = "JX202600128"
DEMO_PROVIDER_CODE = "FW20260218"


def demo_today():
    return min(max(date.today(), DEMO_DATA_START), DEMO_DATA_END)


def simulated_health_record(elder_code, day):
    seed = day.toordinal() + sum(ord(ch) for ch in elder_code)
    systolic = round(134 + 7 * math.sin(seed / 4.2) + 3 * math.sin(seed / 11.0))
    diastolic = round(78 + 4 * math.cos(seed / 5.3) + 2 * math.sin(seed / 13.0))
    glucose = round(5.7 + 0.35 * math.sin(seed / 6.1) + 0.18 * math.cos(seed / 2.9), 1)
    heart_rate = round(73 + 6 * math.sin(seed / 3.7) + 3 * math.cos(seed / 8.4))
    temperature = round(36.5 + 0.12 * math.sin(seed / 7.7), 1)
    weight = round(58.2 + 0.45 * math.sin(seed / 15.0), 1)
    sleep_hours = round(7.0 + 0.7 * math.sin(seed / 4.8) + 0.25 * math.cos(seed / 9.2), 1)
    return {
        "vital_id": None,
        "elder_id": None,
        "measured_at": f"{day.isoformat()} 07:30:00",
        "systolic": systolic,
        "diastolic": diastolic,
        "glucose": glucose,
        "heart_rate": heart_rate,
        "temperature": temperature,
        "weight": weight,
        "sleep_hours": max(5.2, min(8.6, sleep_hours)),
        "source": "系统模拟",
    }


def connect_db():
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def _ensure_column(connection, table, column, definition):
    columns = {row["name"] for row in connection.execute(f"PRAGMA table_info({table})")}
    if column not in columns:
        connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def _migrate_elder_priority_table(connection):
    row = connection.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='elderly_profiles'").fetchone()
    if row is None or "'优先'" in (row["sql"] or ""):
        return
    connection.commit()
    connection.execute("PRAGMA foreign_keys=OFF")
    connection.execute("""
    CREATE TABLE elderly_profiles_new (
      elder_id INTEGER PRIMARY KEY AUTOINCREMENT, elder_code TEXT NOT NULL UNIQUE, full_name TEXT NOT NULL, phone TEXT NOT NULL, address TEXT NOT NULL,
      community TEXT NOT NULL DEFAULT '青云社区', gender TEXT DEFAULT '女', id_card TEXT DEFAULT '', birth_date TEXT DEFAULT '', ethnicity TEXT DEFAULT '汉族',
      height_cm REAL, weight_kg REAL, blood_type TEXT DEFAULT '', allergy_history TEXT DEFAULT '', disease_history TEXT DEFAULT '', disability_status TEXT DEFAULT '无',
      self_care_level TEXT DEFAULT '完全自理', medical_type TEXT DEFAULT '', medical_card_no TEXT DEFAULT '', designated_hospital TEXT DEFAULT '',
      emergency_contact_name TEXT DEFAULT '', emergency_contact_relation TEXT DEFAULT '', emergency_contact_phone TEXT DEFAULT '', emergency_backup_phone TEXT DEFAULT '',
      living_situation TEXT DEFAULT '', language_preference TEXT DEFAULT '', diet_requirement TEXT DEFAULT '', visit_time_preference TEXT DEFAULT '',
      elder_tags TEXT NOT NULL DEFAULT '普通', service_priority TEXT NOT NULL DEFAULT '普通' CHECK(service_priority IN ('普通','优先')),
      created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)
    """)
    connection.execute("""INSERT INTO elderly_profiles_new SELECT elder_id,elder_code,full_name,phone,address,community,gender,id_card,birth_date,ethnicity,height_cm,weight_kg,blood_type,allergy_history,disease_history,disability_status,self_care_level,medical_type,medical_card_no,designated_hospital,emergency_contact_name,emergency_contact_relation,emergency_contact_phone,emergency_backup_phone,living_situation,language_preference,diet_requirement,visit_time_preference,elder_tags,'普通',created_at FROM elderly_profiles""")
    connection.execute("DROP TABLE elderly_profiles")
    connection.execute("ALTER TABLE elderly_profiles_new RENAME TO elderly_profiles")
    connection.commit()
    connection.execute("PRAGMA foreign_keys=ON")


def _create_emergency_table(connection):
    connection.execute("""
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
      FOREIGN KEY(elder_id) REFERENCES elderly_profiles(elder_id),
      FOREIGN KEY(assigned_provider_id) REFERENCES service_providers(provider_id)
    )
    """)


def _migrate_emergency_table(connection):
    row = connection.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='emergency_requests'").fetchone()
    if row is None:
        _create_emergency_table(connection)
        return
    table_sql = row["sql"] or ""
    columns = {r["name"] for r in connection.execute("PRAGMA table_info(emergency_requests)")}
    required = {"dispatch_at", "dispatch_note", "depart_at", "depart_latitude", "depart_longitude", "finish_at"}
    if "待调度" in table_sql and required.issubset(columns):
        return
    connection.execute("DROP INDEX IF EXISTS idx_emergency_elder_time")
    connection.execute("DROP INDEX IF EXISTS idx_emergency_status")
    connection.execute("ALTER TABLE emergency_requests RENAME TO emergency_requests_legacy")
    _create_emergency_table(connection)
    legacy = {r["name"] for r in connection.execute("PRAGMA table_info(emergency_requests_legacy)")}
    dispatch_at = "dispatch_at" if "dispatch_at" in legacy else "NULL"
    dispatch_note = "dispatch_note" if "dispatch_note" in legacy else "''"
    depart_at = "depart_at" if "depart_at" in legacy else "NULL"
    depart_latitude = "depart_latitude" if "depart_latitude" in legacy else "NULL"
    depart_longitude = "depart_longitude" if "depart_longitude" in legacy else "NULL"
    finish_at = "finish_at" if "finish_at" in legacy else "NULL"
    connection.execute(f"""
      INSERT INTO emergency_requests(
        emergency_id,emergency_no,elder_id,emergency_type,trigger_source,address,latitude,longitude,status,notify_targets,
        assigned_provider_id,dispatch_at,dispatch_note,response_time,depart_at,depart_latitude,depart_longitude,arrive_time,finish_at,
        handling_measure,result,created_at,updated_at)
      SELECT emergency_id,emergency_no,elder_id,emergency_type,trigger_source,address,latitude,longitude,
        CASE
          WHEN status='已通知' AND assigned_provider_id IS NULL THEN '待调度'
          WHEN status IN ('已通知','联系人已确认') AND assigned_provider_id IS NOT NULL THEN '已派单'
          WHEN status='联系人已确认' THEN '待调度'
          ELSE status
        END,
        notify_targets,assigned_provider_id,{dispatch_at},{dispatch_note},response_time,{depart_at},{depart_latitude},{depart_longitude},arrive_time,{finish_at},
        handling_measure,result,created_at,updated_at
      FROM emergency_requests_legacy
    """)
    connection.execute("DROP TABLE emergency_requests_legacy")


def migrate_database():
    with connect_db() as connection:
        _migrate_elder_priority_table(connection)
        elder_columns = {
            "gender": "TEXT DEFAULT '女'", "id_card": "TEXT DEFAULT ''", "birth_date": "TEXT DEFAULT ''",
            "ethnicity": "TEXT DEFAULT '汉族'", "community": "TEXT DEFAULT '青云社区'", "height_cm": "REAL",
            "weight_kg": "REAL", "blood_type": "TEXT DEFAULT ''", "allergy_history": "TEXT DEFAULT ''",
            "disease_history": "TEXT DEFAULT ''", "disability_status": "TEXT DEFAULT '无'",
            "self_care_level": "TEXT DEFAULT '完全自理'", "medical_type": "TEXT DEFAULT ''",
            "medical_card_no": "TEXT DEFAULT ''", "designated_hospital": "TEXT DEFAULT ''",
            "emergency_contact_name": "TEXT DEFAULT ''", "emergency_contact_relation": "TEXT DEFAULT ''",
            "emergency_contact_phone": "TEXT DEFAULT ''", "emergency_backup_phone": "TEXT DEFAULT ''",
            "living_situation": "TEXT DEFAULT ''", "language_preference": "TEXT DEFAULT ''",
            "diet_requirement": "TEXT DEFAULT ''", "visit_time_preference": "TEXT DEFAULT ''",
            "elder_tags": "TEXT DEFAULT '普通'", "service_priority": "TEXT DEFAULT '普通'", "is_deleted": "INTEGER NOT NULL DEFAULT 0",
        }
        provider_columns = {
            "gender": "TEXT DEFAULT '男'", "id_card": "TEXT DEFAULT ''", "service_years": "INTEGER DEFAULT 0",
            "health_certificate": "TEXT DEFAULT ''", "training_records": "TEXT DEFAULT ''", "profile_intro": "TEXT DEFAULT ''", "is_deleted": "INTEGER NOT NULL DEFAULT 0",
        }
        for column, definition in elder_columns.items():
            _ensure_column(connection, "elderly_profiles", column, definition)
        for column, definition in provider_columns.items():
            _ensure_column(connection, "service_providers", column, definition)
        _ensure_column(connection, "service_orders", "service_latitude", "REAL")
        _ensure_column(connection, "service_orders", "service_longitude", "REAL")
        connection.executescript("""
        CREATE TABLE IF NOT EXISTS auth_accounts (
          account_id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT NOT NULL UNIQUE, password_hash TEXT NOT NULL,
          role TEXT NOT NULL CHECK(role IN ('admin','elder','provider','guest')), display_name TEXT NOT NULL,
          elder_code TEXT, provider_code TEXT, profile_completed INTEGER NOT NULL DEFAULT 0, enabled INTEGER NOT NULL DEFAULT 1,
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
        CREATE TABLE IF NOT EXISTS auth_sessions (
          token TEXT PRIMARY KEY, username TEXT NOT NULL, expires_at TEXT NOT NULL,
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          FOREIGN KEY(username) REFERENCES auth_accounts(username) ON DELETE CASCADE);
        CREATE TABLE IF NOT EXISTS service_reviews (
          review_id INTEGER PRIMARY KEY AUTOINCREMENT, order_id INTEGER NOT NULL UNIQUE, elder_id INTEGER NOT NULL,
          provider_id INTEGER NOT NULL, attitude_score INTEGER NOT NULL CHECK(attitude_score BETWEEN 1 AND 5),
          quality_score INTEGER NOT NULL CHECK(quality_score BETWEEN 1 AND 5), punctual_score INTEGER NOT NULL CHECK(punctual_score BETWEEN 1 AND 5),
          review_text TEXT, photo_note TEXT, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          FOREIGN KEY(order_id) REFERENCES service_orders(order_id), FOREIGN KEY(elder_id) REFERENCES elderly_profiles(elder_id),
          FOREIGN KEY(provider_id) REFERENCES service_providers(provider_id));
        CREATE TABLE IF NOT EXISTS service_complaints (
          complaint_id INTEGER PRIMARY KEY AUTOINCREMENT, complaint_no TEXT NOT NULL UNIQUE, order_id INTEGER NOT NULL,
          elder_id INTEGER NOT NULL, provider_id INTEGER, complaint_type TEXT NOT NULL, complaint_content TEXT NOT NULL,
          evidence_note TEXT DEFAULT '', status TEXT NOT NULL DEFAULT '处理中' CHECK(status IN ('处理中','已处理')),
          admin_reply TEXT DEFAULT '', provider_appeal TEXT DEFAULT '', created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, FOREIGN KEY(order_id) REFERENCES service_orders(order_id),
          FOREIGN KEY(elder_id) REFERENCES elderly_profiles(elder_id), FOREIGN KEY(provider_id) REFERENCES service_providers(provider_id));
        CREATE TABLE IF NOT EXISTS health_vitals (
          vital_id INTEGER PRIMARY KEY AUTOINCREMENT, elder_id INTEGER NOT NULL, measured_at TEXT NOT NULL,
          systolic INTEGER, diastolic INTEGER, glucose REAL, heart_rate INTEGER, temperature REAL, weight REAL, sleep_hours REAL,
          source TEXT NOT NULL DEFAULT '手动录入', FOREIGN KEY(elder_id) REFERENCES elderly_profiles(elder_id));
        CREATE TABLE IF NOT EXISTS exam_reports (
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
          FOREIGN KEY(elder_id) REFERENCES elderly_profiles(elder_id));
        CREATE TABLE IF NOT EXISTS health_alerts (
          alert_id INTEGER PRIMARY KEY AUTOINCREMENT,
          vital_id INTEGER,
          elder_id INTEGER NOT NULL,
          metric_type TEXT NOT NULL,
          current_value TEXT NOT NULL,
          threshold_value TEXT NOT NULL,
          status TEXT NOT NULL DEFAULT '待处理' CHECK(status IN ('待处理','已处理')),
          handling_note TEXT DEFAULT '',
          handled_by TEXT,
          handled_at TEXT,
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          FOREIGN KEY(vital_id) REFERENCES health_vitals(vital_id),
          FOREIGN KEY(elder_id) REFERENCES elderly_profiles(elder_id));
        CREATE TABLE IF NOT EXISTS community_notices (
          notice_id INTEGER PRIMARY KEY AUTOINCREMENT,
          title TEXT NOT NULL,
          content TEXT NOT NULL DEFAULT '',
          publisher TEXT NOT NULL DEFAULT '社区管理员',
          status TEXT NOT NULL DEFAULT '已发布' CHECK(status IN ('已发布','已撤回')),
          published_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
        CREATE TABLE IF NOT EXISTS dashboard_baselines (
          baseline_id INTEGER PRIMARY KEY CHECK(baseline_id=1),
          elder_base INTEGER NOT NULL,
          order_base INTEGER NOT NULL,
          provider_base INTEGER NOT NULL,
          institution_count INTEGER NOT NULL,
          elder_seed_count INTEGER NOT NULL,
          order_seed_count INTEGER NOT NULL,
          provider_seed_count INTEGER NOT NULL,
          trend_order_base TEXT NOT NULL DEFAULT '[14620,17280,19560,21840,23610,24860]',
          trend_completed_base TEXT NOT NULL DEFAULT '[13210,15890,17930,20120,22160,23980]');
        """)
        _ensure_column(connection, "auth_accounts", "profile_completed", "INTEGER NOT NULL DEFAULT 0")
        connection.execute("UPDATE auth_accounts SET profile_completed=1 WHERE role IN ('admin','guest') OR elder_code IS NOT NULL OR provider_code IS NOT NULL")
        _ensure_column(connection, "dashboard_baselines", "trend_order_base", "TEXT NOT NULL DEFAULT '[14620,17280,19560,21840,23610,24860]'")
        _ensure_column(connection, "dashboard_baselines", "trend_completed_base", "TEXT NOT NULL DEFAULT '[13210,15890,17930,20120,22160,23980]'")
        _migrate_emergency_table(connection)
        accounts = [
            ("admin", "Admin123!", "admin", "陈雅", None, None),
            ("elder", "Elder123!", "elder", "李秀梅", "JX202600128", None),
            ("provider", "Provider123!", "provider", "王建国", None, "FW20260218"),
            ("guest", "Guest123!", "guest", "游客", None, None),
        ]
        for username, password, role, display_name, elder_code, provider_code in accounts:
            completed = 1 if role in {'admin','guest'} or elder_code or provider_code else 0
            connection.execute("INSERT OR IGNORE INTO auth_accounts(username,password_hash,role,display_name,elder_code,provider_code,profile_completed) VALUES(?,?,?,?,?,?,?)", (username, hash_password(password), role, display_name, elder_code, provider_code, completed))
        connection.executescript("""
        CREATE INDEX IF NOT EXISTS idx_auth_sessions_expiry ON auth_sessions(expires_at);
        CREATE INDEX IF NOT EXISTS idx_reviews_elder ON service_reviews(elder_id, created_at);
        CREATE INDEX IF NOT EXISTS idx_reviews_provider ON service_reviews(provider_id, created_at);
        CREATE INDEX IF NOT EXISTS idx_complaints_elder ON service_complaints(elder_id, created_at);
        CREATE INDEX IF NOT EXISTS idx_complaints_provider ON service_complaints(provider_id, created_at);
        CREATE INDEX IF NOT EXISTS idx_vitals_elder_time ON health_vitals(elder_id, measured_at);
        CREATE INDEX IF NOT EXISTS idx_exam_reports_elder_date ON exam_reports(elder_id, exam_date);
        CREATE INDEX IF NOT EXISTS idx_health_alert_status ON health_alerts(status, created_at);
        CREATE INDEX IF NOT EXISTS idx_health_alert_elder ON health_alerts(elder_id, created_at);
        CREATE INDEX IF NOT EXISTS idx_emergency_elder_time ON emergency_requests(elder_id, created_at);
        CREATE INDEX IF NOT EXISTS idx_emergency_status ON emergency_requests(status, created_at);
        CREATE INDEX IF NOT EXISTS idx_notices_status_time ON community_notices(status, published_at);
        CREATE INDEX IF NOT EXISTS idx_elders_deleted ON elderly_profiles(is_deleted, elder_id);
        CREATE INDEX IF NOT EXISTS idx_providers_deleted ON service_providers(is_deleted, provider_id);
        """)
        # Existing acceptance databases must not be repopulated with fixed demo identities.
        if globals().get('_acceptance_skip_demo_seed', False):
            return
        if connection.execute("SELECT COUNT(*) c FROM community_notices").fetchone()["c"] == 0:
            connection.executemany("INSERT INTO community_notices(title,content,published_at) VALUES(?,?,?)", [
                ("高温天气关怀提醒", "近期气温升高，请服务人员上门时关注老人饮水与室内通风。", "2026-09-13 09:00:00"),
                ("中秋节社区活动报名", "9月25日社区长者中秋茶话会开放报名，欢迎老人参加。", "2026-09-12 09:00:00"),
                ("护理员培训通知", "本月护理员急救技能复训安排已发布，请相关人员查看。", "2026-09-10 09:00:00")
            ])
        if connection.execute("SELECT 1 FROM dashboard_baselines WHERE baseline_id=1").fetchone() is None:
            elder_count = connection.execute("SELECT COUNT(*) c FROM elderly_profiles WHERE is_deleted=0").fetchone()["c"]
            order_count = connection.execute("SELECT COUNT(*) c FROM service_orders").fetchone()["c"]
            provider_count = connection.execute("SELECT COUNT(*) c FROM service_providers WHERE is_deleted=0").fetchone()["c"]
            connection.execute("""INSERT INTO dashboard_baselines(
              baseline_id,elder_base,order_base,provider_base,institution_count,elder_seed_count,order_seed_count,provider_seed_count,trend_order_base,trend_completed_base)
              VALUES(1,50286,248600,816,100,?,?,?,'[14620,17280,19560,21840,23610,24860]','[13210,15890,17930,20120,22160,23980]')""", (elder_count, order_count, provider_count))
        connection.execute("""UPDATE elderly_profiles SET gender=COALESCE(NULLIF(gender,''),'女'), id_card=CASE WHEN id_card='' THEN '3601**********2846' ELSE id_card END,
          birth_date=CASE WHEN birth_date='' THEN '1948-03-12' ELSE birth_date END, community=CASE WHEN community='' THEN '青云社区' ELSE community END,
          height_cm=COALESCE(height_cm,158), weight_kg=COALESCE(weight_kg,58.2), blood_type=CASE WHEN blood_type='' THEN 'A 型' ELSE blood_type END,
          allergy_history=CASE WHEN allergy_history='' THEN '青霉素过敏' ELSE allergy_history END, disease_history=CASE WHEN disease_history='' THEN '高血压、骨质疏松' ELSE disease_history END,
          self_care_level=CASE WHEN self_care_level='' THEN '轻度失能' ELSE self_care_level END, medical_type=CASE WHEN medical_type='' THEN '城镇职工医保' ELSE medical_type END,
          medical_card_no=CASE WHEN medical_card_no='' THEN 'JX36****8821' ELSE medical_card_no END, designated_hospital=CASE WHEN designated_hospital='' THEN '青云社区卫生服务中心' ELSE designated_hospital END,
          emergency_contact_name=CASE WHEN emergency_contact_name='' THEN '李晓华' ELSE emergency_contact_name END, emergency_contact_relation=CASE WHEN emergency_contact_relation='' THEN '女儿' ELSE emergency_contact_relation END,
          emergency_contact_phone=CASE WHEN emergency_contact_phone='' THEN '138****6721' ELSE emergency_contact_phone END, living_situation=CASE WHEN living_situation='' THEN '独居' ELSE living_situation END,
          language_preference=CASE WHEN language_preference='' THEN '本地方言' ELSE language_preference END, diet_requirement=CASE WHEN diet_requirement='' THEN '低盐饮食' ELSE diet_requirement END,
          visit_time_preference=CASE WHEN visit_time_preference='' THEN '上午 09:00-11:00' ELSE visit_time_preference END,
          elder_tags=CASE WHEN elder_tags='' OR elder_tags IS NULL THEN '独居老人、高龄老人（80岁以上）' ELSE elder_tags END,
          service_priority=CASE WHEN service_priority='' OR service_priority IS NULL THEN '普通' ELSE service_priority END WHERE elder_code='JX202600128'""")
        demo_elders = [
            ('JX202600127','男','1954-07-08','完全自理','与配偶同住','空巢老人'),
            ('JX202600126','女','1945-11-23','中度失能','与子女同住','高龄老人（80岁以上）'),
            ('JX202600125','男','1957-02-19','完全自理','与配偶同住','普通'),
            ('JX202600124','女','1950-05-09','轻度失能','独居','独居老人')
        ]
        for code, gender, birth, level, living, tags in demo_elders:
            connection.execute("""UPDATE elderly_profiles SET
              gender=CASE WHEN gender='' THEN ? ELSE gender END,
              birth_date=CASE WHEN birth_date='' THEN ? ELSE birth_date END,
              self_care_level=CASE WHEN self_care_level='' OR self_care_level='完全自理' THEN ? ELSE self_care_level END,
              living_situation=CASE WHEN living_situation='' THEN ? ELSE living_situation END,
              elder_tags=CASE WHEN elder_tags='' OR elder_tags='普通' THEN ? ELSE elder_tags END
              WHERE elder_code=?""", (gender,birth,level,living,tags,code))
        for elder_row in connection.execute("SELECT elder_id,birth_date,self_care_level FROM elderly_profiles"):
            priority = calculate_service_priority(age_from_birth(elder_row["birth_date"]), elder_row["self_care_level"])
            connection.execute("UPDATE elderly_profiles SET service_priority=? WHERE elder_id=?", (priority, elder_row["elder_id"]))
        connection.execute("""UPDATE service_providers SET gender=COALESCE(NULLIF(gender,''),'男'), id_card=CASE WHEN id_card='' THEN '3601**********1612' ELSE id_card END,
          service_years=CASE WHEN service_years=0 THEN 8 ELSE service_years END, health_certificate=CASE WHEN health_certificate='' THEN '有效期至 2027-05-20' ELSE health_certificate END,
          training_records=CASE WHEN training_records='' THEN '老年人跌倒预防 2026-09-18 合格\n急救技能复训 2026-06-20 优秀' ELSE training_records END,
          profile_intro=CASE WHEN profile_intro='' THEN '从事养老护理 8 年，擅长失能老人照护与康复协助。' ELSE profile_intro END WHERE provider_code='FW20260218'""")
        elder = connection.execute("SELECT elder_id FROM elderly_profiles WHERE elder_code='JX202600128'").fetchone()
        if elder and connection.execute("SELECT COUNT(*) c FROM health_vitals WHERE elder_id=?", (elder["elder_id"],)).fetchone()["c"] == 0:
            from datetime import timedelta
            base_date = datetime(2026, 9, 17, 8, 10)
            for i in range(30):
                day = base_date - timedelta(days=29-i)
                connection.execute("""INSERT INTO health_vitals(elder_id,measured_at,systolic,diastolic,glucose,heart_rate,temperature,weight,sleep_hours,source)
                  VALUES(?,?,?,?,?,?,?,?,?,?)""", (elder["elder_id"], day.strftime('%Y-%m-%d %H:%M:%S'), 124+(i%9), 74+(i%6), round(5.4+(i%7)*0.12,1), 68+(i%10), round(36.3+(i%4)*0.1,1), round(58.8-(i%6)*0.1,1), round(6.0+(i%6)*0.3,1), '智能手环' if i%2==0 else '手动录入'))
        if connection.execute("SELECT COUNT(*) c FROM emergency_requests").fetchone()["c"] == 0:
            demo = [
                ('JQ20260915001','JX202600128','身体不适','APP一键呼叫',28.65,115.92,'服务人员已出发','FW20260218','处理中','2026-09-15 16:42:00'),
                ('JQ20260915002','JX202600126','跌倒','智能手环',28.66,115.91,'已到达','FW20260106','处理中','2026-09-15 10:18:00'),
                ('JQ20260914003','JX202600127','煤气泄漏','呼叫器',28.64,115.90,'待调度',None,'待处理','2026-09-14 19:06:00'),
                ('JQ20260913004','JX202600125','火灾','APP一键呼叫',28.63,115.89,'已解决',None,'已解决','2026-09-13 08:30:00'),
                ('JQ20260912005','JX202600124','入室危险','呼叫器',28.62,115.88,'误报取消',None,'误报','2026-09-12 21:44:00'),
            ]
            for no, elder_code, etype, source, lat, lng, status, provider_code, result, created in demo:
                e = connection.execute("SELECT elder_id,address FROM elderly_profiles WHERE elder_code=? AND is_deleted=0", (elder_code,)).fetchone()
                p = connection.execute("SELECT provider_id FROM service_providers WHERE provider_code=? AND is_deleted=0", (provider_code,)).fetchone() if provider_code else None
                connection.execute("""INSERT INTO emergency_requests(emergency_no,elder_id,emergency_type,trigger_source,address,latitude,longitude,status,assigned_provider_id,result,created_at,updated_at)
                  VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""", (no,e["elder_id"],etype,source,e["address"],lat,lng,status,p["provider_id"] if p else None,result,created,created))
        wang = connection.execute("SELECT provider_id FROM service_providers WHERE provider_code='FW20260218'").fetchone()
        if wang and connection.execute("SELECT COUNT(*) c FROM service_reviews WHERE provider_id=?", (wang["provider_id"],)).fetchone()["c"] == 0:
            demo_order = connection.execute("SELECT order_id FROM service_orders WHERE order_no='YY202609080033'").fetchone()
            if demo_order is None:
                elder_demo = connection.execute("SELECT elder_id,address,phone FROM elderly_profiles WHERE elder_code='JX202600128'").fetchone()
                service_demo = connection.execute("SELECT service_item_id FROM service_items WHERE service_code='JS-002'").fetchone()
                cur = connection.execute("""INSERT INTO service_orders(order_no,elder_id,service_item_id,service_date,time_slot,duration_minutes,service_address,contact_phone,special_requirements,status,created_at,updated_at)
                  VALUES('YY202609080033',?,?,?,?,?,?,?,?, 'COMPLETED','2026-09-07 10:00:00','2026-09-08 16:05:00')""", (elder_demo["elder_id"],service_demo["service_item_id"],'2026-09-08','下午 15:00-16:00',60,elder_demo["address"],elder_demo["phone"],'需会本地方言'))
                demo_order_id = cur.lastrowid
                connection.execute("""INSERT INTO service_assignments(order_id,provider_id,assigned_by,assigned_at,assignment_status,depart_at,arrive_at,finish_at) VALUES(?,?,'陈雅','2026-09-07 11:00:00','FINISHED','2026-09-08 14:35:00','2026-09-08 14:55:00','2026-09-08 16:00:00')""", (demo_order_id, wang["provider_id"]))
            else:
                demo_order_id = demo_order["order_id"]
            elder_id = connection.execute("SELECT elder_id FROM service_orders WHERE order_id=?", (demo_order_id,)).fetchone()["elder_id"]
            if connection.execute("SELECT 1 FROM service_reviews WHERE order_id=?", (demo_order_id,)).fetchone() is None:
                connection.execute("""INSERT INTO service_reviews(order_id,elder_id,provider_id,attitude_score,quality_score,punctual_score,review_text,photo_note,created_at) VALUES(?,?,?,?,?,?,?,?,?)""", (demo_order_id,elder_id,wang["provider_id"],5,5,5,'服务很耐心，沟通清楚，按时上门。','1 张','2026-09-08 18:20:00'))
            if connection.execute("SELECT COUNT(*) c FROM service_complaints").fetchone()["c"] == 0:
                connection.execute("""INSERT INTO service_complaints(complaint_no,order_id,elder_id,provider_id,complaint_type,complaint_content,evidence_note,status,admin_reply,created_at,updated_at)
                  VALUES('TS20260908001',?,?,?,?,?,'通话记录截图 1 张','已处理','已核实并完成回访。','2026-09-08 19:10:00','2026-09-08 20:00:00')""",
                  (demo_order_id,elder_id,wang["provider_id"],'未按时上门','服务到达时间比预约时间晚，已向社区反馈。'))

def initialize_database(reset=False):
    if reset and DB_PATH.exists():
        DB_PATH.unlink()
    if not DB_PATH.exists():
        with connect_db() as connection:
            connection.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    migrate_database()


def order_number(order_id):
    return f"YY{datetime.now():%Y%m%d}{order_id:05d}"


def now_text():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def age_from_birth(birth_date):
    try:
        born = datetime.strptime(str(birth_date), "%Y-%m-%d").date()
        today = date.today()
        return today.year - born.year - ((today.month, today.day) < (born.month, born.day))
    except ValueError:
        return 0


def calculate_service_priority(age, self_care_level):
    age_score = 2 if age >= 80 else 1 if age >= 75 else 0
    care_score = {"完全自理": 0, "自理": 0, "轻度失能": 1, "中度失能": 2, "重度失能": 3}.get(self_care_level, 0)
    return "优先" if age_score + care_score >= 2 else "普通"


class ApiHandler(BaseHTTPRequestHandler):
    server_version = "ElderlyCareCourseDesign/2.5"

    def send_json(self, status, payload):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def read_json(self):
        try:
            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(length).decode("utf-8") if length else "{}"
            return json.loads(raw)
        except (ValueError, json.JSONDecodeError, UnicodeDecodeError):
            raise ValueError("请求数据不是有效的 JSON")

    def api_error(self, status, message):
        self.send_json(status, {"ok": False, "error": message})

    def current_user(self):
        header = self.headers.get("Authorization", "")
        token = header[7:].strip() if header.startswith("Bearer ") else ""
        if not token:
            return None
        now = datetime.now()
        cached = SESSIONS.get(token)
        if cached and cached["expires_at"] > now:
            return cached["user"]
        with connect_db() as connection:
            row = connection.execute("""SELECT a.* , s.expires_at FROM auth_sessions s
              JOIN auth_accounts a ON a.username=s.username
              WHERE s.token=? AND a.enabled=1""", (token,)).fetchone()
            if row is None:
                return None
            expires_at = datetime.strptime(row["expires_at"], "%Y-%m-%d %H:%M:%S")
            if expires_at <= now:
                connection.execute("DELETE FROM auth_sessions WHERE token=?", (token,))
                SESSIONS.pop(token, None)
                return None
            user = self.session_user(row)
            SESSIONS[token] = {"user": user, "expires_at": expires_at}
            return user

    def create_session(self, user, remember=False):
        token = secrets.token_urlsafe(32)
        expires_at = datetime.now() + (timedelta(days=7) if remember else timedelta(hours=12))
        expires_text = expires_at.strftime("%Y-%m-%d %H:%M:%S")
        with connect_db() as connection:
            connection.execute("DELETE FROM auth_sessions WHERE expires_at<=?", (datetime.now().strftime("%Y-%m-%d %H:%M:%S"),))
            connection.execute("INSERT INTO auth_sessions(token,username,expires_at) VALUES(?,?,?)", (token, user["username"], expires_text))
        SESSIONS[token] = {"user": user, "expires_at": expires_at}
        return token

    def require_role(self, *roles):
        user = self.current_user()
        if user is None:
            raise PermissionError("请先登录")
        if user.get("role") in {"elder", "provider"} and not user.get("profile_completed"):
            raise PermissionError("请先完成个人档案")
        if roles and user["role"] not in roles:
            raise PermissionError("当前角色无权执行此操作")
        return user

    def session_user(self, account):
        return {
            "username": account["username"],
            "role": account["role"],
            "display_name": account["display_name"],
            "elder_code": account["elder_code"],
            "provider_code": account["provider_code"],
            "profile_completed": bool(account["profile_completed"]),
        }

    def login(self):
        payload = self.read_json()
        username = str(payload.get("username", "")).strip()
        password = str(payload.get("password", ""))
        role = str(payload.get("role", "")).strip()
        with connect_db() as connection:
            account = connection.execute("SELECT * FROM auth_accounts WHERE username=? AND enabled=1", (username,)).fetchone()
        if account is None or not hmac.compare_digest(account["password_hash"], hash_password(password)):
            raise PermissionError("用户名或密码错误")
        if role and role != account["role"]:
            raise PermissionError("账号与所选角色不匹配")
        user = self.session_user(account)
        token = self.create_session(user, bool(payload.get("remember")))
        self.send_json(200, {"ok": True, "token": token, "user": user})

    def register_account(self):
        payload = self.read_json()
        username = str(payload.get("username", "")).strip()
        password = str(payload.get("password", ""))
        role = str(payload.get("role", "")).strip()
        if role not in {"elder", "provider"}:
            raise ValueError("仅老人 / 家属和服务人员可自行注册")
        if not re.fullmatch(r"[A-Za-z0-9_-]{4,32}", username):
            raise ValueError("账号需为 4-32 位字母、数字、下划线或短横线")
        if len(password) < 8:
            raise ValueError("密码至少 8 位")
        if not re.search(r"[A-Za-z]", password) or not re.search(r"\d", password):
            raise ValueError("密码需同时包含字母和数字")
        with connect_db() as connection:
            if connection.execute("SELECT 1 FROM auth_accounts WHERE username=?", (username,)).fetchone():
                raise ValueError("该账号已被注册")
            connection.execute(
                "INSERT INTO auth_accounts(username,password_hash,role,display_name,profile_completed,enabled) VALUES(?,?,?,?,0,1)",
                (username, hash_password(password), role, username),
            )
        self.send_json(201, {"ok": True, "message": "注册成功，请登录并完成个人档案", "role": role, "username": username})

    def guest_login(self):
        user = {"username": "guest", "role": "guest", "display_name": "游客", "elder_code": None, "provider_code": None, "profile_completed": True}
        token = self.create_session(user)
        self.send_json(200, {"ok": True, "token": token, "user": user})

    def complete_profile(self):
        user = self.current_user()
        if user is None:
            raise PermissionError("请先登录")
        if user.get("role") not in {"elder", "provider"}:
            raise PermissionError("当前角色无需完善注册档案")
        if user.get("profile_completed"):
            raise ValueError("个人档案已经完成")
        payload = self.read_json()
        username = user["username"]
        role = user["role"]
        full_name = str(payload.get("full_name") or "").strip()
        phone = str(payload.get("phone") or "").strip()
        gender = str(payload.get("gender") or "").strip()
        if not full_name or not phone or gender not in {"男", "女"}:
            raise ValueError("请填写姓名、性别和联系电话")
        with connect_db() as connection:
            account = connection.execute("SELECT * FROM auth_accounts WHERE username=? AND enabled=1", (username,)).fetchone()
            if account is None:
                raise PermissionError("账号不存在或已停用")
            if account["profile_completed"]:
                raise ValueError("个人档案已经完成")
            if role == "elder":
                birth_date = str(payload.get("birth_date") or "").strip()
                address = str(payload.get("address") or "").strip()
                community = str(payload.get("community") or "青云社区").strip()
                self_care_level = str(payload.get("self_care_level") or "").strip()
                living_situation = str(payload.get("living_situation") or "").strip()
                if not birth_date or not address or not self_care_level or not living_situation:
                    raise ValueError("请完整填写出生日期、居住地址、自理能力和居住情况")
                age = age_from_birth(birth_date)
                if age < 60 or age > 120:
                    raise ValueError("老人档案年龄应在 60 至 120 岁之间")
                priority = calculate_service_priority(age, self_care_level)
                cursor = connection.execute("""INSERT INTO elderly_profiles(
                  elder_code,full_name,phone,address,community,gender,birth_date,self_care_level,living_situation,
                  emergency_contact_name,emergency_contact_relation,emergency_contact_phone,service_priority)
                  VALUES('',?,?,?,?,?,?,?,?,?,?,?,?)""", (
                    full_name, phone, address, community, gender, birth_date, self_care_level, living_situation,
                    str(payload.get("emergency_contact_name") or "").strip(),
                    str(payload.get("emergency_contact_relation") or "").strip(),
                    str(payload.get("emergency_contact_phone") or "").strip(), priority,
                ))
                code = f"JX{date.today():%Y}{cursor.lastrowid:06d}"
                connection.execute("UPDATE elderly_profiles SET elder_code=? WHERE elder_id=?", (code, cursor.lastrowid))
                connection.execute("UPDATE auth_accounts SET display_name=?,elder_code=?,profile_completed=1 WHERE username=?", (full_name, code, username))
                user.update({"display_name": full_name, "elder_code": code, "profile_completed": True})
            else:
                organization = str(payload.get("organization") or "").strip()
                skills = str(payload.get("skills") or "").strip()
                service_areas = str(payload.get("service_areas") or "").strip()
                certificates = str(payload.get("certificates") or "").strip()
                if not organization or not skills or not service_areas or not certificates:
                    raise ValueError("请完整填写所属机构、服务类型、服务区域和资质证书")
                try:
                    service_years = max(0, int(payload.get("service_years") or 0))
                except (TypeError, ValueError):
                    raise ValueError("服务年限必须为整数")
                cursor = connection.execute("""INSERT INTO service_providers(
                  provider_code,full_name,phone,organization,skills,service_areas,certificates,gender,service_years,
                  health_certificate,profile_intro,employment_status,is_deleted)
                  VALUES('',?,?,?,?,?,?,?,?,?,?,'在职',0)""", (
                    full_name, phone, organization, skills, service_areas, certificates, gender, service_years,
                    str(payload.get("health_certificate") or "").strip(), str(payload.get("profile_intro") or "").strip(),
                ))
                code = f"FW{date.today():%Y}{cursor.lastrowid:04d}"
                connection.execute("UPDATE service_providers SET provider_code=? WHERE provider_id=?", (code, cursor.lastrowid))
                connection.execute("UPDATE auth_accounts SET display_name=?,provider_code=?,profile_completed=1 WHERE username=?", (full_name, code, username))
                user.update({"display_name": full_name, "provider_code": code, "profile_completed": True})
        self.send_json(200, {"ok": True, "message": "个人档案已完成", "user": user})

    def logout(self):
        header = self.headers.get("Authorization", "")
        token = header[7:].strip() if header.startswith("Bearer ") else ""
        if token:
            SESSIONS.pop(token, None)
            with connect_db() as connection:
                connection.execute("DELETE FROM auth_sessions WHERE token=?", (token,))
        self.send_json(200, {"ok": True})

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        try:
            if not path.startswith("/api/"):
                return self.serve_static(path)
            if path == "/api/health":
                return self.send_json(200, {"ok": True, "service": "elderly-care-api", "database": DB_PATH.name})
            if path == "/api/auth/me":
                user = self.current_user()
                if user is None:
                    raise PermissionError("请先登录")
                return self.send_json(200, {"ok": True, "user": user})
            self.require_role("admin", "elder", "provider", "guest")
            if path == "/api/options":
                return self.get_options()
            if path == "/api/elders":
                return self.get_elders()
            if path == "/api/providers":
                return self.get_providers()
            if path == "/api/notices":
                return self.get_notices()
            if path == "/api/dashboard-stats":
                self.require_role("admin")
                return self.get_dashboard_stats()
            if path == "/api/reports/export":
                self.require_role("admin")
                return self.export_report(parse_qs(parsed.query))
            if path == "/api/elders/import-template":
                self.require_role("admin")
                return self.download_elder_import_template()
            if path == "/api/exam-reports":
                self.require_role("admin", "elder")
                return self.get_exam_reports(parse_qs(parsed.query))
            if path.startswith("/api/exam-reports/") and path.endswith("/download"):
                self.require_role("admin", "elder")
                return self.download_exam_report(unquote(path.strip("/").split("/")[2]))
            if path == "/api/emergencies":
                return self.get_emergencies(parse_qs(parsed.query))
            if path == "/api/orders":
                return self.get_orders(parse_qs(parsed.query))
            if path.startswith("/api/profiles/elder/"):
                return self.get_elder_profile(unquote(path.split("/")[4]))
            if path.startswith("/api/profiles/provider/"):
                return self.get_provider_profile(unquote(path.split("/")[4]))
            if path == "/api/reviews":
                return self.get_reviews(parse_qs(parsed.query))
            if path == "/api/health-records":
                return self.get_health_records(parse_qs(parsed.query))
            if path == "/api/health-alerts":
                self.require_role("admin")
                return self.get_health_alerts(parse_qs(parsed.query))
            if path == "/api/complaints":
                self.require_role("admin", "elder", "provider")
                return self.get_complaints(parse_qs(parsed.query))
            if path.startswith("/api/orders/"):
                parts = path.strip("/").split("/")
                token = unquote(parts[2])
                if len(parts) == 4 and parts[3] == "recommend":
                    self.require_role("admin")
                    return self.recommend_providers(token)
                return self.get_order(token)
            self.api_error(404, "接口不存在")
        except PermissionError as error:
            self.api_error(403, str(error))
        except ValueError as error:
            self.api_error(400, str(error))
        except Exception as error:
            self.api_error(500, f"服务器处理失败：{error}")

    def do_POST(self):
        path = urlparse(self.path).path
        try:
            if path == "/api/auth/login":
                return self.login()
            if path == "/api/auth/register":
                return self.register_account()
            if path == "/api/auth/guest":
                return self.guest_login()
            if path == "/api/auth/complete-profile":
                return self.complete_profile()
            if path == "/api/auth/logout":
                return self.logout()
            user = self.require_role("admin", "elder", "provider", "guest")
            if path == "/api/orders":
                self.require_role("admin", "elder")
                return self.create_order()
            if path == "/api/elders":
                self.require_role("admin")
                return self.create_elder()
            if path == "/api/elders/import":
                self.require_role("admin")
                return self.import_elders_excel()
            if path == "/api/exam-reports":
                self.require_role("admin", "elder")
                return self.upload_exam_report()
            if path == "/api/providers":
                self.require_role("admin")
                return self.create_provider()
            if path == "/api/notices":
                self.require_role("admin")
                return self.create_notice()
            if path == "/api/health-records":
                self.require_role("elder")
                return self.create_health_record()
            if path == "/api/emergencies":
                self.require_role("elder")
                return self.create_emergency()
            if path == "/api/complaints":
                self.require_role("elder")
                return self.create_complaint()
            if path.startswith("/api/profiles/elder/"):
                self.require_role("admin", "elder")
                return self.update_elder_profile(unquote(path.split("/")[4]))
            if path.startswith("/api/profiles/provider/"):
                self.require_role("admin", "provider")
                return self.update_provider_profile(unquote(path.split("/")[4]))
            parts = path.strip("/").split("/")
            if len(parts) == 4 and parts[:2] == ["api", "health-alerts"] and parts[3] == "handle":
                self.require_role("admin")
                return self.handle_health_alert(unquote(parts[2]))
            if len(parts) == 4 and parts[:2] == ["api", "emergencies"]:
                token, action = unquote(parts[2]), parts[3]
                if action == "dispatch":
                    self.require_role("admin")
                    return self.dispatch_emergency(token)
                if action == "depart":
                    self.require_role("provider", "admin")
                    return self.depart_emergency(token)
                if action == "arrive":
                    self.require_role("provider", "admin")
                    return self.arrive_emergency(token)
                if action == "complete":
                    self.require_role("provider", "admin")
                    return self.complete_emergency(token)
                if action == "cancel":
                    self.require_role("elder", "admin")
                    return self.cancel_emergency(token)
            if len(parts) == 4 and parts[:2] == ["api", "complaints"] and parts[3] == "appeal":
                self.require_role("provider")
                return self.appeal_complaint(unquote(parts[2]))
            if len(parts) == 4 and parts[:2] == ["api", "orders"]:
                token, action = unquote(parts[2]), parts[3]
                if action == "assign":
                    self.require_role("admin")
                    return self.assign_order(token)
                if action == "depart":
                    self.require_role("provider")
                    return self.depart_order(token)
                if action == "arrive":
                    self.require_role("provider")
                    return self.arrive_order(token)
                if action == "complete":
                    self.require_role("provider")
                    return self.complete_order(token)
                if action == "records":
                    self.require_role("provider")
                    return self.create_service_record(token)
                if action == "cancel":
                    self.require_role("elder", "admin")
                    return self.cancel_order(token)
                if action == "review":
                    self.require_role("elder")
                    return self.create_review(token)
            self.api_error(404, "接口不存在")
        except PermissionError as error:
            self.api_error(403, str(error))
        except ValueError as error:
            self.api_error(400, str(error))
        except sqlite3.IntegrityError as error:
            self.api_error(409, f"数据库约束校验失败：{error}")
        except Exception as error:
            self.api_error(500, f"服务器处理失败：{error}")

    def do_DELETE(self):
        path = urlparse(self.path).path
        try:
            self.require_role("admin")
            parts = path.strip("/").split("/")
            if len(parts) == 3 and parts[:2] == ["api", "notices"]:
                return self.delete_notice(unquote(parts[2]))
            if len(parts) == 3 and parts[:2] == ["api", "elders"]:
                return self.delete_elder(unquote(parts[2]))
            if len(parts) == 3 and parts[:2] == ["api", "providers"]:
                return self.delete_provider(unquote(parts[2]))
            self.api_error(404, "接口不存在")
        except PermissionError as error:
            self.api_error(403, str(error))
        except ValueError as error:
            self.api_error(400, str(error))
        except sqlite3.IntegrityError as error:
            self.api_error(409, f"数据库约束校验失败：{error}")
        except Exception as error:
            self.api_error(500, f"服务器处理失败：{error}")

    def resolve_order(self, connection, token):
        row = connection.execute(
            "SELECT * FROM service_orders WHERE order_no = ? OR CAST(order_id AS TEXT) = ?",
            (token, token),
        ).fetchone()
        if row is None:
            raise ValueError("订单不存在")
        return row

    def order_select_sql(self, where=""):
        return f"""
          SELECT o.order_id, o.order_no,
                 e.elder_code, e.full_name AS elder_name, e.phone AS elder_phone,
                 s.service_code, s.service_name, s.service_type, s.price,
                 o.service_date, o.time_slot, o.duration_minutes, o.service_address,
                 o.service_latitude, o.service_longitude, o.contact_phone, o.special_requirements, o.meal_type, o.delivery_time,
                 o.medical_need, o.symptom_description, o.status, o.audit_remark,
                 o.cancel_reason, o.created_at, o.updated_at,
                 p.provider_code, p.full_name AS provider_name,
                 a.assignment_status, a.assigned_at, a.depart_at,
                 a.depart_latitude, a.depart_longitude, a.arrive_at, a.finish_at,
                 (SELECT COUNT(*) FROM service_records r WHERE r.order_id=o.order_id) AS record_count
          FROM service_orders o
          JOIN elderly_profiles e ON e.elder_id=o.elder_id
          JOIN service_items s ON s.service_item_id=o.service_item_id
          LEFT JOIN service_assignments a ON a.order_id=o.order_id
          LEFT JOIN service_providers p ON p.provider_id=a.provider_id
          {where}
        """

    def row_to_order(self, row):
        item = dict(row)
        item["status_label"] = STATUS_ZH.get(item["status"], item["status"])
        return item

    def get_notices(self):
        with connect_db() as connection:
            rows = [dict(row) for row in connection.execute("SELECT * FROM community_notices WHERE status='已发布' ORDER BY published_at DESC, notice_id DESC")]
        self.send_json(200, {"ok": True, "count": len(rows), "notices": rows})

    def create_notice(self):
        payload = self.read_json()
        title = str(payload.get("title", "")).strip()
        content = str(payload.get("content", "")).strip()
        if not title:
            raise ValueError("公告标题不能为空")
        with connect_db() as connection:
            cursor = connection.execute("INSERT INTO community_notices(title,content,publisher,published_at) VALUES(?,?,?,?)", (title, content, str(payload.get("publisher") or "陈雅"), now_text()))
            row = connection.execute("SELECT * FROM community_notices WHERE notice_id=?", (cursor.lastrowid,)).fetchone()
        self.send_json(201, {"ok": True, "message": "公告已发布", "notice": dict(row)})

    def delete_notice(self, token):
        with connect_db() as connection:
            row = connection.execute("SELECT notice_id FROM community_notices WHERE notice_id=?", (token,)).fetchone()
            if row is None:
                raise ValueError("公告不存在")
            connection.execute("DELETE FROM community_notices WHERE notice_id=?", (row["notice_id"],))
        self.send_json(200, {"ok": True, "message": "公告已删除"})

    def get_dashboard_stats(self):
        all_months = list(range(4, 13))
        order_base = [14620, 17280, 19560, 21840, 23610, 24860, 26340, 27620, 28940]
        completed_base = [13210, 15890, 17930, 20120, 22160, 23980, 25110, 26380, 27650]
        today = demo_today()
        visible_months = [m for m in all_months if m <= today.month] if today.year == 2026 else all_months
        if not visible_months:
            visible_months = [4]
        order_extra = {month: 0 for month in all_months}
        completed_extra = {month: 0 for month in all_months}
        with connect_db() as connection:
            base = connection.execute("SELECT * FROM dashboard_baselines WHERE baseline_id=1").fetchone()
            elder_count = connection.execute("SELECT COUNT(*) c FROM elderly_profiles WHERE is_deleted=0").fetchone()["c"]
            order_count = connection.execute("SELECT COUNT(*) c FROM service_orders").fetchone()["c"]
            provider_count = connection.execute("SELECT COUNT(*) c FROM service_providers WHERE is_deleted=0").fetchone()["c"]
            pending = connection.execute("SELECT COUNT(*) c FROM service_orders WHERE status='PENDING_ASSIGN'").fetchone()["c"]
            active_emergency = connection.execute("SELECT COUNT(*) c FROM emergency_requests WHERE status IN ('待调度','已派单','服务人员已出发','已到达')").fetchone()["c"]
            order_delta = max(0, order_count - base["order_seed_count"])
            boundary = connection.execute("SELECT order_id FROM service_orders ORDER BY order_id LIMIT 1 OFFSET ?", (base["order_seed_count"],)).fetchone()
            if boundary:
                rows = connection.execute("SELECT service_date,status FROM service_orders WHERE order_id>=?", (boundary["order_id"],)).fetchall()
                for row in rows:
                    try:
                        service_day = date.fromisoformat(row["service_date"])
                    except (TypeError, ValueError):
                        continue
                    if service_day.year == 2026 and 4 <= service_day.month <= 12 and service_day <= today:
                        order_extra[service_day.month] += 1
                        if row["status"] == "COMPLETED":
                            completed_extra[service_day.month] += 1
        trend = []
        for month in visible_months:
            index = month - 4
            ratio = 1.0
            if month == today.month:
                ratio = today.day / monthrange(today.year, month)[1]
            trend.append({
                "month": f"{month:02d}月",
                "orders": round(order_base[index] * ratio) + order_extra[month],
                "completed": round(completed_base[index] * ratio) + completed_extra[month],
            })
        total_days = (DEMO_DATA_END - DEMO_DATA_START).days + 1
        elapsed_days = (today - DEMO_DATA_START).days + 1
        simulated_order_total = round(300000 * elapsed_days / total_days)
        service_type_targets = {
            "生活照料类": 150000,
            "医疗护理类": 80000,
            "康复保健类": 30000,
            "精神慰藉类": 20000,
            "紧急救援类": 20000,
        }
        service_type_counts = {name: round(target * elapsed_days / total_days) for name, target in service_type_targets.items()}
        stats = {
            "elder_count": max(0, base["elder_base"] + elder_count - base["elder_seed_count"]),
            "order_count": simulated_order_total + order_delta,
            "provider_count": max(0, base["provider_base"] + provider_count - base["provider_seed_count"]),
            "institution_count": base["institution_count"],
            "pending_orders": pending,
            "active_emergencies": active_emergency,
            "order_trend": trend,
            "service_type_counts": service_type_counts,
            "as_of": today.isoformat(),
            "demo_end": DEMO_DATA_END.isoformat(),
        }
        self.send_json(200, {"ok": True, "stats": stats})

    def send_bytes(self, status, body, content_type, filename=None):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        if filename:
            safe = filename.replace('"', "")
            self.send_header("Content-Disposition", f'attachment; filename="{safe}"')
        self.end_headers()
        self.wfile.write(body)

    def export_report(self, query):
        fmt = str((query.get("format") or ["excel"])[0]).lower()
        if fmt not in {"excel", "pdf"}:
            raise ValueError("format 仅支持 excel 或 pdf")
        today = demo_today()
        total_days = (DEMO_DATA_END - DEMO_DATA_START).days + 1
        elapsed_days = (today - DEMO_DATA_START).days + 1
        simulated_order_total = round(300000 * elapsed_days / total_days)
        service_type_targets = {
            "生活照料类": 150000,
            "医疗护理类": 80000,
            "康复保健类": 30000,
            "精神慰藉类": 20000,
            "紧急救援类": 20000,
        }
        simulated_service_types = [{"service_type": name, "orders": round(target * elapsed_days / total_days)} for name, target in service_type_targets.items()]
        with connect_db() as connection:
            elder_count = connection.execute("SELECT COUNT(*) c FROM elderly_profiles WHERE is_deleted=0").fetchone()["c"]
            provider_count = connection.execute("SELECT COUNT(*) c FROM service_providers WHERE is_deleted=0").fetchone()["c"]
            order_count = connection.execute("SELECT COUNT(*) c FROM service_orders").fetchone()["c"]
            completed_count = connection.execute("SELECT COUNT(*) c FROM service_orders WHERE status='COMPLETED'").fetchone()["c"]
            baseline = connection.execute("SELECT order_seed_count FROM dashboard_baselines WHERE baseline_id=1").fetchone()
            order_delta = max(0, order_count - (baseline["order_seed_count"] if baseline else order_count))
            display_order_total = simulated_order_total + order_delta
            providers = [dict(r) for r in connection.execute(
                "SELECT provider_code,full_name,organization,skills,service_areas,employment_status FROM service_providers WHERE is_deleted=0 ORDER BY provider_id LIMIT 100"
            )]
            service_types = [dict(r) for r in connection.execute("""
                SELECT si.service_type AS service_type, COUNT(so.order_id) AS orders
                FROM service_items si LEFT JOIN service_orders so ON so.service_item_id=si.service_item_id
                GROUP BY si.service_type ORDER BY orders DESC
            """)]
        if fmt == "excel":
            try:
                from openpyxl import Workbook
                from openpyxl.styles import Alignment, Font
            except ImportError as exc:
                raise ValueError("缺少 openpyxl，请先执行 pip install -r requirements.txt") from exc
            wb = Workbook()
            ws = wb.active
            ws.title = "统计概览"
            ws.append(["社区居家养老服务系统统计报表"])
            ws.append(["统计日期", today.isoformat()])
            ws.append(["老人档案数", elder_count])
            ws.append(["服务人员数", provider_count])
            ws.append(["演示年度服务订单（截至统计日）", display_order_total])
            ws.append(["当前演示库真实订单记录", order_count])
            ws.append(["当前演示库已完成订单", completed_count])
            ws["A1"].font = Font(bold=True, size=16)
            ws.merge_cells("A1:D1")
            ws["A1"].alignment = Alignment(horizontal="center")
            ws.column_dimensions["A"].width = 24
            ws.column_dimensions["B"].width = 24
            ws2 = wb.create_sheet("服务类型订单")
            ws2.append(["服务类型", "截至统计日模拟订单数"])
            for row in simulated_service_types:
                ws2.append([row["service_type"], row["orders"]])
            ws3 = wb.create_sheet("服务人员")
            ws3.append(["工号", "姓名", "机构", "服务类型", "服务区域", "状态"])
            for row in providers:
                ws3.append([row["provider_code"], row["full_name"], row["organization"], row["skills"], row["service_areas"], row["employment_status"]])
            for sheet in (ws2, ws3):
                for cell in sheet[1]:
                    cell.font = Font(bold=True)
                for col in sheet.columns:
                    letter = col[0].column_letter
                    sheet.column_dimensions[letter].width = min(36, max(12, max(len(str(c.value or "")) for c in col) + 2))
            buffer = io.BytesIO()
            wb.save(buffer)
            return self.send_bytes(200, buffer.getvalue(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", f"community_care_report_{today.isoformat()}.xlsx")
        try:
            from reportlab.lib.pagesizes import A4
            from reportlab.pdfbase import pdfmetrics
            from reportlab.pdfbase.cidfonts import UnicodeCIDFont
            from reportlab.pdfgen import canvas
        except ImportError as exc:
            raise ValueError("缺少 reportlab，请先执行 pip install -r requirements.txt") from exc
        buffer = io.BytesIO()
        pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))
        c = canvas.Canvas(buffer, pagesize=A4)
        width, height = A4
        c.setFont("STSong-Light", 18)
        c.drawString(55, height - 65, "社区居家养老服务系统统计报表")
        c.setFont("STSong-Light", 10)
        y = height - 95
        lines = [
            f"统计日期：{today.isoformat()}",
            f"老人档案数：{elder_count}",
            f"服务人员数：{provider_count}",
            f"演示年度服务订单（截至统计日）：{display_order_total}",
            f"当前演示库真实订单记录：{order_count}",
            f"当前演示库已完成订单：{completed_count}",
            "",
            "服务类型订单统计：",
        ]
        for row in simulated_service_types:
            lines.append(f"{row['service_type']}：{row['orders']} 单")
        lines += ["", "服务人员（最多展示 20 人）："]
        for row in providers[:20]:
            lines.append(f"{row['provider_code']}  {row['full_name']}  {row['skills']}  {row['employment_status']}")
        for line in lines:
            if y < 55:
                c.showPage()
                c.setFont("STSong-Light", 10)
                y = height - 55
            c.drawString(55, y, line)
            y -= 18
        c.save()
        return self.send_bytes(200, buffer.getvalue(), "application/pdf", f"community_care_report_{today.isoformat()}.pdf")

    def download_elder_import_template(self):
        try:
            from openpyxl import Workbook
            from openpyxl.styles import Font
        except ImportError as exc:
            raise ValueError("缺少 openpyxl，请先执行 pip install -r requirements.txt") from exc
        headers = ["档案编号", "姓名", "性别", "出生日期", "年龄", "联系电话", "所属社区", "居住地址", "自理能力", "居住情况", "身份证号", "既往病史", "过敏史", "紧急联系人", "紧急联系电话", "老人标签", "特殊饮食", "上门时间偏好"]
        wb = Workbook()
        ws = wb.active
        ws.title = "老人档案导入"
        ws.append(headers)
        ws.append(["", "示例老人", "女", "1950-01-01", "", "13800000000", "青云社区", "青云社区示例地址", "完全自理", "独居", "", "高血压", "", "示例联系人", "13900000000", "独居老人", "低盐饮食", "上午 09:00-11:00"])
        for cell in ws[1]:
            cell.font = Font(bold=True)
        for col in ws.columns:
            ws.column_dimensions[col[0].column_letter].width = max(14, min(26, max(len(str(c.value or "")) for c in col) + 2))
        help_sheet = wb.create_sheet("填写说明")
        notes = [
            ["必填字段", "姓名、性别、联系电话、自理能力、居住情况"],
            ["出生日期/年龄", "二选一即可；优先使用出生日期"],
            ["档案编号", "可留空，由系统自动生成；填写时需保证唯一"],
            ["自理能力", "完全自理 / 轻度失能 / 中度失能 / 重度失能"],
            ["居住情况", "独居 / 与配偶同住 / 与子女同住 / 养老机构居住"],
        ]
        for row in notes:
            help_sheet.append(row)
        buffer = io.BytesIO()
        wb.save(buffer)
        return self.send_bytes(200, buffer.getvalue(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "elder_import_template.xlsx")

    def import_elders_excel(self):
        payload = self.read_json()
        filename = str(payload.get("file_name") or "").strip()
        encoded = str(payload.get("file_data") or "")
        if not filename.lower().endswith((".xlsx", ".xlsm")):
            raise ValueError("请上传 .xlsx 或 .xlsm 格式的 Excel 文件")
        try:
            raw = base64.b64decode(encoded, validate=True)
        except Exception as exc:
            raise ValueError("Excel 文件数据无效") from exc
        if len(raw) > 8 * 1024 * 1024:
            raise ValueError("Excel 文件不能超过 8MB")
        try:
            from openpyxl import load_workbook
        except ImportError as exc:
            raise ValueError("缺少 openpyxl，请先执行 pip install -r requirements.txt") from exc
        try:
            wb = load_workbook(io.BytesIO(raw), data_only=True)
            ws = wb.worksheets[0]
        except Exception as exc:
            raise ValueError("无法读取 Excel，请确认文件未损坏且格式正确") from exc
        headers = [str(cell.value or "").strip() for cell in ws[1]]
        index = {name: i for i, name in enumerate(headers)}
        required = ["姓名", "性别", "联系电话", "自理能力", "居住情况"]
        missing = [name for name in required if name not in index]
        if missing:
            raise ValueError("Excel 缺少列：" + "、".join(missing))
        def value(row, name):
            i = index.get(name)
            if i is None or i >= len(row):
                return ""
            item = row[i].value
            if isinstance(item, datetime):
                return item.date().isoformat()
            if isinstance(item, date):
                return item.isoformat()
            return str(item).strip() if item is not None else ""
        imported = 0
        skipped = []
        with connect_db() as connection:
            next_number = connection.execute("SELECT COALESCE(MAX(elder_id),0)+1 n FROM elderly_profiles").fetchone()["n"]
            for row_no, row in enumerate(ws.iter_rows(min_row=2), start=2):
                if not any(cell.value not in (None, "") for cell in row):
                    continue
                try:
                    name = value(row, "姓名")
                    gender = value(row, "性别")
                    phone = value(row, "联系电话")
                    level = value(row, "自理能力")
                    living = value(row, "居住情况")
                    if not all([name, gender, phone, level, living]):
                        raise ValueError("必填字段不完整")
                    elder_code = value(row, "档案编号") or f"JX{date.today().year}{int(next_number):06d}"
                    if connection.execute("SELECT 1 FROM elderly_profiles WHERE elder_code=?", (elder_code,)).fetchone():
                        raise ValueError("档案编号已存在")
                    birth = value(row, "出生日期")
                    if not birth:
                        age_text = value(row, "年龄")
                        if age_text:
                            age = int(float(age_text))
                            birth = f"{date.today().year-age}-01-01"
                    age = age_from_birth(birth) if birth else 0
                    priority = calculate_service_priority(age, level)
                    connection.execute("""INSERT INTO elderly_profiles(
                      elder_code,full_name,phone,address,community,gender,id_card,birth_date,allergy_history,disease_history,self_care_level,
                      emergency_contact_name,emergency_contact_phone,living_situation,diet_requirement,visit_time_preference,elder_tags,service_priority)
                      VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (
                        elder_code,name,phone,value(row,"居住地址"),value(row,"所属社区") or "青云社区",gender,value(row,"身份证号"),birth,
                        value(row,"过敏史"),value(row,"既往病史"),level,value(row,"紧急联系人"),value(row,"紧急联系电话"),living,
                        value(row,"特殊饮食"),value(row,"上门时间偏好"),value(row,"老人标签") or "普通",priority
                    ))
                    imported += 1
                    next_number += 1
                except Exception as exc:
                    skipped.append(f"第 {row_no} 行：{exc}")
        self.send_json(200, {"ok": True, "message": f"成功导入 {imported} 条老人档案", "imported": imported, "skipped": skipped[:20]})

    def get_exam_reports(self, query):
        elder_code = str((query.get("elder_code") or [""])[0]).strip()
        user = self.current_user() or {}
        if user.get("role") == "elder":
            elder_code = user.get("elder_code") or elder_code
        if not elder_code:
            raise ValueError("缺少 elder_code")
        with connect_db() as connection:
            elder = connection.execute("SELECT elder_id FROM elderly_profiles WHERE elder_code=?", (elder_code,)).fetchone()
            if elder is None:
                raise ValueError("老人档案不存在")
            rows = connection.execute(
                "SELECT report_id,exam_date,organization,file_name,mime_type,file_size,abnormal_summary,uploaded_at FROM exam_reports WHERE elder_id=? ORDER BY exam_date DESC, report_id DESC",
                (elder["elder_id"],),
            ).fetchall()
        self.send_json(200, {"ok": True, "reports": [dict(r) for r in rows]})

    def upload_exam_report(self):
        user = self.require_role("admin", "elder")
        payload = self.read_json()
        elder_code = str(payload.get("elder_code") or user.get("elder_code") or "").strip()
        if user["role"] == "elder":
            if elder_code != user.get("elder_code"):
                raise PermissionError("不能向其他老人档案上传体检报告")
            elder_code = user.get("elder_code") or ""
        exam_date = str(payload.get("exam_date") or "").strip()
        organization = str(payload.get("organization") or "").strip()
        abnormal = str(payload.get("abnormal_summary") or "").strip()
        filename = Path(str(payload.get("file_name") or "")).name
        mime_type = str(payload.get("mime_type") or mimetypes.guess_type(filename)[0] or "application/octet-stream")
        encoded = str(payload.get("file_data") or "")
        if not all([elder_code, exam_date, organization, filename, encoded]):
            raise ValueError("请填写体检日期、体检机构并选择报告文件")
        suffix = Path(filename).suffix.lower()
        if suffix not in {".pdf", ".png", ".jpg", ".jpeg", ".webp"}:
            raise ValueError("体检报告仅支持 PDF、PNG、JPG、JPEG、WEBP")
        try:
            raw = base64.b64decode(encoded, validate=True)
        except Exception as exc:
            raise ValueError("报告文件数据无效") from exc
        if len(raw) > 12 * 1024 * 1024:
            raise ValueError("体检报告不能超过 12MB")
        with connect_db() as connection:
            elder = connection.execute("SELECT elder_id FROM elderly_profiles WHERE elder_code=?", (elder_code,)).fetchone()
            if elder is None:
                raise ValueError("老人档案不存在")
            UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
            stored_name = f"{elder_code}_{datetime.now():%Y%m%d%H%M%S}_{secrets.token_hex(4)}{suffix}"
            (UPLOAD_DIR / stored_name).write_bytes(raw)
            cur = connection.execute("""INSERT INTO exam_reports(elder_id,exam_date,organization,file_name,stored_name,mime_type,file_size,abnormal_summary)
              VALUES(?,?,?,?,?,?,?,?)""", (elder["elder_id"], exam_date, organization, filename, stored_name, mime_type, len(raw), abnormal))
            report_id = cur.lastrowid
        self.send_json(201, {"ok": True, "message": "体检报告已上传", "report_id": report_id})

    def download_exam_report(self, token):
        try:
            report_id = int(token)
        except ValueError as exc:
            raise ValueError("报告编号无效") from exc
        user = self.current_user() or {}
        with connect_db() as connection:
            row = connection.execute("""SELECT er.*,e.elder_code FROM exam_reports er JOIN elderly_profiles e ON e.elder_id=er.elder_id WHERE er.report_id=?""", (report_id,)).fetchone()
        if row is None:
            raise ValueError("体检报告不存在")
        if user.get("role") == "elder" and user.get("elder_code") != row["elder_code"]:
            raise PermissionError("无权下载其他老人的体检报告")
        target = (UPLOAD_DIR / row["stored_name"]).resolve()
        if not target.is_file() or UPLOAD_DIR.resolve() not in target.parents:
            raise ValueError("报告文件不存在")
        return self.send_bytes(200, target.read_bytes(), row["mime_type"] or "application/octet-stream", row["file_name"])

    def create_elder(self):
        payload = self.read_json()
        required = {"full_name": "姓名", "gender": "性别", "age": "年龄", "phone": "联系电话", "self_care_level": "自理能力", "living_situation": "居住情况"}
        missing = [label for key, label in required.items() if str(payload.get(key, "")).strip() == ""]
        if missing:
            raise ValueError("请填写必填项：" + "、".join(missing))
        try:
            age = int(payload["age"])
        except (TypeError, ValueError):
            raise ValueError("年龄必须为整数")
        if age < 60 or age > 120:
            raise ValueError("年龄应在 60 至 120 岁之间")
        level = str(payload["self_care_level"]).strip()
        priority = calculate_service_priority(age, level)
        birth_date = f"{date.today().year - age:04d}-01-01"
        with connect_db() as connection:
            cursor = connection.execute("""INSERT INTO elderly_profiles(
              elder_code,full_name,phone,address,community,gender,id_card,birth_date,ethnicity,allergy_history,disease_history,
              self_care_level,medical_type,medical_card_no,emergency_contact_name,emergency_contact_relation,emergency_contact_phone,
              emergency_backup_phone,living_situation,language_preference,diet_requirement,visit_time_preference,elder_tags,service_priority)
              VALUES('',?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (
              str(payload["full_name"]).strip(), str(payload["phone"]).strip(), str(payload.get("address") or "地址待补充").strip(),
              str(payload.get("community") or "青云社区").strip(), str(payload["gender"]).strip(), str(payload.get("id_card") or "").strip(), birth_date,
              str(payload.get("ethnicity") or "汉族").strip(), str(payload.get("allergy_history") or "").strip(), str(payload.get("disease_history") or "").strip(),
              level, str(payload.get("medical_type") or "").strip(), str(payload.get("medical_card_no") or "").strip(),
              str(payload.get("emergency_contact_name") or "").strip(), str(payload.get("emergency_contact_relation") or "").strip(),
              str(payload.get("emergency_contact_phone") or "").strip(), str(payload.get("emergency_backup_phone") or "").strip(),
              str(payload["living_situation"]).strip(), str(payload.get("language_preference") or "").strip(), str(payload.get("diet_requirement") or "").strip(),
              str(payload.get("visit_time_preference") or "").strip(), str(payload.get("elder_tags") or "普通").strip(), priority))
            elder_code = f"JX{date.today():%Y}{cursor.lastrowid:06d}"
            connection.execute("UPDATE elderly_profiles SET elder_code=? WHERE elder_id=?", (elder_code, cursor.lastrowid))
            row = connection.execute("SELECT * FROM elderly_profiles WHERE elder_id=?", (cursor.lastrowid,)).fetchone()
        profile = dict(row)
        profile["age"] = age
        profile["service_priority"] = priority
        self.send_json(201, {"ok": True, "message": "老人档案已创建", "profile": profile})

    def delete_elder(self, token):
        with connect_db() as connection:
            connection.execute('BEGIN IMMEDIATE')
            elder = connection.execute("SELECT elder_id, full_name FROM elderly_profiles WHERE elder_code=? AND is_deleted=0", (token,)).fetchone()
            if elder is None:
                raise ValueError("老人档案不存在")
            active_order = connection.execute("SELECT 1 FROM service_orders WHERE elder_id=? AND status IN ('PENDING_ASSIGN','PENDING_SERVICE','IN_SERVICE','PENDING_REVIEW') LIMIT 1", (elder["elder_id"],)).fetchone()
            active_emergency = connection.execute("SELECT 1 FROM emergency_requests WHERE elder_id=? AND status IN ('待调度','已派单','服务人员已出发','已到达') LIMIT 1", (elder["elder_id"],)).fetchone()
            if active_order or active_emergency:
                raise ValueError("该老人存在未结束订单或紧急求助，请先处理完成")
            connection.execute("UPDATE elderly_profiles SET is_deleted=1 WHERE elder_id=?", (elder["elder_id"],))
        self.send_json(200, {"ok": True, "message": f"老人档案已删除：{elder['full_name']}"})

    def get_providers(self):
        self.require_role("admin")
        with connect_db() as connection:
            rows = [dict(row) for row in connection.execute("SELECT * FROM service_providers WHERE is_deleted=0 ORDER BY provider_id")]
        self.send_json(200, {"ok": True, "count": len(rows), "providers": rows})

    def create_provider(self):
        payload = self.read_json()
        name = str(payload.get("full_name") or payload.get("name") or "").strip()
        if not name:
            raise ValueError("服务人员姓名不能为空")
        with connect_db() as connection:
            cursor = connection.execute("""INSERT INTO service_providers(provider_code,full_name,phone,organization,skills,service_areas,certificates,gender,id_card,service_years,health_certificate,training_records,profile_intro,employment_status,is_deleted)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,'在职',0)""", (
                f"TEMP-{datetime.now().timestamp()}", name, str(payload.get("phone") or "待录入").strip(), str(payload.get("organization") or payload.get("org") or "待分配机构").strip(),
                str(payload.get("skills") or "待设置").strip(), str(payload.get("service_areas") or payload.get("areas") or "待设置").strip(), str(payload.get("certificates") or payload.get("certs") or "").strip(),
                str(payload.get("gender") or "男").strip(), str(payload.get("id_card") or payload.get("idCard") or "").strip(), int(payload.get("service_years") or payload.get("years") or 0),
                str(payload.get("health_certificate") or payload.get("health") or "").strip(), str(payload.get("training_records") or payload.get("training") or "").strip(), str(payload.get("profile_intro") or payload.get("intro") or "").strip()))
            provider_code = str(payload.get("provider_code") or payload.get("id") or f"FW{date.today():%Y}{cursor.lastrowid:04d}").strip()
            connection.execute("UPDATE service_providers SET provider_code=? WHERE provider_id=?", (provider_code, cursor.lastrowid))
            row = connection.execute("SELECT * FROM service_providers WHERE provider_id=?", (cursor.lastrowid,)).fetchone()
        self.send_json(201, {"ok": True, "message": "服务人员档案已创建", "profile": dict(row)})

    def delete_provider(self, token):
        with connect_db() as connection:
            connection.execute('BEGIN IMMEDIATE')
            provider = connection.execute("SELECT provider_id, full_name FROM service_providers WHERE provider_code=? AND is_deleted=0", (token,)).fetchone()
            if provider is None:
                raise ValueError("服务人员不存在")
            active_order = connection.execute("""SELECT 1 FROM service_assignments a JOIN service_orders o ON o.order_id=a.order_id
                WHERE a.provider_id=? AND o.status IN ('PENDING_SERVICE','IN_SERVICE') LIMIT 1""", (provider["provider_id"],)).fetchone()
            active_emergency = connection.execute("SELECT 1 FROM emergency_requests WHERE assigned_provider_id=? AND status IN ('已派单','服务人员已出发','已到达') LIMIT 1", (provider["provider_id"],)).fetchone()
            if active_order or active_emergency:
                raise ValueError("该服务人员存在未结束任务，请先完成或改派")
            connection.execute("UPDATE service_providers SET is_deleted=1, employment_status='离职' WHERE provider_id=?", (provider["provider_id"],))
        self.send_json(200, {"ok": True, "message": f"服务人员档案已删除：{provider['full_name']}"})

    def get_options(self):
        user = self.current_user() or {}
        with connect_db() as connection:
            if user.get("role") == "admin":
                elders = [dict(row) for row in connection.execute(
                    "SELECT elder_id, elder_code, full_name, phone, address, community FROM elderly_profiles WHERE is_deleted=0 ORDER BY elder_id"
                )]
            elif user.get("role") == "elder" and user.get("elder_code"):
                elders = [dict(row) for row in connection.execute(
                    "SELECT elder_id, elder_code, full_name, phone, address, community FROM elderly_profiles WHERE elder_code=? AND is_deleted=0",
                    (user["elder_code"],),
                )]
            else:
                elders = []
            services = [dict(row) for row in connection.execute(
                "SELECT service_item_id, service_code, service_name, service_type, price, duration_minutes FROM service_items WHERE enabled=1 ORDER BY service_item_id"
            )]
            providers = [dict(row) for row in connection.execute(
                "SELECT provider_id, provider_code, full_name, phone, organization, skills, service_areas, certificates, employment_status FROM service_providers WHERE is_deleted=0 AND employment_status='在职' ORDER BY provider_id"
            )]
        self.send_json(200, {"ok": True, "elders": elders, "services": services, "providers": providers})

    def get_elders(self):
        self.require_role("admin")
        with connect_db() as connection:
            rows = [dict(row) for row in connection.execute("SELECT * FROM elderly_profiles WHERE is_deleted=0 ORDER BY elder_id")]
        for row in rows:
            row["age"] = age_from_birth(row.get("birth_date", ""))
            row["service_priority"] = calculate_service_priority(row["age"], row.get("self_care_level", ""))
        self.send_json(200, {"ok": True, "count": len(rows), "elders": rows})

    def get_emergencies(self, query):
        user = self.require_role("admin", "elder", "provider")
        clauses, params = [], []
        if user["role"] == "elder":
            clauses.append("e.elder_code = ?")
            params.append(user.get("elder_code") or "")
        elif user["role"] == "provider":
            clauses.append("p.provider_code = ?")
            params.append(user.get("provider_code") or "")
        else:
            if query.get("elder_code"):
                clauses.append("e.elder_code = ?")
                params.append(query["elder_code"][0])
            if query.get("provider_code"):
                clauses.append("p.provider_code = ?")
                params.append(query["provider_code"][0])
        if query.get("status"):
            clauses.append("er.status = ?")
            params.append(query["status"][0])
        where = "WHERE " + " AND ".join(clauses) if clauses else ""
        sql = f"""SELECT er.*, e.elder_code, e.full_name AS elder_name, e.phone AS elder_phone,
                  p.provider_code, p.full_name AS provider_name
                  FROM emergency_requests er
                  JOIN elderly_profiles e ON e.elder_id=er.elder_id
                  LEFT JOIN service_providers p ON p.provider_id=er.assigned_provider_id
                  {where} ORDER BY er.created_at DESC, er.emergency_id DESC"""
        with connect_db() as connection:
            rows = [dict(row) for row in connection.execute(sql, params)]
        self.send_json(200, {"ok": True, "count": len(rows), "emergencies": rows})

    def resolve_emergency(self, connection, token):
        row = connection.execute("SELECT * FROM emergency_requests WHERE emergency_no=? OR CAST(emergency_id AS TEXT)=?", (token, token)).fetchone()
        if row is None:
            raise ValueError("紧急求助记录不存在")
        return row

    def create_emergency(self):
        user = self.require_role("elder")
        payload = self.read_json()
        elder_code = user.get("elder_code") or ""
        if payload.get("elder_code") and str(payload.get("elder_code")).strip() != elder_code:
            raise PermissionError("不能以其他老人身份发起求助")
        emergency_type = str(payload.get("emergency_type", "")).strip()
        if not elder_code or not emergency_type:
            raise ValueError("老人档案或紧急类型不能为空")
        with connect_db() as connection:
            elder = connection.execute("SELECT * FROM elderly_profiles WHERE elder_code=? AND is_deleted=0", (elder_code,)).fetchone()
            if elder is None:
                raise ValueError("老人档案不存在")
            cursor = connection.execute("""INSERT INTO emergency_requests(
                emergency_no,elder_id,emergency_type,trigger_source,address,latitude,longitude,status,notify_targets,result,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?,'待调度',?,'待处理',?,?)""", (
                f"TEMP-{datetime.now().timestamp()}", elder["elder_id"], emergency_type,
                str(payload.get("source") or "APP一键呼叫"), str(payload.get("address") or elder["address"]),
                payload.get("latitude"), payload.get("longitude"),
                str(payload.get("notify_targets") or "紧急联系人、社区管理员、家庭医生"), now_text(), now_text()))
            emergency_id = cursor.lastrowid
            emergency_no = f"JQ{datetime.now():%Y%m%d}{emergency_id:04d}"
            connection.execute("UPDATE emergency_requests SET emergency_no=? WHERE emergency_id=?", (emergency_no, emergency_id))
        self.send_json(201, {"ok": True, "message": "紧急求助已登记，等待管理员调度", "emergency_no": emergency_no, "status": "待调度"})

    def dispatch_emergency(self, token):
        payload = self.read_json()
        provider_code = str(payload.get("provider_code", "")).strip()
        if not provider_code:
            raise ValueError("请选择服务人员")
        with connect_db() as connection:
            row = self.resolve_emergency(connection, token)
            if row["status"] not in {"待调度", "已派单"}:
                raise ValueError("当前状态不能重新调度")
            provider = connection.execute("SELECT * FROM service_providers WHERE provider_code=? AND is_deleted=0", (provider_code,)).fetchone()
            if provider is None:
                raise ValueError("服务人员不存在")
            if provider["employment_status"] != "在职":
                raise ValueError("只能指派在职服务人员")
            ts = now_text()
            try:
                start = datetime.strptime(row["created_at"], "%Y-%m-%d %H:%M:%S")
                elapsed = max(0, int((datetime.strptime(ts, "%Y-%m-%d %H:%M:%S") - start).total_seconds()))
                response_time = f"{elapsed // 60:02d}:{elapsed % 60:02d}"
            except ValueError:
                response_time = row["response_time"] or ""
            connection.execute("""UPDATE emergency_requests SET status='已派单',assigned_provider_id=?,dispatch_at=?,dispatch_note=?,response_time=?,updated_at=? WHERE emergency_id=?""", (
                provider["provider_id"], ts, str(payload.get("dispatch_note") or "").strip(), response_time, ts, row["emergency_id"]))
        self.send_json(200, {"ok": True, "message": "紧急任务已派给服务人员", "emergency_no": row["emergency_no"], "status": "已派单", "provider_code": provider_code, "dispatch_at": ts})

    def depart_emergency(self, token):
        user = self.require_role("provider", "admin")
        payload = self.read_json()
        provider_code = user.get("provider_code") if user["role"] == "provider" else str(payload.get("provider_code", "")).strip()
        if not provider_code:
            raise ValueError("缺少服务人员信息")
        with connect_db() as connection:
            connection.execute('BEGIN IMMEDIATE')
            row = self.resolve_emergency(connection, token)
            if row["status"] != "已派单":
                raise ValueError("只有已派单的紧急任务可以出发")
            provider = connection.execute("SELECT provider_id FROM service_providers WHERE provider_code=? AND is_deleted=0", (provider_code,)).fetchone()
            if provider is None or provider["provider_id"] != row["assigned_provider_id"]:
                raise PermissionError("该紧急任务未分配给当前服务人员")
            ts = now_text()
            connection.execute("""UPDATE emergency_requests SET status='服务人员已出发',depart_at=?,depart_latitude=?,depart_longitude=?,updated_at=? WHERE emergency_id=?""", (
                ts, payload.get("latitude"), payload.get("longitude"), ts, row["emergency_id"]))
        self.send_json(200, {"ok": True, "message": "已记录出发时间和定位", "emergency_no": row["emergency_no"], "status": "服务人员已出发", "depart_at": ts})

    def arrive_emergency(self, token):
        user = self.require_role("provider", "admin")
        payload = self.read_json()
        provider_code = user.get("provider_code") if user["role"] == "provider" else str(payload.get("provider_code", "")).strip()
        if not provider_code:
            raise ValueError("缺少服务人员信息")
        with connect_db() as connection:
            connection.execute('BEGIN IMMEDIATE')
            row = self.resolve_emergency(connection, token)
            if row["status"] != "服务人员已出发":
                raise ValueError("只有已出发的紧急任务可以确认到达")
            provider = connection.execute("SELECT provider_id FROM service_providers WHERE provider_code=? AND is_deleted=0", (provider_code,)).fetchone()
            if provider is None or provider["provider_id"] != row["assigned_provider_id"]:
                raise PermissionError("该紧急任务未分配给当前服务人员")
            ts = now_text()
            connection.execute("UPDATE emergency_requests SET status='已到达',arrive_time=?,updated_at=? WHERE emergency_id=?", (ts, ts, row["emergency_id"]))
        self.send_json(200, {"ok": True, "message": "已记录到达时间", "emergency_no": row["emergency_no"], "status": "已到达", "arrive_time": ts})

    def complete_emergency(self, token):
        user = self.require_role("provider", "admin")
        payload = self.read_json()
        provider_code = user.get("provider_code") if user["role"] == "provider" else str(payload.get("provider_code", "")).strip()
        result = str(payload.get("result", "已解决")).strip()
        if result not in {"已解决", "转送医院", "联系家属"}:
            raise ValueError("处理结果不合法")
        if not provider_code:
            raise ValueError("缺少服务人员信息")
        with connect_db() as connection:
            connection.execute('BEGIN IMMEDIATE')
            row = self.resolve_emergency(connection, token)
            if row["status"] != "已到达":
                raise ValueError("只有已到达的紧急任务可以完成")
            provider = connection.execute("SELECT provider_id FROM service_providers WHERE provider_code=? AND is_deleted=0", (provider_code,)).fetchone()
            if provider is None or provider["provider_id"] != row["assigned_provider_id"]:
                raise PermissionError("该紧急任务未分配给当前服务人员")
            ts = now_text()
            connection.execute("""UPDATE emergency_requests SET status='已解决',finish_at=?,handling_measure=?,result=?,updated_at=? WHERE emergency_id=?""", (
                ts, str(payload.get("handling_measure") or "").strip(), result, ts, row["emergency_id"]))
        self.send_json(200, {"ok": True, "message": "紧急任务已完成", "emergency_no": row["emergency_no"], "status": "已解决", "result": result, "finish_at": ts})

    def cancel_emergency(self, token):
        user = self.require_role("elder", "admin")
        with connect_db() as connection:
            connection.execute('BEGIN IMMEDIATE')
            row = self.resolve_emergency(connection, token)
            if user["role"] == "elder":
                elder = connection.execute("SELECT elder_code FROM elderly_profiles WHERE elder_id=?", (row["elder_id"],)).fetchone()
                if elder is None or elder["elder_code"] != user.get("elder_code"):
                    raise PermissionError("无权取消其他老人的紧急求助")
            if row["status"] != "待调度":
                raise ValueError("只有尚未调度的求助可以取消")
            try:
                created = datetime.strptime(row["created_at"], "%Y-%m-%d %H:%M:%S")
                if (datetime.now() - created).total_seconds() > 30:
                    raise ValueError("已超过 30 秒取消时间")
            except ValueError as error:
                if str(error) == "已超过 30 秒取消时间":
                    raise
            ts = now_text()
            connection.execute("UPDATE emergency_requests SET status='误报取消',result='误报',finish_at=?,updated_at=? WHERE emergency_id=?", (ts, ts, row["emergency_id"]))
        self.send_json(200, {"ok": True, "message": "紧急求助已取消", "emergency_no": row["emergency_no"], "status": "误报取消"})

    def get_orders(self, query):
        user = self.require_role("admin", "elder", "provider")
        clauses, params = [], []
        if user["role"] == "elder":
            clauses.append("e.elder_code = ?")
            params.append(user.get("elder_code") or "")
        elif user["role"] == "provider":
            clauses.append("p.provider_code = ?")
            params.append(user.get("provider_code") or "")
        else:
            if query.get("elder_code"):
                clauses.append("e.elder_code = ?")
                params.append(query["elder_code"][0])
            if query.get("provider_code"):
                clauses.append("p.provider_code = ?")
                params.append(query["provider_code"][0])
        if query.get("status"):
            clauses.append("o.status = ?")
            params.append(query["status"][0])
        where = "WHERE " + " AND ".join(clauses) if clauses else ""
        sql = self.order_select_sql(where) + " ORDER BY o.created_at DESC, o.order_id DESC"
        with connect_db() as connection:
            orders = [self.row_to_order(row) for row in connection.execute(sql, params)]
        self.send_json(200, {"ok": True, "count": len(orders), "orders": orders})

    def get_order(self, token):
        user = self.require_role("admin", "elder", "provider")
        with connect_db() as connection:
            base = self.resolve_order(connection, token)
            row = connection.execute(self.order_select_sql("WHERE o.order_id = ?"), (base["order_id"],)).fetchone()
            payload = self.row_to_order(row)
            if user["role"] == "elder" and payload["elder_code"] != user.get("elder_code"):
                raise PermissionError("无权查看其他老人的订单")
            if user["role"] == "provider" and payload.get("provider_code") != user.get("provider_code"):
                raise PermissionError("无权查看未分配给自己的订单")
            records = [dict(r) for r in connection.execute(
                "SELECT record_id, actual_start_time, actual_end_time, completion_status, process_description, elder_observation, materials, submitted_at FROM service_records WHERE order_id=? ORDER BY submitted_at DESC",
                (base["order_id"],),
            )]
        payload["records"] = records
        self.send_json(200, {"ok": True, "order": payload})

    def get_elder_profile(self, elder_code):
        user = self.require_role("admin", "elder")
        if user["role"] == "elder" and elder_code != user.get("elder_code"):
            raise PermissionError("无权查看其他老人档案")
        with connect_db() as connection:
            row = connection.execute("SELECT * FROM elderly_profiles WHERE elder_code=?", (elder_code,)).fetchone()
        if row is None:
            raise ValueError("老人档案不存在")
        profile = dict(row)
        profile["age"] = age_from_birth(profile.get("birth_date", ""))
        profile["service_priority"] = calculate_service_priority(profile["age"], profile.get("self_care_level", ""))
        self.send_json(200, {"ok": True, "profile": profile})

    def update_elder_profile(self, elder_code):
        user = self.require_role("admin", "elder")
        if user["role"] == "elder" and elder_code != user.get("elder_code"):
            raise PermissionError("无权修改其他老人档案")
        payload = self.read_json()
        allowed = ["full_name","gender","id_card","birth_date","ethnicity","phone","community","address","height_cm","weight_kg","blood_type","allergy_history","disease_history","disability_status","self_care_level","medical_type","medical_card_no","designated_hospital","emergency_contact_name","emergency_contact_relation","emergency_contact_phone","emergency_backup_phone","living_situation","language_preference","diet_requirement","visit_time_preference","elder_tags"]
        values = {k: payload.get(k) for k in allowed if k in payload}
        if not values:
            raise ValueError("没有可保存的个人信息")
        with connect_db() as connection:
            current = connection.execute("SELECT * FROM elderly_profiles WHERE elder_code=?", (elder_code,)).fetchone()
            if current is None:
                raise ValueError("老人档案不存在")
            merged = dict(current)
            merged.update(values)
            required = {"full_name": "姓名", "gender": "性别", "phone": "联系电话", "self_care_level": "自理能力", "living_situation": "居住情况"}
            missing = [label for key, label in required.items() if str(merged.get(key, "")).strip() == ""]
            age = age_from_birth(merged.get("birth_date", ""))
            if age <= 0:
                missing.append("年龄")
            if missing:
                raise ValueError("请填写必填项：" + "、".join(missing))
            set_sql = ", ".join(f"{k}=?" for k in values)
            connection.execute(f"UPDATE elderly_profiles SET {set_sql} WHERE elder_code=?", [*values.values(), elder_code])
            updated = connection.execute("SELECT * FROM elderly_profiles WHERE elder_code=?", (elder_code,)).fetchone()
            priority = calculate_service_priority(age_from_birth(updated["birth_date"]), updated["self_care_level"])
            connection.execute("UPDATE elderly_profiles SET service_priority=? WHERE elder_code=?", (priority, elder_code))
            row = connection.execute("SELECT * FROM elderly_profiles WHERE elder_code=?", (elder_code,)).fetchone()
            if user["role"] == "elder" and "full_name" in values:
                connection.execute("UPDATE auth_accounts SET display_name=? WHERE elder_code=?", (str(values["full_name"]).strip(), elder_code))
                user["display_name"] = str(values["full_name"]).strip()
        profile = dict(row)
        profile["age"] = age_from_birth(profile.get("birth_date", ""))
        profile["service_priority"] = calculate_service_priority(profile["age"], profile.get("self_care_level", ""))
        self.send_json(200, {"ok": True, "message": "老人个人信息已保存到数据库", "profile": profile})

    def get_provider_profile(self, provider_code):
        user = self.require_role("admin", "provider")
        if user["role"] == "provider" and provider_code != user.get("provider_code"):
            raise PermissionError("无权查看其他服务人员档案")
        with connect_db() as connection:
            row = connection.execute("SELECT * FROM service_providers WHERE provider_code=?", (provider_code,)).fetchone()
        if row is None:
            raise ValueError("服务人员不存在")
        self.send_json(200, {"ok": True, "profile": dict(row)})

    def update_provider_profile(self, provider_code):
        user = self.require_role("admin", "provider")
        if user["role"] == "provider" and provider_code != user.get("provider_code"):
            raise PermissionError("无权修改其他服务人员档案")
        payload = self.read_json()
        allowed = ["full_name","gender","id_card","phone","organization","skills","service_areas","certificates","service_years","health_certificate","training_records","profile_intro","employment_status"]
        values = {k: payload.get(k) for k in allowed if k in payload}
        if not values:
            raise ValueError("没有可保存的人员信息")
        with connect_db() as connection:
            if connection.execute("SELECT 1 FROM service_providers WHERE provider_code=?", (provider_code,)).fetchone() is None:
                raise ValueError("服务人员不存在")
            set_sql = ", ".join(f"{k}=?" for k in values)
            connection.execute(f"UPDATE service_providers SET {set_sql} WHERE provider_code=?", [*values.values(), provider_code])
            row = connection.execute("SELECT * FROM service_providers WHERE provider_code=?", (provider_code,)).fetchone()
            if user["role"] == "provider" and "full_name" in values:
                connection.execute("UPDATE auth_accounts SET display_name=? WHERE provider_code=?", (str(values["full_name"]).strip(), provider_code))
                user["display_name"] = str(values["full_name"]).strip()
        self.send_json(200, {"ok": True, "message": "服务人员资料已保存到数据库", "profile": dict(row)})

    def get_reviews(self, query):
        user = self.require_role("admin", "elder", "provider")
        elder_code = (query.get("elder_code") or [""])[0]
        provider_code = (query.get("provider_code") or [""])[0]
        if user["role"] == "elder":
            elder_code = user.get("elder_code") or ""
            provider_code = ""
        elif user["role"] == "provider":
            provider_code = user.get("provider_code") or ""
            elder_code = ""
        if not elder_code and not provider_code:
            raise ValueError("缺少 elder_code 或 provider_code")
        if elder_code:
            where = "e.elder_code=? AND o.status IN ('PENDING_REVIEW','COMPLETED')"
            params = (elder_code,)
        else:
            where = "p.provider_code=? AND r.review_id IS NOT NULL"
            params = (provider_code,)
        with connect_db() as connection:
            rows = connection.execute(f"""SELECT o.order_no,o.status,o.service_date,s.service_name,
              e.elder_code,e.full_name elder_name,p.provider_code,p.full_name provider_name,
              r.attitude_score,r.quality_score,r.punctual_score,r.review_text,r.photo_note,r.created_at
              FROM service_orders o JOIN elderly_profiles e ON e.elder_id=o.elder_id JOIN service_items s ON s.service_item_id=o.service_item_id
              LEFT JOIN service_assignments a ON a.order_id=o.order_id LEFT JOIN service_providers p ON p.provider_id=a.provider_id
              LEFT JOIN service_reviews r ON r.order_id=o.order_id WHERE {where}
              ORDER BY COALESCE(r.created_at,o.service_date) DESC,o.order_id DESC""", params).fetchall()
        reviews=[]
        for row in rows:
            d=dict(row); scores=[d.get("attitude_score"),d.get("quality_score"),d.get("punctual_score")]
            d["overall"] = round(sum(scores)/3,1) if all(v is not None for v in scores) else None
            d["state"] = "已评价" if d.get("attitude_score") is not None else "待评价"
            reviews.append(d)
        rated=[r for r in reviews if r["overall"] is not None]
        stats={"count":len(rated),"average":round(sum(r["overall"] for r in rated)/len(rated),1) if rated else None,
               "good_rate":round(sum(1 for r in rated if r["overall"]>=4)/len(rated)*100,1) if rated else 0}
        self.send_json(200,{"ok":True,"reviews":reviews,"stats":stats})

    def create_review(self, token):
        user = self.require_role("elder")
        payload=self.read_json()
        scores=[]
        for key in ("attitude_score","quality_score","punctual_score"):
            try: value=int(payload.get(key))
            except (TypeError,ValueError): raise ValueError("评分必须是 1-5 的整数")
            if value<1 or value>5: raise ValueError("评分必须在 1-5 星之间")
            scores.append(value)
        with connect_db() as connection:
            order=self.resolve_order(connection,token)
            elder = connection.execute("SELECT elder_code FROM elderly_profiles WHERE elder_id=?", (order["elder_id"],)).fetchone()
            if elder is None or elder["elder_code"] != user.get("elder_code"):
                raise PermissionError("无权评价其他老人的订单")
            if order["status"]!="PENDING_REVIEW": raise ValueError("只有待评价订单可以提交评价")
            assignment=self.get_assignment(connection,order["order_id"])
            connection.execute("""INSERT INTO service_reviews(order_id,elder_id,provider_id,attitude_score,quality_score,punctual_score,review_text,photo_note)
              VALUES(?,?,?,?,?,?,?,?)""",(order["order_id"],order["elder_id"],assignment["provider_id"],scores[0],scores[1],scores[2],str(payload.get("review_text","")).strip(),str(payload.get("photo_note","")).strip()))
            connection.execute("UPDATE service_orders SET status='COMPLETED',updated_at=? WHERE order_id=?",(now_text(),order["order_id"]))
        self.send_json(201,{"ok":True,"message":"评价已保存，订单已进入已完成状态","order_no":order["order_no"],"status":"COMPLETED","overall":round(sum(scores)/3,1)})

    def get_complaints(self, query):
        user = self.require_role("admin", "elder", "provider")
        clauses, params = [], []
        if user["role"] == "elder":
            clauses.append("e.elder_code=?")
            params.append(user.get("elder_code") or "")
        elif user["role"] == "provider":
            clauses.append("p.provider_code=?")
            params.append(user.get("provider_code") or "")
        else:
            if query.get("elder_code"):
                clauses.append("e.elder_code=?")
                params.append(query["elder_code"][0])
            if query.get("provider_code"):
                clauses.append("p.provider_code=?")
                params.append(query["provider_code"][0])
        where = "WHERE " + " AND ".join(clauses) if clauses else ""
        with connect_db() as connection:
            rows = [dict(r) for r in connection.execute(f"""SELECT c.complaint_id,c.complaint_no,c.complaint_type,c.complaint_content,c.evidence_note,
                c.status,c.admin_reply,c.provider_appeal,c.created_at,c.updated_at,c.handled_by,c.handled_at,o.order_no,s.service_name,
                e.elder_code,e.full_name AS elder_name,p.provider_code,p.full_name AS provider_name
                FROM service_complaints c
                JOIN service_orders o ON o.order_id=c.order_id
                JOIN service_items s ON s.service_item_id=o.service_item_id
                JOIN elderly_profiles e ON e.elder_id=c.elder_id
                LEFT JOIN service_providers p ON p.provider_id=c.provider_id
                {where} ORDER BY c.created_at DESC,c.complaint_id DESC""", params)]
        self.send_json(200,{"ok":True,"count":len(rows),"complaints":rows})

    def create_complaint(self):
        user = self.require_role("elder")
        payload = self.read_json()
        order_no = str(payload.get("order_no") or "").strip()
        complaint_type = str(payload.get("complaint_type") or "").strip()
        content = str(payload.get("complaint_content") or "").strip()
        if not order_no or not complaint_type or not content:
            raise ValueError("请选择关联订单并填写投诉类型和投诉内容")
        with connect_db() as connection:
            order = connection.execute("""SELECT o.*,e.elder_code,a.provider_id FROM service_orders o
                JOIN elderly_profiles e ON e.elder_id=o.elder_id
                LEFT JOIN service_assignments a ON a.order_id=o.order_id WHERE o.order_no=?""", (order_no,)).fetchone()
            if order is None:
                raise ValueError("关联订单不存在")
            if order["elder_code"] != user.get("elder_code"):
                raise PermissionError("只能投诉当前账号自己的服务订单")
            cursor = connection.execute("""INSERT INTO service_complaints(
                complaint_no,order_id,elder_id,provider_id,complaint_type,complaint_content,evidence_note,status,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?,'处理中',?,?)""", (
                f"TEMP-{datetime.now().timestamp()}", order["order_id"], order["elder_id"], order["provider_id"], complaint_type,
                content, str(payload.get("evidence_note") or "").strip(), now_text(), now_text()))
            complaint_no = f"TS{datetime.now():%Y%m%d}{cursor.lastrowid:04d}"
            connection.execute("UPDATE service_complaints SET complaint_no=? WHERE complaint_id=?", (complaint_no, cursor.lastrowid))
        self.send_json(201,{"ok":True,"message":"投诉已提交，等待管理员处理","complaint_no":complaint_no})

    def appeal_complaint(self, token):
        user = self.require_role("provider")
        payload = self.read_json()
        appeal = str(payload.get("appeal") or "").strip()
        if not appeal:
            raise ValueError("请填写申诉说明")
        with connect_db() as connection:
            row = connection.execute("""SELECT c.complaint_id,p.provider_code FROM service_complaints c
                LEFT JOIN service_providers p ON p.provider_id=c.provider_id
                WHERE c.complaint_no=? OR CAST(c.complaint_id AS TEXT)=?""", (token, token)).fetchone()
            if row is None:
                raise ValueError("投诉记录不存在")
            if row["provider_code"] != user.get("provider_code"):
                raise PermissionError("无权申诉不属于自己的投诉")
            connection.execute("UPDATE service_complaints SET provider_appeal=?,updated_at=? WHERE complaint_id=?", (appeal, now_text(), row["complaint_id"]))
        self.send_json(200,{"ok":True,"message":"申诉说明已保存"})

    def get_health_records(self, query):
        user = self.require_role("admin", "elder")
        elder_code=(query.get("elder_code") or [""])[0]
        if user["role"] == "elder":
            elder_code = user.get("elder_code") or ""
        if not elder_code:
            raise ValueError("缺少 elder_code")
        try:
            days=max(1,min(90,int((query.get("days") or ["30"])[0])))
        except ValueError:
            days=30
        end_day=demo_today()
        start_day=end_day-timedelta(days=days-1)
        with connect_db() as connection:
            elder=connection.execute("SELECT elder_id FROM elderly_profiles WHERE elder_code=?",(elder_code,)).fetchone()
            if elder is None:
                raise ValueError("老人档案不存在")
            rows=connection.execute(
                "SELECT * FROM health_vitals WHERE elder_id=? AND date(measured_at) BETWEEN ? AND ? ORDER BY measured_at DESC, vital_id DESC",
                (elder["elder_id"],start_day.isoformat(),end_day.isoformat()),
            ).fetchall()
        actual_by_day={}
        for row in rows:
            key=str(row["measured_at"])[:10]
            if key not in actual_by_day:
                actual_by_day[key]=dict(row)
        if elder_code != DEMO_ELDER_CODE:
            records=list(actual_by_day.values())
            records.sort(key=lambda item: str(item.get("measured_at") or ""), reverse=True)
            return self.send_json(200,{"ok":True,"days":days,"start_date":start_day.isoformat(),"end_date":end_day.isoformat(),"records":records,"simulated":False})
        records=[]
        for offset in range(days):
            day=start_day+timedelta(days=offset)
            simulated=simulated_health_record(elder_code,day)
            actual=actual_by_day.get(day.isoformat())
            if actual:
                merged=simulated.copy()
                for key in ("vital_id","elder_id","measured_at","systolic","diastolic","glucose","heart_rate","temperature","weight","sleep_hours"):
                    if actual.get(key) is not None:
                        merged[key]=actual[key]
                merged["source"]=actual.get("source") or "手动录入"
                records.append(merged)
            else:
                records.append(simulated)
        records.reverse()
        self.send_json(200,{"ok":True,"days":days,"start_date":start_day.isoformat(),"end_date":end_day.isoformat(),"records":records,"simulated":True})

    def get_health_alerts(self, query):
        clauses, params = [], []
        if query.get("elder_code"):
            clauses.append("e.elder_code=?")
            params.append(query["elder_code"][0])
        if query.get("status"):
            clauses.append("ha.status=?")
            params.append(query["status"][0])
        where = "WHERE " + " AND ".join(clauses) if clauses else ""
        with connect_db() as connection:
            rows = [dict(row) for row in connection.execute(f"""SELECT ha.*, e.elder_code, e.full_name AS elder_name, hv.measured_at
                FROM health_alerts ha JOIN elderly_profiles e ON e.elder_id=ha.elder_id
                LEFT JOIN health_vitals hv ON hv.vital_id=ha.vital_id
                {where} ORDER BY ha.created_at DESC, ha.alert_id DESC""", params)]
            stats_row = connection.execute("""SELECT COUNT(*) AS total,
                SUM(CASE WHEN status='待处理' THEN 1 ELSE 0 END) AS pending,
                SUM(CASE WHEN status='已处理' THEN 1 ELSE 0 END) AS handled,
                SUM(CASE WHEN metric_type='血压' THEN 1 ELSE 0 END) AS blood_pressure,
                SUM(CASE WHEN metric_type='空腹血糖' THEN 1 ELSE 0 END) AS glucose,
                SUM(CASE WHEN metric_type='心率' THEN 1 ELSE 0 END) AS heart_rate,
                SUM(CASE WHEN metric_type='体温' THEN 1 ELSE 0 END) AS temperature,
                SUM(CASE WHEN metric_type='睡眠时长' THEN 1 ELSE 0 END) AS sleep
                FROM health_alerts""").fetchone()
        stats = {k: int(stats_row[k] or 0) for k in stats_row.keys()}
        self.send_json(200, {"ok": True, "count": len(rows), "stats": stats, "alerts": rows})

    def handle_health_alert(self, token):
        payload = self.read_json()
        note = str(payload.get("handling_note", "")).strip()
        handler = str(payload.get("handled_by", "陈雅")).strip() or "陈雅"
        if not note:
            raise ValueError("请填写处理说明")
        with connect_db() as connection:
            row = connection.execute("SELECT * FROM health_alerts WHERE alert_id=?", (token,)).fetchone()
            if row is None:
                raise ValueError("健康预警不存在")
            ts = now_text()
            connection.execute("UPDATE health_alerts SET status='已处理',handling_note=?,handled_by=?,handled_at=? WHERE alert_id=?", (note, handler, ts, row["alert_id"]))
        self.send_json(200, {"ok": True, "message": "健康预警已处理", "alert_id": row["alert_id"], "status": "已处理", "handled_at": ts})

    def create_health_record(self):
        user = self.require_role("elder")
        payload = self.read_json()
        elder_code = user.get("elder_code") or ""
        if payload.get("elder_code") and str(payload.get("elder_code")).strip() != elder_code:
            raise PermissionError("不能向其他老人档案写入健康数据")
        if not elder_code:
            raise ValueError("缺少 elder_code")
        bp = str(payload.get("bp") or "").replace("／", "/")
        systolic = diastolic = None
        if "/" in bp:
            a, b = bp.split("/", 1)
            systolic = int(a.strip()) if a.strip() else None
            diastolic = int(b.strip()) if b.strip() else None
        glucose = float(payload["glucose"]) if str(payload.get("glucose") or "").strip() else None
        heart_rate = int(float(payload["heart_rate"])) if str(payload.get("heart_rate") or "").strip() else None
        temperature = float(payload["temperature"]) if str(payload.get("temperature") or "").strip() else None
        weight = float(payload["weight"]) if str(payload.get("weight") or "").strip() else None
        sleep_hours = float(payload["sleep_hours"]) if str(payload.get("sleep_hours") or "").strip() else None
        with connect_db() as connection:
            elder = connection.execute("SELECT elder_id FROM elderly_profiles WHERE elder_code=?", (elder_code,)).fetchone()
            if elder is None:
                raise ValueError("老人档案不存在")
            ts = str(payload.get("measured_at") or now_text()).replace("T", " ")
            if len(ts) == 16:
                ts += ":00"
            cur = connection.execute("""INSERT INTO health_vitals(elder_id,measured_at,systolic,diastolic,glucose,heart_rate,temperature,weight,sleep_hours,source)
              VALUES(?,?,?,?,?,?,?,?,?,?)""", (elder["elder_id"],ts,systolic,diastolic,glucose,heart_rate,temperature,weight,sleep_hours,str(payload.get("source") or "手动录入")))
            alerts = []
            if (systolic is not None and systolic >= 140) or (diastolic is not None and diastolic >= 90):
                value = f"{systolic if systolic is not None else '—'}/{diastolic if diastolic is not None else '—'} mmHg"
                connection.execute("INSERT INTO health_alerts(vital_id,elder_id,metric_type,current_value,threshold_value,created_at) VALUES(?,?,?,?,?,?)", (cur.lastrowid, elder["elder_id"], "血压", value, "140/90 mmHg", ts))
                alerts.append("血压")
            if glucose is not None and glucose >= 7.0:
                connection.execute("INSERT INTO health_alerts(vital_id,elder_id,metric_type,current_value,threshold_value,created_at) VALUES(?,?,?,?,?,?)", (cur.lastrowid, elder["elder_id"], "空腹血糖", f"{glucose:g} mmol/L", "≥ 7.0 mmol/L", ts))
                alerts.append("空腹血糖")
            if heart_rate is not None and (heart_rate < 60 or heart_rate > 100):
                connection.execute("INSERT INTO health_alerts(vital_id,elder_id,metric_type,current_value,threshold_value,created_at) VALUES(?,?,?,?,?,?)", (cur.lastrowid, elder["elder_id"], "心率", f"{heart_rate} 次/分", "60-100 次/分", ts))
                alerts.append("心率")
            if temperature is not None and temperature >= 38.0:
                connection.execute("INSERT INTO health_alerts(vital_id,elder_id,metric_type,current_value,threshold_value,created_at) VALUES(?,?,?,?,?,?)", (cur.lastrowid, elder["elder_id"], "体温", f"{temperature:g} ℃", "< 38.0 ℃", ts))
                alerts.append("体温")
            if sleep_hours is not None and (sleep_hours < 7.0 or sleep_hours > 8.0):
                connection.execute("INSERT INTO health_alerts(vital_id,elder_id,metric_type,current_value,threshold_value,created_at) VALUES(?,?,?,?,?,?)", (cur.lastrowid, elder["elder_id"], "睡眠时长", f"{sleep_hours:g} 小时", "7-8 小时", ts))
                alerts.append("睡眠时长")
        message = "健康数据已保存"
        if alerts:
            message += "，已生成异常预警：" + "、".join(alerts)
        self.send_json(201,{"ok":True,"message":message,"vital_id":cur.lastrowid,"alert_count":len(alerts),"alerts":alerts})

    def create_order(self):
        user = self.require_role("admin", "elder")
        payload = self.read_json()
        if user["role"] == "elder":
            own_code = user.get("elder_code") or ""
            if payload.get("elder_code") and str(payload.get("elder_code")).strip() != own_code:
                raise PermissionError("不能为其他老人创建预约")
            payload["elder_code"] = own_code
        required = ["elder_code", "service_code", "service_date", "time_slot", "service_address"]
        missing = [field for field in required if not str(payload.get(field, "")).strip()]
        if missing:
            raise ValueError(f"以下字段不能为空：{', '.join(missing)}")
        try:
            service_day = date.fromisoformat(payload["service_date"])
        except ValueError:
            raise ValueError("服务日期格式必须为 YYYY-MM-DD")
        if service_day < date.today():
            raise ValueError("服务日期不能早于今天")
        with connect_db() as connection:
            elder = connection.execute("SELECT * FROM elderly_profiles WHERE elder_code=? AND is_deleted=0", (payload["elder_code"],)).fetchone()
            service = connection.execute("SELECT * FROM service_items WHERE service_code=? AND enabled=1", (payload["service_code"],)).fetchone()
            if elder is None:
                raise ValueError("服务对象不存在")
            if service is None:
                raise ValueError("服务项目不存在或已停用")
            duration = int(payload.get("duration_minutes") or service["duration_minutes"])
            cursor = connection.execute(
                """INSERT INTO service_orders (
                  order_no, elder_id, service_item_id, service_date, time_slot, duration_minutes,
                  service_address, service_latitude, service_longitude, contact_phone, special_requirements, meal_type, delivery_time,
                  medical_need, symptom_description, status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'PENDING_ASSIGN')""",
                (f"TEMP-{datetime.now().timestamp()}", elder["elder_id"], service["service_item_id"], payload["service_date"], str(payload["time_slot"]).strip(), duration,
                 str(payload["service_address"]).strip(), payload.get("service_latitude"), payload.get("service_longitude"), str(payload.get("contact_phone") or elder["phone"]).strip(),
                 str(payload.get("special_requirements", "")).strip(), str(payload.get("meal_type", "")).strip() or None, str(payload.get("delivery_time", "")).strip() or None,
                 str(payload.get("medical_need", "")).strip() or None, str(payload.get("symptom_description", "")).strip() or None),
            )
            order_id = cursor.lastrowid
            number = order_number(order_id)
            connection.execute("UPDATE service_orders SET order_no=?, updated_at=? WHERE order_id=?", (number, now_text(), order_id))
        self.send_json(201, {"ok": True, "message": "预约提交成功，等待管理员审核并分配服务人员", "order_id": order_id, "order_no": number, "status": "PENDING_ASSIGN", "status_label": "待分配"})

    def provider_recommendations(self, connection, order):
        service = connection.execute("SELECT * FROM service_items WHERE service_item_id=?", (order["service_item_id"],)).fetchone()
        elder = connection.execute("SELECT * FROM elderly_profiles WHERE elder_id=?", (order["elder_id"],)).fetchone()
        required_cert = ""
        if service["service_type"] == "医疗护理类":
            required_cert = "护士执业证"
        elif service["service_type"] == "康复保健类":
            required_cert = "康复师证"
        elif service["service_type"] == "精神慰藉类" and "心理" in service["service_name"]:
            required_cert = "心理咨询师证"
        elif service["service_type"] == "紧急救援类":
            required_cert = "急救培训证"
        elif service["service_name"] in {"助浴服务", "陪护服务"}:
            required_cert = "养老护理员证"
        providers = connection.execute("SELECT * FROM service_providers WHERE is_deleted=0 AND employment_status='在职'").fetchall()
        results = []
        for p in providers:
            reasons = []
            area_ok = elder["community"] in (p["service_areas"] or "") or elder["community"] in (order["service_address"] or "")
            skill = service["service_type"].replace("类", "")
            skill_ok = skill in (p["skills"] or "")
            cert_ok = not required_cert or required_cert in (p["certificates"] or "")
            conflict = connection.execute("""SELECT 1 FROM service_orders o JOIN service_assignments a ON a.order_id=o.order_id
                WHERE a.provider_id=? AND o.service_date=? AND o.time_slot=? AND o.status IN ('PENDING_SERVICE','IN_SERVICE') LIMIT 1""",
                (p["provider_id"], order["service_date"], order["time_slot"])).fetchone() is not None
            workload = connection.execute("""SELECT COUNT(*) c FROM service_orders o JOIN service_assignments a ON a.order_id=o.order_id
                WHERE a.provider_id=? AND o.service_date=? AND o.status IN ('PENDING_SERVICE','IN_SERVICE','PENDING_REVIEW','COMPLETED')""",
                (p["provider_id"], order["service_date"])).fetchone()["c"]
            recent = connection.execute("""SELECT depart_latitude,depart_longitude FROM service_assignments
                WHERE provider_id=? AND depart_latitude IS NOT NULL AND depart_longitude IS NOT NULL ORDER BY depart_at DESC LIMIT 1""", (p["provider_id"],)).fetchone()
            distance = haversine_km(order["service_latitude"], order["service_longitude"], recent["depart_latitude"], recent["depart_longitude"]) if recent else None
            preference = 0
            req = order["special_requirements"] or ""
            if "女护理员" in req and p["gender"] == "女":
                preference += 5
                reasons.append("符合性别偏好")
            if "男护理员" in req and p["gender"] == "男":
                preference += 5
                reasons.append("符合性别偏好")
            if "方言" in req and "方言" in (p["profile_intro"] or ""):
                preference += 5
                reasons.append("符合语言偏好")
            if p["provider_code"] in req or p["full_name"] in req:
                preference += 15
                reasons.append("老人意向服务人员")
            score = (30 if area_ok else 0) + (25 if skill_ok else 0) + (15 if cert_ok else 0) + (15 if not conflict else 0) + max(0, 10 - workload * 2) + preference
            if distance is not None:
                score += 5 if distance <= 2 else 3 if distance <= 5 else 1 if distance <= 10 else 0
            reasons.append("服务区域覆盖" if area_ok else "服务区域需人工确认")
            reasons.append("服务类型匹配" if skill_ok else "服务类型未匹配")
            reasons.append("资质符合" if cert_ok else "缺少推荐资质")
            reasons.append("排班无冲突" if not conflict else "预约时段冲突")
            reasons.append(f"当日负荷{workload}单")
            if distance is not None:
                reasons.append(f"最近定位约{distance:.1f}km")
            eligible = area_ok and skill_ok and cert_ok and not conflict
            if not eligible:
                level = 1
            elif score >= 95:
                level = 5
            elif score >= 85:
                level = 4
            elif score >= 75:
                level = 3
            elif score >= 65:
                level = 2
            else:
                level = 1
            results.append({
                "provider_code": p["provider_code"],
                "provider_name": p["full_name"],
                "priority_level": level,
                "score": score,
                "eligible": eligible,
                "workload": workload,
                "distance_km": round(distance, 1) if distance is not None else None,
                "reasons": reasons,
            })
        return sorted(results, key=lambda x: (not x["eligible"], -x["priority_level"], -x["score"], x["workload"]))

    def recommend_providers(self, token):
        with connect_db() as connection:
            order = self.resolve_order(connection, token)
            recommendations = self.provider_recommendations(connection, order)
        self.send_json(200, {"ok": True, "order_no": order["order_no"], "recommendations": recommendations})

    def assign_order(self, token):
        payload = self.read_json()
        action = str(payload.get("action", "ASSIGN")).upper()
        auditor = str(payload.get("auditor", "陈雅")).strip() or "陈雅"
        remark = str(payload.get("remark", "")).strip()
        if action not in {"ASSIGN", "WAIT", "REJECT"}:
            raise ValueError("action 只能是 ASSIGN、WAIT 或 REJECT")
        with connect_db() as connection:
            order = self.resolve_order(connection, token)
            if order["status"] != "PENDING_ASSIGN":
                raise ValueError("只有待分配订单可以审核/分配")
            if action == "WAIT":
                connection.execute("UPDATE service_orders SET audit_remark=?, updated_at=? WHERE order_id=?", (remark or "待调度", now_text(), order["order_id"]))
            elif action == "REJECT":
                if not remark:
                    raise ValueError("驳回时必须填写原因")
                connection.execute("UPDATE service_orders SET status='CANCELLED', cancel_reason=?, audit_remark=?, updated_at=? WHERE order_id=?", (remark, remark, now_text(), order["order_id"]))
            else:
                provider_code = str(payload.get("provider_code", "")).strip()
                if str(payload.get("assign_mode", "")).upper() == "SMART":
                    recommendations = self.provider_recommendations(connection, order)
                    eligible = [item for item in recommendations if item["eligible"]]
                    if not eligible:
                        raise ValueError("暂无同时满足区域、服务类型、资质和排班条件的服务人员")
                    provider_code = eligible[0]["provider_code"]
                    remark = (remark + "；" if remark else "") + "智能分配：" + "、".join(eligible[0]["reasons"])
                if not provider_code:
                    raise ValueError("审核通过时必须选择服务人员")
                provider = connection.execute("SELECT * FROM service_providers WHERE provider_code=? AND is_deleted=0", (provider_code,)).fetchone()
                if provider is None:
                    raise ValueError("服务人员不存在")
                if provider["employment_status"] != "在职":
                    raise ValueError("只能分配给在职服务人员")
                conflict = connection.execute(
                    """SELECT 1 FROM service_orders o JOIN service_assignments a ON a.order_id=o.order_id
                       WHERE a.provider_id=? AND o.service_date=? AND o.time_slot=? AND o.status IN ('PENDING_SERVICE','IN_SERVICE') LIMIT 1""",
                    (provider["provider_id"], order["service_date"], order["time_slot"]),
                ).fetchone()
                if conflict:
                    raise ValueError("该服务人员在相同日期和时段已有未完成订单，请改派其他人员")
                connection.execute("UPDATE service_orders SET status='PENDING_SERVICE', audit_remark=?, updated_at=? WHERE order_id=?", (remark, now_text(), order["order_id"]))
                connection.execute(
                    "INSERT INTO service_assignments (order_id, provider_id, assigned_by, assigned_at, assignment_status) VALUES (?, ?, ?, ?, 'ASSIGNED')",
                    (order["order_id"], provider["provider_id"], auditor, now_text()),
                )
            connection.execute("INSERT INTO audit_logs (order_id, action, auditor, remark) VALUES (?, ?, ?, ?)", (order["order_id"], action, auditor, remark))
        labels = {"ASSIGN": "订单已审核并分配服务人员", "WAIT": "订单保持待分配，已记录待调度说明", "REJECT": "订单已驳回并取消"}
        self.send_json(200, {"ok": True, "message": labels[action], "order_no": order["order_no"], "action": action})

    def get_assignment(self, connection, order_id):
        row = connection.execute(
            "SELECT a.*, p.provider_code, p.full_name FROM service_assignments a JOIN service_providers p ON p.provider_id=a.provider_id WHERE a.order_id=?",
            (order_id,),
        ).fetchone()
        if row is None:
            raise ValueError("订单尚未分配服务人员")
        return row

    def depart_order(self, token):
        user = self.require_role("provider")
        payload = self.read_json()
        provider_code = user.get("provider_code") or ""
        with connect_db() as connection:
            connection.execute('BEGIN IMMEDIATE')
            order = self.resolve_order(connection, token)
            assignment = self.get_assignment(connection, order["order_id"])
            if assignment["provider_code"] != provider_code:
                raise PermissionError("该订单未分配给当前服务人员")
            if order["status"] != "PENDING_SERVICE" or assignment["assignment_status"] != "ASSIGNED":
                raise ValueError("只有待服务且尚未出发的订单可以执行出发")
            lat = payload.get("latitude")
            lon = payload.get("longitude")
            ts = now_text()
            connection.execute("UPDATE service_assignments SET assignment_status='DEPARTED', depart_at=?, depart_latitude=?, depart_longitude=? WHERE assignment_id=?", (ts, lat, lon, assignment["assignment_id"]))
            connection.execute("UPDATE service_orders SET status='IN_SERVICE', updated_at=? WHERE order_id=?", (ts, order["order_id"]))
        self.send_json(200, {"ok": True, "message": "已记录出发，订单进入服务中", "order_no": order["order_no"], "status": "IN_SERVICE", "assignment_status": "DEPARTED", "depart_at": ts, "latitude": lat, "longitude": lon})

    def arrive_order(self, token):
        user = self.require_role("provider")
        provider_code = user.get("provider_code") or ""
        with connect_db() as connection:
            connection.execute('BEGIN IMMEDIATE')
            order = self.resolve_order(connection, token)
            assignment = self.get_assignment(connection, order["order_id"])
            if assignment["provider_code"] != provider_code:
                raise PermissionError("该订单未分配给当前服务人员")
            if order["status"] != "IN_SERVICE" or assignment["assignment_status"] != "DEPARTED":
                raise ValueError("只有已出发且尚未到达的订单可以执行到达")
            ts = now_text()
            connection.execute("UPDATE service_assignments SET assignment_status='ARRIVED', arrive_at=? WHERE assignment_id=?", (ts, assignment["assignment_id"]))
            connection.execute("UPDATE service_orders SET updated_at=? WHERE order_id=?", (ts, order["order_id"]))
        self.send_json(200, {"ok": True, "message": "已记录到达时间，可以开始服务", "order_no": order["order_no"], "status": "IN_SERVICE", "assignment_status": "ARRIVED", "arrive_at": ts})

    def complete_order(self, token):
        user = self.require_role("provider")
        provider_code = user.get("provider_code") or ""
        with connect_db() as connection:
            order = self.resolve_order(connection, token)
            assignment = self.get_assignment(connection, order["order_id"])
            if assignment["provider_code"] != provider_code:
                raise PermissionError("该订单未分配给当前服务人员")
            if order["status"] != "IN_SERVICE" or assignment["assignment_status"] not in {"DEPARTED", "ARRIVED"}:
                raise ValueError("只有服务中的订单可以完成")
            ts = now_text()
            connection.execute("UPDATE service_assignments SET assignment_status='FINISHED', finish_at=? WHERE assignment_id=?", (ts, assignment["assignment_id"]))
            connection.execute("UPDATE service_orders SET status='PENDING_REVIEW', updated_at=? WHERE order_id=?", (ts, order["order_id"]))
        self.send_json(200, {"ok": True, "message": "服务已完成，订单进入待评价", "order_no": order["order_no"], "status": "PENDING_REVIEW", "assignment_status": "FINISHED", "finish_at": ts})

    def create_service_record(self, token):
        user = self.require_role("provider")
        payload = self.read_json()
        completion = str(payload.get("completion_status", "")).strip()
        if completion not in {"全部完成", "部分完成", "未执行"}:
            raise ValueError("completion_status 必须是 全部完成、部分完成 或 未执行")
        provider_code = user.get("provider_code") or ""
        with connect_db() as connection:
            order = self.resolve_order(connection, token)
            assignment = self.get_assignment(connection, order["order_id"])
            if assignment["provider_code"] != provider_code:
                raise PermissionError("该订单未分配给当前服务人员")
            if assignment["assignment_status"] != "FINISHED":
                raise ValueError("请先完成服务，再提交服务记录")
            if connection.execute("SELECT 1 FROM service_records WHERE order_id=? LIMIT 1", (order["order_id"],)).fetchone():
                raise ValueError("该订单已提交服务记录")
            cursor = connection.execute(
                """INSERT INTO service_records (order_id, provider_id, actual_start_time, actual_end_time, completion_status, process_description, elder_observation, materials)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (order["order_id"], assignment["provider_id"], payload.get("actual_start_time") or assignment["arrive_at"] or assignment["depart_at"], payload.get("actual_end_time") or assignment["finish_at"], completion, str(payload.get("process_description", "")).strip(), str(payload.get("elder_observation", "")).strip(), str(payload.get("materials", "")).strip()),
            )
            record_id = cursor.lastrowid
        self.send_json(201, {"ok": True, "message": "服务记录已保存到数据库", "record_id": record_id, "order_no": order["order_no"]})

    def cancel_order(self, token):
        user = self.require_role("elder", "admin")
        payload = self.read_json()
        reason = str(payload.get("reason", "老人/家属取消预约")).strip()
        with connect_db() as connection:
            connection.execute('BEGIN IMMEDIATE')
            order = self.resolve_order(connection, token)
            if user["role"] == "elder":
                elder = connection.execute("SELECT elder_code FROM elderly_profiles WHERE elder_id=?", (order["elder_id"],)).fetchone()
                if elder is None or elder["elder_code"] != user.get("elder_code"):
                    raise PermissionError("无权取消其他老人的订单")
            if order["status"] not in {"PENDING_ASSIGN", "PENDING_SERVICE"}:
                raise ValueError("只有待分配或待服务订单可以取消")
            ts = now_text()
            connection.execute("UPDATE service_orders SET status='CANCELLED', cancel_reason=?, updated_at=? WHERE order_id=?", (reason, ts, order["order_id"]))
            connection.execute("UPDATE service_assignments SET assignment_status='CANCELLED' WHERE order_id=?", (order["order_id"],))
            connection.execute("INSERT INTO audit_logs (order_id, action, auditor, remark) VALUES (?, 'CANCEL', ?, ?)", (order["order_id"], user.get("display_name") or "老人/家属", reason))
        self.send_json(200, {"ok": True, "message": "订单已取消", "order_no": order["order_no"], "status": "CANCELLED"})

    def serve_static(self, path):
        relative = "index.html" if path in {"", "/"} else path.lstrip("/")
        target = (STATIC_DIR / relative).resolve()
        static_root = STATIC_DIR.resolve()
        if static_root not in target.parents and target != static_root:
            return self.api_error(403, "禁止访问该路径")
        if not target.is_file():
            return self.api_error(404, "页面不存在")
        body = target.read_bytes()
        content_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        self.send_response(200)
        self.send_header("Content-Type", f"{content_type}; charset=utf-8" if content_type.startswith("text/") or content_type in {"application/javascript"} else content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        print(f"[{self.log_date_time_string()}] {format % args}")


def main():
    reset = "--reset" in sys.argv
    port = 8080
    host = "127.0.0.1"
    if "--port" in sys.argv:
        port = int(sys.argv[sys.argv.index("--port") + 1])
    if "--host" in sys.argv:
        host = sys.argv[sys.argv.index("--host") + 1]
    if '--data-dir' in sys.argv:
        global BASE_DIR, DB_PATH, UPLOAD_DIR
        BASE_DIR = Path(sys.argv[sys.argv.index('--data-dir') + 1]).resolve()
        BASE_DIR.mkdir(parents=True, exist_ok=True)
        DB_PATH = BASE_DIR / 'elderly_care.db'
        UPLOAD_DIR = BASE_DIR / 'uploads' / 'exams'
    initialize_database(reset=reset)
    server = ThreadingHTTPServer((host, port), ApiHandler)
    display_host = "127.0.0.1" if host == "0.0.0.0" else host
    print(f"社区居家养老服务系统：http://{display_host}:{port}")
    print(f"数据库：{DB_PATH}")
    print("API 健康检查：/api/health")
    print("按 Ctrl+C 停止服务")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n服务已停止")
    finally:
        server.server_close()


from acceptance.core import install as _install_acceptance
_install_acceptance(sys.modules[__name__])

if __name__ == "__main__":
    main()
