"""Isolated import -> project attachment -> logout/login persistence checks."""
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main():
    with tempfile.TemporaryDirectory(prefix='methodpal-import-') as root:
        os.environ.update(METHODPAL_DATA_ROOT=root, METHODPAL_ENV='development',
                          METHODPAL_ALLOWED_HOSTS='testserver', METHODPAL_JOB_WORKER='0',
                          METHODPAL_CAPTCHA_REQUIRED='0', METHODPAL_EMAIL_REQUIRED='0',
                          METHODPAL_REGISTRATION='open')
        os.environ.pop('METHODPAL_DATABASE_PATH', None)
        from fastapi.testclient import TestClient
        from main import app

        def checked(response, status=200):
            assert response.status_code == status, (response.status_code, response.text[:300])
            return response.json()

        with TestClient(app) as a, TestClient(app) as b:
            credentials = {'username': 'import_test', 'password': 'test-import-password-123'}
            a.headers['X-MethodPal-Request'] = '1'
            auth = checked(a.post('/api/auth/register', json=credentials), 201)
            a.headers['X-CSRF-Token'] = auth['csrf_token']
            b.headers['X-MethodPal-Request'] = '1'
            other = checked(b.post('/api/auth/register', json={**credentials, 'username': 'other_import_test'}), 201)
            b.headers['X-CSRF-Token'] = other['csrf_token']
            saved = []
            for kind in ('sample', 'paste', 'upload'):
                project = checked(a.post('/api/projects', json={'name': kind + '导入项目'}))
                if kind == 'sample':
                    dataset = checked(a.post('/api/datasets/sample'))
                elif kind == 'paste':
                    dataset = checked(a.post('/api/datasets/paste', json={
                        'name': 'manual', 'headers': ['编号', '年龄', '家庭支持'],
                        'rows': [['1', '20', '4.2'], ['2', '', '3.5'], ['3', '22', '']]}))
                    assert dataset['profile']['row_count'] == 3
                    assert dataset['profile']['missing_count'] == 2
                else:
                    dataset = checked(a.post('/api/datasets/upload', files={
                        'file': ('test.csv', '年龄,得分\n20,72\n22,65\n'.encode('utf-8'), 'text/csv')}))
                    assert dataset['profile']['row_count'] == 2
                pid, did = project['project_id'], dataset['dataset_id']
                attached = checked(a.patch('/api/projects/' + pid + '/workspace', json={
                    'dataset_id': did, 'selected_variables': None, 'selected_model': None,
                    'model_options': {}, 'data_review_status': 'review_required',
                    'dataset_version': 1, 'analysis_id': None, 'ai_explanation': None, 'progress': 35}))
                assert attached['dataset_id'] == did
                checked(b.get('/api/projects/' + pid), 404)
                checked(b.get('/api/datasets/' + did), 404)
                saved.append((pid, did, dataset['profile']['row_count']))
                print('PASS', kind, 'import, durable project attachment, owner isolation')
            scopes = ['no_project', saved[0][0], saved[1][0]]
            for scope in scopes:
                checked(a.put('/api/agent/history/' + scope, json={
                    'project_id': scope, 'messages': [{'role': 'user', 'content': 'only-in-' + scope}]}))
            checked(a.post('/api/auth/logout'))
            checked(a.get('/api/projects'), 401)
            auth = checked(a.post('/api/auth/login', json=credentials))
            a.headers['X-CSRF-Token'] = auth['csrf_token']
            for pid, did, count in saved:
                project = checked(a.get('/api/projects/' + pid))
                assert project['dataset_id'] == did and project['dataset_version'] == 1
                dataset = checked(a.get('/api/datasets/' + project['dataset_id']))
                assert dataset['profile']['row_count'] == count
            for scope in scopes:
                history = checked(a.get('/api/agent/history/' + scope))
                assert len(history['messages']) == 1
                assert history['messages'][0]['content'] == 'only-in-' + scope
                if scope == 'no_project':
                    assert not checked(b.get('/api/agent/history/' + scope))['messages']
                else:
                    checked(b.get('/api/agent/history/' + scope), 404)
            checked(a.delete('/api/agent/history/' + scopes[1]))
            assert not checked(a.get('/api/agent/history/' + scopes[1]))['messages']
            for scope in (scopes[0], scopes[2]):
                assert checked(a.get('/api/agent/history/' + scope))['messages'][0]['content'] == 'only-in-' + scope
            print('PASS lobby and two project histories: independent, durable, owner-isolated, scoped clearing')
            print('PASS all three imports survive logout/login and fresh API reads')


if __name__ == '__main__':
    main()
