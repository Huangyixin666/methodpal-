"""Isolated API regression, including real subprocess jobs and binary exports."""
import io
import json
import os
from pathlib import Path
import random
import secrets
import sys
import tempfile
import time
import zipfile

def main():
    root = tempfile.TemporaryDirectory(prefix='methodpal-hardening-')
    os.environ.update(METHODPAL_DATA_ROOT=root.name, METHODPAL_ENV='development',
        METHODPAL_ALLOWED_ORIGINS='http://testserver', METHODPAL_ALLOWED_HOSTS='testserver,localhost,127.0.0.1',
        METHODPAL_JOB_WORKER='1')
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from fastapi.testclient import TestClient
    from main import app
    from app.services.background_jobs import stop, start
    from app.services.user_context import set_current_user_id, reset_current_user_id
    from app.services.store import JOBS
    def checked(r, status=200):
        assert r.status_code == status, (r.status_code, r.text[:500])
        return r.json()
    with TestClient(app) as c, TestClient(app) as b:
        for client in (c, b):
            client.headers['X-MethodPal-Request'] = '1'
            auth = checked(client.post('/api/auth/register', json={'username': 'regression_' + secrets.token_hex(5),
                'password': secrets.token_urlsafe(24)}), 201)
            client.headers['X-CSRF-Token'] = auth['csrf_token']
        owner = checked(c.get('/api/auth/me'))['user']['id']
        pid = checked(c.post('/api/projects', json={'name': '版本和导出回归测试'}))['project_id']
        rng = random.Random(2026)
        rows = []
        for i in range(400):
            x1, x2 = rng.gauss(0, 1), rng.gauss(0, 1)
            rows.append([str(x1), str(x2), str(2*x1-x2+rng.gauss(0,.2)), str(i % 5 + 1)])
        ds = checked(c.post('/api/datasets/paste', json={'name': '独立模拟数据',
            'headers': ['x1','x2','y','rating'], 'rows': rows}))
        did = ds['dataset_id']
        assert ds['data_fingerprint'] and ds['dataset_revision'] == 1
        selected = {'dependent_variable': 'y', 'independent_variables': ['x1','x2'], 'control_variables': [],
            'variable_types': {'x1':'连续型','x2':'连续型','y':'连续型','rating':'有序型'}}
        options = {'n_estimators': 20, 'cv_folds': 2}
        checked(c.patch('/api/projects/'+pid+'/workspace', json={'dataset_id': did}))
        def agent(context, message='自动完成', answers=None, thread=None):
            return checked(c.post('/api/agent/message', json={'mode':'autopilot','action':'autopilot',
                'message':message, 'context': {'project_id':pid, 'dataset_id':did, **context},
                'answers': answers or {}, 'thread_id': thread}))
        paused = agent({})
        assert paused['status'] == 'waiting_for_user'
        ctx = {'selected_model':'linear_regression','selected_variables': selected,
               'data_review_status':'reviewed','model_options': options}
        done = agent(ctx, '', {'research_question':'预测y','analysis_goal':'prediction',
            'target_variable':'y','target_type':'continuous','predictor_variables':['x1','x2']}, paused['thread_id'])
        assert done['status'] == 'completed' and done['execution_result']['model'] == 'linear_regression', done
        aid = done['execution_result']['analysis_id']
        repeated = agent(ctx)
        assert repeated['execution_result']['analysis_id'] == aid and not repeated['questions'], repeated
        changed = agent({**ctx, 'selected_model':'xgboost_regression'}, '把模型换成线性回归并重新运行')
        assert changed['execution_result']['model'] == 'linear_regression', changed
        changed_vars = agent(ctx, '自变量改为x1，并重新运行')
        assert changed_vars['execution_result']['independent_variables'] == ['x1'], changed_vars
        unknown = agent(ctx, '模型换成一个不存在的模型')
        assert unknown['status'] == 'waiting_for_user' and unknown['questions'][0]['id'] == 'change_request'
        type_question = agent({'research_spec': {'research_question': '预测rating', 'analysis_goal': 'prediction',
            'target_variable':'rating'}, 'selected_variables': {'dependent_variable':'rating'}, 'data_review_status':'pending'})
        assert type_question['status'] == 'waiting_for_user' and type_question['questions'][0]['id'] == 'target_type', type_question
        request = {'project_id':pid, 'dataset_id':did, 'model':'xgboost_regression', **selected, 'options':options}
        a = checked(c.post('/api/analysis/run', json=request))
        aid = a['analysis_id']
        checked(c.patch('/api/projects/'+pid+'/workspace', json={'analysis_id':aid, 'selected_model':a['model'],
            'selected_variables':selected, 'model_options':options}))
        importance = checked(c.post('/api/charts/generate', json={'dataset_id':did,'analysis_id':aid,
            'chart_type':'feature_importance_bar'}))['chart']
        corr = checked(c.post('/api/charts/generate', json={'dataset_id':did,'chart_type':'correlation_heatmap'}))['chart']
        assert 'rating' not in corr['column_labels']
        assert importance['meta']['scope'] == 'analysis' and corr['meta']['scope'] == 'dataset'
        chart_ids = [importance['chart_id'], corr['chart_id']]
        report = checked(c.post('/api/reports/generate', json={'project_id':pid,'dataset_id':did,'analysis_id':aid,
            'title':'版本修复与中文图表导出验证', 'report_mode':'template','chart_ids':chart_ids,'format':'pdf'}))
        assert report['download_url'] and report['source_snapshot']['data_fingerprint'] == ds['data_fingerprint']
        out = Path(os.getenv('METHODPAL_EXPORT_QA_DIR', str(Path(__file__).resolve().parents[2] / 'tmp' / 'export-qa')))
        out.mkdir(parents=True, exist_ok=True)
        for fmt in ('docx','pdf','markdown'):
            r = c.get('/api/reports/'+report['report_id']+'/download?format='+fmt)
            assert r.status_code == 200, r.text[:300]
            if fmt == 'pdf': assert r.content.startswith(b'%PDF')
            if fmt == 'docx':
                archive = zipfile.ZipFile(io.BytesIO(r.content))
                assert any(name.startswith('word/media/') for name in archive.namelist())
            if fmt == 'markdown': assert b'data:image/png;base64,' in r.content
            (out / ('sample.' + ('md' if fmt == 'markdown' else fmt))).write_bytes(r.content)
            assert b.get('/api/reports/'+report['report_id']+'/download?format='+fmt).status_code == 404
        for fmt in ('png', 'svg'):
            r = c.get('/api/charts/'+importance['chart_id']+'/download?format='+fmt)
            assert r.status_code == 200
            assert r.content.startswith(b'\x89PNG') if fmt == 'png' else b'<svg' in r.content
            (out / ('chart.' + fmt)).write_bytes(r.content)
            assert b.get('/api/charts/'+importance['chart_id']+'/download?format='+fmt).status_code == 404
        assert len(checked(c.get('/api/charts', params={'project_id':pid}))['items']) >= 2
        assert checked(c.get('/api/reports', params={'project_id':pid}))['items'][0]['report_id'] == report['report_id']
        checked(c.put('/api/reports/'+report['report_id']+'/draft', json={'content': '# 保存到服务器的草稿\n\n跨设备恢复。'}))
        assert checked(c.get('/api/reports/'+report['report_id']))['draft_content'].startswith('# 保存')
        edited = checked(c.patch('/api/datasets/'+did+'/cells', json={'changes':[{'row':0,'col':0,'value':'999'}]}))
        assert edited['dataset_revision'] > ds['dataset_revision'] and edited['data_fingerprint'] != ds['data_fingerprint']
        assert c.post('/api/charts/generate',json={'analysis_id':aid,'chart_type':'feature_importance_bar'}).status_code == 409
        assert c.post('/api/reports/generate',json={'dataset_id':did,'analysis_id':aid}).status_code == 409
        fresh = checked(c.post('/api/analysis/run', json=request))
        filtered = checked(c.post('/api/reports/generate', json={'project_id':pid,'dataset_id':did,
            'analysis_id':fresh['analysis_id'],'chart_ids':chart_ids}))
        assert not filtered['charts'] and filtered['warnings'], filtered
        assert c.get('/api/reports/'+report['report_id']+'/download?format=pdf').status_code == 200
        page = checked(c.get('/api/datasets/'+did+'/rows',params={'offset':250,'limit':20}))
        assert len(page['cells']) == 20 and page['cells'][0][0]['value'] == rows[250][0]
        assert c.post('/api/reports/generate',json={'format':'html'}).status_code == 422
        def wait_job(j):
            end = time.monotonic()+40
            while j['status'] in ('queued','running') and time.monotonic() < end:
                time.sleep(.25)
                j = checked(c.get('/api/jobs/'+j['job_id']))
            assert j['status'] == 'completed', j
            return j
        job_body = {'kind':'analysis','payload':{**request,'model':'linear_regression'},'idempotency_key':'same-request'}
        job = checked(c.post('/api/jobs',json=job_body))
        duplicate = checked(c.post('/api/jobs',json=job_body))
        assert duplicate['job_id'] == job['job_id']
        assert b.get('/api/jobs/'+job['job_id']).status_code == 404
        assert b.post('/api/jobs/'+job['job_id']+'/cancel').status_code == 404
        finished = wait_job(job)
        assert finished['result']['model'] == 'linear_regression'
        assert checked(c.get('/api/projects/'+pid))['analysis_id'] == finished['result']['analysis_id']
        stop()
        before_id = checked(c.get('/api/projects/'+pid))['analysis_id']
        changed_job = checked(c.post('/api/jobs',json={'kind':'analysis','payload':request}))
        checked(c.patch('/api/projects/'+pid+'/workspace',json={'selected_model':'ridge_regression'}))
        start()
        changed_result = wait_job(changed_job)
        assert changed_result['workspace_changed'] is True
        assert checked(c.get('/api/projects/'+pid))['analysis_id'] == before_id
        # Stop scheduling to deterministically cover queued cancellation/retry and recovery.
        stop()
        queued = checked(c.post('/api/jobs',json={'kind':'analysis','payload':request}))
        cancelled = checked(c.post('/api/jobs/'+queued['job_id']+'/cancel'))
        assert cancelled['status'] == 'cancelled'
        retry = checked(c.post('/api/jobs/'+queued['job_id']+'/retry'))
        token = set_current_user_id(owner)
        try:
            fake_running = JOBS[retry['job_id']]
            fake_running['status'] = 'running'
            JOBS[retry['job_id']] = fake_running
        finally:
            reset_current_user_id(token)
        start()
        assert checked(c.get('/api/jobs/'+retry['job_id']))['status'] == 'interrupted'
        retried = checked(c.post('/api/jobs/'+retry['job_id']+'/retry'))
        wait_job(retried)
        # Running cancellation stops its subprocess, not merely the browser's wait.
        slow = checked(c.post('/api/jobs',json={'kind':'analysis','payload':{**request,
            'options':{'n_estimators':3000,'max_depth':8,'cv_folds':5}}}))
        for _ in range(40):
            state = checked(c.get('/api/jobs/'+slow['job_id']))
            if state['status'] == 'running': break
            time.sleep(.1)
        assert state['status'] == 'running', state
        assert checked(c.post('/api/jobs/'+slow['job_id']+'/cancel'))['status'] == 'cancelled'
        assert checked(c.get('/api/jobs/'+slow['job_id']))['status'] == 'cancelled'
        print(json.dumps({'ok':True,'checks':['paused_context_merge','repeat_goal','model_change','variable_change',
            'unclear_change','ordinal_confirmation','provenance','stale_analysis_rejection','stale_chart_filter',
            'docx_pdf_md','png_svg','history','draft_sync','row_paging','job_idempotency','job_execution',
            'job_cancellation','restart_recovery','workspace_change_guard','cross_account_isolation'],'qa_dir':str(out)}, ensure_ascii=False))
    root.cleanup()

if __name__ == '__main__':
    main()
