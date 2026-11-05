"""Persistent course acceptance resources and ownership checked attachment storage."""
import base64
import binascii
from contextlib import contextmanager
from datetime import date, datetime, timedelta
import json
import math
from pathlib import Path
import re
import secrets


def _now():
    return datetime.now().strftime('%Y-%m-%d %H:%M:%S')


@contextmanager
def _db(module):
    connection = module.connect_db()
    try:
        with connection:
            yield connection
    finally:
        connection.close()


def migrate(module):
    """Upgrade in place, keeping legacy identities and never approving old certificates."""
    with _db(module) as c:
        additions = {
            'service_items': {'content':"TEXT DEFAULT ''", 'service_mode':"TEXT DEFAULT '上门'", 'target_group':"TEXT DEFAULT ''", 'booking_notes':"TEXT DEFAULT ''", 'required_certificate':"TEXT DEFAULT ''", 'service_status':"TEXT DEFAULT '正常提供'"},
            'service_orders': {'start_at':'TEXT', 'end_at':'TEXT', 'service_name_snapshot':'TEXT', 'price_snapshot':'REAL', 'duration_snapshot':'INTEGER', 'condition_check':"TEXT DEFAULT ''"},
            'service_records': {'medical_details':"TEXT DEFAULT ''", 'rehab_details':"TEXT DEFAULT ''", 'materials_json':"TEXT DEFAULT '[]'", 'attachments_json':"TEXT DEFAULT '[]'", 'review_status':"TEXT DEFAULT '待审核'", 'review_note':"TEXT DEFAULT ''", 'reviewed_by':'TEXT', 'reviewed_at':'TEXT'},
            'elderly_profiles': {'photo_attachment_id':'INTEGER', 'archive_reason':"TEXT DEFAULT ''", 'archived_at':'TEXT'},
            'service_complaints': {'handled_by':'TEXT', 'handled_at':'TEXT'},
        }
        new_service_metadata = False
        for table, columns in additions.items():
            existing = {r['name'] for r in c.execute('PRAGMA table_info(' + table + ')')}
            for column, definition in columns.items():
                if column not in existing:
                    c.execute(f'ALTER TABLE {table} ADD COLUMN {column} {definition}')
                    if table == 'service_items' and column == 'required_certificate':
                        new_service_metadata = True
        c.executescript('''
        CREATE TABLE IF NOT EXISTS acceptance_migrations(name TEXT PRIMARY KEY,applied_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS provider_credentials(
          credential_id INTEGER PRIMARY KEY,provider_id INTEGER NOT NULL REFERENCES service_providers(provider_id),
          name TEXT NOT NULL,certificate_no TEXT DEFAULT '',expires_on TEXT,
          status TEXT NOT NULL DEFAULT 'PENDING' CHECK(status IN ('PENDING','APPROVED','REJECTED')),
          attachment_id INTEGER,review_note TEXT DEFAULT '',reviewed_by TEXT,reviewed_at TEXT,created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS provider_locations(provider_id INTEGER PRIMARY KEY REFERENCES service_providers(provider_id),latitude REAL NOT NULL,longitude REAL NOT NULL,recorded_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS health_thresholds(threshold_id INTEGER PRIMARY KEY,elder_id INTEGER NOT NULL REFERENCES elderly_profiles(elder_id),metric TEXT NOT NULL,lower_bound REAL,upper_bound REAL,enabled INTEGER NOT NULL DEFAULT 1,action TEXT DEFAULT '',UNIQUE(elder_id,metric));
        CREATE TABLE IF NOT EXISTS notifications(notification_id INTEGER PRIMARY KEY,recipient_role TEXT NOT NULL,recipient_code TEXT DEFAULT '',kind TEXT NOT NULL,title TEXT NOT NULL,body TEXT DEFAULT '',reference TEXT DEFAULT '',is_read INTEGER DEFAULT 0,created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS external_notifications(notification_id INTEGER PRIMARY KEY,emergency_id INTEGER NOT NULL REFERENCES emergency_requests(emergency_id),target TEXT NOT NULL,channel TEXT NOT NULL,status TEXT NOT NULL,created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS business_logs(log_id INTEGER PRIMARY KEY,entity_type TEXT NOT NULL,entity_key TEXT NOT NULL,action TEXT NOT NULL,actor TEXT NOT NULL,detail TEXT DEFAULT '',created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS attachments(attachment_id INTEGER PRIMARY KEY,owner_type TEXT NOT NULL,owner_key TEXT NOT NULL,purpose TEXT NOT NULL,file_name TEXT NOT NULL,stored_name TEXT NOT NULL UNIQUE,mime_type TEXT NOT NULL,file_size INTEGER NOT NULL,uploaded_by TEXT NOT NULL,created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS assignment_history(history_id INTEGER PRIMARY KEY,order_id INTEGER NOT NULL REFERENCES service_orders(order_id),provider_id INTEGER REFERENCES service_providers(provider_id),action TEXT NOT NULL,reason TEXT DEFAULT '',actor TEXT NOT NULL,created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS institutions(institution_id INTEGER PRIMARY KEY,institution_code TEXT NOT NULL UNIQUE,name TEXT NOT NULL,institution_type TEXT DEFAULT '',province TEXT DEFAULT '',city TEXT DEFAULT '',district TEXT DEFAULT '',street TEXT DEFAULT '',community TEXT DEFAULT '',address TEXT DEFAULT '',phone TEXT DEFAULT '',bed_count INTEGER DEFAULT 0,introduction TEXT DEFAULT '',created_at TEXT NOT NULL,updated_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS institution_services(institution_id INTEGER NOT NULL REFERENCES institutions(institution_id),service_item_id INTEGER NOT NULL REFERENCES service_items(service_item_id),PRIMARY KEY(institution_id,service_item_id));
        CREATE TABLE IF NOT EXISTS institution_attachments(institution_id INTEGER NOT NULL REFERENCES institutions(institution_id),attachment_id INTEGER NOT NULL REFERENCES attachments(attachment_id),PRIMARY KEY(institution_id,attachment_id));
        CREATE TABLE IF NOT EXISTS service_packages(id INTEGER PRIMARY KEY,name TEXT NOT NULL,price REAL NOT NULL,valid_until TEXT NOT NULL,status TEXT NOT NULL,created_at TEXT NOT NULL,updated_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS package_items(package_id INTEGER NOT NULL REFERENCES service_packages(id),service_item_id INTEGER NOT NULL REFERENCES service_items(service_item_id),frequency INTEGER NOT NULL,PRIMARY KEY(package_id,service_item_id));
        CREATE TABLE IF NOT EXISTS medication_records(id INTEGER PRIMARY KEY,elder_id INTEGER NOT NULL REFERENCES elderly_profiles(elder_id),name TEXT NOT NULL,spec TEXT DEFAULT '',usage TEXT DEFAULT '',scheduled_at TEXT NOT NULL,status TEXT NOT NULL,created_at TEXT NOT NULL,updated_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS visit_records(id INTEGER PRIMARY KEY,elder_id INTEGER NOT NULL REFERENCES elderly_profiles(elder_id),visit_date TEXT NOT NULL,hospital TEXT NOT NULL,department TEXT DEFAULT '',diagnosis TEXT DEFAULT '',advice TEXT DEFAULT '',prescription TEXT DEFAULT '',follow_up TEXT DEFAULT '',created_at TEXT NOT NULL,updated_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS provider_training(id INTEGER PRIMARY KEY,provider_id INTEGER NOT NULL REFERENCES service_providers(provider_id),name TEXT NOT NULL,training_date TEXT NOT NULL,result TEXT DEFAULT '',service_type TEXT DEFAULT '',service_years REAL DEFAULT 0,created_at TEXT NOT NULL,updated_at TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS idx_notifications_recipient ON notifications(recipient_role,recipient_code,notification_id);
        CREATE UNIQUE INDEX IF NOT EXISTS idx_credential_expiry_reminder ON notifications(recipient_role,recipient_code,reference) WHERE kind='credential_expiry';
        CREATE INDEX IF NOT EXISTS idx_credentials_provider_status ON provider_credentials(provider_id,status,expires_on);
        CREATE INDEX IF NOT EXISTS idx_medication_elder_time ON medication_records(elder_id,scheduled_at);
        CREATE INDEX IF NOT EXISTS idx_visit_elder_time ON visit_records(elder_id,visit_date);
        CREATE INDEX IF NOT EXISTS idx_training_provider ON provider_training(provider_id,training_date);
        CREATE INDEX IF NOT EXISTS idx_attachments_owner ON attachments(owner_type,owner_key,attachment_id);
        CREATE INDEX IF NOT EXISTS idx_institutions_region ON institutions(community,institution_type);
        CREATE INDEX IF NOT EXISTS idx_business_logs_entity ON business_logs(entity_type,entity_key,log_id);
        ''')
        if new_service_metadata:
            for row in c.execute('SELECT * FROM service_items').fetchall():
                required = ''
                if row['service_type'] == '医疗护理类': required = '护士执业证'
                elif row['service_type'] == '康复保健类': required = '康复师证'
                elif row['service_type'] == '精神慰藉类' and '心理' in row['service_name']: required = '心理咨询师证'
                elif row['service_type'] == '紧急救援类': required = '急救培训证'
                elif row['service_name'] in {'助浴服务', '陪护服务'}: required = '养老护理员证'
                c.execute('UPDATE service_items SET required_certificate=?,service_status=? WHERE service_item_id=?', (required,'正常提供' if row['enabled'] else '暂停提供',row['service_item_id']))
        if not c.execute("SELECT 1 FROM acceptance_migrations WHERE name='legacy_credentials_records_v1'").fetchone():
            for provider in c.execute('SELECT provider_id,certificates,health_certificate FROM service_providers').fetchall():
                for source in (provider['certificates'], provider['health_certificate']):
                    for name in re.split(r'[、,，;；\n/]+', source or ''):
                        name = name.strip()
                        if not name: continue
                        expiry = None
                        match = re.search(r'\b(\d{4}-\d{2}-\d{2})\b', name)
                        if match:
                            try: expiry = date.fromisoformat(match.group(1)).isoformat()
                            except ValueError: pass
                        c.execute('INSERT INTO provider_credentials(provider_id,name,expires_on,status,created_at) VALUES(?,?,?,\'PENDING\',?)',(provider['provider_id'],name,expiry,_now()))
            c.execute("UPDATE service_records SET review_status='历史导入' WHERE EXISTS(SELECT 1 FROM service_reviews r WHERE r.order_id=service_records.order_id)")
            c.execute("INSERT INTO acceptance_migrations VALUES('legacy_credentials_records_v1',?)",(_now(),))
        c.execute('''UPDATE service_orders SET service_name_snapshot=COALESCE(service_name_snapshot,(SELECT service_name FROM service_items WHERE service_item_id=service_orders.service_item_id)),price_snapshot=COALESCE(price_snapshot,(SELECT price FROM service_items WHERE service_item_id=service_orders.service_item_id)),duration_snapshot=COALESCE(duration_snapshot,duration_minutes)''')


