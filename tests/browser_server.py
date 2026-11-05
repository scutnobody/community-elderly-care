"""Disposable local-only browser acceptance server. Never starts the shipped DB."""
import json
import secrets
import shutil
import sys
import tempfile
import threading
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import server

def main():
    with tempfile.TemporaryDirectory(prefix='care-browser-') as directory:
        root=Path(directory);project=Path(__file__).resolve().parents[1]
        shutil.copy2(project/'elderly_care.db',root/'elderly_care.db')
        shutil.copytree(project/'uploads',root/'uploads',dirs_exist_ok=True)
        server.DB_PATH=root/'elderly_care.db';server.BASE_DIR=root;server.UPLOAD_DIR=root/'uploads'/'exams'
        server.initialize_database()
        secret=secrets.token_urlsafe(18)
        class Handler(server.ApiHandler):
            def log_message(self,*args):pass
            def do_POST(self):
                if self.path=='/__test_shutdown' and self.headers.get('X-Test-Secret')==secret:
                    self.send_json(200,{'ok':True});threading.Thread(target=http.shutdown,daemon=True).start();return
                super().do_POST()
        http=server.ThreadingHTTPServer(('127.0.0.1',0),Handler)
        print(json.dumps({'url':f'http://127.0.0.1:{http.server_port}','secret':secret,'data_dir':str(root)},ensure_ascii=False),flush=True)
        try:http.serve_forever()
        finally:http.server_close()

if __name__=='__main__':main()
