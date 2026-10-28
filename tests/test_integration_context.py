import unittest
from app.orchestrator.integration_context import repair_context,review_task_description
import json


class IntegrationContextTests(unittest.TestCase):
    def test_all_failures_for_owner_are_sent_without_unrelated_checks(self):
        failures=[{'path':'store.py','check_id':'normal','status':'FAIL'},
                  {'path':'store.py','check_id':'invalid','status':'FAIL'},
                  {'path':'main.py','check_id':'integration','status':'FAIL'}]
        contract={'checks':[{'id':'normal','argv':['python','-c','assert normal']},
                            {'id':'invalid','argv':['python','-c','assert invalid']},
                            {'id':'integration','argv':['python','main.py']}]}
        packet=repair_context([{'path':'store.py','content':'def add(title): return title'},
            {'path':'main.py','content':'def summarize(db):\n    return "unrelated body"'}], 'store.py',failures,contract)
        self.assertEqual([c['id'] for c in packet['failed_checks']],['normal','invalid'])
        self.assertIn('def summarize(db)',packet['sibling_interfaces'][0]['interfaces'])
        self.assertNotIn('unrelated body',str(packet['sibling_interfaces']))
        self.assertEqual(packet['current_file']['content'],'def add(title): return title')

    def test_long_current_source_is_explicitly_bounded_and_keeps_tail(self):
        source='#'+'x'*15000+'\ndef fetch_label(key, transport): return transport(key)\n'
        packet=repair_context([{'path':'api.py','content':source}],'api.py',[],{'checks':[]})
        current=packet['current_file']
        self.assertTrue(current['truncated'])
        self.assertLess(len(current['content']),8100)
        self.assertIn('fetch_label',current['content'])
        self.assertIn('def fetch_label(key, transport)',current['interfaces'])

    def test_qa_keeps_original_scope_but_does_not_repeat_old_sources(self):
        packet={'failed_checks':[{'id':'normal'}], 'current_file':{'content':'OLD_REJECTED_SOURCE'},
                'sibling_interfaces':[{'path':'api.py','interfaces':['def fetch_label(key, transport)']}]}
        description=('원래 작업의 요구사항과 대상 파일을 유지해 다시 수행하세요.\n\n원래 작업 지시:\n'
                     'main.py 하나만 작성하고 오류를 전파하세요.\n\nQA 검토 내용:\nfailed\n\n'
                     '[통합 수정 근거 — 대표 기준과 대상 파일 유지]\n'+json.dumps(packet))
        review=review_task_description(description)
        self.assertIn('main.py 하나만',review)
        self.assertIn('fetch_label',review)
        self.assertIn('normal',review)
        self.assertNotIn('OLD_REJECTED_SOURCE',review)
