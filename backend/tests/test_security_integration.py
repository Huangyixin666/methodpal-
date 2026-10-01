"""Run separately from legacy contract tests (which install module stubs)."""
import os
import sys
import tempfile
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
TEMP = tempfile.TemporaryDirectory()
os.environ['METHODPAL_DATA_ROOT'] = TEMP.name
from fastapi.testclient import TestClient
from main import app
from app.services.database import connect
from app.services.authentication import COOKIE, digest

class SecurityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.lifecycle = TestClient(app)
        cls.lifecycle.__enter__()
        cls.a = TestClient(app)
        cls.b = TestClient(app)
        for client, name in [(cls.a, 'alice'), (cls.b, 'bob')]:
            client.headers['X-MethodPal-Request'] = '1'
            r = client.post('/api/auth/register', json={'username': name, 'password': 'a-strong-password-123'})
            assert r.status_code == 201, r.text
            client.headers['X-CSRF-Token'] = r.json()['csrf_token']
        cls.project = cls.a.post('/api/projects', json={'name':'隔离测试'}).json()['project_id']
        cls.dataset = cls.a.post('/api/datasets/paste', json={'headers':['x','y'], 'rows':[['1','2'],['2','3'],['3','5'],['4','8'],['5','9']], 'name':'test'}).json()['dataset_id']

    @classmethod
    def tearDownClass(cls):
        cls.a.close(); cls.b.close(); cls.lifecycle.__exit__(None,None,None)
        TEMP.cleanup()

    def test_01_header_forgery_and_ownership(self):
        anon = TestClient(app)
        self.assertEqual(anon.get('/api/projects', headers={'X-MethodPal-User':'alice'}).status_code,401)
        self.assertEqual(self.b.get('/api/projects/'+self.project, headers={'X-MethodPal-User':'alice'}).status_code,404)
        for method, path, data in [('get','/api/datasets/'+self.dataset,None), ('delete','/api/datasets/'+self.dataset,None), ('post','/api/analysis/run',{'dataset_id':self.dataset,'model':'descriptive_statistics'}), ('get','/api/agent/history/'+self.project,None), ('put','/api/agent/state/'+self.project, {})]:
            r = self.b.request(method,path,json=data)
            self.assertEqual(r.status_code,404,r.text)
        own = self.b.post('/api/projects',json={'name':'bob'}).json()['project_id']
        self.assertEqual(self.b.patch('/api/projects/'+own+'/workspace',json={'dataset_id':self.dataset}).status_code,404)
        import json
        for content_type in ['', 'application/vnd.methodpal+json']:
            response=self.b.patch('/api/projects/'+own+'/workspace',content=json.dumps({'dataset_id':self.dataset}),headers={'Content-Type':content_type})
            self.assertEqual(response.status_code,404,response.text)
        self.assertEqual(self.b.post('/api/agent/message',json={'context':{'project_id':own,'dataset_id':self.dataset}}).status_code,404)

    def test_02_csrf(self):
        self.assertEqual(self.a.post('/api/projects',json={'name':'bad'},headers={'X-CSRF-Token':''}).status_code,403)
        self.assertEqual(self.a.post('/api/projects',json={'name':'bad'},headers={'Origin':'https://evil.example'}).status_code,403)
        anon = TestClient(app)
        self.assertEqual(anon.post('/api/auth/login',json={'username':'alice','password':'wrong'}).status_code,403)

    def test_03_credentials(self):
        anon = TestClient(app,headers={'X-MethodPal-Request':'1'})
        self.assertEqual(anon.post('/api/auth/register',json={'username':'ALICE','password':'a-strong-password-123'}).status_code,409)
        self.assertEqual(anon.post('/api/auth/login',json={'username':'alice','password':'wrong'}).status_code,401)
        r=anon.post('/api/auth/login',json={'username':'ALICE','password':'a-strong-password-123'})
        self.assertEqual(r.status_code,200,r.text)
        cookie=r.headers['set-cookie'].lower()
        self.assertIn('httponly',cookie); self.assertIn('samesite=lax',cookie)
        self.assertEqual(anon.get('/api/auth/me').json()['user']['username'],'alice')
        with connect() as db:
            stored=db.execute('SELECT password_hash FROM users WHERE username=?',('alice',)).fetchone()[0]
            self.assertTrue(stored.startswith('scrypt$'))
            self.assertNotIn('a-strong-password',stored)
        token=anon.cookies.get(COOKIE)
        anon.headers['X-CSRF-Token']=r.json()['csrf_token']
        self.assertEqual(anon.post('/api/auth/logout').status_code,200)
        anon.cookies.set(COOKIE,token)
        self.assertEqual(anon.get('/api/projects').status_code,401)

    def test_04_expiration(self):
        client=TestClient(app,headers={'X-MethodPal-Request':'1'})
        self.assertEqual(client.post('/api/auth/login',json={'username':'alice','password':'a-strong-password-123'}).status_code,200)
        with connect() as db:
            db.execute('UPDATE sessions SET expires_at=0 WHERE token_hash=?',(digest(client.cookies.get(COOKIE)),))
        self.assertEqual(client.get('/api/auth/me').status_code,401)

    def test_05_analysis_and_upload(self):
        r=self.a.post('/api/analysis/run',json={'dataset_id':self.dataset,'model':'descriptive_statistics','independent_variables':['x','y']})
        self.assertEqual(r.status_code,200,r.text)
        aid=r.json()['analysis_id']
        regression=self.a.post('/api/analysis/run',json={'dataset_id':self.dataset,'model':'linear_regression','dependent_variable':'y','independent_variables':['x']})
        self.assertEqual(regression.status_code,200,regression.text)
        self.assertTrue(regression.json()['regression_table'])
        self.assertEqual(self.b.get('/api/analysis/'+aid).status_code,404)
        r=self.a.post('/api/datasets/upload',files={'file':('../../escape.csv',b'x,y\n1,2\n3,4','text/csv')})
        self.assertEqual(r.status_code,200,r.text)
        self.assertEqual(r.json()['name'],'escape.csv')
        did=r.json()['dataset_id']
        self.assertEqual(self.a.post('/api/datasets/'+did+'/header/reparse',json={'header_mode':'row','header_row':1}).status_code,200)
        self.assertEqual(self.b.get('/api/datasets/'+did).status_code,404)

    def test_06_limits_and_validation(self):
        self.assertEqual(self.a.get('/api/datasets/sample').status_code,404)
        self.assertEqual(self.a.post('/api/datasets/sample').status_code,200)
        from app.services import security
        old=security.MAX_BODY
        try:
            security.MAX_BODY=64
            self.assertEqual(self.a.post('/api/datasets/paste',content=b'x'*65).status_code,413)
            self.assertEqual(self.a.post('/api/datasets/paste',content=iter([b'x'*32,b'x'*33])).status_code,413)
        finally:
            security.MAX_BODY=old
        self.assertEqual(self.a.get('/api/projects',headers={'host':'evil.example'}).status_code,400)
        r=self.a.post('/api/auth/login',json={'username':'alice','password':'secret'*100})
        self.assertEqual(r.status_code,422)
        self.assertNotIn('secret',r.text)

    def test_07_reports_charts_agent(self):
        # A 5-point integer sample is inferred as an unconfirmed ordinal scale;
        # the type-aware Pearson heatmap intentionally excludes it.
        ds=self.a.post('/api/datasets/paste',json={'headers':['a','b'],'rows':[[str(i+.2),str(i*i+.3)] for i in range(30)],'name':'numeric'}).json()['dataset_id']
        r=self.a.post('/api/charts/generate',json={'dataset_id':ds,'chart_type':'correlation_heatmap'})
        self.assertEqual(r.status_code,200,r.text)
        chart=r.json()['chart']
        self.assertEqual(self.b.get('/api/charts/'+chart['chart_id']).status_code,404)
        r=self.a.post('/api/reports/generate',json={'project_id':self.project,'dataset_id':ds,'charts':[chart],'report_mode':'template'})
        self.assertEqual(r.status_code,200,r.text)
        rid=r.json()['report_id']
        self.assertEqual(self.b.get('/api/reports/'+rid).status_code,404)
        self.assertEqual(self.b.get('/api/report/'+rid).status_code,404)
        self.assertEqual(self.b.post('/api/reports/generate',json={'chart_ids':[chart['chart_id']]}).status_code,404)
        r=self.a.put('/api/agent/history/'+self.project,json={'project_id':self.project,'messages':[{'role':'user','content':'private-chat'}]})
        self.assertEqual(r.status_code,200,r.text)
        self.assertEqual(self.a.get('/api/agent/history/'+self.project).json()['messages'][0]['content'],'private-chat')
        r=self.a.post('/api/agent/message',json={'mode':'autopilot','message':'自动完成','context':{'project_id':self.project,'dataset_id':self.dataset}})
        self.assertEqual(r.status_code,200,r.text)
        self.assertEqual(self.b.post('/api/agent/resume',json={'thread_id':r.json().get('thread_id'),'context':{'project_id':self.project,'dataset_id':self.dataset}}).status_code,404)

    def test_08_store_and_migration(self):
        from app.services.store import PersistentSqliteDict, owned_file_path
        from app.services.user_context import set_current_user_id, reset_current_user_id
        from scripts.import_legacy import import_legacy
        import json
        with connect() as db:
            alice=db.execute('SELECT id FROM users WHERE username=?',('alice',)).fetchone()[0]
            bob=db.execute('SELECT id FROM users WHERE username=?',('bob',)).fetchone()[0]
        for namespace in ['projects','datasets','analyses','charts','reports','variable_configs','agent_chats','agent_states']:
            store=PersistentSqliteDict(namespace)
            token=set_current_user_id(alice)
            try:
                store['same-id']={'secret':'alice'}
                with self.assertRaises(ValueError): owned_file_path(Path(TEMP.name)/'escape.csv')
            finally: reset_current_user_id(token)
            token=set_current_user_id(bob)
            try:
                self.assertIsNone(store.get('same-id'))
                store['same-id']={'secret':'bob'}
                self.assertEqual(store['same-id']['secret'],'bob')
            finally: reset_current_user_id(token)
            token=set_current_user_id(alice)
            try: self.assertEqual(store['same-id']['secret'],'alice')
            finally: reset_current_user_id(token)
        with self.assertRaises(RuntimeError): PersistentSqliteDict('projects').get('same-id')
        client=TestClient(app,headers={'X-MethodPal-Request':'1'})
        self.assertEqual(client.post('/api/auth/register',json={'username':'migrated','password':'test-only-password-123'}).status_code,201)
        source=Path(TEMP.name)/'legacy'
        source.mkdir(); (source/'raw_datasets').mkdir()
        (source/'raw_datasets'/'example.csv').write_text('x,y\n1,2',encoding='utf-8')
        (source/'datasets.json').write_text(json.dumps({'legacy-ds':{'name':'legacy','_source':{'path':'C:/old/example.csv'}}}),encoding='utf-8')
        self.assertFalse(import_legacy(source,'migrated')['applied'])
        self.assertTrue(import_legacy(source,'migrated',True)['applied'])
        with self.assertRaises(ValueError): import_legacy(source,'migrated',True)
        self.assertEqual((source/'raw_datasets'/'example.csv').read_text(),'x,y\n1,2')

    def test_09_rate_limit(self):
        from app.services.authentication import digest
        with connect() as db:
            import time
            db.execute('INSERT OR REPLACE INTO auth_attempts VALUES (?,?,?)',(digest('name:blocked'),int(time.time()),12))
        client=TestClient(app,headers={'X-MethodPal-Request':'1'})
        self.assertEqual(client.post('/api/auth/login',json={'username':'blocked','password':'wrong'}).status_code,429)

if __name__=='__main__':
    unittest.main()
