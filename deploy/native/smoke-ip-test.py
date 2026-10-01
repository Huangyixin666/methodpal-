"""Verify a deployment with a pinned TLS certificate and disposable test accounts."""
import argparse
import secrets
import time
import httpx

parser = argparse.ArgumentParser()
parser.add_argument('--base-url', required=True)
parser.add_argument('--ca-file', required=True)
parser.add_argument('--origin', help='External origin when testing via a loopback connection')
args = parser.parse_args()
headers = {'X-MethodPal-Request': '1', 'Origin': args.origin or args.base_url}

def checked(response, expected=200):
    assert response.status_code == expected, (response.status_code, response.text[:400])
    return response.json()

with httpx.Client(base_url=args.base_url, verify=args.ca_file, timeout=120, headers=headers, trust_env=False) as a, \
        httpx.Client(base_url=args.base_url, verify=args.ca_file, timeout=120, headers=headers, trust_env=False) as b:
    assert a.get('/').status_code == 200
    assert checked(a.get('/api/health'))['ok']
    assert a.get('/api/projects').status_code == 401
    creds = {'username': 'deploytest_' + secrets.token_hex(5), 'password': secrets.token_urlsafe(24)}
    registration = a.post('/api/auth/register', json=creds)
    auth = checked(registration, 201)
    assert '__Host-methodpal_session=' in registration.headers['set-cookie']
    assert 'secure' in registration.headers['set-cookie'].lower()
    a.headers['X-CSRF-Token'] = auth['csrf_token']
    assert checked(a.get('/api/auth/me'))['user']['username'] == creds['username']
    project = checked(a.post('/api/projects', json={'name': 'Deployment smoke test'}))
    csv = 'x,z,y\n' + ''.join(f'{i},{i % 5},{2*i+3*(i%5)+1}\n' for i in range(1, 101))
    dataset = checked(a.post('/api/datasets/upload', files={'file': ('smoke.csv', csv.encode(), 'text/csv')}))
    pid, did = project['project_id'], dataset['dataset_id']
    checked(a.patch('/api/projects/' + pid + '/workspace', json={'dataset_id': did}))
    request = {'dataset_id': did, 'model': 'xgboost_regression', 'dependent_variable': 'y',
               'independent_variables': ['x', 'z'], 'options': {'n_estimators': 30}}
    analysis = checked(a.post('/api/analysis/run', json=request))
    assert analysis['model'] == 'xgboost_regression' and analysis['sample_size'] > 0
    checked(a.get('/api/analysis/' + analysis['analysis_id']))
    queued = checked(a.post('/api/jobs', json={'kind':'analysis','payload':{**request, 'project_id':pid}}))
    deadline = time.monotonic() + 60
    while queued['status'] in ('queued','running') and time.monotonic() < deadline:
        time.sleep(.3)
        queued = checked(a.get('/api/jobs/' + queued['job_id']))
    assert queued['status'] == 'completed', queued
    analysis = queued['result']
    report = checked(a.post('/api/reports/generate', json={
        'project_id': pid, 'dataset_id': did, 'analysis_id': analysis['analysis_id'],
        'format': 'markdown', 'title': 'Deployment smoke report'}))
    assert report.get('report_id')
    for fmt, magic in [('pdf', b'%PDF'), ('docx', b'PK')]:
        exported = a.get('/api/reports/' + report['report_id'] + '/download', params={'format':fmt})
        assert exported.status_code == 200 and exported.content.startswith(magic)
    assert any(item['report_id'] == report['report_id'] for item in checked(a.get('/api/reports', params={'project_id':pid}))['items'])
    agent = checked(a.post('/api/agent/message', json={
        'message': '预测y，根据现有变量完成分析', 'mode': 'autopilot', 'action': 'autopilot',
        'context': {'project_id': pid, 'dataset_id': did, 'selected_model': 'xgboost_regression',
                    'research_goal': '预测y', 'analysis_goal': 'prediction',
                    'selected_variables': {'dependent_variable': 'y', 'independent_variables': ['x', 'z']},
                    'model_options': {'n_estimators': 30}},
        'answers': {'research_question': '预测y', 'analysis_goal': 'prediction', 'target_variable': 'y'}}))
    assert agent['ok'], agent
    print('Agent status:', agent['status'], '; questions:', len(agent.get('questions', [])))
    other = {'username': 'deploytest_' + secrets.token_hex(5), 'password': secrets.token_urlsafe(24)}
    auth_b = checked(b.post('/api/auth/register', json=other), 201)
    b.headers['X-CSRF-Token'] = auth_b['csrf_token']
    assert b.get('/api/projects/' + pid).status_code == 404
    assert b.get('/api/datasets/' + did).status_code == 404
    assert b.get('/api/analysis/' + analysis['analysis_id']).status_code == 404
    assert b.get('/api/jobs/' + queued['job_id']).status_code == 404
    assert b.get('/api/reports/' + report['report_id'] + '/download?format=pdf').status_code == 404
    assert a.post('/api/projects', headers={'X-CSRF-Token': 'invalid'}, json={'name': 'must fail'}).status_code == 403
    checked(a.post('/api/auth/logout'))
    assert a.get('/api/projects').status_code == 401
    auth = checked(a.post('/api/auth/login', json=creds))
    a.headers['X-CSRF-Token'] = auth['csrf_token']
    assert checked(a.get('/api/projects/' + pid))['dataset_id'] == did
    checked(a.post('/api/auth/logout'))
    checked(b.post('/api/auth/logout'))
    print('PASS: TLS, frontend, health, registration, secure session, upload, XGBoost, report, Agent API, user isolation, CSRF, logout/login persistence')
