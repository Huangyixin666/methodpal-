import os
import sys
import tempfile
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
with tempfile.TemporaryDirectory() as data:
    os.environ.update(METHODPAL_ENV='production',METHODPAL_ORIGINS='https://testserver',METHODPAL_ALLOWED_HOSTS='testserver',METHODPAL_DATA_ROOT=data)
    from fastapi.testclient import TestClient
    from main import app
    with TestClient(app,base_url='https://testserver',headers={'X-MethodPal-Request':'1','Origin':'https://testserver'}) as client:
        r=client.post('/api/auth/register',json={'username':'production_test','password':'test-only-strong-password'})
        assert r.status_code==201,r.text
        cookie=r.headers['set-cookie'].lower()
        assert '__host-methodpal_session=' in cookie and 'secure' in cookie and 'httponly' in cookie and 'domain=' not in cookie
        assert 'strict-transport-security' in r.headers
        client.headers['X-CSRF-Token']=r.json()['csrf_token']
        assert client.get('/api/auth/me').status_code==200
        assert client.get('/docs').status_code==404
        assert client.get('/openapi.json').status_code==404
        assert client.post('/api/projects',json={'name':'bad'},headers={'Origin':'http://testserver'}).status_code==403
        assert client.post('/api/auth/logout').status_code==200
        print('PASS: production cookie flags, HTTPS origin policy, HSTS, disabled API docs, logout')
