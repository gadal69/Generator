import contextlib
import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from app.qa.functional_cases import cases_from_request,verification_cases_from_request
from app.qa.function_verifier import FunctionVerifier
from app.qa.pure_python_worker import run_case
from app.orchestrator.orchestrator import Orchestrator
from app.orchestrator.work_contract import read_contract
from app.storage.database import Database
from app.tools.project_file_tool import ProjectFileTool
from app.prompts.prompt_manager import PromptManager
from app.orchestrator.team_runner import TeamRunner
from app.qa.artifact_validation import validate_artifacts


PARSER = '''
import ast
import operator as op
OPERATORS = {ast.Add: op.add, ast.Sub: op.sub, ast.Mult: op.mul, ast.Div: op.truediv}
def calculate(expression):
    def visit(node):
        if isinstance(node, ast.Constant) and type(node.value) in (int, float):
            return node.value
        if isinstance(node, ast.BinOp) and type(node.op) in OPERATORS:
            return OPERATORS[type(node.op)](visit(node.left), visit(node.right))
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
            return -visit(node.operand)
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.UAdd):
            return visit(node.operand)
        raise ValueError("Invalid expression")
    return visit(ast.parse(expression, mode="eval").body)
raise RuntimeError("Module top-level statements must never execute")
'''


class FunctionVerificationTests(unittest.TestCase):
    def pending_approval(self,root):
        orchestrator=Orchestrator(Database(root/'test.db'))
        project=orchestrator.create_project('approval-check')
        orchestrator.register_agent('DEV',project,'DEV','Software Developer','any-model')
        task=orchestrator.create_task(project,'implementation','test','DEV')
        result=orchestrator.result_manager.save_result(task,'DEV','complete')
        orchestrator.qa_task(project,task,result,status='PASS',score=100)
        approval=orchestrator.request_approval(project,task,result)
        return orchestrator,project,task,result,approval

    def test_failed_revalidation_invalidates_pending_approval(self):
        with tempfile.TemporaryDirectory() as directory:
            o,project,task,result,approval=self.pending_approval(Path(directory))
            o.qa_task(project,task,result,status='FAIL',score=0,summary='functional mismatch')
            self.assertEqual(o.approval_manager.get(approval)[5],'INVALIDATED')
            with self.assertRaisesRegex(ValueError,'재검증'):
                o.approve(approval,'APPROVED')
            self.assertEqual(o.project_manager.get_project(project)[3],'BLOCKED')

    def test_failed_revalidation_invalidates_already_approved_result(self):
        with tempfile.TemporaryDirectory() as directory:
            o,project,task,result,approval=self.pending_approval(Path(directory))
            o.approve(approval,'APPROVED')
            o.qa_task(project,task,result,status='FAIL',score=0,summary='later measured execution failure')
            self.assertEqual(o.approval_manager.get(approval)[5],'INVALIDATED')
            self.assertEqual(o.project_manager.get_project(project)[3],'BLOCKED')
            with self.assertRaises(ValueError):o.approve(approval,'APPROVED')

    def test_invalidated_result_displays_latest_failure_in_existing_screen(self):
        import os
        os.environ['QT_QPA_PLATFORM']='offscreen'
        from qtpy import QtWidgets
        from app.gui import qt_dashboard
        app=QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        with tempfile.TemporaryDirectory() as directory:
            o,project,task,result,approval=self.pending_approval(Path(directory))
            o.qa_task(project,task,result,status='FAIL',score=0,summary='independent arithmetic mismatch')
            with patch.object(qt_dashboard,'make_database',return_value=o.database):
                window=qt_dashboard.Dashboard()
                item=window._approval_details['final:'+approval]
                self.assertIn('independent arithmetic mismatch',item['result'])
                self.assertIn('승인할 수 없습니다',item['result'])
                self.assertIn('재검증',window.approvals_table.item(0,5).text())
                window.refresh_timer.stop();window.close();app.processEvents()

    def test_approval_cannot_use_qa_for_different_result(self):
        with tempfile.TemporaryDirectory() as directory:
            o,project,task,result,approval=self.pending_approval(Path(directory))
            revised=o.result_manager.save_result(task,'DEV','new revision')
            with self.assertRaisesRegex(ValueError,'다릅니다'):
                o.request_approval(project,task,revised)
            o.qa_task(project,task,revised,status='PASS',score=100)
            with self.assertRaisesRegex(ValueError,'동일 결과'):
                o.approve(approval,'APPROVED')

    def test_domain_shorthand_does_not_invent_test_cases(self):
        request=Path('docs/CALCULATOR_TEST_PROMPT.txt').read_text(encoding='utf-8')
        self.assertEqual(cases_from_request(request),[])

    def test_general_function_examples_are_supported(self):
        cases=cases_from_request('add(2, 3) = 5, add(-2, 1) -> -1')
        result=FunctionVerifier().verify('def add(a,b): return a+b',cases)
        self.assertEqual(result['status'],'PASS')
        self.assertEqual(len(result['cases']),2)

    def test_explicit_additional_cases_expose_hardcoded_answers(self):
        cases=cases_from_request('add(2,3)=5, add(6,7)=13')
        source='def add(a,b): return 5'
        self.assertEqual(FunctionVerifier().verify(source,cases)['status'],'FAIL')
        self.assertEqual(FunctionVerifier().verify('def add(a,b): return a+b',cases)['status'],'PASS')

    def test_static_validation_does_not_guess_program_domain(self):
        source='def convert(s): return {"one":1,"two":2}.get(s)'
        self.assertEqual(validate_artifacts([{'path':'convert.py','content':source}],['convert.py'],''),'')

    def test_f_string_errors_are_interpreted_without_allowing_arbitrary_format_specs(self):
        source='def f(s):\n    try: return 1/0\n    except ZeroDivisionError as exc: return f"Error: {str(exc)}"'
        self.assertEqual(run_case(source,{'function':'f','args':['bad'],'error':True})['status'],'PASS')
        self.assertEqual(run_case('def f(s): return f"{1:1000000000}"',{'function':'f','args':['x'],'expected':'x'})['status'],'NOT_RUN')

    def test_wrong_result_is_failure(self):
        case={'function':'add','args':[2,3],'expected':5}
        self.assertEqual(run_case('def add(a,b):return a-b',case)['status'],'FAIL')

    def test_wrong_error_behavior_is_failure(self):
        self.assertEqual(run_case('def calculate(x):return 0',{'function':'calculate','args':['1/0'],'error':True})['status'],'FAIL')
        self.assertEqual(run_case('def calculate(x):return "no error"',{'function':'calculate','args':['1/0'],'error':True})['status'],'FAIL')

    def test_missing_required_function_is_failure(self):
        result=run_case('def unrelated(x):return 1',{'function':'calculate','args':['1+1'],'expected':2})
        self.assertEqual(result['status'],'FAIL')

    def test_syntax_error_cannot_pass_an_expected_error_case(self):
        result=FunctionVerifier().verify('def broken(', [{'function':'calculate','args':['1/0'],'error':True}])
        self.assertEqual(result['status'],'FAIL')

    def test_unsupported_parser_class_is_not_misreported_as_pass(self):
        result=run_case('class Parser: pass\ndef calculate(s): return Parser().parse(s)',{'function':'calculate','args':['1+1'],'expected':2})
        self.assertEqual(result['status'],'NOT_RUN')

    def test_no_cases_is_not_run(self):
        self.assertEqual(FunctionVerifier().verify('x=1',[])['status'],'NOT_RUN')

    def test_infinite_loop_is_bounded(self):
        result=run_case('def f(x):\n    while True: pass',{'function':'f','args':[1],'expected':1})
        self.assertEqual(result['status'],'FAIL')
        self.assertIn('limit',result['reason'])

    def test_huge_allocations_are_refused_before_creation(self):
        for expression in ["'x' * 1000000000",'2 ** 1000000000']:
            result=run_case('def f(x):return '+expression,{'function':'f','args':[1],'expected':1})
            self.assertEqual(result['status'],'FAIL')

    def test_import_and_filesystem_escapes_are_unavailable(self):
        for source in [
            "def f(x):return __import__('os').getcwd()",
            "def f(x):return open('should-not-exist','w')",
            "def f(x):return ().__class__.__bases__",
            "def f(x):\n    import os\n    return os.getcwd()",
            "def f(x):\n    try: return open('should-not-exist')\n    except: return 1",
        ]:
            self.assertEqual(run_case(source,{'function':'f','args':[1],'expected':1})['status'],'NOT_RUN')
        self.assertFalse(Path('should-not-exist').exists())

    def test_network_module_is_never_imported(self):
        source='import socket\ndef f(x): return socket.gethostname()'
        self.assertEqual(run_case(source,{'function':'f','args':[1],'expected':'anything'})['status'],'NOT_RUN')

    def test_finally_cannot_hide_unsupported_operations(self):
        source="def f(x):\n    try: return open('should-not-exist')\n    finally: return 1"
        self.assertEqual(run_case(source,{'function':'f','args':[1],'expected':1})['status'],'NOT_RUN')

    def test_mutable_defaults_keep_python_call_semantics(self):
        source='def collect(x, items=[]):\n    items.append(x)\n    return len(items)\ndef f(x):\n    collect(1)\n    return collect(2)'
        self.assertEqual(run_case(source,{'function':'f','args':[0],'expected':2})['status'],'PASS')

    def test_decorated_nested_functions_are_not_silently_ignored(self):
        source='def f(x):\n    @unknown\n    def g(y): return y\n    return g(x)'
        self.assertEqual(run_case(source,{'function':'f','args':[1],'expected':1})['status'],'NOT_RUN')

    def test_generator_is_not_misreported_as_list(self):
        source='def f(x): return isinstance((i for i in range(x)),list)'
        self.assertEqual(run_case(source,{'function':'f','args':[2],'expected':False})['status'],'PASS')

    def test_mutated_module_globals_are_not_silently_assumed_constant(self):
        for source in ['value=1\nvalue+=1\ndef f(x):return value',
                       'values=[]\nvalues.append(1)\ndef f(x):return len(values)',
                       'value=1\nif True: value=2\ndef f(x):return value']:
            self.assertEqual(run_case(source,{'function':'f','args':[0],'expected':1})['status'],'NOT_RUN')

    def test_global_bindings_shadow_builtin_names(self):
        self.assertEqual(run_case('int=5\ndef f(x):return int',{'function':'f','args':[0],'expected':5})['status'],'PASS')

    def test_current_description_is_not_duplicated_in_model_context(self):
        task='UNIQUE_CURRENT_REQUIREMENT'
        context={'task':{'description':task,'task_id':'T'},'previous_results':[{'summary':'accepted handoff'}]}
        prompt=PromptManager().build_prompt('Software Developer',task,context)
        self.assertEqual(prompt.count(task),1)
        self.assertIn('accepted handoff',prompt)
        self.assertEqual(context['task']['description'],task)

    def test_repair_strategy_addresses_the_measured_failure(self):
        prompt=TeamRunner._build_rework_instruction('original task','금지한 eval 사용',['ARTIFACT_VALIDATION_FAIL'])
        self.assertIn('입력 형식',prompt)
        self.assertIn('완전히 제거',prompt)
        prompt=TeamRunner._build_rework_instruction('original task','wrong result',['FUNCTIONAL_TEST_FAIL'])
        self.assertIn('예상 결과',prompt)
        self.assertIn('하드코딩하지',prompt)

    def test_invalid_analysis_is_replanned_before_confirmation(self):
        class AnalysisAdapter:
            def generate(self,prompt):
                return json.dumps({'core_roles':['Software Developer'],
                    'task_assignments':[{'role':'Software Developer','files':['logic.py'],'deliverable':'implementation'}]})
        with tempfile.TemporaryDirectory() as directory:
            orchestrator=Orchestrator(Database(Path(directory)/'test.db'))
            decision=orchestrator.team_runner.analyze_user_request('calculator.py 단일 파일을 만들어 주세요.',
                adapter_factory=lambda model:AnalysisAdapter())
            self.assertEqual(decision['source'],'RULE_PLAN_RECOVERY')
            self.assertIn('logic.py',decision['rationale'])
            self.assertEqual(decision['task_assignments'],[])
            self.assertEqual(decision['core_roles'],['Software Developer'])

    def test_plan_recovery_does_not_bypass_cloud_approval_requirements(self):
        class AnalysisAdapter:
            def generate(self,prompt):
                return json.dumps({'core_roles':['Software Developer'],
                    'cloud_ai_required':True,'cloud_purpose':'review','cloud_scope':'source','cloud_data_scope':'requested source',
                    'task_assignments':[{'role':'Software Developer','files':['extra.py'],'deliverable':'implementation'}]})
        with tempfile.TemporaryDirectory() as directory:
            orchestrator=Orchestrator(Database(Path(directory)/'test.db'))
            decision=orchestrator.team_runner.analyze_user_request('calculator.py 단일 파일을 만들어 주세요.',
                adapter_factory=lambda model:AnalysisAdapter())
            self.assertEqual(decision['source'],'RULE_PLAN_RECOVERY')
            self.assertTrue(decision['cloud_ai_required'])
            self.assertEqual(decision['cloud_scope'],'source')

    def test_trusted_worker_timeout_is_unverified_not_semantic_failure(self):
        with patch('app.qa.function_verifier.subprocess.run',side_effect=subprocess.TimeoutExpired('worker',1)):
            self.assertEqual(FunctionVerifier().verify('def f(x): return x',[{'function':'f','args':[1],'expected':1}])['status'],'NOT_RUN')

    def test_pipeline_reworks_semantically_wrong_code_before_approval(self):
        state={'calls':0}
        class Adapter:
            model='any-model:tag'
            def generate(self,prompt):
                contract=read_contract(prompt)
                state['calls']+=1
                content='def add(a,b): return a-b' if state['calls']==1 else 'def add(a,b): return a+b'
                return '<artifact path="sample.py">'+content+'</artifact>'
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);database=Database(root/'test.db');orchestrator=Orchestrator(database)
            orchestrator.project_file_tool=ProjectFileTool(root)
            orchestrator.tool_registry.register('write_project_file',orchestrator.project_file_tool.write_file)
            with contextlib.redirect_stdout(io.StringIO()):
                result=orchestrator.run_user_request('sample.py 파일을 작성하세요. add(2, 3) = 5',
                    qa_mode='rule',approval_mode='auto',verbose=False,model_override='any-model:tag',
                    analysis_override={'core_roles':['Software Developer'],'file_creation_required':True},
                    adapter_factory=lambda model:Adapter())
            self.assertEqual(state['calls'],2)
            self.assertEqual(result['qa_reviews'][0][5],'FAIL')
            self.assertIn('FUNCTIONAL_TEST_FAIL',result['qa_reviews'][0][8])
            self.assertTrue(any(row['type']=='FUNCTIONAL_TEST' and row['status']=='PASS' for row in result['code_verifications']))
            self.assertEqual(result['status'],'PENDING_APPROVAL')
            self.assertTrue((root/'projects'/result['project_id']/'artifacts/sample.py').is_file())


if __name__=='__main__':
    unittest.main()
