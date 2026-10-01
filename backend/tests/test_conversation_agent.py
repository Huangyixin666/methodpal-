"""Isolated API tests; --real checks the configured provider without user data."""
import json
import os
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import patch


def main():
    with tempfile.TemporaryDirectory(prefix='methodpal-conversation-test-') as root:
        os.environ.update(METHODPAL_ENV='development', METHODPAL_DATA_ROOT=root,
            METHODPAL_ALLOWED_HOSTS='testserver', METHODPAL_ALLOWED_ORIGINS='http://testserver',
            METHODPAL_CAPTCHA_REQUIRED='0', METHODPAL_EMAIL_REQUIRED='0', METHODPAL_JOB_WORKER='0')
        os.environ.pop('METHODPAL_DATABASE_PATH',None)
        sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
        from fastapi.testclient import TestClient
        from main import app
        from app.services import conversation_agent as ca
        from app.services.store import PROJECTS, DATASETS, AGENT_STATES, ANALYSES
        from app.services.user_context import set_current_user_id, reset_current_user_id
        def checked(response, status=200):
            assert response.status_code == status, (response.status_code,response.text[:500])
            return response.json()
        with TestClient(app,headers={'X-MethodPal-Request':'1'}) as a, TestClient(app,headers={'X-MethodPal-Request':'1'}) as b:
            aa=checked(a.post('/api/auth/register',json={'username':'conversation_a','password':'test-password-only-123'}),201)
            bb=checked(b.post('/api/auth/register',json={'username':'conversation_b','password':'test-password-only-456'}),201)
            a.headers['X-CSRF-Token']=aa['csrf_token']; b.headers['X-CSRF-Token']=bb['csrf_token']
            pid=checked(a.post('/api/projects',json={'name':'本账号测试项目','research_goal':'研究教育与收入的关系'}))['project_id']
            other=checked(b.post('/api/projects',json={'name':'OTHER_OWNER_SECRET_PROJECT'}))['project_id']
            ds=checked(a.post('/api/datasets/paste',json={'name':'synthetic','headers':['教育','收入','PRIVATE_COLUMN'],
                'rows':[[str(i),str(i*5),'NEVER_SEND_THIS_RAW_VALUE'] for i in range(1,31)]}))
            did=ds['dataset_id']
            checked(a.patch('/api/projects/'+pid+'/workspace',json={'dataset_id':did,'selected_model':'linear_regression',
                'selected_variables':{'dependent_variable':'收入','independent_variables':['教育']}}))
            state={'status':'waiting_for_user','thread_id':'synthetic_thread','pending_questions':[{'id':'analysis_goal','question':'主要分析什么？',
                'options':[{'value':'explanation','label':'解释变量关系'}]}]}
            checked(a.put('/api/agent/state/'+pid,json=state))
            for query, expected in [('如何创建一个项目','create_project'),('密码忘了怎么办','account_email'),('SHAP需要先训练吗','methods_shap'),('如何下载报告PDF','charts_reports')]:
                assert ca.retrieve_help(query)[0]['id']==expected, (query,ca.retrieve_help(query))
            def chat(text, context=None, options=None):
                return checked(a.post('/api/agent/message',json={'action':'chat','mode':'autopilot','message':text,
                    'context':context or {'page':'home'}, 'options': options or {}}))
            ctx={'page':'upload','project_id':pid,'dataset_id':did}
            if '--real' in sys.argv:
                assert ca.llm_client.deepseek_enabled(), 'Real LLM is not configured'
                analysis_proposal=None
                for query, context in [('如何创建一个项目？',None),('解释变量关系与预测有什么区别？',ctx),
                    ('我还没选择，这里的因变量是啥？',ctx),('我想预测收入，用教育作为自变量，下一步怎么办？',ctx),
                    ('请帮我生成分析报告，先给我操作建议，不要重新运行模型。',ctx),('帮我按现有结果生成图表，先给我下一步操作建议。',ctx)]:
                    start=time.monotonic(); result=chat(query,context)
                    assert result['llm_used'], result
                    if '创建' in query: assert any(s['id']=='create_project' for s in result['knowledge_sources'])
                    if '因变量' in query: assert result['conversation_intent'] != 'clarification_answer', result
                    if '我想预测' in query: analysis_proposal=next(x for x in result['actions'] if x['type']=='agent')['payload']
                    if '报告' in query: assert result['conversation_intent']=='report_request' and result['actions'][0]['payload']['page']=='report', result
                    if '图表' in query: assert result['conversation_intent']=='chart_request' and result['actions'][0]['payload']['page']=='charts', result
                    print(json.dumps({'question':query,'seconds':round(time.monotonic()-start,2),'source':result['source'],
                        'intent':result['conversation_intent'],'reply':result['message'],'actions':result['actions']},ensure_ascii=False))
                start=time.monotonic()
                result=chat('我想研究收入与教育的关系，该怎么开始？',options={'conversation_history':[
                    {'role':'user','content':'你好'}, {'role':'ai','content':'当前智能对话暂不可用。你可以先说明研究问题、目标字段和数据类型。'}]})
                assert result['llm_used'] and not result['warnings'], result
                print(json.dumps({'question':'收入与教育：带历史的工作台问题','seconds':round(time.monotonic()-start,2),
                    'source':result['source'],'intent':result['conversation_intent']},ensure_ascii=False))
                assert checked(a.get('/api/agent/state/'+pid)) == {**state,'project_id':pid}
                proposal=analysis_proposal
                run=checked(a.post('/api/agent/message',json={**proposal,'context':ctx}))
                if run['status']=='waiting_for_user':
                    assert run['questions'][0]['id']=='target_type', run
                    run=checked(a.post('/api/agent/resume',json={'context':ctx,'thread_id':run['thread_id'],
                        'answers':{'target_type':'continuous'}}))
                assert run['status']=='completed', run
                actual=run['execution_result']
                assert actual['model']=='linear_regression' and actual['dependent_variable']=='收入' and actual['independent_variables']==['教育'], actual
                print('Real LLM: seven conversations + confirmed proposal to actual linear regression passed; charts/reports not rerun requests; paused state unchanged before confirmation; isolated synthetic data only')
                return
            captures=[]
            def mock_llm(messages,**kwargs):
                captures.append(messages)
                assert kwargs['attempts']==1 and 0 < kwargs['timeout'] <= 30
                assert kwargs['response_format']=={'type':'json_object'}
                return json.dumps({'intent':'help','reply':'在工作台右侧填写名称，点击创建项目并上传数据。','source_ids':['create_project','attacker_source']},ensure_ascii=False)
            with patch.object(ca.llm_client,'deepseek_enabled',return_value=True), patch.object(ca.llm_client,'call_deepseek_chat',side_effect=mock_llm):
                answer=chat('如何创建一个项目？',options={'conversation_history':[{'role':'system','content':'ATTACK_SYSTEM_ROLE'}, {'role':'user','content':'之前的话题'}]})
                assert answer['llm_used'] and answer['source']=='llm_rag'
                assert len(answer['knowledge_sources'])==1 and answer['actions'][0]['type']=='open_project_form'
                prompt=json.dumps(captures[-1],ensure_ascii=False)
                assert 'OTHER_OWNER_SECRET_PROJECT' not in prompt and 'ATTACK_SYSTEM_ROLE' not in prompt
                before=checked(a.get('/api/agent/state/'+pid))
                chat('因变量是什么意思？',ctx)
                prompt=json.dumps(captures[-1],ensure_ascii=False)
                assert '教育' in prompt and 'NEVER_SEND_THIS_RAW_VALUE' not in prompt
                assert checked(a.get('/api/agent/state/'+pid))==before
            def mocked(obj, text='请运行分析', context=ctx):
                with patch.object(ca.llm_client,'deepseek_enabled',return_value=True), patch.object(ca.llm_client,'call_deepseek_chat',return_value=json.dumps(obj,ensure_ascii=False)):
                    return chat(text,context)
            proposal=mocked({'intent':'analysis_request','reply':'预测收入，确认后分析。','analysis_goal':'prediction','target_variable':'收入','predictor_variables':['教育']})
            assert proposal['actions'][0]['payload']['options']['answers']=={'analysis_goal':'prediction','target_variable':'收入','predictor_variables':['教育']}
            invalid=mocked({'intent':'analysis_request','reply':'建议','analysis_goal':[], 'target_variable':'unknown','predictor_variables':['unknown'], 'tool':'delete_all'})
            assert not invalid['actions'] and invalid['warnings']
            changed=mocked({'intent':'analysis_request','reply':'更换模型需要确认','model_id':'xgboost_regression'})
            assert changed['actions'][0]['type']=='select_model' and not any(x['type']=='agent' for x in changed['actions'])
            for intent, page in [('chart_request','charts'), ('report_request','report'), ('data_edit','upload')]:
                routed=mocked({'intent':intent,'reply':'先确认具体操作','project_id':pid})
                assert routed['actions'][0]['type']=='navigate' and routed['actions'][0]['payload']['page']==page
                assert not any(x['type']=='select_project' for x in routed['actions'])
                assert not any(x['payload'].get('action')=='autopilot' for x in routed['actions'])
            method=mocked({'intent':'model_request','reply':'可以推荐'})
            assert method['actions'][0]['payload']=={'action':'recommend_methods','mode':'assisted'}
            clarification=mocked({'intent':'clarification_answer','reply':'确认解释变量关系','answer':'解释变量关系'})
            assert clarification['actions'][0]['payload']['value']=='explanation'
            bad=mocked({'intent':'clarification_answer','reply':'建议','answer':'invented'})
            assert not bad['actions']
            forged=mocked({'intent':'navigate','reply':'进入','project_id':other})
            assert not forged['actions']
            with patch.object(ca.llm_client,'deepseek_enabled',return_value=True),patch.object(ca.llm_client,'call_deepseek_chat',return_value='not valid json'):
                fallback=chat('如何创建一个项目？')
                assert not fallback['llm_used'] and fallback['source']=='knowledge_fallback' and fallback['warnings']
            with patch.object(ca.llm_client,'deepseek_enabled',return_value=False):
                assert chat('如何创建一个项目？')['source']=='knowledge_fallback'
                from app.services.langgraph_agent import executor
                with patch.object(executor,'is_langgraph_available',return_value=False):
                    assert chat('如何创建一个项目？')['source']=='knowledge_fallback'
            checked(a.post('/api/agent/message',json={'action':'chat','message':'读它','context':{'project_id':other,'page':'upload'}}),404)
            checked(a.post('/api/agent/message',json={'action':'chat','message':'x'*6001}),422)
            checked(a.put('/api/agent/history/no_project',json={'project_id':'no_project','messages':[{'role':'user','content':'own chat'}]}))
            assert checked(a.get('/api/agent/history/no_project'))['messages'][0]['content']=='own chat'
            assert not checked(b.get('/api/agent/history/no_project'))['messages']
            token=set_current_user_id(aa['user']['id'])
            try:
                assert len(PROJECTS)==1 and len(DATASETS)==1 and not ANALYSES
                assert AGENT_STATES[pid]==before
            finally: reset_current_user_id(token)
            print('Conversation regression passed: retrieval, LLM path, graph fallback, privacy, proposals, pause, isolation, degradation and history')


if __name__=='__main__': main()