def _text(value, required=False, limit=20000):
    if value is None: value = ''
    if not isinstance(value, str): raise ValueError('文本字段格式错误')
    value = value.strip()
    if (required and not value) or len(value) > limit: raise ValueError('必填文本为空或过长')
    return value


def _number(value, low=None, high=None, integer=False, nullable=False):
    if nullable and value in (None, ''): return None
    if isinstance(value, bool): raise ValueError('数字格式错误')
    try: number = float(value)
    except (TypeError, ValueError, OverflowError): raise ValueError('数字格式错误')
    if not math.isfinite(number) or (low is not None and number < low) or (high is not None and number > high): raise ValueError('数字超出允许范围')
    if integer and not number.is_integer(): raise ValueError('请输入整数')
    return int(number) if integer else number


def _date(value, required=True, timestamp=False):
    value = _text(value, required)
    if not value: return ''
    try:
        if not re.fullmatch(r'\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d{1,6})?)?)?' if timestamp else r'\d{4}-\d{2}-\d{2}',value):raise ValueError()
        parsed = datetime.fromisoformat(value) if timestamp else date.fromisoformat(value)
        if timestamp:
            if 'T' not in value and ' ' not in value: raise ValueError()
            if parsed.tzinfo is not None: raise ValueError()
            return parsed.strftime('%Y-%m-%d %H:%M:%S')
        return parsed.isoformat()
    except ValueError: raise ValueError('日期时间格式无效，使用本地日期或日期时间')


