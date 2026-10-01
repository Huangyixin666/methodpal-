"""Isolated regression for job guards, result publication and correlation safety."""
import os
import sys
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

ROOT = tempfile.TemporaryDirectory(prefix='methodpal-job-guards-')
os.environ.update(METHODPAL_ENV='development', METHODPAL_DATA_ROOT=ROOT.name,
    METHODPAL_DATABASE_PATH=str(Path(ROOT.name) / 'test.sqlite3'), METHODPAL_JOB_WORKER='0',
    METHODPAL_CAPTCHA_REQUIRED='0', METHODPAL_EMAIL_REQUIRED='0', METHODPAL_REGISTRATION='open',
    METHODPAL_ALLOWED_HOSTS='testserver', METHODPAL_LOG_LEVEL='WARNING', DEEPSEEK_API_KEY='')
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fastapi.testclient import TestClient
from main import app
from app.services import background_jobs as jobs
from app.services.job_progress import job_id_context
from app.services.user_context import set_current_user_id, reset_current_user_id
from app.services.store import PROJECTS, JOBS, ANALYSES


class JobWorkspaceTests(unittest.TestCase):
    def checked(self, response, status=200):
        self.assertEqual(response.status_code, status, response.text[:200])
        return response.json()

    def setUp(self):
        self.client = TestClient(app, headers={'X-MethodPal-Request':'1'})
        self.client.__enter__()
        auth = self.checked(self.client.post('/api/auth/register', json={
            'username':'job_' + uuid.uuid4().hex[:12], 'password':'isolated-test-password-123'}), 201)
        self.owner = auth['user']['id']
        self.client.headers['X-CSRF-Token'] = auth['csrf_token']
        self.pid = self.checked(self.client.post('/api/projects', json={'name':'Synthetic test'}))['project_id']
        self.did = self.checked(self.client.post('/api/datasets/paste', json={'headers':['x','y','constant'],
            'rows':[[str(i),str(2*i + i%3), '1'] for i in range(1,41)]}))['dataset_id']
        self.selection = {'dependent_variable':'y', 'independent_variables':['x'],
            'variable_types':{'x':'连续型','y':'连续型','constant':'连续型'}}
        self.update({'dataset_id':self.did,'selected_model':'linear_regression',
            'selected_variables':self.selection,'model_options':{}})

    def tearDown(self):
        self.client.__exit__(None,None,None)

    def update(self, changes):
        return self.checked(self.client.patch('/api/projects/'+self.pid+'/workspace',json=changes))

    def submit(self, kind='analysis', payload=None):
        request = payload or {'project_id':self.pid,'dataset_id':self.did,'model':'linear_regression',**self.selection}
        return self.checked(self.client.post('/api/jobs',json={'kind':kind,'payload':request}))

    def execute(self, job):
        token = set_current_user_id(self.owner)
        context = job_id_context.set(None)
        try:
            jobs.execute_worker(self.owner, job['job_id'])
        finally:
            job_id_context.reset(context)
            reset_current_user_id(token)
        return self.checked(self.client.get('/api/jobs/'+job['job_id']))

    def test_metadata_autosave_does_not_discard_results(self):
        job = self.submit()
        self.update({'progress':70,'data_review_status':'reviewed'})
        done = self.execute(job)
        self.assertEqual(done['status'],'completed')
        self.assertFalse(done['workspace_changed'])
        project = self.checked(self.client.get('/api/projects/'+self.pid))
        self.assertEqual(project['analysis_id'],done['result']['analysis_id'])
        self.update({'progress':95,'ai_explanation':{'text':'A display-only update'}})
        self.assertFalse(self.checked(self.client.get('/api/jobs/'+job['job_id']))['workspace_changed'])

    def test_real_model_and_type_changes_keep_results_historical(self):
        for change in ({'selected_model':'ridge_regression'}, {'selected_variables':{**self.selection,'independent_variables':[]}},
                       {'selected_variables':{**self.selection,'variable_types':{**self.selection['variable_types'],'x':'文本型'}}},
                       {'model_options':{'alpha':.5}}):
            self.update({'selected_model':'linear_regression','selected_variables':self.selection,'model_options':{},'analysis_id':None})
            job = self.submit()
            self.update(change)
            done = self.execute(job)
            self.assertTrue(done['workspace_changed'])
            self.assertIsNone(self.checked(self.client.get('/api/projects/'+self.pid))['analysis_id'])

    def test_dataset_content_change_is_not_a_timestamp(self):
        job = self.submit()
        self.checked(self.client.patch('/api/datasets/'+self.did+'/cells',json={'changes':[{'row':0,'col':0,'value':'999'}]}))
        self.assertEqual(self.execute(job)['status'],'failed')

    def test_atomic_publication_keeps_metadata_and_refuses_changed_inputs(self):
        job = self.submit()
        token=set_current_user_id(self.owner); context=job_id_context.set(job['job_id'])
        try:
            project=dict(PROJECTS[self.pid]); project['name']='Renamed';PROJECTS[self.pid]=project
            self.assertEqual(jobs.publish_workspace_changes(self.pid,{'progress':85})['name'],'Renamed')
            project=dict(PROJECTS[self.pid]);project['selected_model']='ridge_regression';PROJECTS[self.pid]=project
            self.assertIsNone(jobs.publish_workspace_changes(self.pid,{'analysis_id':'must_not_publish'}))
            self.assertIsNone(PROJECTS[self.pid].get('analysis_id'))
        finally:
            job_id_context.reset(context);reset_current_user_id(token)

    def test_agent_can_publish_its_own_plan_and_result(self):
        payload={'action':'autopilot','mode':'autopilot','message':'',
            'answers':{'research_question':'解释y与x的关联','analysis_goal':'explanation',
                       'target_variable':'y','target_type':'continuous','predictor_variables':['x']},
            'context':{'project_id':self.pid,'dataset_id':self.did,'selected_model':'linear_regression','selected_variables':self.selection}}
        done=self.execute(self.submit('agent',payload))
        self.assertEqual(done['result']['status'],'completed',done)
        self.assertFalse(done['workspace_changed'])
        self.assertEqual(self.checked(self.client.get('/api/projects/'+self.pid))['analysis_id'],done['result']['execution_result']['analysis_id'])

    def test_report_guard_tracks_analysis_pointer_but_analysis_guard_does_not(self):
        token=set_current_user_id(self.owner)
        try:
            project=PROJECTS[self.pid]; changed={**project,'analysis_id':'new_analysis'}
            self.assertEqual(jobs.workspace_fingerprint(project,'analysis'),jobs.workspace_fingerprint(changed,'analysis'))
            self.assertNotEqual(jobs.workspace_fingerprint(project,'report'),jobs.workspace_fingerprint(changed,'report'))
        finally: reset_current_user_id(token)

    def test_correlation_completes_with_constant_field_warning(self):
        selection={'independent_variables':['x','y','constant'],'variable_types':self.selection['variable_types']}
        self.update({'selected_model':'correlation_analysis','selected_variables':selection})
        done=self.execute(self.submit(payload={'project_id':self.pid,'dataset_id':self.did,'model':'correlation_analysis',**selection}))
        self.assertEqual(done['status'],'completed',done)
        self.assertFalse(done['workspace_changed'])
        self.assertEqual(len(done['result']['correlation_matrix']),3)
        self.assertEqual(len(done['result']['result_table']),1)
        self.assertTrue(done['result']['warnings'])

    def test_oversized_correlation_returns_actionable_error(self):
        from app.schemas.analysis import AnalysisRunRequest
        from app.services.model_tools import correlation_analysis
        import pandas as pd
        columns=['x'+str(i) for i in range(101)]
        with patch.object(correlation_analysis,'dataset_to_dataframe',return_value=(pd.DataFrame([[1]*101]*3,columns=columns),{})):
            from fastapi import HTTPException
            with self.assertRaises(HTTPException) as caught:
                correlation_analysis.run(AnalysisRunRequest(dataset_id='synthetic',model='correlation_analysis'))
            self.assertIn('100',caught.exception.detail)

    def test_poll_lists_summaries_but_detail_keeps_full_results(self):
        done=self.execute(self.submit())
        with patch.object(jobs.DATASETS,'get',side_effect=AssertionError('A modern poll must not materialize dataset cells')):
            with patch.object(jobs,'workspace_fingerprint',wraps=jobs.workspace_fingerprint) as fingerprint:
                token=set_current_user_id(self.owner)
                try:
                    job=JOBS[done['job_id']]
                    another={**job,'job_id':'synthetic-second-job'}
                    JOBS[another['job_id']]=another
                finally: reset_current_user_id(token)
                items=self.checked(self.client.get('/api/jobs',params={'project_id':self.pid}))['items']
                self.assertEqual(fingerprint.call_count,1)
        self.assertEqual(len(items),2)
        self.assertNotIn('coefficients',items[0]['result'])
        self.assertNotIn('source_snapshot',items[0]['result'])
        full=self.checked(self.client.get('/api/jobs/'+done['job_id']))
        self.assertEqual(full['result'],done['result'])
        self.assertFalse(full['workspace_changed'])


if __name__ == '__main__':
    try: unittest.main(verbosity=2)
    finally: ROOT.cleanup()
