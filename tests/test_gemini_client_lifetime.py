import unittest
from types import SimpleNamespace
from unittest.mock import patch
from app.models.gemini_search_adapter import GeminiSearchAdapter


class GeminiClientLifetimeTests(unittest.TestCase):
    def test_client_lives_through_call_and_closes_on_success_or_error(self):
        for operation in ('generate', 'search'):
            for fail in (False, True):
                with self.subTest(operation=operation, fail=fail):
                    state = {'closed': False, 'calls': 0}
                    class Interactions:
                        def create(self, **kwargs):
                            self_outer.assertFalse(state['closed'])
                            state['calls'] += 1
                            if fail:
                                raise ValueError('simulated API failure')
                            return SimpleNamespace(output_text='ok')
                    class Client:
                        def __init__(self, **kwargs):
                            self.interactions = Interactions()
                        def close(self):
                            state['closed'] = True
                        def __del__(self):
                            self.close()
                    self_outer = self
                    adapter = GeminiSearchAdapter(api_key='fake-test-key')
                    adapter.search_enabled = True
                    with patch('google.genai.Client', Client), patch.object(adapter, '_sources', return_value=[{'title': 'test', 'url': 'https://example.com'}]):
                        if fail:
                            with self.assertRaises(RuntimeError):
                                getattr(adapter, operation)('test')
                        else:
                            self.assertTrue(getattr(adapter, operation)('test'))
                    self.assertEqual(state['calls'], 1)
                    self.assertTrue(state['closed'])

if __name__ == '__main__':
    unittest.main()
