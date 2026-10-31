import json
import unittest
from unittest.mock import Mock
from app.chief.chief_decision import ChiefDecision
from app.chief.verification_plan import repair_plan, plan_gaps
from app.qa.verification_contract import build_contract

class VerificationPlanRepairTests(unittest.TestCase):
    def test_nonempty_invalid_existing_plan_cannot_skip_repair(self):
        valid = {'version': 1, 'requirements': [{'id': 'R1', 'description': '파일'}],
                 'checks': [{'id': 'C1', 'kind': 'file', 'target': 'x.py', 'requirements': ['R1']}]}
        for missing in ('id', 'kind'):
            broken = json.loads(json.dumps(valid))
            del broken['checks'][0][missing]
            self.assertTrue(plan_gaps(broken))
            adapter = Mock(last_response={})
            adapter.generate.return_value = json.dumps(valid)
            result = repair_plan(ChiefDecision(verification_contract=broken), 'x.py', adapter)
            self.assertFalse(plan_gaps(result.verification_contract))
            adapter.generate.assert_called_once()
            adapter.generate.return_value = json.dumps(broken)
            result = repair_plan(ChiefDecision(), 'x.py', adapter)
            self.assertFalse(result.requirements_ready)
            self.assertIn(missing, result.analysis_issues[-1]['reason'])

    def test_malformed_json_is_regenerated_once_with_diagnostic(self):
        contract = {'version': 1, 'requirements': [{'id': 'R1', 'description': '파일'}],
                    'checks': [{'id': 'C1', 'kind': 'file', 'target': 'x.py', 'requirements': ['R1']}]}
        adapter = Mock(last_response={})
        adapter.generate.side_effect = ['{"version": }', json.dumps(contract)]
        decision = repair_plan(ChiefDecision(), 'x.py 파일 작성', adapter)
        self.assertEqual(adapter.generate.call_count, 2)
        self.assertIn('JSONDecodeError', adapter.generate.call_args.args[0])
        self.assertEqual([x['status'] for x in decision.verification_plan_attempts], ['INVALID', 'VALID'])
        self.assertFalse(plan_gaps(decision.verification_contract))

    def test_repair_confirmation_preserves_targets_and_expectations(self):
        contract = {'version': 1, 'requirements': [{'id': 'R1', 'description': '덧셈', 'required_categories': ['normal']}],
            'checks': [{'id': 'C1', 'kind': 'python_function', 'target': 'calc.py', 'function': 'add',
                        'requirements': ['R1'], 'cases': [{'args': [2, 3], 'expected': 5}]}]}
        adapter = Mock(last_response={})
        adapter.generate.return_value = json.dumps(contract)
        decision = repair_plan(ChiefDecision(), 'calc.py 덧셈 2+3=5', adapter)
        self.assertFalse(plan_gaps(decision.verification_contract))
        self.assertTrue(decision.acceptance_criteria)
        confirmed = build_contract('', proposed=build_contract('', proposed=decision.verification_contract), confirmed=True)
        self.assertEqual(confirmed['checks'][0]['cases'][0]['expected'], 5)
        self.assertEqual(confirmed['source'], 'REPRESENTATIVE_CONFIRMED')
        self.assertEqual(confirmed['checks'][0]['target'], 'calc.py')

    def test_failure_and_user_contract_fail_closed(self):
        adapter = Mock(last_response={})
        adapter.generate.return_value = 'no json'
        decision = repair_plan(ChiefDecision(), '프로그램 제작', adapter)
        self.assertFalse(decision.requirements_ready)
        self.assertEqual(decision.analysis_issues[0]['kind'], 'PLAN_ERROR')
        self.assertIn('JSON', decision.analysis_issues[0]['reason'])
        self.assertFalse(decision.clarification_questions)
        self.assertEqual(adapter.generate.call_count, 2)
        self.assertEqual(len(decision.verification_plan_attempts), 2)
        adapter.reset_mock()
        repair_plan(ChiefDecision(), '프로그램 제작', adapter, supplied={'version': 1})
        adapter.generate.assert_not_called()

if __name__ == '__main__':
    unittest.main()
