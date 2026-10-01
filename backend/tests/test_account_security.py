"""Isolated tests: deterministic CAPTCHA and mocked email transport only here."""
import os
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))

def main():
    with tempfile.TemporaryDirectory() as root:
        os.environ.update(METHODPAL_ENV='development',METHODPAL_DATA_ROOT=root,
            METHODPAL_ALLOWED_HOSTS='testserver',METHODPAL_CAPTCHA_REQUIRED='0',METHODPAL_EMAIL_REQUIRED='0',METHODPAL_JOB_WORKER='0')
        os.environ.pop('METHODPAL_DATABASE_PATH',None)
        # Create the legacy database first; upgrade must preserve its user and data.
        import sqlite3
        with sqlite3.connect(str(Path(root)/'methodpal.sqlite3')) as db:
            db.executescript('CREATE TABLE users(id TEXT PRIMARY KEY,username TEXT UNIQUE COLLATE NOCASE,password_hash TEXT,created_at INTEGER);'
                'CREATE TABLE sessions(token_hash TEXT PRIMARY KEY,user_id TEXT,csrf_token TEXT,expires_at INTEGER);PRAGMA user_version=1;')
        from fastapi.testclient import TestClient
        from main import app
        from app.services.database import connect, migrate
        from app.services import account_verification as v
        from app.services.authentication import COOKIE
        captured = {}
        def email_mock(email, code, purpose):
            captured[(email,purpose)] = code
        def checked(r, status=200):
            assert r.status_code==status, (r.status_code,r.text)
            return r.json()
        headers={'X-MethodPal-Request':'1'}
        password='test-only-password-123'
        with TestClient(app,headers=headers) as a, TestClient(app,headers=headers) as b:
            old=checked(a.post('/api/auth/register',json={'username':'legacy_user','password':password}),201)
            a.headers['X-CSRF-Token']=old['csrf_token']
            pid=checked(a.post('/api/projects',json={'name':'must survive password reset'}))['project_id']
            migrate()
            with connect() as db:
                assert db.execute('PRAGMA user_version').fetchone()[0]==2
            os.environ['METHODPAL_CAPTCHA_REQUIRED']='1'
            def captcha(c):
                with patch.object(v.secrets,'choice',side_effect=lambda alphabet:alphabet[0]):
                    ch=checked(c.get('/api/auth/captcha'))
                assert ch['image'].startswith('data:image/png;base64,') and 'answer' not in ch
                return {'captcha_id':ch['captcha_id'],'captcha_answer':'AAAAA'}
            cp=captcha(b)
            checked(b.post('/api/auth/login',json={'username':'legacy_user','password':password,**cp,'captcha_answer':'WRONG'}),400)
            checked(b.post('/api/auth/login',json={'username':'legacy_user','password':password,**cp}),400)
            cp=captcha(b)
            with connect() as db: db.execute('UPDATE auth_captchas SET expires_at=0 WHERE id=?',(cp['captcha_id'],))
            checked(b.post('/api/auth/login',json={'username':'legacy_user','password':password,**cp}),400)
            with patch.object(v,'mail_enabled',return_value=False):
                checked(b.post('/api/auth/email/code',json={'email':'a@example.org','purpose':'reset',**captcha(b)}),503)
            with patch.object(v,'mail_enabled',return_value=True), patch.object(v,'send_email',side_effect=email_mock):
                os.environ['METHODPAL_EMAIL_REQUIRED']='1'
                checked(b.post('/api/auth/register',json={'username':'missing_email','password':password,**captcha(b)}),422)
                def code(c,email,purpose):
                    r=checked(c.post('/api/auth/email/code',json={'email':email,'purpose':purpose,**captcha(c)}))
                    assert 'code' not in r
                    return {'email':email,'verification_id':r['verification_id'],'email_code':captured[(email,purpose)]}
                proof=code(b,'new@example.org','register')
                checked(b.post('/api/auth/email/code',json={'email':'new@example.org','purpose':'register',**captcha(b)}),429)
                fresh=checked(b.post('/api/auth/register',json={'username':'verified_user','password':password,**proof,**captcha(b)}),201)
                b.headers['X-CSRF-Token']=fresh['csrf_token']
                assert fresh['user']['email_verified'] and fresh['user']['email']=='new@example.org'
                checked(b.post('/api/auth/register',json={'username':'replay_email','password':password,**proof,**captcha(b)}),400)
                checked(b.get('/api/projects/'+pid),404)
                with TestClient(app,headers=headers) as anon:
                    checked(anon.post('/api/auth/email/code',json={'email':'bind@example.org','purpose':'bind',**captcha(anon)}),403)
                bind=code(a,'legacy@example.org','bind')
                checked(a.post('/api/auth/email/bind',json={**bind,'current_password':'wrong'}),400)
                checked(b.post('/api/auth/email/bind',json={**bind,'current_password':password}),409)
                # The email proof is bound to the authenticated user as well as purpose.
                checked(a.post('/api/auth/password/reset',json={**bind,'new_password':'another-test-password-123',**captcha(a)}),400)
                checked(a.post('/api/auth/email/bind',json={**bind,'current_password':password}))
                assert checked(a.get('/api/auth/me'))['user']['email_verified']
                with TestClient(app,headers=headers) as other:
                    login=checked(other.post('/api/auth/login',json={'username':'legacy@example.org','password':password,**captcha(other)}))
                    other.headers['X-CSRF-Token']=login['csrf_token']
                    assert len(checked(a.get('/api/auth/sessions'))['items'])==2
                    checked(a.delete('/api/auth/sessions/others'))
                    checked(other.get('/api/auth/me'),401)
                    with connect() as db:
                        db.execute('UPDATE email_challenges SET created_at=created_at-61 WHERE email=?',('legacy@example.org',))
                    reset=code(other,'legacy@example.org','reset')
                    checked(other.post('/api/auth/password/reset',json={**reset,'new_password':'recovered-password-456',**captcha(other)}))
                    checked(a.get('/api/auth/me'),401)
                    checked(other.post('/api/auth/password/reset',json={**reset,'new_password':'replayed-password-456',**captcha(other)}),400)
                    login=checked(a.post('/api/auth/login',json={'username':'legacy@example.org','password':'recovered-password-456',**captcha(a)}))
                    a.headers['X-CSRF-Token']=login['csrf_token']
                    assert checked(a.get('/api/projects/'+pid))['name']=='must survive password reset'
                # Five wrong attempts lock an email challenge; purpose swapping also fails.
                fail=code(b,'wrong@example.org','reset')
                for _ in range(5):
                    checked(b.post('/api/auth/password/reset',json={**fail,'email_code':'999999' if fail['email_code']!='999999' else '888888','new_password':'reset-test-password-123',**captcha(b)}),400)
                checked(b.post('/api/auth/password/reset',json={**fail,'new_password':'reset-test-password-123',**captcha(b)}),400)
                expired=code(b,'expired@example.org','register')
                with connect() as db: db.execute('UPDATE email_challenges SET expires_at=0 WHERE id=?',(expired['verification_id'],))
                checked(b.post('/api/auth/register',json={**expired,'username':'expired_user','password':password,**captcha(b)}),400)
                with patch.dict(os.environ,{'METHODPAL_ACCOUNT_PROJECT_LIMIT':'1'}):
                    checked(a.post('/api/projects',json={'name':'over limit'}),413)
                with patch.dict(os.environ,{'METHODPAL_ACCOUNT_QUOTA_BYTES':'1'}):
                    checked(a.post('/api/datasets/paste',json={'headers':['x'],'rows':[['1']]}),413)
                usage=checked(a.get('/api/projects/__user_storage'))
                assert usage['project_count']==1 and usage['quota_bytes']==500*1024*1024
                checked(a.post('/api/auth/password/change',json={'current_password':'wrong','new_password':'changed-password-789'}),400)
                checked(a.post('/api/auth/password/change',json={'current_password':'recovered-password-456','new_password':'changed-password-789'}))
                checked(a.get('/api/auth/me'),401)
            # Trusted reverse proxy forwarding is narrowly scoped to loopback and production.
            from types import SimpleNamespace
            request=SimpleNamespace(client=SimpleNamespace(host='8.8.8.8'),headers={'x-real-ip':'1.2.3.4'})
            with patch.dict(os.environ,{'METHODPAL_ENV':'production'}):
                assert v.client_ip(request)=='8.8.8.8'
                request.client.host='127.0.0.1'
                assert v.client_ip(request)=='1.2.3.4'
            print('PASS: migration, CAPTCHA wrong/expiry/replay, mail fail-closed/cooldown, verified registration, code replay/purpose/expiry/attempt limits, legacy binding, email login, password recovery and data retention, session revocation, password change, account isolation, quotas, trusted proxy IP')

if __name__=='__main__':
    main()
