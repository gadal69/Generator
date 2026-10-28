import unittest
from unittest.mock import patch

from app.qa.function_verifier import FunctionVerifier
from app.qa.project_verifier import ProjectVerifier
from app.qa.isolated_runner import IsolatedRunner
from app.qa.verification_contract import build_contract,validate_contract
from app.qa.pure_python_worker import run_case,values_equal


class VerifierAccuracyTests(unittest.TestCase):
    def test_word_count_boolean_values_are_not_integer_counts(self):
        source='def word_counts(text): return {word: True for word in text.split()}'
        result=FunctionVerifier().verify(source,[{'function':'word_counts','args':['hello world'],
                                                'expected':{'hello':1,'world':1}}])
        self.assertEqual(result['status'],'FAIL')

    def test_nested_container_types_are_checked_recursively(self):
        self.assertFalse(values_equal({'items':[{'count':True}]},{'items':[{'count':1}]}))
        self.assertFalse(values_equal({'count':1.0},{'count':1}))
        self.assertTrue(values_equal({'items':[{'count':1}]},{'items':[{'count':1}]}))

    def test_json_nested_boolean_cannot_satisfy_numeric_contract(self):
        contract=build_contract('',{'version':1,'checks':[{'id':'count','kind':'json_value','target':'x.json','expected':{'count':1}}]})
        self.assertEqual(ProjectVerifier().verify([{'path':'x.json','content':'{"count":true}'}],contract)[0]['status'],'FAIL')

    def test_expected_exception_type_rejects_wrong_exception(self):
        case={'function':'normalize_text','args':[123],'error':True,'error_type':'TypeError'}
        self.assertEqual(run_case('def normalize_text(text): raise ValueError("wrong")',case)['status'],'FAIL')
        self.assertEqual(run_case('def normalize_text(text): raise TypeError("expected")',case)['status'],'PASS')

    def test_error_text_does_not_replace_a_required_exception(self):
        case={'function':'f','args':[],'error':True,'error_type':'TypeError'}
        self.assertEqual(run_case('def f(): return "Error: wrong type"',case)['status'],'FAIL')

    def test_unspecified_error_contract_remains_backward_compatible(self):
        self.assertEqual(run_case('def f(): raise ValueError("invalid")',{'function':'f','args':[],'error':True})['status'],'PASS')

    def test_exception_type_requires_an_error_case(self):
        for case in [{'args':[],'expected':1,'error_type':'TypeError'},
                     {'args':[],'error':True,'error_type':123},
                     {'args':[],'error':True,'error_type':'builtins.TypeError'}]:
            with self.subTest(case=case),self.assertRaises(ValueError):
                validate_contract({'checks':[{'id':'f','kind':'python_function','target':'x.py','function':'f','cases':[case]}]})

    def test_docker_comparison_rejects_nested_boolean(self):
        contract=build_contract('',{'checks':[{'id':'f','kind':'python_function','engine':'docker','target':'x.py',
            'function':'f','cases':[{'args':[],'expected':{'count':1}}]}]})
        with patch.object(IsolatedRunner,'run',return_value={'status':'OBSERVED','observation':{'cases':[{'value':{'count':True}}]}}):
            self.assertEqual(ProjectVerifier().verify([{'path':'x.py','content':'def f(): return {}'}],contract)[0]['status'],'FAIL')

    def test_docker_comparison_requires_the_correct_exception(self):
        contract=build_contract('',{'checks':[{'id':'f','kind':'python_function','engine':'docker','target':'x.py',
            'function':'f','cases':[{'args':[],'error':True,'error_type':'TypeError'}]}]})
        for exception,status in [('ValueError','FAIL'),('TypeError','PASS')]:
            with self.subTest(exception=exception),patch.object(IsolatedRunner,'run',return_value={'status':'OBSERVED','observation':{'cases':[{'exception':exception}]}}):
                self.assertEqual(ProjectVerifier().verify([{'path':'x.py','content':'def f(): return 1'}],contract)[0]['status'],status)

    def test_scalar_numeric_tolerance_is_preserved(self):
        self.assertEqual(run_case('def f(): return 0.1+0.2',{'function':'f','args':[],'expected':0.3})['status'],'PASS')


if __name__=='__main__':unittest.main()
