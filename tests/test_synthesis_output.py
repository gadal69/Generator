import json
import unittest
from unittest.mock import Mock
from app.chief.chief_agent import ChiefAgent
from app.chief.chief_decision import ChiefDecision
from app.runtime.run_control import RunCancelled

class SynthesisOutputTests(unittest.TestCase):
    def adapter(self, replies):
        adapter = Mock(last_response={})
        adapter.generate.side_effect = replies
        return adapter

    def valid(self):
        return json.dumps({'comparison_summary': '공통점·차이 검토', 'work_summary': '보고서 작성',
                           'rationale': '두 의견을 종합', 'core_roles': ['문서 담당자']})

    def test_invalid_json_then_recovery_preserves_cloud_policy(self):
        adapter = self.adapter(['{"comparison_summary": }', self.valid()])
        local = ChiefDecision(work_summary='보고서 작성', cloud_ai_required=False)
        decision = ChiefAgent().synthesize_analyses('보고서 작성', local, 'Gemini 의견', adapter)
        self.assertEqual(adapter.generate.call_count, 2)
        self.assertFalse(decision.cloud_ai_required)
        self.assertEqual(decision.model_reviews['gemini'], 'Gemini 의견')
        self.assertIn('JSONDecodeError', adapter.generate.call_args.args[0])

    def test_repeated_failures_stop_without_returning_analysis(self):
        for output in ('{}', 'not json', '{"comparison_summary": 123}', '[]'):
            with self.subTest(output=output):
                adapter = self.adapter([output, output])
                with self.assertRaisesRegex(RuntimeError, '2회'):
                    ChiefAgent().synthesize_analyses('보고서 작성', ChiefDecision(), '의견', adapter)
                self.assertEqual(adapter.generate.call_count, 2)

    def test_truncated_and_cancelled_responses(self):
        adapter = self.adapter([self.valid(), self.valid()])
        adapter.last_response = {'truncated': True}
        with self.assertRaisesRegex(RuntimeError, '중단'):
            ChiefAgent().synthesize_analyses('보고서 작성', ChiefDecision(), '의견', adapter)
        adapter = self.adapter([RunCancelled()])
        with self.assertRaises(RunCancelled):
            ChiefAgent().synthesize_analyses('보고서 작성', ChiefDecision(), '의견', adapter)
        self.assertEqual(adapter.generate.call_count, 1)

if __name__ == '__main__':
    unittest.main()