def _enum(value, choices):
    value = _text(value, True)
    if value not in choices: raise ValueError('状态或类别不合法')
    return value


def _boolean(value):
    if value in (True,1,'1'): return 1
    if value in (False,0,'0'): return 0
    raise ValueError('启用状态必须为 0 或 1')


def _q(query, key, default=''):
    value = query.get(key, default)
    return value[0] if isinstance(value, list) and value else value


def _require(user, *roles):
    if not user or user.get('role') not in roles: raise PermissionError('当前账号无权执行此操作')


def _profile(c, user, payload, kind):
    roles = ('admin','elder') if kind == 'elder' else ('admin','provider')
    _require(user,*roles)
    key = kind + '_code'
    code = payload.get(key) or user.get(key)
    if user['role'] != 'admin' and code != user.get(key): raise PermissionError('不能访问其他人的资料')
    table = 'elderly_profiles' if kind == 'elder' else 'service_providers'
    row = c.execute(f'SELECT * FROM {table} WHERE {key}=? AND is_deleted=0',(code,)).fetchone()
    if not row: raise ValueError('档案不存在或已注销')
    return row


def _log(c, user, kind, key, action, detail=''):
    c.execute('INSERT INTO business_logs(entity_type,entity_key,action,actor,detail,created_at) VALUES(?,?,?,?,?,?)',(kind,str(key),action,user.get('username') or user.get('display_name') or user['role'],str(detail),_now()))


def _notify(c, role, code, kind, title, body, reference):
    c.execute('INSERT INTO notifications(recipient_role,recipient_code,kind,title,body,reference,created_at) VALUES(?,?,?,?,?,?,?)',(role,code or '',kind,title,body,str(reference),_now()))


def _save(c, table, key, identifier, values):
    if identifier:
        identifier = _number(identifier,1,integer=True)
        if not c.execute(f'SELECT 1 FROM {table} WHERE {key}=?',(identifier,)).fetchone(): raise ValueError('记录不存在')
        c.execute(f"UPDATE {table} SET {','.join(k+'=?' for k in values)} WHERE {key}=?",[*values.values(),identifier])
        return identifier
    columns = ','.join(values)
    return c.execute(f"INSERT INTO {table}({columns}) VALUES({','.join('?' for _ in values)})",list(values.values())).lastrowid


def _page(c, sql, params, query):
    page = _number(_q(query,'page',1),1,integer=True)
    size = _number(_q(query,'page_size',50),1,200,integer=True)
    total = c.execute('SELECT COUNT(*) FROM ('+sql+')',params).fetchone()[0]
    rows = [dict(row) for row in c.execute(sql+' LIMIT ? OFFSET ?',[*params,size,(page-1)*size])]
    return {'ok':True,'items':rows,'total':total,'page':page,'page_size':size}


def _item(c, resource, identifier):
    table, key = _RESOURCES[resource][:2]
    if resource in {'medications','visits','thresholds'}:
        sql=f'SELECT t.*,e.elder_code FROM {table} t JOIN elderly_profiles e ON e.elder_id=t.elder_id WHERE t.{key}=?'
    elif resource in {'credentials','training'}:
        sql=f'SELECT t.*,p.provider_code FROM {table} t JOIN service_providers p ON p.provider_id=t.provider_id WHERE t.{key}=?'
    else: sql=f'SELECT * FROM {table} WHERE {key}=?'
    row = dict(c.execute(sql,(identifier,)).fetchone())
    row['id'] = row[key]
    return _decorate(c,resource,row)


def _decorate(c, resource, row):
    if resource == 'institutions':
        row['service_ids']=[r[0] for r in c.execute('SELECT service_item_id FROM institution_services WHERE institution_id=? ORDER BY service_item_id',(row['institution_id'],))]
        row['attachment_ids']=[r[0] for r in c.execute('SELECT attachment_id FROM institution_attachments WHERE institution_id=? ORDER BY attachment_id',(row['institution_id'],))]
    elif resource == 'packages':
        row['items']=[dict(r) for r in c.execute('SELECT i.service_item_id,i.frequency,s.service_name FROM package_items i JOIN service_items s ON s.service_item_id=i.service_item_id WHERE package_id=? ORDER BY i.service_item_id',(row['id'],))]
    return row


_RESOURCES = {
    'institutions':('institutions','institution_id','name'), 'services':('service_items','service_item_id','service_name'),
    'packages':('service_packages','id','name'), 'medications':('medication_records','id','name'),
    'visits':('visit_records','id','hospital'), 'thresholds':('health_thresholds','threshold_id','metric'),
    'credentials':('provider_credentials','credential_id','name'), 'training':('provider_training','id','name'),
}


