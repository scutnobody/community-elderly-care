"""Compatibility adapters and validated business rules for the course edition.

Legacy routes are preserved; new functions share real persistent domain tables.
All writes run against the caller's configured database, including test copies.
"""
import base64
import hashlib
import hmac
import io
import json
import math
import re
import secrets
import sqlite3
import threading
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlparse


class ClosingConnection(sqlite3.Connection):
    def __exit__(self, *args):
        try:
            result=super().__exit__(*args)
            if args[0] is None and self.total_changes and getattr(self,'after_commit',None):self.after_commit()
            return result
        finally:
            self.close()


def hash_password(password):
    salt = secrets.token_hex(16)
    iterations = 200000
    digest = hashlib.pbkdf2_hmac('sha256', password.encode(), salt.encode(), iterations).hex()
    return f'pbkdf2_sha256${iterations}${salt}${digest}'


def verify_password(password, stored):
    if stored.startswith('pbkdf2_sha256$'):
        try:
            _, iterations, salt, digest = stored.split('$')
            iterations = int(iterations)
            if not 100000 <= iterations <= 2000000:
                return False
            actual = hashlib.pbkdf2_hmac('sha256', password.encode(), salt.encode(), iterations).hex()
        except (ValueError, TypeError):
            return False
        return hmac.compare_digest(actual, digest)
    return hmac.compare_digest(hashlib.sha256(password.encode()).hexdigest(), stored)


def timestamp(value, default=None):
    value = value or default
    if not isinstance(value, str):
        raise ValueError('日期时间格式无效')
    try:
        parsed = datetime.fromisoformat(value.strip().replace('T', ' '))
    except ValueError:
        raise ValueError('日期时间格式无效')
    if parsed.tzinfo is not None:
        raise ValueError('请使用本地日期时间，不附带时区')
    return parsed.strftime('%Y-%m-%d %H:%M:%S')


def number(value, label, minimum=0, maximum=100000, integer=False):
    try:
        if isinstance(value, bool):
            raise ValueError()
        result = float(value)
    except (ValueError, TypeError, OverflowError):
        raise ValueError(label+'必须是有效数值')
    if not math.isfinite(result) or result < minimum or result > maximum:
        raise ValueError(f'{label}超出允许输入范围 {minimum}—{maximum}')
    if integer and not result.is_integer():
        raise ValueError(label+'必须为整数')
    return int(result) if integer else result


def interval(service_date, slot, duration):
    try:
        day = date.fromisoformat(str(service_date))
    except ValueError:
        raise ValueError('服务日期格式必须为 YYYY-MM-DD')
    matches = re.findall(r'(\d{1,2}):(\d{2})', str(slot))
    if len(matches) != 2:
        raise ValueError('预约时段必须包含开始及结束时间，如 09:00-11:00')
    try:
        start = datetime.combine(day, datetime.min.time()).replace(hour=int(matches[0][0]), minute=int(matches[0][1]))
        end = datetime.combine(day, datetime.min.time()).replace(hour=int(matches[1][0]), minute=int(matches[1][1]))
    except ValueError:
        raise ValueError('预约时段不是有效时间')
    if '次日' in str(slot):end+=timedelta(days=1)
    duration = number(duration, '服务时长', 1, 1440, integer=True)
    if end-start>timedelta(days=1):raise ValueError('单张预约区间不能超过24小时')
    if end <= start or end-start < timedelta(minutes=duration):
        raise ValueError('所选时段必须覆盖服务时长，长时服务请选择连续时段')
    return start.strftime('%Y-%m-%d %H:%M:%S'), end.strftime('%Y-%m-%d %H:%M:%S')


