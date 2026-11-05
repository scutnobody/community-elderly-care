from concurrent.futures import ThreadPoolExecutor
from datetime import date,timedelta
from tests.support import ApiCase

class CoreRules(ApiCase):
    def test_nonpositive_or_nonfinite_booking_duration_is_rejected(self):
        for duration in (0,-1,'NaN',float('inf')):
            status,p=self.call('POST','/api/orders',{'service_code':'FW-003','service_date':(date.today()+timedelta(days=15)).isoformat(),'time_slot':'09:00-11:00','duration_minutes':duration,'service_address':'青云社区测试地址'},'elder')
            self.assertEqual(status,400,p)

    def test_day_long_booking_checks_next_day_overlap_and_boundary(self):
        self.prepare_provider();order=self.new_order('09:00-次日09:00',duration=1440)
        detail=self.call('GET',f'/api/orders/{order}',role='elder')[1]['order']
        next_day=(date.today()+timedelta(days=16)).isoformat()
        self.assertEqual(detail['end_at'],next_day+' 09:00:00');self.assertEqual(self.assign(order)[0],200)
        for slot,wanted in [('08:00-10:00',400),('09:00-11:00',200)]:
            status,p=self.call('POST','/api/orders',{'service_code':'FW-003','service_date':next_day,'time_slot':slot,'duration_minutes':120,'service_address':'青云社区测试地址'},'elder')
            self.assertEqual(status,201,p);self.assertEqual(self.assign(p['order_no'])[0],wanted)

    def test_filtered_exports_retain_more_than_one_hundred_providers(self):
        import io
        from openpyxl import load_workbook
        from pypdf import PdfReader
        with self.server.connect_db() as c:
            row=dict(c.execute('SELECT * FROM service_providers LIMIT 1').fetchone());row.pop('provider_id')
            for i in range(105):
                row.update(provider_code='EXPORT-'+str(i),full_name='EXPORT-PERSON-'+str(i))
                c.execute('INSERT INTO service_providers('+','.join(row)+') VALUES('+','.join('?' for _ in row)+')',tuple(row.values()))
        order=self.new_order();day=(date.today()+timedelta(days=15)).isoformat()
        query=f'date_from={day}&date_to={day}'
        status,raw=self.call('GET','/api/reports/export?format=excel&'+query,role='admin',binary=True)
        self.assertEqual(status,200)
        wb=load_workbook(io.BytesIO(raw),read_only=True)
        self.assertEqual(wb['统计概览']['B5'].value,1)
        self.assertEqual(wb['人员绩效'].max_row-1,len(self.sql('SELECT provider_id FROM service_providers WHERE is_deleted=0')))
        self.assertEqual(wb['人员绩效'].cell(wb['人员绩效'].max_row,1).value,'EXPORT-104');wb.close()
        status,raw=self.call('GET','/api/reports/export?format=pdf&'+query,role='admin',binary=True)
        self.assertEqual(status,200)
        text=''.join(page.extract_text() for page in PdfReader(io.BytesIO(raw)).pages)
        self.assertIn('EXPORT-104',text)

    def test_chronic_coverage_includes_period_visit_and_deduplicates_elder(self):
        with self.server.connect_db() as c:
            c.execute("UPDATE elderly_profiles SET elder_tags='普通'")
            c.execute("UPDATE elderly_profiles SET elder_tags='慢病' WHERE elder_code='JX202600128'")
        day=date.today().isoformat()
        self.assertIn(self.call('POST','/api/acceptance/visits',{'visit_date':day,'hospital':'社区卫生服务中心','diagnosis':'慢病复诊'},'elder')[0],(200,201))
        status,p=self.call('GET',f'/api/acceptance/statistics?date_from={day}&date_to={day}',role='admin')
        self.assertEqual(status,200,p);self.assertEqual(p['stats']['chronic_coverage_rate'],100)

    def test_order_price_snapshot_survives_catalog_changes(self):
        order=self.new_order()
        original=self.call('GET',f'/api/orders/{order}',role='elder')[1]['order']['price']
        with self.server.connect_db() as c:c.execute("UPDATE service_items SET price=9999 WHERE service_code='FW-003'")
        detail=self.call('GET',f'/api/orders/{order}',role='elder')[1]['order']
        listing=self.call('GET','/api/orders',role='elder')[1]['orders']
        self.assertEqual(detail['price'],original)
        self.assertEqual(next(o for o in listing if o['order_no']==order)['price'],original)

    def test_assigned_provider_sees_care_precautions_without_identity_documents(self):
        self.prepare_provider();order=self.new_order();self.assign(order)
        status,p=self.call('GET',f'/api/orders/{order}',role='provider')
        self.assertEqual(status,200,p)
        care=p['order'].get('care_precautions',{})
        self.assertIn('allergy_history',care)
        self.assertIn('disease_history',care)
        self.assertIn('emergency_contact_phone',care)
        self.assertNotIn('id_card',care)
        self.assertNotIn('medical_card_no',care)

    def test_returned_unexecuted_record_can_be_resubmitted_without_fake_time(self):
        self.prepare_provider();order=self.new_order();self.assign(order)
        status,p=self.call('POST',f'/api/orders/{order}/exception',{'completion_status':'未执行','reason':'无法入户'},'provider');self.assertEqual(status,201,p)
        self.assertEqual(self.call('POST',f"/api/acceptance/records/{p['record_id']}/review",{'status':'已退回','review_note':'请补充原因'},'admin')[0],200)
        status,p=self.call('POST',f'/api/orders/{order}/records',{'completion_status':'未执行','process_description':'老人外出，已电话确认无法入户'},'provider')
        self.assertEqual(status,201,p)
        self.assertIsNone(self.sql('SELECT actual_start_time FROM service_records WHERE record_id=?',(p['record_id'],))[0]['actual_start_time'])

    def test_chinese_exam_filename_download(self):
        import io,base64
        from reportlab.pdfgen import canvas
        data=io.BytesIO();pdf=canvas.Canvas(data);pdf.drawString(10,10,'test');pdf.save();raw=data.getvalue()
        status,p=self.call('POST','/api/exam-reports',{'exam_date':date.today().isoformat(),'organization':'测试机构','file_name':'体检报告.pdf','mime_type':'application/pdf','file_data':base64.b64encode(raw).decode()},'elder')
        self.assertEqual(status,201,p)
        status,p=self.call('GET',f"/api/exam-reports/{p['report_id']}/download",role='elder',binary=True)
        self.assertEqual(status,200,p);self.assertEqual(p,raw)

    def test_personal_performance_is_scoped_and_period_filterable(self):
        status,p=self.call('GET','/api/acceptance/my-statistics?date_from=2026-09-01&date_to=2026-09-30&provider_code=OTHER',role='provider')
        self.assertEqual(status,200,p)
        self.assertEqual(p['stats']['provider_code'],'FW20260218')
        self.assertNotIn('elder_count',p['stats'])
        self.assertNotIn('provider_performance',p['stats'])

    def test_statistics_cache_invalidates_after_committed_write(self):
        scans=[];factory=self.server.connect_db
        def watched():
            c=factory();c.set_trace_callback(lambda q:scans.append(q) if q.startswith('CREATE TEMP TABLE report_orders') else None);return c
        self.server.connect_db=watched;self.server.acceptance_extension.connect_db=watched
        self.call('GET','/api/acceptance/statistics',role='admin');self.call('GET','/api/acceptance/statistics',role='admin')
        self.assertEqual(len(scans),1,'相同数据版本的统计应复用，不重复扫描大表')
        self.new_order()
        self.call('GET','/api/acceptance/statistics',role='admin')
        self.assertEqual(len(scans),2,'业务写入后缓存必须立即失效')

    def test_catalog_search_finds_business_code(self):
        status,p=self.call('GET','/api/acceptance/services?q=FW-003',role='admin')
        self.assertEqual(status,200,p)
        self.assertTrue(any(s['service_code']=='FW-003' for s in p['items']))

    def test_statistics_does_not_query_health_once_per_elder(self):
        with self.server.connect_db() as c:
            c.execute("UPDATE elderly_profiles SET elder_tags='普通'")
            for i in range(40):c.execute("INSERT INTO elderly_profiles(elder_code,full_name,phone,address,elder_tags) VALUES(?,?,?,?,?)",('COHORT'+str(i),'统计测试','13800000000','测试社区','慢病'))
        queries=[];factory=self.server.connect_db
        def observed():
            c=factory();c.set_trace_callback(queries.append);return c
        self.server.connect_db=observed
        self.server.acceptance_extension.connect_db=observed
        status,p=self.call('GET','/api/acceptance/statistics',role='admin')
        self.assertEqual(status,200,p)
        scans=[q for q in queries if q.lower().startswith('select') and 'from health_vitals' in q.lower()]
        self.assertTrue(scans,'必须实际观察到健康表查询')
        self.assertLessEqual(len(scans),2,'慢病覆盖应批量聚合，不逐人访问健康表')

    def test_record_candidates_exclude_already_submitted_records(self):
        status,p=self.call('GET','/api/orders?status=PENDING_REVIEW&record_needed=1',role='admin')
        self.assertEqual(status,200,p)
        self.assertTrue(all(o['record_count']==0 or o['record_review_status']=='已退回' for o in p['orders']))

    def test_departure_reads_assignment_inside_write_transaction(self):
        self.prepare_provider();order=self.new_order();self.assign(order)
        observed=[];original=self.server.ApiHandler.get_assignment
        def inspect(handler,connection,ident):
            observed.append(connection.in_transaction)
            return original(handler,connection,ident)
        self.server.ApiHandler.get_assignment=inspect
        self.assertEqual(self.call('POST',f'/api/orders/{order}/depart',{},'provider')[0],200)
        self.assertTrue(observed and all(observed),'ownership/state reads must be protected by write transaction')

    def test_unexecuted_service_has_reason_and_no_fabricated_arrival(self):
        self.prepare_provider();order=self.new_order();self.assign(order)
        self.assertEqual(self.call('POST',f'/api/orders/{order}/exception',{'completion_status':'未执行','reason':''},'provider')[0],400)
        status,p=self.call('POST',f'/api/orders/{order}/exception',{'completion_status':'未执行','reason':'老人临时外出，无法入户'},'provider')
        self.assertEqual(status,201,p)
        detail=self.call('GET',f'/api/orders/{order}',role='provider')[1]['order']
        self.assertIsNone(detail['arrive_at']);self.assertIsNone(detail['records'][0]['actual_start_time'])
        self.assertEqual(detail['records'][0]['review_status'],'待审核')

    def test_no_disease_is_not_a_chronic_cohort(self):
        with self.server.connect_db() as c:c.execute("UPDATE elderly_profiles SET disease_history='无',elder_tags='普通'")
        status,p=self.call('GET','/api/acceptance/statistics',role='admin')
        self.assertEqual(status,200,p);self.assertIsNone(p['stats']['chronic_coverage_rate'])

    def test_elder_history_is_complete_and_private(self):
        status,p=self.call('GET','/api/acceptance/elders/JX202600128/history',role='admin')
        self.assertEqual(status,200,p)
        for key in ('profile','orders','health','emergencies','records'):self.assertIn(key,p)
        self.assertEqual(self.call('GET','/api/acceptance/elders/JX202600127/history',role='elder')[0],403)

    def test_admin_priority_survives_read_and_restart(self):
        status,p=self.call('POST','/api/profiles/elder/JX202600128',{'service_priority':'紧急干预'},'admin')
        self.assertEqual(status,200,p)
        self.server.initialize_database()
        status,p=self.call('GET','/api/profiles/elder/JX202600128',role='admin')
        self.assertEqual(p['profile']['service_priority'],'紧急干预')

    def test_self_registered_provider_cannot_grant_service_authority(self):
        self.assertEqual(self.call('POST','/api/auth/register',{'username':'newworker','password':'TestWorker123!','role':'provider'})[0],201)
        _,p=self.call('POST','/api/auth/login',{'username':'newworker','password':'TestWorker123!','role':'provider'})
        self.tokens['newworker']=p['token']
        status,p=self.call('POST','/api/auth/complete-profile',{'full_name':'测试人员','phone':'13800000000','gender':'男','organization':'测试机构','skills':'医疗护理 / 生活照料','service_areas':'青云社区','certificates':'自行填写护士证'},'newworker')
        self.assertEqual(status,200,p)
        person=self.sql('SELECT * FROM service_providers WHERE provider_code=?',(p['user']['provider_code'],))[0]
        self.assertNotIn('医疗护理',person['skills'])

    def test_legacy_duplicate_records_not_destroyed_by_upgrade(self):
        with self.server.connect_db() as c:
            c.execute('DROP INDEX IF EXISTS one_record_per_order')
            assignment = c.execute('SELECT order_id, provider_id FROM service_assignments LIMIT 1').fetchone()
            c.executemany(
                'INSERT INTO service_records(order_id, provider_id, completion_status) VALUES (?, ?, ?)',
                [
                    (assignment['order_id'], assignment['provider_id'], '全部完成'),
                    (assignment['order_id'], assignment['provider_id'], '部分完成'),
                ],
            )
        count=self.sql('SELECT count(*) n FROM service_records')[0]['n']
        self.server.initialize_database()
        self.assertEqual(self.sql('SELECT count(*) n FROM service_records')[0]['n'],count)

    def test_exam_fake_signature_is_rejected(self):
        import base64
        status,p=self.call('POST','/api/exam-reports',{'exam_date':date.today().isoformat(),'organization':'测试机构','file_name':'report.pdf','mime_type':'application/pdf','file_data':base64.b64encode(b'not a pdf').decode()},'elder')
        self.assertEqual(status,400,p)

    def test_order_list_exposes_record_review_status(self):
        _,p=self.call('GET','/api/orders',role='admin')
        self.assertIn('record_review_status',p['orders'][0])

    def prepare_provider(self):
        with self.server.connect_db() as c:
            c.execute("UPDATE service_providers SET skills='生活照料 / 医疗护理 / 康复保健',service_areas='青云社区',employment_status='在职' WHERE provider_code='FW20260218'")
            if 'required_certificate' in [r[1] for r in c.execute('PRAGMA table_info(service_items)')]:
                c.execute("UPDATE service_items SET required_certificate='' WHERE service_code='FW-003'")

    def assign(self,order):
        return self.call('POST',f'/api/orders/{order}/assign',{'action':'ASSIGN','provider_code':'FW20260218','assign_mode':'MANUAL'},'admin')

    def test_overlap_rejected_boundary_allowed(self):
        self.prepare_provider()
        a=self.new_order();b=self.new_order('上午 10:00-12:00');c=self.new_order('上午 11:00-13:00')
        self.assertEqual(self.assign(a)[0],200)
        self.assertIn(self.assign(b)[0],(400,409))
        self.assertEqual(self.assign(c)[0],200)

    def test_concurrent_overlap_only_one_assignment(self):
        self.prepare_provider()
        orders=[self.new_order(),self.new_order('上午 10:00-12:00')]
        with ThreadPoolExecutor(max_workers=2) as pool:
            statuses=list(pool.map(lambda order:self.assign(order)[0],orders))
        self.assertEqual(statuses.count(200),1,statuses)
        self.assertNotIn(500,statuses)

    def test_manual_rejects_ineligible_person(self):
        self.prepare_provider()
        with self.server.connect_db() as c:c.execute("UPDATE service_providers SET skills='精神慰藉',service_areas='其他社区' WHERE provider_code='FW20260218'")
        self.assertIn(self.assign(self.new_order())[0],(400,409))

    def test_provider_management_fields_forbidden(self):
        status,_=self.call('POST','/api/profiles/provider/FW20260218',{'skills':'医疗护理','employment_status':'在职','certificates':'护士证'},'provider')
        self.assertEqual(status,403)

    def test_cannot_complete_without_arrival(self):
        self.prepare_provider();order=self.new_order();self.assertEqual(self.assign(order)[0],200)
        self.assertEqual(self.call('POST',f'/api/orders/{order}/depart',{},'provider')[0],200)
        self.assertEqual(self.call('POST',f'/api/orders/{order}/complete',{},'provider')[0],400)

    def test_invalid_health_does_not_write(self):
        before=self.sql('SELECT COUNT(*) n FROM health_vitals')[0]['n']
        for body in ({'bp':'-10/-20','weight':-5,'measured_at':'not-a-date'},{'weight':-5},{'temperature':'NaN'},{'heart_rate':float('inf')}):
            self.assertEqual(self.call('POST','/api/health-records',body,'elder')[0],400)
        self.assertEqual(self.sql('SELECT COUNT(*) n FROM health_vitals')[0]['n'],before)

    def test_health_history_not_synthesized(self):
        status,payload=self.call('GET','/api/health-records?days=7',role='elder')
        self.assertEqual(status,200)
        self.assertFalse(payload['simulated'])
        self.assertTrue(all(r['vital_id'] is not None and r['source']!='系统模拟' for r in payload['records']))

    def test_dashboard_counts_database_rows(self):
        status,p=self.call('GET','/api/dashboard-stats',role='admin')
        self.assertEqual(status,200)
        self.assertEqual(p['stats']['elder_count'],self.sql('SELECT COUNT(*) n FROM elderly_profiles WHERE is_deleted=0')[0]['n'])
        self.assertEqual(p['stats']['order_count'],self.sql('SELECT COUNT(*) n FROM service_orders')[0]['n'])

    def test_legacy_password_upgraded_after_login(self):
        value=self.sql("SELECT password_hash FROM auth_accounts WHERE username='admin'")[0]['password_hash']
        self.assertTrue(value.startswith('pbkdf2_sha256$'))

    def test_emergency_creates_real_station_notifications(self):
        status,p=self.call('POST','/api/emergencies',{'emergency_type':'跌倒'},'elder')
        self.assertEqual(status,201,p)
        status,notices=self.call('GET','/api/acceptance/notifications',role='admin')
        self.assertEqual(status,200,notices)
        self.assertTrue(any(n['reference']==p['emergency_no'] for n in notices['items']))

    def test_record_requires_review_and_keeps_special_fields(self):
        self.prepare_provider();order=self.new_order();self.assign(order)
        for step in ('depart','arrive','complete'):
            self.assertEqual(self.call('POST',f'/api/orders/{order}/{step}',{},'provider')[0],200)
        status,p=self.call('POST',f'/api/orders/{order}/records',{'completion_status':'全部完成','medical_details':'复诊提醒','rehab_details':'耐受良好','materials_json':[{'name':'手套','quantity':2}]},'provider')
        self.assertEqual(status,201,p)
        status,_=self.call('POST',f'/api/orders/{order}/review',{'attitude_score':5,'quality_score':5,'punctual_score':5},'elder')
        self.assertEqual(status,400)
        row=self.sql('SELECT * FROM service_records WHERE record_id=?',(p['record_id'],))[0]
        self.assertEqual(row['medical_details'],'复诊提醒');self.assertEqual(row['rehab_details'],'耐受良好')
        status,_=self.call('POST',f"/api/acceptance/records/{p['record_id']}/review",{'status':'已通过'},'admin')
        self.assertEqual(status,200)
        self.assertEqual(self.call('POST',f'/api/orders/{order}/review',{'attitude_score':5,'quality_score':5,'punctual_score':5},'elder')[0],201)