def _list_resource(c,user,resource,query):
    table,key,search = _RESOURCES[resource]
    params=[]; clauses=[]
    select='SELECT t.*'; joins=''
    if resource in {'medications','visits','thresholds'}:
        _require(user,'admin','elder')
        joins=' JOIN elderly_profiles e ON e.elder_id=t.elder_id'; select+=',e.elder_code'
        if user['role']=='elder':
            _profile(c,user,{},'elder'); clauses.append('e.elder_code=?');params.append(user.get('elder_code'))
        elif _q(query,'elder_code'): clauses.append('e.elder_code=?'); params.append(_q(query,'elder_code'))
    elif resource in {'credentials','training'}:
        _require(user,'admin','provider')
        joins=' JOIN service_providers p ON p.provider_id=t.provider_id'; select+=',p.provider_code'
        if user['role']=='provider':
            _profile(c,user,{},'provider'); clauses.append('p.provider_code=?'); params.append(user.get('provider_code'))
        elif _q(query,'provider_code'): clauses.append('p.provider_code=?');params.append(_q(query,'provider_code'))
    if _q(query,'q'):
        fields=[f't.{search}']
        if resource=='services':fields+=['t.service_code','t.service_type']
        elif resource=='institutions':fields+=['t.institution_code','t.community','t.address']
        elif resource in {'medications','visits','thresholds'}:fields+=['e.elder_code']
        elif resource in {'credentials','training'}:fields+=['p.provider_code']
        clauses.append('('+' OR '.join(f+' LIKE ?' for f in fields)+')');params.extend(['%'+_q(query,'q')+'%']*len(fields))
    filters = {'institutions':['institution_type','province','city','district','street','community'],'services':['service_type','service_status'],'packages':['status'],'medications':['status'],'credentials':['status'],'thresholds':['metric'],'training':['service_type']}.get(resource,[])
    for field in filters:
        if _q(query,field): clauses.append(f't.{field}=?'); params.append(_q(query,field))
    if resource == 'institutions':
        if _q(query,'service_item_id'):
            clauses.append('EXISTS(SELECT 1 FROM institution_services x WHERE x.institution_id=t.institution_id AND x.service_item_id=?)');params.append(_number(_q(query,'service_item_id'),1,integer=True))
        if _q(query,'service_type'):
            clauses.append('EXISTS(SELECT 1 FROM institution_services x JOIN service_items s ON s.service_item_id=x.service_item_id WHERE x.institution_id=t.institution_id AND s.service_type=?)');params.append(_q(query,'service_type'))
    date_field={'medications':'scheduled_at','visits':'visit_date','training':'training_date','credentials':'created_at','institutions':'created_at','packages':'created_at'}.get(resource)
    if date_field:
        for suffix,operator in [('from','>='),('to','<=')]:
            value=_q(query,'date_'+suffix)
            if value: clauses.append(f'date(t.{date_field}) {operator} ?');params.append(_date(value))
    sql=select+f' FROM {table} t'+joins+(' WHERE '+' AND '.join(clauses) if clauses else '')+f' ORDER BY t.{key} DESC'
    result=_page(c,sql,params,query)
    for row in result['items']:
        row['id']=row[key];_decorate(c,resource,row)
    return result


def _check_attachment(c, user, identifier, owner_type, owner_key, purpose):
    identifier=_number(identifier,1,integer=True)
    row=c.execute('SELECT * FROM attachments WHERE attachment_id=?',(identifier,)).fetchone()
    if not row or row['owner_type']!=owner_type or row['owner_key']!=str(owner_key) or row['purpose']!=purpose: raise ValueError('附件归属或用途不匹配')
    _authorize_attachment(c,user,owner_type,str(owner_key),purpose,False)
    return identifier


def _write_resource(c,user,resource,payload):
    table,key,_ = _RESOURCES[resource]
    identifier=payload.get('id') or payload.get(key)
    existing = None
    if identifier:
        identifier=_number(identifier,1,integer=True)
        existing=c.execute(f'SELECT * FROM {table} WHERE {key}=?',(identifier,)).fetchone()
        if not existing: raise ValueError('记录不存在')
    data=dict(existing) if existing else {}
    data.update(payload)
    values={}
    if resource in {'institutions','services','packages'}: _require(user,'admin')
    if resource in {'medications','visits','thresholds'}:
        subject=dict(payload)
        if existing and not subject.get('elder_code'):
            subject['elder_code']=c.execute('SELECT elder_code FROM elderly_profiles WHERE elder_id=?',(existing['elder_id'],)).fetchone()['elder_code']
        profile=_profile(c,user,subject,'elder')
        if existing and existing['elder_id']!=profile['elder_id']: raise PermissionError('不能修改其他老人记录')
        values['elder_id']=profile['elder_id']
    elif resource in {'credentials','training'}:
        subject=dict(payload)
        if existing and not subject.get('provider_code'):
            subject['provider_code']=c.execute('SELECT provider_code FROM service_providers WHERE provider_id=?',(existing['provider_id'],)).fetchone()['provider_code']
        profile=_profile(c,user,subject,'provider')
        if existing and existing['provider_id']!=profile['provider_id']: raise PermissionError('不能修改其他人员记录')
        values['provider_id']=profile['provider_id']
    if resource == 'institutions':
        for field in ['institution_code','name','institution_type','province','city','district','street','community','address','phone','introduction']:
            values[field]=_text(data.get(field),field in {'institution_code','name'})
        values['bed_count']=_number(data.get('bed_count',0),0,100000,integer=True)
    elif resource == 'services':
        for field in ['service_code','service_name','service_type','content','service_mode','target_group','booking_notes','required_certificate']:
            values[field]=_text(data.get(field,'上门' if field=='service_mode' else ''),field in {'service_code','service_name','service_type'})
        values['price']=_number(data.get('price'),0,100000000)
        values['duration_minutes']=_number(data.get('duration_minutes'),1,525600,integer=True)
        values['service_status']=_enum(data.get('service_status','正常提供'),{'正常提供','暂停提供','限时促销','促销','正常','暂停','促销中','paused','active','promotion'})
        values['enabled']=0 if values['service_status'] in {'暂停提供','暂停','paused'} else 1
    elif resource == 'packages':
        values.update(name=_text(data.get('name'),True),price=_number(data.get('price'),0,100000000),valid_until=_date(data.get('valid_until')),status=_enum(data.get('status','正常'),{'正常','暂停','促销','正常提供','暂停提供','限时促销','促销中','active','paused'}))
    elif resource == 'medications':
        for field in ['name','spec','usage']:values[field]=_text(data.get(field),field=='name')
        values.update(scheduled_at=_date(data.get('scheduled_at'),timestamp=True),status=_enum(data.get('status','未服'),{'已服','未服','漏服'}))
    elif resource == 'visits':
        for field in ['hospital','department','diagnosis','advice','prescription']:values[field]=_text(data.get(field),field=='hospital')
        values.update(visit_date=_date(data.get('visit_date')),follow_up=_date(data.get('follow_up'),False))
    elif resource == 'thresholds':
        values['metric']=_enum(data.get('metric'),{'systolic','diastolic','glucose','heart_rate','temperature','weight','sleep_hours'})
        values['lower_bound']=_number(data.get('lower_bound'),nullable=True)
        values['upper_bound']=_number(data.get('upper_bound'),nullable=True)
        values['enabled']=_boolean(data.get('enabled',1));values['action']=_text(data.get('action'))
        lower,upper=values['lower_bound'],values['upper_bound']
        if values['enabled'] and lower is None and upper is None: raise ValueError('启用阈值至少设置一个边界')
        if lower is not None and upper is not None and lower>=upper: raise ValueError('下界必须小于上界')
        if not identifier:
            row=c.execute('SELECT threshold_id FROM health_thresholds WHERE elder_id=? AND metric=?',(values['elder_id'],values['metric'])).fetchone()
            if row:identifier=row['threshold_id']
    elif resource == 'credentials':
        values.update(name=_text(data.get('name'),True),certificate_no=_text(data.get('certificate_no')),expires_on=_date(data.get('expires_on'),False) or None,status='PENDING',review_note='',reviewed_by=None,reviewed_at=None)
        values['attachment_id']=None
        if data.get('attachment_id'): values['attachment_id']=_check_attachment(c,user,data['attachment_id'],'provider',profile['provider_code'],'certificate')
        if not existing:values['created_at']=_now()
    elif resource == 'training':
        values.update(name=_text(data.get('name'),True),training_date=_date(data.get('training_date')),result=_text(data.get('result')),service_type=_text(data.get('service_type')),service_years=_number(data.get('service_years',0),0,100))
    if resource not in {'services','credentials','thresholds'}:
        values['updated_at']=_now()
        if not existing:values['created_at']=_now()
    identifier=_save(c,table,key,identifier,values)
    if resource=='institutions':
        if 'service_ids' in payload:
            ids=payload['service_ids']
            if not isinstance(ids,list): raise ValueError('服务项目必须为数组')
            c.execute('DELETE FROM institution_services WHERE institution_id=?',(identifier,))
            for sid in set(_number(v,1,integer=True) for v in ids):
                if not c.execute('SELECT 1 FROM service_items WHERE service_item_id=?',(sid,)).fetchone():raise ValueError('关联服务不存在')
                c.execute('INSERT INTO institution_services VALUES(?,?)',(identifier,sid))
        if 'attachment_ids' in payload:
            if not isinstance(payload['attachment_ids'],list):raise ValueError('附件必须为数组')
            c.execute('DELETE FROM institution_attachments WHERE institution_id=?',(identifier,))
            for aid in set(payload['attachment_ids']):
                aid=_check_attachment(c,user,aid,'institution',values['institution_code'],'facility')
                c.execute('INSERT INTO institution_attachments VALUES(?,?)',(identifier,aid))
    elif resource=='packages' and ('items' in payload or not existing):
        items=payload.get('items')
        if not isinstance(items,list) or not items:raise ValueError('套餐必须包含服务项目')
        c.execute('DELETE FROM package_items WHERE package_id=?',(identifier,))
        for entry in items:
            if not isinstance(entry,dict):raise ValueError('套餐项目格式错误')
            sid=_number(entry.get('service_item_id'),1,integer=True)
            if not c.execute('SELECT 1 FROM service_items WHERE service_item_id=?',(sid,)).fetchone():raise ValueError('套餐服务不存在')
            c.execute('INSERT INTO package_items VALUES(?,?,?)',(identifier,sid,_number(entry.get('frequency'),1,100,integer=True)))
    _log(c,user,resource,identifier,'UPDATE' if existing else 'CREATE')
    return _item(c,resource,identifier)


