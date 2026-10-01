"""Offline JSON-output regression tests: no provider credentials or user data."""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

_root = tempfile.TemporaryDirectory(prefix='methodpal-json-tests-')
os.environ.update(METHODPAL_ENV='development', METHODPAL_DATA_ROOT=_root.name)
os.environ.pop('METHODPAL_DATABASE_PATH', None)
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.services import llm_client as lc, conversation_agent as ca
import requests

VALID = json.dumps({'intent':'general','reply':'因变量是你想解释或预测的结果变量。','source_ids':[]}, ensure_ascii=False)


class ClientTests(unittest.TestCase):
    def setUp(self):
        self.session = MagicMock()
        self.response = self.session.post.return_value
        self.response.status_code = 200
        self.response.json.return_value = {'choices':[{'finish_reason':'stop','message':{'content':VALID}}]}
        self.key = patch.object(lc, 'DEEPSEEK_API_KEY', 'synthetic-test-key')
        self.factory = patch.object(lc.requests, 'Session', return_value=self.session)
        self.key.start(); self.factory.start()
        self.addCleanup(self.key.stop); self.addCleanup(self.factory.stop)

    def call(self, **kwargs):
        return lc.call_deepseek_chat([{'role':'user','content':'generic JSON question'}], **kwargs)

    def test_json_mode_is_explicit_and_sessions_close(self):
        self.assertEqual(self.call(response_format={'type':'json_object'}), VALID)
        self.assertEqual(self.session.post.call_args.kwargs['json']['response_format'], {'type':'json_object'})
        self.session.close.assert_called_once()

    def test_plain_reports_do_not_receive_json_mode(self):
        self.response.json.return_value['choices'][0]['message']['content'] = '# 报告\n正常文字'
        self.assertIn('# 报告', self.call())
        self.assertNotIn('response_format', self.session.post.call_args.kwargs['json'])

    def test_truncation_is_rejected_only_for_structured_output(self):
        self.response.json.return_value['choices'][0]['finish_reason'] = 'length'
        with self.assertRaises(lc.LLMError) as caught:
            self.call(response_format={'type':'json_object'})
        self.assertEqual(caught.exception.code, 'truncated_output')
        self.assertEqual(self.call(), VALID)

    def test_empty_output_is_explicit(self):
        self.response.json.return_value['choices'][0]['message']['content'] = '  '
        with self.assertRaises(lc.LLMError) as caught: self.call()
        self.assertEqual(caught.exception.code, 'empty_output')

    def test_invalid_provider_envelope_is_not_confused_with_reply_json(self):
        for value in ({}, {'choices':[]}, {'choices':[None]}):
            self.response.json.return_value = value
            with self.assertRaises(lc.LLMError) as caught: self.call()
            self.assertEqual(caught.exception.code, 'invalid_provider_response')
        self.response.json.side_effect = ValueError('sensitive-response-body')
        with self.assertRaises(lc.LLMError) as caught: self.call()
        self.assertEqual(caught.exception.code, 'invalid_provider_response')
        self.assertNotIn('sensitive', str(caught.exception))

    def test_http_error_is_not_retried_or_leaked(self):
        self.response.status_code = 402
        self.response.text = 'sensitive-provider-body'
        with self.assertRaises(lc.LLMError) as caught: self.call(attempts=3)
        self.assertEqual(caught.exception.status_code, 402)
        self.assertEqual(self.session.post.call_count, 1)
        self.assertNotIn('sensitive', str(caught.exception))

    def test_timeout_classification_and_bounded_attempts(self):
        self.session.post.side_effect = requests.Timeout('sensitive-network-detail')
        with self.assertRaises(lc.LLMError) as caught: self.call(attempts=2)
        self.assertEqual(caught.exception.code, 'timeout')
        self.assertEqual(self.session.post.call_count, 2)
        self.assertNotIn('sensitive', str(caught.exception))


