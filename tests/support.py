import importlib.util
import json
import shutil
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

PROJECT = Path(__file__).resolve().parents[1]

class ApiCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='care-test-')
        self.root = Path(self.temp.name)
        shutil.copy2(PROJECT/'elderly_care.db', self.root/'care.db')
        spec=importlib.util.spec_from_file_location('care_test_server', PROJECT/'server.py')
        self.server=importlib.util.module_from_spec(spec)
        import sys
        sys.modules[spec.name]=self.server
        spec.loader.exec_module(self.server)
        self.server.DB_PATH=self.root/'care.db'
        self.server.BASE_DIR=self.root
        self.server.UPLOAD_DIR=self.root/'uploads'/'exams'
        self.server.initialize_database()
        self.server.ApiHandler.log_message=lambda *args: None
        self.http=self.server.ThreadingHTTPServer(('127.0.0.1',0),self.server.ApiHandler)
        self.thread=threading.Thread(target=self.http.serve_forever,daemon=True)
        self.thread.start()
        self.base=f'http://127.0.0.1:{self.http.server_port}'
        self.tokens={}
        for role,pwd in [('admin','Admin123!'),('elder','Elder123!'),('provider','Provider123!')]:
            status,payload=self.call('POST','/api/auth/login',{'username':role,'password':pwd,'role':role})
            self.assertEqual(status,200,payload)
            self.tokens[role]=payload['token']

    def tearDown(self):
        self.http.shutdown();self.http.server_close();self.thread.join(timeout=3)
        import gc
        gc.collect()
        self.temp.cleanup()

    def call(self,method,path,data=None,role=None,binary=False):
        headers={'Content-Type':'application/json'}
        if role: headers['Authorization']='Bearer '+self.tokens.get(role,role)
        req=Request(self.base+path,data=json.dumps(data,ensure_ascii=False).encode('utf-8') if data is not None else None,headers=headers,method=method)
        try:
            with urlopen(req,timeout=15) as response:
                raw=response.read()
                return response.status,raw if binary else json.loads(raw)
        except HTTPError as error:
            return error.code,json.loads(error.read())

    def sql(self,query,params=()):
        with self.server.connect_db() as c:
            return [dict(row) for row in c.execute(query,params)]

    def new_order(self,slot='上午 09:00-11:00',service='FW-003',duration=120):
        from datetime import date,timedelta
        status,result=self.call('POST','/api/orders',{'elder_code':'JX202600128','service_code':service,'service_date':(date.today()+timedelta(days=15)).isoformat(),'time_slot':slot,'duration_minutes':duration,'service_address':'青云社区测试地址'},'elder')
        self.assertEqual(status,201,result)
        return result['order_no']