def _authorize_attachment(c,user,owner_type,owner_key,purpose,writing):
    allowed={'elder':{'portrait'},'provider':{'certificate'},'institution':{'facility'},'order':{'record','review'},'complaint':{'complaint'}}
    if owner_type not in allowed or purpose not in allowed[owner_type]:raise ValueError('附件对象和用途不合法')
    role=user['role']
    if owner_type=='elder':
        row=c.execute('SELECT * FROM elderly_profiles WHERE elder_code=? OR CAST(elder_id AS TEXT)=?',(owner_key,owner_key)).fetchone()
        if not row:raise ValueError('老人不存在')
        if role!='admin' and (writing or role!='elder' or user.get('elder_code')!=row['elder_code']):raise PermissionError('无权访问证件照片')
        return row['elder_code']
    if owner_type=='provider':
        row=c.execute('SELECT * FROM service_providers WHERE provider_code=? OR CAST(provider_id AS TEXT)=?',(owner_key,owner_key)).fetchone()
        if not row:raise ValueError('人员不存在')
        if role!='admin' and (role!='provider' or user.get('provider_code')!=row['provider_code']):raise PermissionError('无权访问人员附件')
        return row['provider_code']
    if owner_type=='institution':
        row=c.execute('SELECT * FROM institutions WHERE institution_code=? OR CAST(institution_id AS TEXT)=?',(owner_key,owner_key)).fetchone()
        if not row:raise ValueError('机构不存在')
        if writing and role!='admin':raise PermissionError('仅管理员可上传机构照片')
        return row['institution_code']
    if owner_type=='order':
        row=c.execute('''SELECT o.*,e.elder_code,p.provider_code FROM service_orders o JOIN elderly_profiles e ON e.elder_id=o.elder_id LEFT JOIN service_assignments a ON a.order_id=o.order_id LEFT JOIN service_providers p ON p.provider_id=a.provider_id WHERE o.order_no=? OR CAST(o.order_id AS TEXT)=?''',(owner_key,owner_key)).fetchone()
        if not row:raise ValueError('订单不存在')
        if role!='admin':
            if role=='elder' and row['elder_code']==user.get('elder_code') and (not writing or purpose=='review'):pass
            elif role=='provider' and row['provider_code']==user.get('provider_code') and (not writing or purpose=='record'):pass
            else:raise PermissionError('无权访问订单附件')
        return row['order_no']
    row=c.execute('''SELECT x.*,e.elder_code,p.provider_code FROM service_complaints x JOIN elderly_profiles e ON e.elder_id=x.elder_id LEFT JOIN service_providers p ON p.provider_id=x.provider_id WHERE x.complaint_no=? OR CAST(x.complaint_id AS TEXT)=?''',(owner_key,owner_key)).fetchone()
    if not row:raise ValueError('投诉不存在')
    if role!='admin' and not (role=='elder' and row['elder_code']==user.get('elder_code')) and not (role=='provider' and row['provider_code']==user.get('provider_code') and not writing):raise PermissionError('无权访问投诉附件')
    return row['complaint_no']


