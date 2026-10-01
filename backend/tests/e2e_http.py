"""Minimal HTTP E2E through the real Vite/nginx proxy. Use an isolated test data root."""
import argparse
import secrets
import httpx

parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--base-url',default='http://127.0.0.1:5173')
args=parser.parse_args()
with httpx.Client(base_url=args.base_url,timeout=30,headers={'X-MethodPal-Request':'1'}) as client:
    page=client.get('/')
    assert page.status_code == 200 and '<div id="root">' in page.text
    credentials={'username':'e2e_'+secrets.token_hex(5),'password':secrets.token_urlsafe(24)}
    result=client.post('/api/auth/register',json=credentials)
    assert result.status_code==201,result.text
    client.headers['X-CSRF-Token']=result.json()['csrf_token']
    assert client.get('/api/auth/me').json()['user']['username']==credentials['username']
    project=client.post('/api/projects',json={'name':'HTTP E2E'}).json()
    data=client.post('/api/datasets/sample')
    assert data.status_code==200,data.text
    linked=client.patch('/api/projects/'+project['project_id']+'/workspace',json={'dataset_id':data.json()['dataset_id']})
    assert linked.status_code==200,linked.text
    assert client.get('/api/projects/'+project['project_id']).json()['dataset_id']==data.json()['dataset_id']
    with httpx.Client(base_url=args.base_url) as other:
        assert other.get('/api/projects',headers={'X-MethodPal-User':credentials['username']}).status_code==401
    assert client.post('/api/auth/logout').status_code==200
    assert client.get('/api/projects').status_code==401
    result=client.post('/api/auth/login',json=credentials)
    assert result.status_code==200,result.text
    assert len(client.get('/api/projects').json())==1
    print('PASS: frontend, proxy, registration, restoration, project/data ownership, forged header, logout, login')