class Extension:
    def __init__(self, module):
        self.m = module
        self._revision=0
        self._stats_cache={}
        self._stats_lock=threading.Lock()
        self.original_initialize = module.initialize_database
        self.original = {name: getattr(module.ApiHandler, name) for name in (
            'current_user', 'require_role', 'do_GET', 'do_POST', 'get_order',
            'get_elder_profile', 'get_provider_profile', 'update_provider_profile',
            'create_review', 'upload_exam_report', 'get_reviews', 'delete_elder', 'complete_profile',
        )}

    def connect_db(self):
        c = sqlite3.connect(self.m.DB_PATH, timeout=15, factory=ClosingConnection)
        c.row_factory = sqlite3.Row
        c.execute('PRAGMA foreign_keys=ON')
        c.execute('PRAGMA busy_timeout=15000')
        c.after_commit=self.changed
        return c

    def changed(self):
        self._revision+=1

    def initialize(self, reset=False):
        # The retained project copy is backed up before its first migration.
        path = Path(self.m.DB_PATH)
        if path.exists() and not reset:
            backup = path.with_suffix('.before-acceptance.db')
            if not backup.exists():
                with sqlite3.connect(path) as src, sqlite3.connect(backup) as dst:
                    src.backup(dst)
                src.close(); dst.close()
        self.m._acceptance_skip_demo_seed=path.exists() and not reset
        self.original_initialize(reset)
        from . import business
        business.migrate(self.m)
        with self.connect_db() as c:
            c.execute('PRAGMA journal_mode=WAL')
            c.execute('''CREATE TABLE IF NOT EXISTS device_imports(
                import_id INTEGER PRIMARY KEY,elder_id INTEGER NOT NULL REFERENCES elderly_profiles(elder_id),
                device_code TEXT NOT NULL,external_id TEXT NOT NULL,vital_id INTEGER,
                created_at TEXT NOT NULL,UNIQUE(elder_id,device_code,external_id))''')
            duplicate=c.execute('SELECT 1 FROM service_records GROUP BY order_id HAVING count(*)>1 LIMIT 1').fetchone()
            if not duplicate:c.execute('CREATE UNIQUE INDEX IF NOT EXISTS one_record_per_order ON service_records(order_id)')
            self.m._ensure_column(c,'elderly_profiles','priority_override',"TEXT DEFAULT ''")
            c.execute('CREATE INDEX IF NOT EXISTS idx_order_intervals ON service_orders(service_date,start_at,end_at,status)')
            for row in c.execute('SELECT * FROM service_orders WHERE start_at IS NULL OR service_name_snapshot IS NULL').fetchall():
                try:
                    start, end = interval(row['service_date'], row['time_slot'], row['duration_minutes'])
                except ValueError:
                    # Retain legacy long appointments without truncating their duration.
                    m = re.search(r'(\d{1,2}):(\d{2})', row['time_slot'] or '')
                    if not m:
                        continue
                    try:
                        dt = datetime.fromisoformat(row['service_date']).replace(hour=int(m[1]),minute=int(m[2]))
                    except ValueError:
                        continue
                    start=dt.strftime('%Y-%m-%d %H:%M:%S'); end=(dt+timedelta(minutes=row['duration_minutes'])).strftime('%Y-%m-%d %H:%M:%S')
                service=c.execute('SELECT * FROM service_items WHERE service_item_id=?',(row['service_item_id'],)).fetchone()
                c.execute('UPDATE service_orders SET start_at=?,end_at=?,service_name_snapshot=COALESCE(service_name_snapshot,?),price_snapshot=COALESCE(price_snapshot,?),duration_snapshot=COALESCE(duration_snapshot,?) WHERE order_id=?',
                          (start,end,service['service_name'],service['price'],row['duration_minutes'],row['order_id']))

    def read_json(self, h):
        try:
            size=int(h.headers.get('Content-Length','0'))
            if size < 0 or size > 18*1024*1024:
                raise ValueError('请求体不能超过 18 MB')
            data=json.loads(h.rfile.read(size).decode('utf-8') if size else '{}',parse_constant=lambda value:(_ for _ in ()).throw(ValueError('不允许非有限数值')))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise ValueError('请求必须是有效 JSON')
        if not isinstance(data,dict):
            raise ValueError('JSON 请求必须是对象')
        return data

    def current_user(self, h):
        # Always validate persisted account state; revoked/disabled accounts cannot use cached sessions.
        header=h.headers.get('Authorization','')
        token=header[7:].strip() if header.startswith('Bearer ') else ''
        self.m.SESSIONS.pop(token,None)
        user=self.original['current_user'](h)
        if user and user['role'] in ('elder','provider') and user.get('profile_completed'):
            table,field,code=('elderly_profiles','elder_code',user.get('elder_code')) if user['role']=='elder' else ('service_providers','provider_code',user.get('provider_code'))
            with self.connect_db() as c:
                row=c.execute(f'SELECT is_deleted FROM {table} WHERE {field}=?',(code,)).fetchone()
            if row is None or row['is_deleted']:
                return None
        return user

    def login(self, h):
        p=h.read_json(); username=str(p.get('username','')).strip(); password=str(p.get('password',''))
        if len(password)>512:
            raise ValueError('密码过长')
        with self.connect_db() as c:
            account=c.execute('SELECT * FROM auth_accounts WHERE username=? AND enabled=1',(username,)).fetchone()
            if account is None or not verify_password(password,account['password_hash']):
                raise PermissionError('用户名或密码错误')
            if p.get('role') and p['role']!=account['role']:
                raise PermissionError('账号与所选角色不匹配')
            if not account['password_hash'].startswith('pbkdf2_sha256$'):
                c.execute('UPDATE auth_accounts SET password_hash=? WHERE account_id=?',(hash_password(password),account['account_id']))
        user=h.session_user(account); token=h.create_session(user,bool(p.get('remember')))
        h.send_json(200,{'ok':True,'token':token,'user':user})

    def update_provider_profile(self, h, code):
        user=h.require_role('admin','provider'); p=h.read_json()
        managed={'skills','certificates','employment_status','service_years','health_certificate','training_records'}
        if user['role']=='provider' and managed.intersection(p):
            raise PermissionError('服务权限、资质、健康证明和在职状态需管理员审核，请使用资质提交入口')
        if user['role']=='provider' and code!=user.get('provider_code'):
            raise PermissionError('无权修改其他人员')
        allowed={'full_name','gender','id_card','phone','organization','service_areas','profile_intro'} | (managed if user['role']=='admin' else set())
        values={k:v for k,v in p.items() if k in allowed}
        if not values: raise ValueError('没有可保存资料')
        with self.connect_db() as c:
            c.execute('BEGIN IMMEDIATE')
            row=c.execute('SELECT * FROM service_providers WHERE provider_code=? AND is_deleted=0',(code,)).fetchone()
            if row is None: raise ValueError('人员不存在')
            if p.get('employment_status') in ('请假','离职'):
                active=c.execute("SELECT 1 FROM service_assignments a JOIN service_orders o ON a.order_id=o.order_id WHERE a.provider_id=? AND o.status IN ('PENDING_SERVICE','IN_SERVICE')",(row['provider_id'],)).fetchone()
                emergency=c.execute("SELECT 1 FROM emergency_requests WHERE assigned_provider_id=? AND status IN ('已派单','服务人员已出发','已到达')",(row['provider_id'],)).fetchone()
                if active or emergency: raise ValueError('存在未完成任务，请先交接或处理')
            if 'service_years' in values: values['service_years']=number(values['service_years'],'从业年限',0,80,True)
            c.execute('UPDATE service_providers SET '+','.join(k+'=?' for k in values)+' WHERE provider_code=?',(*values.values(),code))
            if 'full_name' in values:c.execute('UPDATE auth_accounts SET display_name=? WHERE provider_code=?',(values['full_name'],code))
            result=dict(c.execute('SELECT * FROM service_providers WHERE provider_code=?',(code,)).fetchone())
        h.send_json(200,{'ok':True,'profile':result,'message':'人员资料已保存'})

    def complete_profile(self,h):
        p=h.read_json();user=h.current_user()
        if user and user['role']=='provider':
            # Self-declared skills are a request, not an admin authorization.
            requested=str(p.get('skills') or '')
            if not requested.strip():raise ValueError('请填写申请服务类型')
            p=dict(p,skills='待管理员授权',profile_intro=(str(p.get('profile_intro') or '')+'\n申请服务类型：'+requested).strip())
        h.read_json=lambda:p
        return self.original['complete_profile'](h)

    def elder_profile(self,c,row):
        p=dict(row);p['age']=self.m.age_from_birth(p.get('birth_date',''))
        p['service_priority']=p.get('priority_override') or ('重点关注' if p['service_priority']=='优先' else p['service_priority'])
        return p

    def get_elder_profile(self,h,code):
        user=h.require_role('admin','elder')
        if user['role']=='elder' and code!=user.get('elder_code'):raise PermissionError('无权查看其他老人档案')
        with self.connect_db() as c:row=c.execute('SELECT * FROM elderly_profiles WHERE elder_code=?',(code,)).fetchone()
        if row is None:raise ValueError('老人不存在')
        h.send_json(200,{'ok':True,'profile':self.elder_profile(None,row)})

    def update_elder_profile(self,h,code):
        user=h.require_role('admin','elder');p=h.read_json()
        if user['role']=='elder' and code!=user.get('elder_code'):raise PermissionError('不能修改他人档案')
        if user['role']!='admin' and ('service_priority' in p or 'photo_attachment_id' in p):raise PermissionError('优先级和证件照需管理员维护')
        allowed={'full_name','gender','id_card','birth_date','ethnicity','phone','community','address','height_cm','weight_kg','blood_type','allergy_history','disease_history','disability_status','self_care_level','medical_type','medical_card_no','designated_hospital','emergency_contact_name','emergency_contact_relation','emergency_contact_phone','emergency_backup_phone','living_situation','language_preference','diet_requirement','visit_time_preference','elder_tags'}
        values={k:v for k,v in p.items() if k in allowed}
        if 'service_priority' in p:
            if p['service_priority'] not in ('普通','重点关注','紧急干预','优先'):raise ValueError('优先级无效')
            values['priority_override']='重点关注' if p['service_priority']=='优先' else p['service_priority']
        for k,maximum in [('height_cm',250),('weight_kg',500)]:
            if k in values and values[k] is not None:values[k]=number(values[k],k,1,maximum)
        if 'birth_date' in values:
            try:birth=date.fromisoformat(values['birth_date'])
            except (ValueError,TypeError):raise ValueError('出生日期无效')
            if birth>date.today():raise ValueError('出生日期不能在未来')
        if not values:raise ValueError('没有可保存字段')
        with self.connect_db() as c:
            c.execute('BEGIN IMMEDIATE');row=c.execute('SELECT * FROM elderly_profiles WHERE elder_code=? AND is_deleted=0',(code,)).fetchone()
            if row is None:raise ValueError('老人不存在或已注销')
            merged=dict(row);merged.update(values)
            for k in ('full_name','gender','phone','self_care_level','living_situation'):
                if not str(merged.get(k) or '').strip():raise ValueError(k+'不能为空')
            c.execute('UPDATE elderly_profiles SET '+','.join(k+'=?' for k in values)+' WHERE elder_code=?',(*values.values(),code))
            if 'full_name' in values:c.execute('UPDATE auth_accounts SET display_name=? WHERE elder_code=?',(values['full_name'],code))
            result=self.elder_profile(c,c.execute('SELECT * FROM elderly_profiles WHERE elder_code=?',(code,)).fetchone())
        h.send_json(200,{'ok':True,'profile':result,'message':'老人资料已保存'})

    def get_options(self,h):
        user=h.require_role('admin','elder','provider','guest')
        with self.connect_db() as c:
            services=[dict(r) for r in c.execute('SELECT * FROM service_items WHERE enabled=1 ORDER BY service_item_id')]
            if user['role']=='admin':elders=[dict(r) for r in c.execute('SELECT elder_id,elder_code,full_name,phone,address,community FROM elderly_profiles WHERE is_deleted=0 ORDER BY elder_id LIMIT 200')]
            elif user['role']=='elder':elders=[dict(r) for r in c.execute('SELECT elder_id,elder_code,full_name,phone,address,community FROM elderly_profiles WHERE elder_code=? AND is_deleted=0',(user.get('elder_code'),))]
            else:elders=[]
            if user['role']=='guest':providers=[]
            else:providers=[dict(r) for r in c.execute("SELECT provider_id,provider_code,full_name,organization,skills,service_areas,employment_status FROM service_providers WHERE is_deleted=0 AND employment_status='在职' ORDER BY provider_id LIMIT 200")]
        h.send_json(200,{'ok':True,'elders':elders,'services':services,'providers':providers})

    def directory(self,h,kind):
        h.require_role('admin');q=parse_qs(urlparse(h.path).query);term=(q.get('q') or [''])[0];page=int(number((q.get('page') or [1])[0],'页码',1,1000000,True));size=int(number((q.get('page_size') or [50])[0],'每页条数',1,200,True))
        table,code,key=('elderly_profiles','elder_code','elders') if kind=='elders' else ('service_providers','provider_code','providers')
        where='is_deleted=0';params=[]
        if term:where+=' AND (full_name LIKE ? OR '+code+' LIKE ? OR '+('community' if kind=='elders' else 'service_areas')+' LIKE ?)';params=['%'+term+'%']*3
        with self.connect_db() as c:
            total=c.execute(f'SELECT count(*) FROM {table} WHERE '+where,params).fetchone()[0]
            items=[dict(r) for r in c.execute(f'SELECT * FROM {table} WHERE '+where+f' ORDER BY {code} LIMIT ? OFFSET ?',(*params,size,(page-1)*size))]
        if kind=='elders':items=[self.elder_profile(None,r) for r in items]
        h.send_json(200,{'ok':True,key:items,'count':total,'total':total,'page':page,'page_size':size})

    def upload_exam_report(self,h):
        user=h.require_role('admin','elder');p=h.read_json()
        from .business import _attachment_bytes
        name,mime,raw,suffix=_attachment_bytes(p)
        try:date.fromisoformat(p.get('exam_date',''))
        except (ValueError,TypeError):raise ValueError('体检日期无效')
        if not str(p.get('organization') or '').strip():raise ValueError('体检机构不能为空')
        code=user.get('elder_code') if user['role']=='elder' else p.get('elder_code')
        if user['role']=='elder' and p.get('elder_code') and p['elder_code']!=code:raise PermissionError('不能上传其他老人报告')
        with self.connect_db() as c:
            elder=c.execute('SELECT * FROM elderly_profiles WHERE elder_code=? AND is_deleted=0',(code,)).fetchone()
            if elder is None:raise ValueError('老人不存在')
            folder=Path(self.m.UPLOAD_DIR);folder.mkdir(parents=True,exist_ok=True);stored=secrets.token_hex(24)+suffix;target=folder/stored
            try:
                target.write_bytes(raw)
                ident=c.execute('INSERT INTO exam_reports(elder_id,exam_date,organization,file_name,stored_name,mime_type,file_size,abnormal_summary) VALUES(?,?,?,?,?,?,?,?)',(elder['elder_id'],p['exam_date'],p['organization'],name,stored,mime,len(raw),str(p.get('abnormal_summary') or ''))).lastrowid
            except Exception:
                target.unlink(missing_ok=True);raise
        h.send_json(201,{'ok':True,'report_id':ident,'message':'报告已验证并保存'})

    def create_order(self, h):
        user=h.require_role('admin','elder'); p=h.read_json()
        code=p.get('elder_code')
        if user['role']=='elder':
            if code and code!=user.get('elder_code'):raise PermissionError('不能为其他老人预约')
            code=user.get('elder_code')
        required=[code,p.get('service_code'),p.get('service_date'),p.get('time_slot'),p.get('service_address')]
        if any(not str(v or '').strip() for v in required): raise ValueError('服务对象、项目、日期、时段和地址不能为空')
        if str(p['service_date'])<date.today().isoformat():raise ValueError('服务日期不能早于今天')
        with self.connect_db() as c:
            c.execute('BEGIN IMMEDIATE')
            elder=c.execute('SELECT * FROM elderly_profiles WHERE elder_code=? AND is_deleted=0',(code,)).fetchone()
            service=c.execute('SELECT * FROM service_items WHERE service_code=? AND enabled=1',(p['service_code'],)).fetchone()
            if elder is None or service is None:raise ValueError('服务对象或可用服务项目不存在')
            duration=number(p.get('duration_minutes',service['duration_minutes']),'时长',1,1440,True)
            start,end=interval(p['service_date'],p['time_slot'],duration)
            if service['service_name']=='助餐服务' and (p.get('meal_type') not in ('普通餐','软食','流食','糖尿病餐','低盐餐') or not p.get('delivery_time')):
                raise ValueError('助餐服务需选择餐食类型及配送时间')
            if service['service_name']=='助医服务' and (p.get('medical_need') not in ('上门巡诊','陪同就医','代取药品') or not str(p.get('symptom_description') or '').strip()):
                raise ValueError('助医服务需填写就诊需求及症状描述')
            values={'order_no':'TMP-'+secrets.token_hex(12),'elder_id':elder['elder_id'],'service_item_id':service['service_item_id'],
                    'service_date':p['service_date'],'time_slot':str(p['time_slot']).strip(),'duration_minutes':duration,
                    'service_address':str(p['service_address']).strip(),'contact_phone':p.get('contact_phone') or elder['phone'],
                    'special_requirements':str(p.get('special_requirements') or ''),'meal_type':p.get('meal_type'),'delivery_time':p.get('delivery_time'),
                    'medical_need':p.get('medical_need'),'symptom_description':p.get('symptom_description'),'status':'PENDING_ASSIGN',
                    'start_at':start,'end_at':end,'service_name_snapshot':service['service_name'],'price_snapshot':service['price'],
                    'duration_snapshot':duration,'condition_check':str(p.get('condition_check') or '')}
            for key in ('service_latitude','service_longitude'):
                if p.get(key) is not None:values[key]=number(p[key],key,-90 if key.endswith('latitude') else -180,90 if key.endswith('latitude') else 180)
            cursor=c.execute('INSERT INTO service_orders('+','.join(values)+') VALUES('+','.join('?' for _ in values)+')',tuple(values.values()))
            ident=cursor.lastrowid; order_no=self.m.order_number(ident)
            c.execute('UPDATE service_orders SET order_no=?,updated_at=? WHERE order_id=?',(order_no,self.m.now_text(),ident))
        h.send_json(201,{'ok':True,'order_id':ident,'order_no':order_no,'status':'PENDING_ASSIGN','status_label':'待分配','message':'预约已保存，等待审核分配'})

    def eligibility(self, c, order, provider):
        service=c.execute('SELECT * FROM service_items WHERE service_item_id=?',(order['service_item_id'],)).fetchone()
        elder=c.execute('SELECT * FROM elderly_profiles WHERE elder_id=?',(order['elder_id'],)).fetchone()
        areas=set(re.split(r'[、,，;；/\s]+',provider['service_areas'] or ''))
        area_ok=elder['community'] in areas
        skill=service['service_type'].replace('类',''); skill_ok=skill in (provider['skills'] or '')
        required=service['required_certificate'] or ''
        cert_ok=True
        if required:
            cert_ok=bool(c.execute("SELECT 1 FROM provider_credentials WHERE provider_id=? AND name=? AND status='APPROVED' AND certificate_no!='' AND expires_on>=?",(provider['provider_id'],required,date.today().isoformat())).fetchone())
        start,end=(order['start_at'],order['end_at']) if order['start_at'] and order['end_at'] else interval(order['service_date'],order['time_slot'],order['duration_minutes'])
        conflict=c.execute("""SELECT 1 FROM service_orders o JOIN service_assignments a ON o.order_id=a.order_id
            WHERE a.provider_id=? AND o.order_id!=? AND o.status IN ('PENDING_SERVICE','IN_SERVICE')
            AND o.start_at<? AND o.end_at>? LIMIT 1""",(provider['provider_id'],order['order_id'],end,start)).fetchone()
        active=not provider['is_deleted'] and provider['employment_status']=='在职'
        reasons=[('服务区域覆盖' if area_ok else '服务区域未覆盖'),('服务类型匹配' if skill_ok else '服务类型未匹配'),('资质符合' if cert_ok else '缺少有效已审核资质'),('排班无冲突' if not conflict else '预约时间重叠')]
        return active and area_ok and skill_ok and cert_ok and not conflict,reasons

    def provider_recommendations(self, h, c, order):
        result=[]
        for p in c.execute('SELECT * FROM service_providers WHERE is_deleted=0').fetchall():
            eligible,reasons=self.eligibility(c,order,p)
            load=c.execute("SELECT count(*) FROM service_assignments a JOIN service_orders o ON a.order_id=o.order_id WHERE a.provider_id=? AND o.service_date=? AND o.status!='CANCELLED'",(p['provider_id'],order['service_date'])).fetchone()[0]
            location=c.execute('SELECT * FROM provider_locations WHERE provider_id=?',(p['provider_id'],)).fetchone()
            distance=None
            if location and location['recorded_at'] >= (datetime.now()-timedelta(minutes=5)).strftime('%Y-%m-%d %H:%M:%S'):
                distance=self.m.haversine_km(order['service_latitude'],order['service_longitude'],location['latitude'],location['longitude'])
            preferred=p['provider_code'] in (order['special_requirements'] or '') or p['full_name'] in (order['special_requirements'] or '')
            score=(90 if eligible else 0)+max(0,10-load*2)+(15 if preferred else 0)
            if distance is not None:score+=max(0,5-distance/2);reasons.append(f'最近位置距离 {distance:.1f} km')
            result.append({'provider_code':p['provider_code'],'provider_name':p['full_name'],'eligible':bool(eligible),'priority_level':5 if eligible else 1,'score':round(score,1),'workload':load,'distance_km':round(distance,1) if distance is not None else None,'reasons':reasons})
        return sorted(result,key=lambda r:(not r['eligible'],-r['score'],r['workload']))

    def assign_order(self, h, token, reassign=False):
        user=h.require_role('admin'); p=h.read_json(); action=str(p.get('action','ASSIGN')).upper(); reason=str(p.get('reason') or p.get('remark') or '').strip()
        if action not in ('ASSIGN','WAIT','REJECT'):raise ValueError('无效调度动作')
        if reassign and not reason:raise ValueError('交接改派必须填写原因')
        with self.connect_db() as c:
            c.execute('BEGIN IMMEDIATE')
            order=h.resolve_order(c,token)
            if order['status']!=('PENDING_SERVICE' if reassign else 'PENDING_ASSIGN'):raise ValueError('当前订单状态不允许分配或改派')
            if action=='WAIT':
                c.execute('UPDATE service_orders SET audit_remark=?,updated_at=? WHERE order_id=?',(reason or '待调度',self.m.now_text(),order['order_id']))
            elif action=='REJECT':
                if not reason:raise ValueError('驳回须填写原因')
                c.execute("UPDATE service_orders SET status='CANCELLED',cancel_reason=?,audit_remark=?,updated_at=? WHERE order_id=?",(reason,reason,self.m.now_text(),order['order_id']))
            else:
                code=p.get('provider_code')
                if p.get('assign_mode')=='SMART':
                    options=[r for r in self.provider_recommendations(h,c,order) if r['eligible']]
                    if not options:raise ValueError('暂无符合全部条件的服务人员')
                    code=options[0]['provider_code']
                provider=c.execute('SELECT * FROM service_providers WHERE provider_code=?',(code,)).fetchone()
                if provider is None:raise ValueError('服务人员不存在')
                eligible,reasons=self.eligibility(c,order,provider)
                if not eligible:raise ValueError('不能派单：'+'；'.join(reasons))
                if reassign:
                    old=h.get_assignment(c,order['order_id'])
                    c.execute('INSERT INTO assignment_history(order_id,provider_id,action,reason,actor,created_at) VALUES(?,?,?,?,?,?)',(order['order_id'],old['provider_id'],'REASSIGN_FROM',reason,user['username'],self.m.now_text()))
                    c.execute("UPDATE service_assignments SET provider_id=?,assigned_by=?,assigned_at=?,assignment_status='ASSIGNED' WHERE order_id=?",(provider['provider_id'],user['display_name'],self.m.now_text(),order['order_id']))
                else:
                    c.execute("INSERT INTO service_assignments(order_id,provider_id,assigned_by,assigned_at,assignment_status) VALUES(?,?,?,?,'ASSIGNED')",(order['order_id'],provider['provider_id'],user['display_name'],self.m.now_text()))
                c.execute("UPDATE service_orders SET status='PENDING_SERVICE',audit_remark=?,condition_check=?,updated_at=? WHERE order_id=?",(reason,str(p.get('condition_check') or order['condition_check'] or ''),self.m.now_text(),order['order_id']))
                self.notify(c,'provider',provider['provider_code'],'order','新的服务任务',order['order_no'],order['order_no'])
            c.execute('INSERT INTO audit_logs(order_id,action,auditor,remark) VALUES(?,?,?,?)',(order['order_id'],action,user['display_name'],reason))
        h.send_json(200,{'ok':True,'order_no':order['order_no'],'action':action,'message':'调度操作已保存'})

    def complete_order(self, h, token):
        user=h.require_role('provider')
        with self.connect_db() as c:
            c.execute('BEGIN IMMEDIATE'); order=h.resolve_order(c,token); assignment=h.get_assignment(c,order['order_id'])
            if assignment['provider_code']!=user.get('provider_code'):raise PermissionError('非本人任务')
            if order['status']!='IN_SERVICE' or assignment['assignment_status']!='ARRIVED':raise ValueError('必须先登记到达，再完成服务')
            ts=self.m.now_text(); c.execute("UPDATE service_assignments SET assignment_status='FINISHED',finish_at=? WHERE order_id=?",(ts,order['order_id']))
            c.execute("UPDATE service_orders SET status='PENDING_REVIEW',updated_at=? WHERE order_id=?",(ts,order['order_id']))
        h.send_json(200,{'ok':True,'order_no':order['order_no'],'status':'PENDING_REVIEW','assignment_status':'FINISHED','finish_at':ts,'message':'服务已完成，请提交记录审核'})

    def notify(self,c,role,code,kind,title,body,reference):
        c.execute('INSERT INTO notifications(recipient_role,recipient_code,kind,title,body,reference,is_read,created_at) VALUES(?,?,?,?,?,?,0,?)',(role,code or '',kind,title,body,reference,self.m.now_text()))

    def attachment_ids(self,c,ids,order_no,user):
        if not isinstance(ids,list):raise ValueError('附件必须是编号列表')
        for ident in ids:
            r=c.execute('SELECT * FROM attachments WHERE attachment_id=?',(ident,)).fetchone()
            if r is None or r['owner_type']!='order' or str(r['owner_key'])!=str(order_no):raise PermissionError('附件不属于该订单')
        return ids

    def create_service_record(self,h,token):
        user=h.require_role('provider');p=h.read_json()
        if p.get('completion_status') not in ('全部完成','部分完成','未执行'):raise ValueError('完成情况无效')
        with self.connect_db() as c:
            c.execute('BEGIN IMMEDIATE');order=h.resolve_order(c,token);assignment=h.get_assignment(c,order['order_id'])
            if assignment['provider_code']!=user.get('provider_code'):raise PermissionError('非本人任务')
            if assignment['assignment_status']!='FINISHED':raise ValueError('先完成服务再提交记录')
            old=c.execute('SELECT * FROM service_records WHERE order_id=?',(order['order_id'],)).fetchone()
            if old and old['review_status']!='已退回':raise ValueError('记录已提交或已通过，不可重复提交')
            if p['completion_status']=='未执行':
                if not str(p.get('process_description') or '').strip():raise ValueError('请说明未执行原因')
                start=end=None
            else:
                if not assignment['arrive_at']:raise ValueError('实际开展服务前须登记到达')
                if p['completion_status']=='部分完成' and not str(p.get('process_description') or '').strip():raise ValueError('请说明部分完成原因')
                start=timestamp(p.get('actual_start_time'),assignment['arrive_at']);end=timestamp(p.get('actual_end_time'),assignment['finish_at'])
                if end<start:raise ValueError('实际结束时间不能早于开始时间')
            ids=self.attachment_ids(c,p.get('attachments_json') or [],order['order_no'],user)
            materials=p.get('materials_json') or []
            if not isinstance(materials,list):raise ValueError('耗材须为明细列表')
            for entry in materials:
                if not isinstance(entry,dict) or not str(entry.get('name','')).strip():raise ValueError('耗材需要名称和数量')
                entry['quantity']=number(entry.get('quantity'),'耗材数量',0.01,100000)
            values={'actual_start_time':start,'actual_end_time':end,'completion_status':p['completion_status'],
                    'process_description':str(p.get('process_description') or ''),'elder_observation':str(p.get('elder_observation') or ''),'materials':str(p.get('materials') or ''),
                    'medical_details':str(p.get('medical_details') or ''),'rehab_details':str(p.get('rehab_details') or ''),
                    'materials_json':json.dumps(materials,ensure_ascii=False),'attachments_json':json.dumps(ids),'review_status':'待审核','review_note':''}
            if old:
                ident=old['record_id'];c.execute('UPDATE service_records SET '+','.join(k+'=?' for k in values)+',submitted_at=? WHERE record_id=?',(*values.values(),self.m.now_text(),ident))
            else:
                values.update(order_id=order['order_id'],provider_id=assignment['provider_id'])
                ident=c.execute('INSERT INTO service_records('+','.join(values)+') VALUES('+','.join('?' for _ in values)+')',tuple(values.values())).lastrowid
            self.notify(c,'admin','','record','服务记录待审核',order['order_no'],order['order_no'])
        h.send_json(201,{'ok':True,'record_id':ident,'review_status':'待审核','message':'记录已保存，等待管理员审核'})

    def create_review(self,h,token):
        # Validate approval before allowing legacy insert; lock protects review/status update together.
        user=h.require_role('elder');p=h.read_json()
        with self.connect_db() as c:
            c.execute('BEGIN IMMEDIATE');order=h.resolve_order(c,token)
            elder=c.execute('SELECT elder_code FROM elderly_profiles WHERE elder_id=?',(order['elder_id'],)).fetchone()
            if elder['elder_code']!=user.get('elder_code'):raise PermissionError('不能评价其他老人订单')
            if order['status']!='PENDING_REVIEW':raise ValueError('订单不是待评价状态')
            record=c.execute('SELECT review_status FROM service_records WHERE order_id=?',(order['order_id'],)).fetchone()
            if not record or record['review_status'] not in ('已通过','历史导入'):raise ValueError('服务记录需通过审核后才能评价')
            scores=[number(p.get(k),'评分',1,5,True) for k in ('attitude_score','quality_score','punctual_score')]
            ids=self.attachment_ids(c,p.get('attachments_json') or [],order['order_no'],user)
            assignment=h.get_assignment(c,order['order_id'])
            c.execute('INSERT INTO service_reviews(order_id,elder_id,provider_id,attitude_score,quality_score,punctual_score,review_text,photo_note) VALUES(?,?,?,?,?,?,?,?)',(order['order_id'],order['elder_id'],assignment['provider_id'],*scores,str(p.get('review_text') or ''),json.dumps(ids)))
            c.execute("UPDATE service_orders SET status='COMPLETED',updated_at=? WHERE order_id=?",(self.m.now_text(),order['order_id']))
        h.send_json(201,{'ok':True,'order_no':order['order_no'],'status':'COMPLETED','overall':round(sum(scores)/3,1),'message':'评价已保存'})

    def health_values(self,p):
        limits={'systolic':(1,400),'diastolic':(1,300),'glucose':(0,100),'heart_rate':(1,300),'temperature':(1,60),'weight':(1,500),'sleep_hours':(0,24)}
        if p.get('bp'):
            parts=str(p['bp']).replace('／','/').split('/')
            if len(parts)!=2:raise ValueError('血压格式必须为收缩压/舒张压')
            p=dict(p,systolic=parts[0],diastolic=parts[1])
        values={key:number(p[key],key,*bounds,integer=key in ('systolic','diastolic','heart_rate')) if p.get(key) not in ('',None) else None for key,bounds in limits.items()}
        if not any(v is not None for v in values.values()):raise ValueError('至少填写一个体征指标')
        if values['systolic'] is not None and values['diastolic'] is not None and values['systolic']<values['diastolic']:raise ValueError('收缩压不能小于舒张压')
        return values

    def save_health(self,c,elder,p):
        values=self.health_values(p);ts=timestamp(p.get('measured_at'),self.m.now_text())
        ident=c.execute('INSERT INTO health_vitals(elder_id,measured_at,systolic,diastolic,glucose,heart_rate,temperature,weight,sleep_hours,source) VALUES(?,?,?,?,?,?,?,?,?,?)',(elder['elder_id'],ts,*values.values(),str(p.get('source') or '手动录入'))).lastrowid
        defaults={'systolic':(None,140),'diastolic':(None,90),'glucose':(None,7),'heart_rate':(60,100),'temperature':(None,38),'sleep_hours':(7,8)}
        labels={'systolic':'血压','diastolic':'血压','glucose':'空腹血糖','heart_rate':'心率','temperature':'体温','sleep_hours':'睡眠时长','weight':'体重'}
        alerts=[]
        for metric,value in values.items():
            if value is None:continue
            setting=c.execute('SELECT * FROM health_thresholds WHERE elder_id=? AND metric=?',(elder['elder_id'],metric)).fetchone()
            if setting and not setting['enabled']:continue
            lower,upper=(setting['lower_bound'],setting['upper_bound']) if setting else defaults.get(metric,(None,None))
            # Preserve original one-sided inclusive highs, two-sided reference ranges exclusive outside.
            triggered=(lower is not None and value<lower) or (upper is not None and (value>=upper if lower is None else value>upper))
            if triggered:
                label=labels[metric];threshold=f'{lower if lower is not None else "不限"}—{upper if upper is not None else "不限"}'
                c.execute('INSERT INTO health_alerts(vital_id,elder_id,metric_type,current_value,threshold_value,created_at) VALUES(?,?,?,?,?,?)',(ident,elder['elder_id'],label,str(value),threshold,ts))
                alerts.append(label)
        if alerts:self.notify(c,'admin','','health','新的健康预警',elder['elder_code']+'：'+'、'.join(alerts),str(ident))
        return ident,alerts

    def create_health_record(self,h):
        user=h.require_role('elder');p=h.read_json()
        if p.get('elder_code') and p['elder_code']!=user.get('elder_code'):raise PermissionError('不能录入其他老人数据')
        with self.connect_db() as c:
            c.execute('BEGIN IMMEDIATE');elder=c.execute('SELECT * FROM elderly_profiles WHERE elder_code=? AND is_deleted=0',(user.get('elder_code'),)).fetchone()
            if elder is None:raise ValueError('老人不存在')
            ident,alerts=self.save_health(c,elder,p)
        h.send_json(201,{'ok':True,'vital_id':ident,'alert_count':len(alerts),'alerts':alerts,'message':'健康记录已保存'+('，已生成预警' if alerts else '')})

    def get_health_records(self,h,query):
        user=h.require_role('admin','elder');code=user.get('elder_code') if user['role']=='elder' else (query.get('elder_code') or [''])[0]
        days=int(number((query.get('days') or [30])[0],'天数',1,90,True));end=date.today();start=end-timedelta(days=days-1)
        with self.connect_db() as c:
            elder=c.execute('SELECT * FROM elderly_profiles WHERE elder_code=?',(code,)).fetchone()
            if elder is None:raise ValueError('老人不存在')
            records=[dict(r) for r in c.execute('SELECT * FROM health_vitals WHERE elder_id=? AND measured_at>=? AND measured_at<? ORDER BY measured_at DESC,vital_id DESC',(elder['elder_id'],start.isoformat(),(end+timedelta(days=1)).isoformat()))]
        h.send_json(200,{'ok':True,'records':records,'days':days,'start_date':start.isoformat(),'end_date':end.isoformat(),'simulated':False})

    def create_emergency(self,h):
        user=h.require_role('elder');p=h.read_json()
        if p.get('elder_code') and p['elder_code']!=user.get('elder_code'):raise PermissionError('不能为其他老人发起求助')
        if p.get('emergency_type') not in ('身体不适','跌倒','火灾','煤气泄漏','入室危险'):raise ValueError('无效求助类型')
        with self.connect_db() as c:
            c.execute('BEGIN IMMEDIATE');elder=c.execute('SELECT * FROM elderly_profiles WHERE elder_code=? AND is_deleted=0',(user.get('elder_code'),)).fetchone()
            if elder is None:raise ValueError('老人不存在')
            ts=self.m.now_text();lat=p.get('latitude');lon=p.get('longitude')
            if lat is not None:lat=number(lat,'纬度',-90,90)
            if lon is not None:lon=number(lon,'经度',-180,180)
            ident=c.execute("INSERT INTO emergency_requests(emergency_no,elder_id,emergency_type,trigger_source,address,latitude,longitude,status,notify_targets,result,created_at,updated_at) VALUES(?,?,?,?,?,?,?,'待调度',?,'待处理',?,?)",('TMP-'+secrets.token_hex(12),elder['elder_id'],p['emergency_type'],'APP一键呼叫',p.get('address') or elder['address'],lat,lon,'紧急联系人、社区管理员、家庭医生',ts,ts)).lastrowid
            token=f'JQ{datetime.now():%Y%m%d}{ident:04d}';c.execute('UPDATE emergency_requests SET emergency_no=? WHERE emergency_id=?',(token,ident))
            self.notify(c,'admin','','emergency','新的紧急求助',elder['full_name']+'：'+p['emergency_type'],token)
            self.notify(c,'elder',elder['elder_code'],'emergency','求助已登记','管理员可查看；外部短信/微信渠道未配置',token)
            for target in ('紧急联系人','家庭医生'):
                c.execute('INSERT INTO external_notifications(emergency_id,target,channel,status,created_at) VALUES(?,?,?,?,?)',(ident,target,'external','UNCONFIGURED',ts))
        h.send_json(201,{'ok':True,'emergency_id':ident,'emergency_no':token,'status':'待调度','created_at':ts,'cancel_until':(datetime.fromisoformat(ts)+timedelta(seconds=30)).strftime('%Y-%m-%d %H:%M:%S'),'external_status':'UNCONFIGURED','message':'求助已立即登记并产生站内通知；外部渠道未配置'})

    def dispatch_emergency(self,h,token):
        user=h.require_role('admin');p=h.read_json()
        with self.connect_db() as c:
            c.execute('BEGIN IMMEDIATE');row=h.resolve_emergency(c,token)
            if row['status'] not in ('待调度','已派单'):raise ValueError('当前状态不能改派')
            person=c.execute("SELECT * FROM service_providers WHERE provider_code=? AND is_deleted=0 AND employment_status='在职'",(p.get('provider_code'),)).fetchone()
            if person is None:raise ValueError('只能指派在职有效人员')
            ts=self.m.now_text();seconds=max(0,int((datetime.fromisoformat(ts)-datetime.fromisoformat(row['created_at'])).total_seconds()))
            elapsed=f'{seconds//60:02d}:{seconds%60:02d}'
            # First response time is retained when dispatcher later changes assignee.
            c.execute("UPDATE emergency_requests SET status='已派单',assigned_provider_id=?,dispatch_at=COALESCE(dispatch_at,?),dispatch_note=?,response_time=CASE WHEN response_time IS NULL OR response_time='' THEN ? ELSE response_time END,updated_at=? WHERE emergency_id=?",(person['provider_id'],ts,str(p.get('dispatch_note') or ''),elapsed,ts,row['emergency_id']))
            self.notify(c,'provider',person['provider_code'],'emergency','新的紧急任务',row['emergency_no'],row['emergency_no'])
            c.execute('INSERT INTO business_logs(entity_type,entity_key,action,actor,detail,created_at) VALUES(?,?,?,?,?,?)',('emergency',row['emergency_no'],'DISPATCH',user['username'],person['provider_code'],ts))
        h.send_json(200,{'ok':True,'emergency_no':row['emergency_no'],'status':'已派单','provider_code':person['provider_code'],'dispatch_at':ts,'message':'应急任务已指派并产生站内提醒'})

    def get_order(self,h,token):
        # Include approval and specialist fields in the original detail response.
        user=h.require_role('admin','elder','provider')
        with self.connect_db() as c:
            base=h.resolve_order(c,token);row=c.execute(h.order_select_sql('WHERE o.order_id=?'),(base['order_id'],)).fetchone();payload=h.row_to_order(row)
            if user['role']=='elder' and payload['elder_code']!=user.get('elder_code'):raise PermissionError('无权查看其他老人订单')
            if user['role']=='provider' and payload.get('provider_code')!=user.get('provider_code'):raise PermissionError('非本人任务')
            payload.update({k:base[k] for k in ('start_at','end_at','service_name_snapshot','price_snapshot','duration_snapshot','condition_check')})
            payload['records']=[dict(r) for r in c.execute('SELECT * FROM service_records WHERE order_id=? ORDER BY submitted_at DESC',(base['order_id'],))]
            payload['service_name']=base['service_name_snapshot'] or payload['service_name']
            if base['price_snapshot'] is not None:payload['price']=base['price_snapshot']
            elder=c.execute('SELECT allergy_history,disease_history,self_care_level,diet_requirement,language_preference,emergency_contact_name,emergency_contact_relation,emergency_contact_phone FROM elderly_profiles WHERE elder_id=?',(base['elder_id'],)).fetchone()
            payload['care_precautions']=dict(elder) if elder else {}
        h.send_json(200,{'ok':True,'order':payload})

    def get_orders(self,h,query):
        user=h.require_role('admin','elder','provider');where=[];params=[]
        if user['role']=='elder':where.append('e.elder_code=?');params.append(user.get('elder_code'))
        elif user['role']=='provider':where.append('p.provider_code=?');params.append(user.get('provider_code'))
        for name,column in [('status','o.status'),('elder_code','e.elder_code'),('provider_code','p.provider_code'),('community','e.community'),('service_type','s.service_type')]:
            if query.get(name) and (name not in ('elder_code','provider_code') or user['role']=='admin'):
                where.append(column+'=?');params.append(query[name][0])
        if (query.get('record_needed') or [''])[0]=='1':
            where.append("(NOT EXISTS(SELECT 1 FROM service_records sr WHERE sr.order_id=o.order_id) OR EXISTS(SELECT 1 FROM service_records sr WHERE sr.order_id=o.order_id AND sr.review_status='已退回'))")
        clause='WHERE '+' AND '.join(where) if where else ''
        page=int(number((query.get('page') or [1])[0],'页码',1,1000000,True));size=int(number((query.get('page_size') or [200])[0],'每页条数',1,200,True))
        with self.connect_db() as c:
            total=c.execute('SELECT COUNT(*) FROM service_orders o JOIN elderly_profiles e ON e.elder_id=o.elder_id JOIN service_items s ON s.service_item_id=o.service_item_id LEFT JOIN service_assignments a ON a.order_id=o.order_id LEFT JOIN service_providers p ON p.provider_id=a.provider_id '+clause,params).fetchone()[0]
            rows=c.execute(h.order_select_sql(clause)+' ORDER BY o.created_at DESC,o.order_id DESC LIMIT ? OFFSET ?',(*params,size,(page-1)*size)).fetchall()
            orders=[]
            for row in rows:
                d=h.row_to_order(row);base=c.execute('SELECT service_name_snapshot,price_snapshot,start_at,end_at FROM service_orders WHERE order_id=?',(d['order_id'],)).fetchone()
                record=c.execute('SELECT review_status FROM service_records WHERE order_id=? ORDER BY submitted_at DESC,record_id DESC LIMIT 1',(d['order_id'],)).fetchone()
                condition=c.execute('SELECT condition_check FROM service_orders WHERE order_id=?',(d['order_id'],)).fetchone()
                d.update(start_at=base['start_at'],end_at=base['end_at'],record_review_status=record['review_status'] if record else None,condition_check=condition['condition_check']);d['service_name']=base['service_name_snapshot'] or d['service_name'];orders.append(d)
                if base['price_snapshot'] is not None:d['price']=base['price_snapshot']
        h.send_json(200,{'ok':True,'orders':orders,'count':total,'total':total,'page':page,'page_size':size})

    def stats(self,query):
        from .reporting import statistics
        key=tuple(sorted((str(k),tuple(str(x) for x in v)) for k,v in query.items()))
        with self._stats_lock:
            revision=self._revision;cached=self._stats_cache.get(key)
            if cached and cached[0]==revision and time.monotonic()-cached[1]<2:return cached[2]
            result=statistics(self.m,query)
            if len(self._stats_cache)>32:self._stats_cache.clear()
            if revision==self._revision:self._stats_cache[key]=(revision,time.monotonic(),result)
            return result

    def get_dashboard_stats(self,h):
        h.require_role('admin');q=parse_qs(urlparse(h.path).query);h.send_json(200,{'ok':True,'stats':self.stats(q)})

    def export_report(self,h,query):
        h.require_role('admin');fmt=(query.get('format') or ['excel'])[0];stats=self.stats(query);buf=io.BytesIO()
        if fmt=='excel':
            from openpyxl import Workbook
            from openpyxl.styles import Font
            wb=Workbook();ws=wb.active;ws.title='统计概览';ws.append(['社区居家养老课程验收统计',stats['as_of']]);ws.append(['数据来源',stats['source']]);ws.append(['筛选',json.dumps(stats['filters'],ensure_ascii=False)])
            for key in ('elder_count','order_count','provider_count','institution_count','completion_rate','good_rate','health_alert_count','emergency_count','average_response_seconds','average_arrival_seconds','false_alarm_rate','chronic_coverage_rate'):ws.append([key,stats[key]])
            ws.append(['统计口径',stats['notes']])
            for key in ('service_type_counts','community_counts','time_slot_counts','age_groups','gender_groups','self_care_groups','living_groups','health_alert_groups','emergency_type_counts','emergency_result_counts'):
                sheet=wb.create_sheet(key[:31]);sheet.append(['分组','条数']);[sheet.append([k,v]) for k,v in stats[key].items()]
            sheet=wb.create_sheet('人员绩效');sheet.append(['工号','姓名','已履约单量','实际时长h','好评率%','投诉率%'])
            for r in stats['provider_performance']:sheet.append([r[k] for k in ('provider_code','full_name','orders','hours','good_rate','complaint_rate')])
            sheet=wb.create_sheet('订单趋势');sheet.append(['月份','订单','已完成']);[sheet.append([r['month'],r['orders'],r['completed']]) for r in stats['order_trend']]
            for sheet in wb:
                for cell in sheet[1]:cell.font=Font(bold=True)
                for col in sheet.columns:sheet.column_dimensions[col[0].column_letter].width=24
            wb.save(buf);mime='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet';ext='xlsx'
        elif fmt=='pdf':
            from reportlab.pdfgen import canvas
            from reportlab.lib.pagesizes import A4
            from reportlab.pdfbase import pdfmetrics
            from reportlab.pdfbase.cidfonts import UnicodeCIDFont
            pdfmetrics.registerFont(UnicodeCIDFont('STSong-Light'));cv=canvas.Canvas(buf,pagesize=A4);y=790
            lines=['社区居家养老服务系统课程验收统计',stats['source'],'筛选 '+json.dumps(stats['filters'],ensure_ascii=False),'统计日期 '+stats['as_of']]
            for key,value in stats.items():
                if key in ('source','filters','as_of'):continue
                if isinstance(value,dict):lines.append(key);lines.extend('  '+str(k)+'：'+str(v) for k,v in value.items())
                elif isinstance(value,list):lines.append(key);lines.extend(json.dumps(r,ensure_ascii=False) for r in value)
                else:lines.append(key+'：'+str(value if value is not None else '无有效数据'))
            for line in lines:
                # Conservative character wrapping keeps CJK and long identifiers within the page.
                for at in range(0,max(len(line),1),60):
                    if y<55:cv.showPage();y=790
                    cv.setFont('STSong-Light',10);cv.drawString(45,y,line[at:at+60]);y-=17
            cv.save();mime='application/pdf';ext='pdf'
        else:raise ValueError('format 仅支持 excel/pdf')
        h.send_bytes(200,buf.getvalue(),mime,'care-report-'+stats['as_of']+'.'+ext)

    def device_route(self,h,method,path,query):
        if method=='GET' and path=='/api/acceptance/device-info':
            h.require_role('admin','elder');h.send_json(200,{'ok':True,'mode':'课程模拟导入','connected':False,'message':'未连接真实智能设备'});return True
        if method=='POST' and path=='/api/acceptance/device-import':
            user=h.require_role('admin','elder');p=h.read_json();code=user.get('elder_code') if user['role']=='elder' else p.get('elder_code')
            if user['role']=='elder' and p.get('elder_code') and p['elder_code']!=code:raise PermissionError('无权导入其他老人数据')
            if not p.get('device_code') or not p.get('external_id') or not isinstance(p.get('measurement'),dict):raise ValueError('需填写设备编号、去重编号和测量数据')
            with self.connect_db() as c:
                c.execute('BEGIN IMMEDIATE');elder=c.execute('SELECT * FROM elderly_profiles WHERE elder_code=? AND is_deleted=0',(code,)).fetchone()
                if elder is None:raise ValueError('老人不存在')
                old=c.execute('SELECT vital_id FROM device_imports WHERE elder_id=? AND device_code=? AND external_id=?',(elder['elder_id'],p['device_code'],p['external_id'])).fetchone()
                if old:h.send_json(200,{'ok':True,'duplicate':True,'vital_id':old['vital_id'],'simulated':True});return True
                measurement=dict(p['measurement'],source='课程模拟导入：'+str(p['device_code']));ident,alerts=self.save_health(c,elder,measurement)
                c.execute('INSERT INTO device_imports(elder_id,device_code,external_id,vital_id,created_at) VALUES(?,?,?,?,?)',(elder['elder_id'],p['device_code'],p['external_id'],ident,self.m.now_text()))
            h.send_json(201,{'ok':True,'vital_id':ident,'simulated':True,'connected':False,'message':'课程模拟数据已导入，不代表真实设备连接'});return True
        return False

    def abort(self,h,token):
        user=h.require_role('admin');p=h.read_json();reason=str(p.get('reason') or '').strip()
        if not reason:raise ValueError('必须填写异常中止原因')
        with self.connect_db() as c:
            c.execute('BEGIN IMMEDIATE');o=h.resolve_order(c,token)
            if o['status']!='IN_SERVICE':raise ValueError('仅服务中订单可异常中止')
            c.execute("UPDATE service_orders SET status='CANCELLED',cancel_reason=?,updated_at=? WHERE order_id=?",(reason,self.m.now_text(),o['order_id']))
            c.execute("UPDATE service_assignments SET assignment_status='CANCELLED' WHERE order_id=?",(o['order_id'],))
            c.execute('INSERT INTO business_logs(entity_type,entity_key,action,actor,detail,created_at) VALUES(?,?,?,?,?,?)',('order',o['order_no'],'ABORT',user['username'],reason,self.m.now_text()))
        h.send_json(200,{'ok':True,'status':'CANCELLED','message':'异常中止已记录，历史保留'})

    def exception_record(self,h,token):
        user=h.require_role('admin','provider');p=h.read_json();reason=str(p.get('reason') or '').strip();completion=p.get('completion_status')
        if not reason or completion not in ('未执行','部分完成'):raise ValueError('异常处理须选择未执行或部分完成并填写具体原因')
        with self.connect_db() as c:
            c.execute('BEGIN IMMEDIATE');order=h.resolve_order(c,token);assignment=h.get_assignment(c,order['order_id'])
            if user['role']=='provider' and assignment['provider_code']!=user.get('provider_code'):raise PermissionError('非本人任务')
            if order['status'] not in ('PENDING_SERVICE','IN_SERVICE'):raise ValueError('当前状态不允许异常记录')
            if completion=='部分完成' and assignment['assignment_status']!='ARRIVED':raise ValueError('部分完成需有实际到达记录，未入户请选择未执行')
            if c.execute('SELECT 1 FROM service_records WHERE order_id=?',(order['order_id'],)).fetchone():raise ValueError('该订单已有服务记录')
            now=self.m.now_text();actual_start=assignment['arrive_at'] if completion=='部分完成' else None;actual_end=now if actual_start else None
            ident=c.execute("INSERT INTO service_records(order_id,provider_id,actual_start_time,actual_end_time,completion_status,process_description,review_status) VALUES(?,?,?,?,?,?,'待审核')",(order['order_id'],assignment['provider_id'],actual_start,actual_end,completion,'异常原因：'+reason)).lastrowid
            c.execute("UPDATE service_assignments SET assignment_status='FINISHED',finish_at=? WHERE order_id=?",(now,order['order_id']))
            c.execute("UPDATE service_orders SET status='PENDING_REVIEW',updated_at=? WHERE order_id=?",(now,order['order_id']))
            c.execute('INSERT INTO business_logs(entity_type,entity_key,action,actor,detail,created_at) VALUES(?,?,?,?,?,?)',('order',order['order_no'],'SERVICE_EXCEPTION',user['username'],reason,now))
            self.notify(c,'admin','','record','异常服务记录待审核',order['order_no']+'：'+reason,order['order_no'])
        h.send_json(201,{'ok':True,'record_id':ident,'status':'PENDING_REVIEW','review_status':'待审核','message':'异常情况已记录，未伪造到达或服务开始时间'})

    def elder_history(self,h,code,query):
        user=h.require_role('admin','elder')
        if user['role']=='elder' and code!=user.get('elder_code'):raise PermissionError('无权查看其他老人历史')
        page=int(number((query.get('page') or [1])[0],'页码',1,1000000,True));size=int(number((query.get('page_size') or [50])[0],'每页',1,200,True))
        with self.connect_db() as c:
            elder=c.execute('SELECT * FROM elderly_profiles WHERE elder_code=?',(code,)).fetchone()
            if elder is None:raise ValueError('老人不存在')
            result={'ok':True,'profile':self.elder_profile(c,elder),'page':page,'page_size':size,'totals':{}}
            for name,table,criteria,params,sort in (
                ('orders','service_orders','elder_id=?',(elder['elder_id'],),'created_at DESC,order_id DESC'),
                ('health','health_vitals','elder_id=?',(elder['elder_id'],),'measured_at DESC,vital_id DESC'),
                ('emergencies','emergency_requests','elder_id=?',(elder['elder_id'],),'created_at DESC,emergency_id DESC'),
                ('records','service_records','order_id IN (SELECT order_id FROM service_orders WHERE elder_id=?)',(elder['elder_id'],),'submitted_at DESC,record_id DESC')):
                result['totals'][name]=c.execute(f'SELECT COUNT(*) FROM {table} WHERE {criteria}',params).fetchone()[0]
                result[name]=[dict(r) for r in c.execute(f'SELECT * FROM {table} WHERE {criteria} ORDER BY {sort} LIMIT ? OFFSET ?',(*params,size,(page-1)*size))]
        h.send_json(200,result)

    def send_bytes(self,h,status,body,content_type,filename=None):
        h.send_response(status)
        h.send_header('Content-Type',content_type)
        h.send_header('Content-Length',str(len(body)))
        h.send_header('Cache-Control','no-store')
        h.send_header('X-Content-Type-Options','nosniff')
        if filename:
            clean=str(filename).replace('\r','').replace('\n','').replace('"','')
            fallback=clean.encode('ascii','ignore').decode() or 'download'
            h.send_header('Content-Disposition',f'attachment; filename="{fallback}"; filename*=UTF-8\'\'{quote(clean,safe="")}')
        h.end_headers()
        h.wfile.write(body)

    def dispatch(self,h,method):
        parsed=urlparse(h.path);path=unquote(parsed.path);query=parse_qs(parsed.query)
        try:
            if path.startswith('/api/acceptance/'):
                if path=='/api/acceptance/statistics' and method=='GET':return self.get_dashboard_stats(h)
                if path=='/api/acceptance/my-statistics' and method=='GET':
                    user=h.require_role('provider');scoped={k:v for k,v in query.items() if k in ('date_from','date_to')};scoped['provider_code']=[user['provider_code']]
                    report=self.stats(scoped);person=next((p for p in report['provider_performance'] if p['provider_code']==user['provider_code']),None)
                    if person is None:raise ValueError('人员不存在')
                    return h.send_json(200,{'ok':True,'stats':dict(person,order_trend=report['order_trend'],service_type_counts=report['service_type_counts'],filters=report['filters'],as_of=report['as_of'])})
                history=re.fullmatch('/api/acceptance/elders/([^/]+)/history',path)
                if history and method=='GET':return self.elder_history(h,history[1],query)
                if self.device_route(h,method,path,query):return
                from . import business
                if business.route(h,self.m,method,path,query):return
                return h.api_error(404,'接口不存在')
            if method=='POST':
                match=re.fullmatch('/api/orders/([^/]+)/(reassign|abort|exception)',path)
                if match:
                    if match[2]=='reassign':return self.assign_order(h,match[1],True)
                    if match[2]=='exception':return self.exception_record(h,match[1])
                    return self.abort(h,match[1])
            return self.original['do_'+method](h)
        except PermissionError as error:h.api_error(403,str(error))
        except (ValueError,TypeError) as error:h.api_error(400,str(error))
        except sqlite3.IntegrityError as error:h.api_error(409,'记录冲突或约束不满足：'+str(error))
        except sqlite3.OperationalError as error:h.api_error(409,'数据库暂忙，请稍后重试' if 'locked' in str(error) else '数据库操作失败')


def install(module):
    ext=Extension(module);module.connect_db=ext.connect_db;module.initialize_database=ext.initialize;module.hash_password=hash_password
    names=('read_json','send_bytes','current_user','login','complete_profile','get_elder_profile','update_elder_profile','get_options','upload_exam_report','update_provider_profile','create_order','provider_recommendations','assign_order','complete_order','create_service_record','create_review','create_health_record','get_health_records','create_emergency','dispatch_emergency','get_order','get_orders','get_dashboard_stats','export_report')
    for name in names:
        def method(handler,*args,_name=name,**kwargs):return getattr(ext,_name)(handler,*args,**kwargs)
        setattr(module.ApiHandler,name,method)
    module.ApiHandler.do_GET=lambda h:ext.dispatch(h,'GET')
    module.ApiHandler.do_POST=lambda h:ext.dispatch(h,'POST')
    module.ApiHandler.get_elders=lambda h:ext.directory(h,'elders')
    module.ApiHandler.get_providers=lambda h:ext.directory(h,'providers')
    module.acceptance_extension=ext