def _attachment_bytes(payload):
    name=_text(payload.get('file_name'),True,255)
    # A path-like submitted name is retained only as its display basename.
    name=Path(name.replace('\\','/')).name
    if name in {'','.','..'}:raise ValueError('文件名无效')
    encoded=payload.get('file_data')
    if not isinstance(encoded,str) or len(encoded)>16*1024*1024+16:raise ValueError('单文件不得超过 12 MB')
    try:raw=base64.b64decode(encoded,validate=True)
    except (ValueError,binascii.Error):raise ValueError('附件不是有效 base64')
    if not raw or len(raw)>12*1024*1024:raise ValueError('单文件不得超过 12 MB')
    mime=payload.get('mime_type')
    signatures={
        'application/pdf':(raw.startswith(b'%PDF-') and b'%%EOF' in raw[-2048:],'.pdf'),
        'image/png':(raw.startswith(b'\x89PNG\r\n\x1a\n') and len(raw)>=33 and raw[12:16]==b'IHDR' and raw[-12:-8]==b'\x00\x00\x00\x00' and raw[-8:-4]==b'IEND','.png'),
        'image/jpeg':(raw.startswith(b'\xff\xd8\xff') and raw.endswith(b'\xff\xd9'),'.jpg'),
        'image/webp':(len(raw)>=20 and raw[:4]==b'RIFF' and raw[8:12]==b'WEBP' and raw[12:16] in {b'VP8 ',b'VP8L',b'VP8X'} and int.from_bytes(raw[4:8],'little')==len(raw)-8,'.webp'),
    }
    if mime not in signatures or not signatures[mime][0]:raise ValueError('附件内容与允许的 PDF/PNG/JPEG/WebP 类型不一致')
    return name,mime,raw,signatures[mime][1]


def _upload(c,module,user,payload):
    owner_type=_text(payload.get('owner_type'),True);owner_key=str(payload.get('owner_key') or '')
    purpose=_text(payload.get('purpose'),True)
    owner_key=_authorize_attachment(c,user,owner_type,owner_key,purpose,True)
    name,mime,raw,extension=_attachment_bytes(payload)
    directory=Path(module.BASE_DIR)/'uploads'/'attachments';directory.mkdir(parents=True,exist_ok=True)
    stored=secrets.token_hex(24)+extension
    target=directory/stored
    try:
        with target.open('xb') as stream:stream.write(raw)
        identifier=c.execute('INSERT INTO attachments(owner_type,owner_key,purpose,file_name,stored_name,mime_type,file_size,uploaded_by,created_at) VALUES(?,?,?,?,?,?,?,?,?)',(owner_type,owner_key,purpose,name,stored,mime,len(raw),user.get('username') or user['role'],_now())).lastrowid
        if owner_type=='elder' and purpose=='portrait':c.execute('UPDATE elderly_profiles SET photo_attachment_id=? WHERE elder_code=?',(identifier,owner_key))
        if owner_type=='institution':
            institution=c.execute('SELECT institution_id FROM institutions WHERE institution_code=?',(owner_key,)).fetchone()
            c.execute('INSERT INTO institution_attachments VALUES(?,?)',(institution['institution_id'],identifier))
        _log(c,user,'attachments',identifier,'UPLOAD',purpose)
    except Exception:
        target.unlink(missing_ok=True)
        raise
    return dict(c.execute('SELECT * FROM attachments WHERE attachment_id=?',(identifier,)).fetchone())


def _review(c,user,resource,token,payload):
    _require(user,'admin')
    c.execute('BEGIN IMMEDIATE')
    identifier=_number(token,1,integer=True)
    if resource=='credentials':
        row=c.execute('SELECT * FROM provider_credentials WHERE credential_id=?',(identifier,)).fetchone()
        if not row:raise ValueError('证书不存在')
        status=_enum(payload.get('status'),{'APPROVED','REJECTED'})
        note=_text(payload.get('review_note'),status=='REJECTED')
        name=_text(payload.get('name',row['name']),True)
        number=_text(payload.get('certificate_no',row['certificate_no']),status=='APPROVED')
        expires=_date(payload.get('expires_on',row['expires_on']),status=='APPROVED') or None
        if status=='APPROVED' and expires<date.today().isoformat():raise ValueError('证书已过期')
        c.execute('UPDATE provider_credentials SET name=?,certificate_no=?,expires_on=?,status=?,review_note=?,reviewed_by=?,reviewed_at=? WHERE credential_id=?',(name,number,expires,status,note,user.get('username'),_now(),identifier))
        _log(c,user,resource,identifier,'REVIEW',status+': '+note)
        provider=c.execute('SELECT provider_code FROM service_providers WHERE provider_id=?',(row['provider_id'],)).fetchone()
        _notify(c,'provider',provider['provider_code'],'credential_review','资质审核结果',status+': '+note,identifier)
        return _item(c,resource,identifier)
    row=c.execute('''SELECT r.*,o.order_no,o.elder_id,p.provider_code FROM service_records r JOIN service_orders o ON o.order_id=r.order_id JOIN service_providers p ON p.provider_id=r.provider_id WHERE r.record_id=?''',(identifier,)).fetchone()
    if not row:raise ValueError('服务记录不存在')
    if row['review_status'] not in {'待审核'}:raise ValueError('仅待审核服务记录可审核')
    status=_enum(payload.get('status'),{'已通过','已退回'})
    note=_text(payload.get('review_note'),status=='已退回')
    c.execute('UPDATE service_records SET review_status=?,review_note=?,reviewed_by=?,reviewed_at=? WHERE record_id=?',(status,note,user.get('username'),_now(),identifier))
    _log(c,user,'records',identifier,'REVIEW',status+': '+note)
    _notify(c,'provider',row['provider_code'],'record_review','服务记录审核结果',status+': '+note,row['order_no'])
    elder=c.execute('SELECT elder_code FROM elderly_profiles WHERE elder_id=?',(row['elder_id'],)).fetchone()
    if status=='已通过':_notify(c,'elder',elder['elder_code'],'record_review','服务记录已通过审核','可查看记录并评价服务',row['order_no'])
    return dict(c.execute('SELECT * FROM service_records WHERE record_id=?',(identifier,)).fetchone())