class ConversationTests(unittest.TestCase):
    def setUp(self):
        self.req = SimpleNamespace(message='因变量是什么？')
        self.prepared = {'context':{'page':'home','projects':[]}, 'history':[]}
        self.enabled = patch.object(lc,'deepseek_enabled',return_value=True)
        self.limit = patch('app.services.account_verification.limited')
        self.owner = patch('app.services.user_context.get_current_user_id',return_value='unit-test-owner')
        self.enabled.start(); self.limit.start(); self.owner.start()
        self.addCleanup(self.enabled.stop); self.addCleanup(self.limit.stop); self.addCleanup(self.owner.stop)

    def run_reply(self, values):
        with patch.object(lc,'call_deepseek_chat',side_effect=values) as mock:
            result = ca.generate_conversation(self.req,self.prepared,[])
        return result, mock

    def test_malformed_output_gets_one_repair_request(self):
        (result, used, warning), mock = self.run_reply(['{"reply":"broken',VALID])
        self.assertTrue(used); self.assertFalse(warning); self.assertEqual(result['intent'],'general')
        self.assertEqual(mock.call_count, 2)
        for call in mock.call_args_list:
            self.assertEqual(call.kwargs['response_format'],{'type':'json_object'})
            self.assertEqual(call.kwargs['attempts'],1)
            self.assertLessEqual(call.kwargs['timeout'],30)
        self.assertIn('前一次回复未通过格式校验',mock.call_args_list[1].args[0][0]['content'])
        self.assertNotIn('broken', json.dumps(mock.call_args_list[1].args,ensure_ascii=False))

    def test_failed_retry_falls_back_without_accepting_invalid_tools(self):
        (result, used, warning), mock = self.run_reply(['not json','{"intent":"delete_all","reply":"已删除"}'])
        self.assertFalse(used); self.assertEqual(mock.call_count,2)
        self.assertEqual(result['intent'],'help'); self.assertNotIn('已删除',result['reply'])
        self.assertIn('模型已响应',warning); self.assertIn('未执行任何操作',warning)

    def test_non_hashable_intent_is_schema_failure_not_internal_exception(self):
        (result, used, warning), mock = self.run_reply(['{"intent":[],"reply":"x"}',VALID])
        self.assertTrue(used); self.assertEqual(mock.call_count,2)

    def test_empty_and_truncated_reply_can_recover(self):
        for code in ('empty_output','truncated_output','invalid_provider_response'):
            (_, used, _), mock = self.run_reply([lc.LLMError('safe',code=code,status_code=200),VALID])
            self.assertTrue(used); self.assertEqual(mock.call_count,2)

    def test_http_auth_balance_and_rate_failures_not_retried(self):
        for status, word in [(401,'身份验证'),(402,'余额不足'),(429,'过于频繁'),(503,'繁忙')]:
            (_, used, warning), mock = self.run_reply([lc.LLMError('safe',code='http_error',status_code=status)])
            self.assertFalse(used); self.assertEqual(mock.call_count,1); self.assertIn(word,warning)

    def test_network_timeout_does_not_trigger_format_retry(self):
        (_, used, warning), mock = self.run_reply([lc.LLMError('safe',code='timeout')])
        self.assertFalse(used); self.assertEqual(mock.call_count,1); self.assertIn('超时',warning)

    def test_time_budget_prevents_extra_retry_and_releases_slot(self):
        with patch.object(ca.time,'monotonic',side_effect=[0,0,40]):
            (_, used, _), mock = self.run_reply(['not json'])
        self.assertFalse(used); self.assertEqual(mock.call_count,1)
        (_, used, _), _ = self.run_reply([VALID])
        self.assertTrue(used)

    def test_code_fence_and_bom_are_safe_format_wrappers(self):
        for raw in ('\ufeff'+VALID, '```JSON\n'+VALID+'\n```'):
            self.assertEqual(ca._parse_conversation_reply(raw)['intent'],'general')
        with self.assertRaises(ValueError): ca._parse_conversation_reply('prefix '+VALID)

    def test_diagnostics_never_log_response_contents(self):
        secret = 'PRIVATE_RESPONSE_MARKER'
        with self.assertLogs(ca.log,level='WARNING') as logs:
            self.run_reply([secret,secret])
        self.assertNotIn(secret,' '.join(logs.output))
        self.assertIn('code=invalid_output',' '.join(logs.output))


if __name__ == '__main__':
    try: unittest.main(verbosity=2)
    finally: _root.cleanup()
