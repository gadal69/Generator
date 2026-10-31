import json
import unittest
from unittest.mock import Mock, patch
from app.chief.verification_schema import PLAN_SCHEMA, normalize_categories
from app.chief.verification_plan import repair_plan
from app.chief.chief_decision import ChiefDecision
from app.models.ollama_adapter import OllamaAdapter
from app.models.vllm_adapter import VllmAdapter

class StructuredVerificationTests(unittest.TestCase):
    def test_provider_payloads(self):
        for cls, field in [(OllamaAdapter, 'format'), (VllmAdapter, 'response_format')]:
            response = Mock()
            response.json.return_value = {'done': True, 'response': '{}', 'choices': [{'message': {'content': '{}'}, 'finish_reason': 'stop'}]}
            with patch('requests.post', return_value=response) as post:
                cls(model='test').generate_json('plan', PLAN_SCHEMA)
                self.assertIn(field, post.call_args.kwargs['json'])
                cls(model='test').generate('ordinary')
                self.assertNotIn(field, post.call_args.kwargs['json'])

    def test_reported_failure_classes_are_replayed(self):
        base = {'version': 1, 'requirements': [{'id': 'R1', 'description': 'file', 'required_categories': ['normal']}],
                'checks': [{'id': 'C1', 'kind': 'file', 'target': 'x.py', 'requirements': ['R1']}]}
        # Reproductions of reported errors; historical raw model responses were not retained.
        for category in [['normal', 'normal'], ['정상'], 'normal', []]:
            candidate = json.loads(json.dumps(base))
            candidate['requirements'][0]['required_categories'] = category
            adapter = Mock(last_response={})
            adapter.generate.side_effect = [json.dumps(candidate), json.dumps(base)]
            result = repair_plan(ChiefDecision(), 'x.py', adapter)
            self.assertEqual(result.verification_contract['requirements'][0]['required_categories'], ['normal'])
            self.assertTrue(result.requirements_ready)
        broken = Mock(last_response={})
        broken.generate.return_value = '{"version": }'
        result = repair_plan(ChiefDecision(), 'x.py', broken)
        self.assertFalse(result.requirements_ready)
        self.assertIn('response_sample', result.verification_plan_attempts[0])

    def test_only_exact_duplicates_are_normalized(self):
        self.assertEqual(normalize_categories({'requirements': [{'required_categories': ['normal', 'normal']}]}),
                         {'requirements': [{'required_categories': ['normal']}]})

if __name__ == '__main__':
    unittest.main()