def _list_records(c,user,query):
    _require(user,'admin','elder','provider')
    clauses=[];params=[]
    if user['role']=='elder':_profile(c,user,{},'elder');clauses.append('e.elder_code=?');params.append(user.get('elder_code'))
    elif user['role']=='provider':_profile(c,user,{},'provider');clauses.append('p.provider_code=?');params.append(user.get('provider_code'))
    for field,sql_field in [('review_status','r.review_status'),('order_no','o.order_no'),('elder_code','e.elder_code'),('provider_code','p.provider_code')]:
        if _q(query,field):clauses.append(sql_field+'=?');params.append(_q(query,field))
    for key,operator in [('date_from','>='),('date_to','<=')]:
        if _q(query,key):clauses.append(f'date(r.submitted_at){operator}?');params.append(_date(_q(query,key)))
    sql='''SELECT r.*,o.order_no,e.elder_code,e.full_name elder_name,p.provider_code,p.full_name provider_name FROM service_records r JOIN service_orders o ON o.order_id=r.order_id JOIN elderly_profiles e ON e.elder_id=o.elder_id JOIN service_providers p ON p.provider_id=r.provider_id'''
    sql+=(' WHERE '+' AND '.join(clauses) if clauses else '')+' ORDER BY r.record_id DESC'
    return _page(c,sql,params,query)


def _notification_scope(user):
    _require(user,'admin','elder','provider','guest')
    code=user.get('elder_code') if user['role']=='elder' else user.get('provider_code') if user['role']=='provider' else ''
    return user['role'],code or ''


def _credential_reminders(c):
    """Persist one reminder per credential expiry, including an administrator copy."""
    rows=c.execute("""SELECT x.credential_id,x.name,x.expires_on,p.provider_code,p.full_name
        FROM provider_credentials x JOIN service_providers p ON p.provider_id=x.provider_id
        WHERE x.status='APPROVED' AND x.expires_on IS NOT NULL AND x.expires_on<=? AND p.is_deleted=0""",
        ((date.today()+timedelta(days=30)).isoformat(),)).fetchall()
    for row in rows:
        reference=f"credential:{row['credential_id']}:{row['expires_on']}"
        body=f"{row['full_name']}的{row['name']}有效期至 {row['expires_on']}，请及时核验或更新。"
        for role,code in [('admin',''),('provider',row['provider_code'])]:
            c.execute("""INSERT OR IGNORE INTO notifications(recipient_role,recipient_code,kind,title,body,reference,created_at)
                VALUES(?,?,'credential_expiry','资质即将到期或已过期',?,?,?)""",(role,code,body,reference,_now()))


def _special(c,user,resource,token,action,payload):
    if resource=='complaints' and action=='handle':
        _require(user,'admin')
        row=c.execute('SELECT * FROM service_complaints WHERE complaint_no=? OR CAST(complaint_id AS TEXT)=?',(token,token)).fetchone()
        if not row:raise ValueError('投诉不存在')
        reply=_text(payload.get('admin_reply'),True);_enum(payload.get('status'),{'已处理'})
        c.execute("UPDATE service_complaints SET status='已处理',admin_reply=?,handled_by=?,handled_at=?,updated_at=? WHERE complaint_id=?",(reply,user.get('username'),_now(),_now(),row['complaint_id']))
        _log(c,user,'complaints',row['complaint_no'],'HANDLE',reply)
        elder=c.execute('SELECT elder_code FROM elderly_profiles WHERE elder_id=?',(row['elder_id'],)).fetchone()
        _notify(c,'elder',elder['elder_code'],'complaint','投诉已处理',reply,row['complaint_no'])
        return dict(c.execute('SELECT * FROM service_complaints WHERE complaint_id=?',(row['complaint_id'],)).fetchone())
    if resource=='notifications' and action=='read':
        role,code=_notification_scope(user)
        row=c.execute('SELECT * FROM notifications WHERE notification_id=?',(_number(token,1,integer=True),)).fetchone()
        if not row:raise ValueError('通知不存在')
        if row['recipient_role']!=role or row['recipient_code'] not in {'',code}:raise PermissionError('无权访问其他账号通知')
        c.execute('UPDATE notifications SET is_read=1 WHERE notification_id=?',(row['notification_id'],))
        return dict(c.execute('SELECT * FROM notifications WHERE notification_id=?',(row['notification_id'],)).fetchone())
    if resource=='elders' and action=='archive':
        _require(user,'admin');reason=_text(payload.get('reason'),True)
        c.execute('BEGIN IMMEDIATE')
        row=c.execute('SELECT * FROM elderly_profiles WHERE elder_code=?',(token,)).fetchone()
        if not row:raise ValueError('老人不存在')
        if row['is_deleted']:raise ValueError('档案已注销')
        active=c.execute("SELECT 1 FROM service_orders WHERE elder_id=? AND status NOT IN ('COMPLETED','CANCELLED') LIMIT 1",(row['elder_id'],)).fetchone()
        emergency=c.execute("SELECT 1 FROM emergency_requests WHERE elder_id=? AND status NOT IN ('已解决','误报取消') LIMIT 1",(row['elder_id'],)).fetchone()
        if active or emergency:raise ValueError('存在未结束订单或应急事项，请先处理')
        c.execute('UPDATE elderly_profiles SET is_deleted=1,archive_reason=?,archived_at=? WHERE elder_id=?',(reason,_now(),row['elder_id']))
        c.execute('UPDATE auth_accounts SET enabled=0 WHERE elder_code=?',(token,))
        c.execute('DELETE FROM auth_sessions WHERE username IN(SELECT username FROM auth_accounts WHERE elder_code=?)',(token,))
        _log(c,user,'elders',token,'ARCHIVE',reason)
        return dict(c.execute('SELECT * FROM elderly_profiles WHERE elder_id=?',(row['elder_id'],)).fetchone())
    if resource=='emergencies' and action in {'confirm','location'}:
        row=c.execute('''SELECT x.*,e.elder_code,e.emergency_contact_name FROM emergency_requests x JOIN elderly_profiles e ON e.elder_id=x.elder_id WHERE x.emergency_no=? OR CAST(x.emergency_id AS TEXT)=?''',(token,token)).fetchone()
        if not row:raise ValueError('应急请求不存在')
        if action=='location':
            _require(user,'admin','elder')
            if user['role']=='elder' and user.get('elder_code')!=row['elder_code']:raise PermissionError('无权修改其他人求助定位')
            latitude=_number(payload.get('latitude'),-90,90);longitude=_number(payload.get('longitude'),-180,180)
            c.execute('UPDATE emergency_requests SET latitude=?,longitude=?,updated_at=? WHERE emergency_id=?',(latitude,longitude,_now(),row['emergency_id']))
            return dict(c.execute('SELECT * FROM emergency_requests WHERE emergency_id=?',(row['emergency_id'],)).fetchone())
        _require(user,'admin');note=_text(payload.get('note'),True)
        c.execute("INSERT INTO external_notifications(emergency_id,target,channel,status,created_at) VALUES(?,?,'COURSE_SIMULATION','SIMULATED_CONFIRMED',?)",(row['emergency_id'],row['emergency_contact_name'] or '紧急联系人',_now()))
        _log(c,user,'emergencies',row['emergency_no'],'SIMULATED_CONFIRM',note)
        _notify(c,'elder',row['elder_code'],'emergency','课程模拟联系人确认',note,row['emergency_no'])
        return {'id':row['emergency_id'],'emergency_no':row['emergency_no'],'status':'SIMULATED_CONFIRMED','note':note}
    raise ValueError('不支持的操作')


