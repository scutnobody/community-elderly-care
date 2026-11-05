import base64
import math
from datetime import date, timedelta
from urllib.parse import urlencode

from tests.support import ApiCase


class BusinessTests(ApiCase):
    def create(self, resource, payload, role='admin'):
        status, result = self.call('POST', '/api/acceptance/' + resource, payload, role)
        self.assertIn(status, (200, 201), result)
        return result['item']

    def listing(self, resource, role='admin', **query):
        status, result = self.call('GET', '/api/acceptance/' + resource + '?' + urlencode(query), role=role)
        self.assertEqual(status, 200, result)
        return result

    def test_institution_service_links_and_package_persist(self):
        service = self.create('services', {'service_code':'TEST-01','service_name':'验收服务','service_type':'生活照料类','price':12.5,'duration_minutes':30,'service_status':'暂停提供'})
        self.assertEqual(service['enabled'], 0)
        institution = self.create('institutions', {'institution_code':'TEST-I','name':'验收机构','institution_type':'日间照料','community':'测试社区','bed_count':8,'service_ids':[service['service_item_id']]})
        package = self.create('packages', {'name':'验收套餐','price':50,'valid_until':'2027-12-31','status':'正常','items':[{'service_item_id':service['service_item_id'],'frequency':2}]})
        self.assertEqual(package['items'][0]['frequency'], 2)
        self.assertEqual(self.listing('institutions', role='elder', community='测试社区')['items'][0]['service_ids'], [service['service_item_id']])
        from acceptance.business import migrate
        migrate(self.server); migrate(self.server)
        self.assertEqual(self.listing('institutions', q='验收机构')['items'][0]['institution_id'], institution['institution_id'])

    def test_health_resources_are_owned_and_validate_thresholds(self):
        medication = self.create('medications', {'name':'测试药','spec':'5mg','usage':'按医嘱','scheduled_at':'2026-10-26T08:00','status':'未服'}, 'elder')
        self.assertEqual(medication['elder_code'], 'JX202600128')
        self.create('visits', {'visit_date':'2026-10-25','hospital':'社区医院','department':'全科','diagnosis':'复诊','follow_up':'2026-11-01'}, 'elder')
        threshold = self.create('thresholds', {'metric':'glucose','lower_bound':4,'upper_bound':8,'enabled':1,'action':'联系医生'}, 'elder')
        self.assertEqual(threshold['upper_bound'], 8)
        self.assertEqual(self.listing('visits','elder')['total'], 1)
        status, _ = self.call('POST','/api/acceptance/thresholds',{'metric':'glucose','lower_bound':9,'upper_bound':4,'enabled':1},'elder')
        self.assertEqual(status, 400)
        status, _ = self.call('POST','/api/acceptance/medications',{'elder_code':'OTHER','name':'药','scheduled_at':'2026-10-26T08:00','status':'未服'},'elder')
        self.assertEqual(status, 403)
        status, _ = self.call('GET','/api/acceptance/medications',role='provider')
        self.assertEqual(status, 403)

    def test_credentials_self_submit_cannot_self_approve(self):
        provider_code = self.sql('SELECT provider_code FROM service_providers ORDER BY provider_id LIMIT 1')[0]['provider_code']
        credential = self.create('credentials', {'name':'养老护理员证','certificate_no':'TEST-C','expires_on':'2027-12-31','status':'APPROVED'}, 'provider')
        self.assertEqual(credential['status'], 'PENDING')
        status, _ = self.call('POST',f"/api/acceptance/credentials/{credential['credential_id']}/review",{'status':'APPROVED'},'provider')
        self.assertEqual(status,403)
        status, result = self.call('POST',f"/api/acceptance/credentials/{credential['credential_id']}/review",{'status':'APPROVED','review_note':'核验原件'},'admin')
        self.assertEqual(status,200,result)
        self.assertEqual(result['item']['status'],'APPROVED')
        updated = self.create('credentials', {'id':credential['credential_id'],'name':'养老护理员证','certificate_no':'CHANGED','expires_on':'2027-12-31'}, 'provider')
        self.assertEqual(updated['status'],'PENDING')
        training = self.create('training', {'provider_code':provider_code,'name':'安全培训','training_date':'2026-10-26','result':'合格','service_type':'生活照料类','service_years':3}, 'provider')
        self.assertEqual(training['service_years'],3)

    def test_notifications_and_locations_are_recipient_scoped(self):
        with self.server.connect_db() as c:
            c.execute("INSERT INTO notifications(recipient_role,recipient_code,kind,title,body,reference,created_at) VALUES('elder','JX202600128','test','本人消息','','','2026-10-26 10:00:00')")
            own_id = c.execute('SELECT last_insert_rowid()').fetchone()[0]
            c.execute("INSERT INTO notifications(recipient_role,recipient_code,kind,title,body,reference,created_at) VALUES('admin','','test','管理员消息','','','2026-10-26 10:00:00')")
            admin_id = c.execute('SELECT last_insert_rowid()').fetchone()[0]
        rows=self.listing('notifications','elder',after_id=own_id-1)['items']
        self.assertEqual([r['notification_id'] for r in rows],[own_id])
        status,_=self.call('POST',f'/api/acceptance/notifications/{admin_id}/read',{},'elder')
        self.assertEqual(status,403)
        self.create('locations',{'latitude':28.68,'longitude':115.85},'provider')
        self.assertAlmostEqual(self.listing('locations')['items'][0]['latitude'],28.68)
        status,_=self.call('POST','/api/acceptance/locations',{'latitude':91,'longitude':0},'provider')
        self.assertEqual(status,400)

    def test_attachment_signature_ownership_and_download(self):
        # A small genuine PNG, not a filename pretending to be an image.
        raw=base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aX1cAAAAASUVORK5CYII=')
        payload={'owner_type':'provider','owner_key':self.sql('SELECT provider_code FROM service_providers ORDER BY provider_id LIMIT 1')[0]['provider_code'],'purpose':'certificate','file_name':'../证书.png','mime_type':'image/png','file_data':base64.b64encode(raw).decode()}
        attachment=self.create('attachments',payload,'provider')
        status,data=self.call('GET',f"/api/acceptance/attachments/{attachment['attachment_id']}/download",role='provider',binary=True)
        self.assertEqual(status,200); self.assertEqual(data,raw)
        status,_=self.call('GET',f"/api/acceptance/attachments/{attachment['attachment_id']}/download",role='elder')
        self.assertEqual(status,403)
        payload['file_data']=base64.b64encode(b'<script>not png</script>').decode()
        status,_=self.call('POST','/api/acceptance/attachments',payload,'provider')
        self.assertEqual(status,400)

    def test_archive_refuses_active_orders_and_requires_reason(self):
        status,_=self.call('POST','/api/acceptance/elders/JX202600128/archive',{'reason':''},'admin')
        self.assertEqual(status,400)
        self.new_order()
        status,_=self.call('POST','/api/acceptance/elders/JX202600128/archive',{'reason':'迁出社区'},'admin')
        self.assertEqual(status,400)
        self.assertEqual(self.sql("SELECT is_deleted FROM elderly_profiles WHERE elder_code='JX202600128'")[0]['is_deleted'],0)

    def test_unconfigured_emergency_confirmation_is_explicitly_simulated(self):
        status,result=self.call('POST','/api/emergencies',{'elder_code':'JX202600128','emergency_type':'身体不适'},'elder')
        self.assertEqual(status,201,result)
        emergency=result['emergency_no']
        status,result=self.call('POST',f'/api/acceptance/emergencies/{emergency}/confirm',{'note':'课程模拟联系人确认'},'admin')
        self.assertEqual(status,200,result)
        rows=self.sql('SELECT status FROM external_notifications WHERE emergency_id=(SELECT emergency_id FROM emergency_requests WHERE emergency_no=?)',(emergency,))
        self.assertIn('SIMULATED_CONFIRMED',[r['status'] for r in rows])
        self.assertNotIn('DELIVERED',[r['status'] for r in rows])

    def test_record_review_and_complaint_handler_require_admin(self):
        records=self.listing('records')['items']
        if records:
            record=records[0]
            status,_=self.call('POST',f"/api/acceptance/records/{record['record_id']}/review",{'status':'已通过'},'provider')
            self.assertEqual(status,403)
        order_no=self.new_order()
        with self.server.connect_db() as c:
            order=c.execute('SELECT order_id,elder_id FROM service_orders WHERE order_no=?',(order_no,)).fetchone()
            c.execute("INSERT INTO service_complaints(complaint_no,order_id,elder_id,complaint_type,complaint_content) VALUES('TS-TEST',?,?,'服务质量','验收投诉')",(order['order_id'],order['elder_id']))
        complaints=[{'complaint_no':'TS-TEST'}]
        status,result=self.call('POST',f"/api/acceptance/complaints/{complaints[0]['complaint_no']}/handle",{'status':'已处理','admin_reply':'已核实并回访'},'admin')
        self.assertEqual(status,200,result)
        self.assertEqual(result['item']['admin_reply'],'已核实并回访')

    def test_record_review_stores_actor_and_rejects_second_decision(self):
        order_no=self.new_order()
        with self.server.connect_db() as c:
            order=c.execute('SELECT order_id FROM service_orders WHERE order_no=?',(order_no,)).fetchone()
            provider=c.execute('SELECT provider_id FROM service_providers ORDER BY provider_id LIMIT 1').fetchone()
            cursor=c.execute("INSERT INTO service_records(order_id,provider_id,completion_status,process_description) VALUES(?,?,'全部完成','真实过程')",(order['order_id'],provider['provider_id']))
            identifier=cursor.lastrowid
        status,result=self.call('POST',f'/api/acceptance/records/{identifier}/review',{'status':'已退回','review_note':'请补充耗材'},'admin')
        self.assertEqual(status,200,result)
        self.assertEqual(result['item']['review_status'],'已退回')
        self.assertEqual(result['item']['reviewed_by'],'admin')
        status,_=self.call('POST',f'/api/acceptance/records/{identifier}/review',{'status':'已通过'},'admin')
        self.assertEqual(status,400)

    def test_invalid_numbers_dates_and_relations_leave_no_partial_write(self):
        status,_=self.call('POST','/api/acceptance/services',{'service_code':'BAD','service_name':'坏价格','service_type':'生活照料类','price':float('nan'),'duration_minutes':30},'admin')
        self.assertEqual(status,400)
        status,_=self.call('POST','/api/acceptance/visits',{'hospital':'医院','visit_date':'2026-02-30'},'elder')
        self.assertEqual(status,400)
        status,_=self.call('POST','/api/acceptance/institutions',{'institution_code':'BAD','name':'不完整机构','service_ids':[9999999]},'admin')
        self.assertEqual(status,400)
        self.assertEqual(self.listing('institutions',q='不完整机构')['total'],0)
        status,_=self.call('POST','/api/acceptance/packages',{'name':'坏套餐','price':5,'valid_until':'2027-01-01','items':[{'service_item_id':1,'frequency':-1}]},'admin')
        self.assertEqual(status,400)
        self.assertEqual(self.listing('packages',q='坏套餐')['total'],0)

    def test_legacy_migration_is_idempotent_and_does_not_approve_credentials(self):
        from acceptance.business import migrate
        before=self.sql('SELECT COUNT(*) count FROM provider_credentials')[0]['count']
        self.assertGreater(before,0)
        self.assertEqual(self.sql("SELECT COUNT(*) count FROM provider_credentials WHERE status='APPROVED'")[0]['count'],0)
        migrate(self.server);migrate(self.server)
        self.assertEqual(self.sql('SELECT COUNT(*) count FROM provider_credentials')[0]['count'],before)
        status,result=self.call('GET','/api/acceptance/institutions?page_size=201',role='admin')
        self.assertEqual(status,400,result)

    def test_empty_unknown_and_unauthenticated_extension_routes(self):
        status,_=self.call('GET','/api/acceptance/institutions')
        self.assertIn(status,(401,403))
        status,_=self.call('GET','/api/acceptance/no-such-resource',role='admin')
        self.assertEqual(status,404)
        self.assertEqual(self.listing('institutions',q='不存在')['total'],0)

    def test_expiring_approved_credentials_generate_one_persistent_reminder(self):
        credential=self.create('credentials',{'name':'短期证书','certificate_no':'EXP-TEST','expires_on':(date.today()+timedelta(days=5)).isoformat()},'provider')
        status,result=self.call('POST',f"/api/acceptance/credentials/{credential['credential_id']}/review",{'status':'APPROVED'},'admin')
        self.assertEqual(status,200,result)
        for _ in range(2): self.listing('notifications','provider')
        reminders=self.sql("SELECT * FROM notifications WHERE kind='credential_expiry' AND recipient_role='provider'")
        self.assertEqual(len(reminders),1)
        self.assertIn('短期证书',reminders[0]['body'])

    def test_migrated_credentials_do_not_reappear_after_manual_rejection(self):
        from acceptance.business import migrate
        rows=self.sql('SELECT credential_id FROM provider_credentials ORDER BY credential_id LIMIT 1')
        identifier=rows[0]['credential_id']
        status,result=self.call('POST',f'/api/acceptance/credentials/{identifier}/review',{'status':'REJECTED','review_note':'待补充原件'},'admin')
        self.assertEqual(status,200,result)
        migrate(self.server)
        self.assertEqual(self.sql('SELECT status FROM provider_credentials WHERE credential_id=?',(identifier,))[0]['status'],'REJECTED')

    def test_partial_admin_updates_retain_subject_and_unchanged_fields(self):
        medication=self.create('medications',{'elder_code':'JX202600128','name':'既有药品','usage':'按医嘱','scheduled_at':'2026-10-26T08:00','status':'未服'})
        updated=self.create('medications',{'id':medication['id'],'status':'已服'})
        self.assertEqual(updated['name'],'既有药品')
        self.assertEqual(updated['elder_code'],'JX202600128')
        self.assertEqual(updated['status'],'已服')