def route(handler,module,method,path,query):
    """Handle only declared routes; core maps validation and ownership exceptions."""
    prefix='/api/acceptance/'
    if not path.startswith(prefix):return False
    parts=path[len(prefix):].strip('/').split('/')
    resource=parts[0]
    simple=resource in _RESOURCES or resource in {'records','notifications','locations','attachments'}
    special=len(parts)==3 and (resource,parts[2]) in {('credentials','review'),('records','review'),('complaints','handle'),('notifications','read'),('elders','archive'),('emergencies','confirm'),('emergencies','location')}
    download=len(parts)==3 and resource=='attachments' and parts[2]=='download'
    if not ((len(parts)==1 and simple and method in {'GET','POST'}) or (special and method=='POST') or (download and method=='GET')):return False
    if len(parts)==1 and resource in {'records','notifications'} and method=='POST':return False
    user=handler.current_user()
    _require(user,'admin','elder','provider','guest')
    # Cached sessions must not let a soft deleted profile continue to act.
    with _db(module) as c:
        if user['role'] in {'elder','provider'}:_profile(c,user,{},user['role'])
        if download:
            identifier=_number(parts[1],1,integer=True)
            row=c.execute('SELECT * FROM attachments WHERE attachment_id=?',(identifier,)).fetchone()
            if not row:raise ValueError('附件不存在')
            _authorize_attachment(c,user,row['owner_type'],row['owner_key'],row['purpose'],False)
            directory=(Path(module.BASE_DIR)/'uploads'/'attachments').resolve()
            target=(directory/row['stored_name']).resolve()
            if target.parent!=directory or not target.is_file():raise ValueError('附件文件不存在或存储路径无效')
            raw=target.read_bytes()
            handler.send_response(200);handler.send_header('Content-Type',row['mime_type']);handler.send_header('Content-Length',str(len(raw)))
            handler.send_header('Content-Disposition','attachment; filename="attachment'+target.suffix+'"');handler.send_header('X-Content-Type-Options','nosniff');handler.end_headers();handler.wfile.write(raw)
            return True
        if method=='GET':
            if resource in _RESOURCES:result=_list_resource(c,user,resource,query)
            elif resource=='records':result=_list_records(c,user,query)
            elif resource=='notifications':
                role,code=_notification_scope(user)
                if role in {'admin','provider'}:_credential_reminders(c)
                result=_page(c,"SELECT * FROM notifications WHERE recipient_role=? AND (recipient_code=? OR recipient_code='') AND notification_id>? ORDER BY notification_id",[role,code,_number(_q(query,'after_id',0),0,integer=True)],query)
            elif resource=='locations':
                _require(user,'admin')
                result=_page(c,'SELECT l.*,p.provider_code,p.full_name provider_name FROM provider_locations l JOIN service_providers p ON p.provider_id=l.provider_id ORDER BY l.recorded_at DESC',[],query)
            else:
                owner_type=_q(query,'owner_type');owner_key=str(_q(query,'owner_key'))
                purpose=_q(query,'purpose') or {'elder':'portrait','provider':'certificate','institution':'facility','order':'record','complaint':'complaint'}.get(owner_type,'')
                owner_key=_authorize_attachment(c,user,owner_type,owner_key,purpose,False)
                params=[owner_type,owner_key];sql='SELECT * FROM attachments WHERE owner_type=? AND owner_key=?'
                if _q(query,'purpose'):sql+=' AND purpose=?';params.append(purpose)
                result=_page(c,sql+' ORDER BY attachment_id DESC',params,query)
        else:
            payload=handler.read_json()
            if not isinstance(payload,dict):raise ValueError('请求必须为 JSON 对象')
            if special:
                if parts[2]=='review':item=_review(c,user,resource,parts[1],payload)
                else:item=_special(c,user,resource,parts[1],parts[2],payload)
            elif resource in _RESOURCES:item=_write_resource(c,user,resource,payload)
            elif resource=='attachments':item=_upload(c,module,user,payload)
            elif resource=='locations':
                profile=_profile(c,user,{},'provider')
                latitude=_number(payload.get('latitude'),-90,90);longitude=_number(payload.get('longitude'),-180,180)
                c.execute('INSERT INTO provider_locations(provider_id,latitude,longitude,recorded_at) VALUES(?,?,?,?) ON CONFLICT(provider_id) DO UPDATE SET latitude=excluded.latitude,longitude=excluded.longitude,recorded_at=excluded.recorded_at',(profile['provider_id'],latitude,longitude,_now()))
                item=dict(c.execute('SELECT * FROM provider_locations WHERE provider_id=?',(profile['provider_id'],)).fetchone())
            else:return False
            result={'ok':True,'item':item,'id':item.get('id') or item.get('attachment_id') or item.get('credential_id') or item.get('record_id') or item.get('notification_id') or item.get('provider_id') or item.get('elder_id') or item.get('complaint_id')}
        # Respond only after a successful commit, so transaction errors cannot look successful.
    handler.send_json(200,result)
    return True
