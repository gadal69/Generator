"""Offline regression tests: no model downloads, cloud calls, or operational DB writes."""
import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch, Mock

from app.agents.base_agent import BaseAgent
from app.chief.chief_decision import ChiefDecision
from app.chief.chief_planner import ChiefPlanner
from app.models.ollama_adapter import OllamaAdapter
from app.models.vllm_adapter import VllmAdapter
from app.models.generation_policy import generation_settings
from app.models.generation_policy import record_response
from app.orchestrator.orchestrator import Orchestrator
from app.orchestrator.work_contract import read_contract, attach_contract
from app.organization.team_composer import TeamComposer
from app.prompts.prompt_manager import PromptManager
from app.qa.artifact_validation import validate_artifacts
from app.storage.database import Database
from app.tools.project_file_tool import ProjectFileTool


class WorkflowAdapter:
    def __init__(self, model, state):
        self.model, self.state = model, state

    def generate(self, prompt):
        self.state.setdefault('prompts', []).append(prompt)
        if '당신은 독립 QA Agent입니다' in prompt:
            self.state['qa_calls'] = self.state.get('qa_calls', 0) + 1
            return 'VERDICT: FAIL\n인계 자료 보완 필요' if 'FAILED_HANDOFF' in prompt else 'VERDICT: PASS\n담당 범위 확인'
        contract = read_contract(prompt)
        if contract and not contract['files']:
            self.state['handoffs'] = self.state.get('handoffs', 0) + 1
            return 'FAILED_HANDOFF' if self.state.get('rework_handoff') and self.state['handoffs'] == 1 else 'ACCEPTED_HANDOFF'
        if contract and contract['files']:
            self.state.setdefault('implementation_prompts', []).append(prompt)
            self.state['writes'] = self.state.get('writes', 0) + 1
            path = contract['files'][0]
            if self.state.get('broken') and self.state['writes'] == 1:
                content = 'def incomplete('
            elif self.state.get('always_broken'):
                content = 'def incomplete('
            else:
                content = 'def add(a, b):\n    return a + b\n'
            return f'<artifact path="{path}">\n{content}</artifact>'
        return '완료'


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.db = Database(self.root / 'test.db')
        self.o = Orchestrator(self.db)
        self.o.project_file_tool = ProjectFileTool(self.root)
        self.o.tool_registry.register('write_project_file', self.o.project_file_tool.write_file)

    def tearDown(self):
        self.temp.cleanup()

    def decision(self, assignments=None):
        return ChiefDecision(core_roles=['DESIGN', 'Software Developer'],
            file_creation_required=True, task_assignments=assignments or []).to_dict()

    def run_flow(self, state, model='arbitrary-model:tag', qa='agent', decision=None, request='sample.py 파일을 만들어 주세요.',approval='manual'):
        with contextlib.redirect_stdout(io.StringIO()):
            return self.o.run_user_request(request, model_override=model, qa_mode=qa,
                approval_mode=approval, max_reworks=2, verbose=False,
                analysis_override=decision or self.decision(),
                adapter_factory=lambda name: WorkflowAdapter(name, state))

    def test_arbitrary_models_complete_without_family_checks(self):
        for model in ['gemma4:e2b', 'gemma4:12b', 'different/vendor-model:v2']:
            with self.subTest(model=model):
                result = self.run_flow({}, model)
                self.assertEqual(result['status'], 'PENDING_APPROVAL')
                self.assertTrue((self.root / 'projects' / result['project_id'] / 'artifacts/sample.py').is_file())
                self.assertTrue(all(member['model'] == model for member in result['team']))

    def test_designer_can_own_ui_code(self):
        decision=self.decision([{'role':'DESIGN','deliverable':'UI 구현','files':['view.py']},
                                {'role':'Software Developer','deliverable':'통합','files':['main.py']}])
        result=self.run_flow({},decision=decision,request='view.py 및 main.py 파일을 만들어 주세요.')
        folder=self.root/'projects'/result['project_id']/'artifacts'
        self.assertTrue((folder/'view.py').exists())
        self.assertTrue((folder/'main.py').exists())

    def test_model_role_aliases_resolve_to_same_file_owner(self):
        for alias in ['UI Designer','System/UI Designer','디자이너']:
            with self.subTest(alias=alias):
                decision=self.decision([{'role':alias,'deliverable':'UI 구현','files':['sample.py']}])
                result=self.run_flow({},decision=decision)
                self.assertEqual(result['status'],'PENDING_APPROVAL')

    def test_conflicting_file_ownership_blocks_before_model_call(self):
        state={}
        decision=self.decision([{'role':role,'files':['sample.py']} for role in ['DESIGN','Software Developer']])
        with self.assertRaisesRegex(ValueError,'중복'):
            self.run_flow(state,decision=decision)
        self.assertFalse(state.get('prompts'))
        failure=list((self.root/'projects').glob('*/team_run_result.json'))
        self.assertEqual(json.loads(failure[0].read_text(encoding='utf-8'))['status'],'ERROR')

    def test_accepted_rework_is_handed_to_next_owner(self):
        state={'rework_handoff':True}
        result=self.run_flow(state)
        self.assertEqual(result['status'],'PENDING_APPROVAL')
        prompt=state['implementation_prompts'][0]
        self.assertIn('ACCEPTED_HANDOFF',prompt)
        self.assertNotIn('FAILED_HANDOFF',prompt)

    def test_rule_qa_does_not_call_model_reviewer(self):
        state={}
        result=self.run_flow(state,qa='rule')
        self.assertEqual(state.get('qa_calls',0),0)
        self.assertTrue(all(review[5]=='PASS_WITH_WARNING' for review in result['qa_reviews']))

    def test_invalid_python_reworks_in_rule_mode(self):
        state={'broken':True}
        result=self.run_flow(state,qa='rule')
        self.assertEqual(state['writes'],2)
        self.assertTrue(any(review[5]=='FAIL' for review in result['qa_reviews']))

    def test_exhausted_rework_writes_error_evidence(self):
        with self.assertRaisesRegex(RuntimeError,'최대 재작업'):
            self.run_flow({'always_broken':True},qa='rule')
        failure=list((self.root/'projects').glob('*/team_run_result.json'))
        data=json.loads(failure[0].read_text(encoding='utf-8'))
        self.assertEqual(data['status'],'ERROR')
        self.assertTrue(data['errors'])
        self.assertEqual(len(data['qa_reviews']),4)  # handoff + three failed implementations

    def test_truncation_is_not_accepted_even_for_handoffs(self):
        task=[None]*5
        task[4]=attach_contract('handoff','DESIGN',[],'UI 인계',[])
        self.assertIn('중단',self.o.team_runner._artifact_precheck(task,'[MODEL_RESPONSE_INCOMPLETE]\npartial'))

    def test_forbidden_eval_is_rejected(self):
        artifacts=[{'path':'sample.py','content':'def calculate(x): return eval(x)'}]
        self.assertIn('금지',validate_artifacts(artifacts,['sample.py'],'eval/exec 사용 금지'))
        self.assertEqual(validate_artifacts(artifacts,['sample.py'],'Python 파일 작성'),'')

    def test_unexecuted_code_cannot_auto_approve(self):
        result=self.run_flow({},approval='auto')
        self.assertEqual(result['status'],'PENDING_APPROVAL')
        self.assertIn('미실행',result['final_result'])
        self.assertTrue(any(item['type']=='COVERAGE' and item['status']=='NOT_RUN' for item in result['code_verifications']))

    def test_fenced_second_file_uses_its_own_contract_path(self):
        description=attach_contract('[파일 산출 지시]\nview.py와 main.py를 작성하세요.','DEV',['main.py'],'통합',[])
        task=[None]*5;task[4]=description
        self.assertEqual(self.o.team_runner._artifact_precheck(task,'```python\nx = 1\n```'),'')
        project=self.o.create_project('fenced')
        written=self.o.team_runner._write_requested_files(project,'view.py main.py 파일 작성',
            [{'name':'DEV','role':'Software Developer','files':['main.py'],'output':'```python\nx=1\n```'}],True)
        self.assertEqual(written[0]['relative_path'],'main.py')

    def test_unclosed_artifact_is_not_rescued_by_code_fence(self):
        task=[None]*5;task[4]='[파일 산출 지시] <artifact path="sample.py">내용</artifact>'
        self.assertIn('닫는 태그',self.o.team_runner._artifact_precheck(task,'<artifact path="sample.py">\n```python\nx=1\n```'))

    def test_valid_ellipsis_text_and_type_hints_are_allowed(self):
        artifacts=[{'path':'sample.py','content':'def f() -> tuple[int, ...]:\n    print("Wait...")\n    return (1,)'}]
        self.assertEqual(validate_artifacts(artifacts,['sample.py'],''),'')

    def test_transport_metadata_is_saved_without_prompt(self):
        adapter=Mock(model='future-model',last_response={'done_reason':'length','truncated':True})
        record_response(self.db,adapter,'implementation')
        with self.db.connection() as conn:
            model,metadata=conn.execute('SELECT model,metadata FROM model_responses').fetchone()
        self.assertEqual(model,'future-model')
        self.assertTrue(json.loads(metadata)['truncated'])

    def test_chief_preserves_ownership_and_acceptance_in_analysis(self):
        planner=ChiefPlanner();fallback=planner.rule_plan('sample.py 파일을 만들어 주세요.')
        decision=planner.parse_llm(json.dumps({'core_roles':['DESIGN','Software Developer'],
            'task_assignments':[{'role':'DESIGN','files':['sample.py'],'deliverable':'UI 구현'}],
            'acceptance_criteria':['버튼 입력 지원']}),fallback)
        self.assertEqual(decision.task_assignments[0]['files'],['sample.py'])
        self.assertIn('버튼 입력 지원',decision.acceptance_criteria)
        self.assertTrue(any('sample.py' in step for step in decision.work_steps))

    def test_wrong_and_duplicate_files_are_rejected(self):
        a={'path':'wrong.py','content':'x=1'}
        self.assertTrue(validate_artifacts([a],['sample.py'],''))
        self.assertTrue(validate_artifacts([a,a],['wrong.py'],''))

    def test_contract_not_split_and_rework_prompt_does_not_grow(self):
        description=attach_contract('x'*2500,'DESIGN',[],'인계',[])
        plan=[{'name':'handoff','description':description,'agent_id':'A','depends_on':[]}]
        self.assertEqual(self.o.team_runner._chunk_large_tasks(plan),plan)
        original=attach_contract('[파일 산출 지시]\n<artifact path="sample.py">내용</artifact>','DEV',['sample.py'],'구현',[])
        first=self.o.team_runner._build_rework_instruction(original,'one',['a'])
        second=self.o.team_runner._build_rework_instruction(first,'two',['b'])
        self.assertNotIn('QA 검토 내용:\none',second)
        self.assertEqual(read_contract(second)['files'],['sample.py'])

    def test_qa_prompt_does_not_request_artifact_output(self):
        prompt=PromptManager().build_prompt('Quality Assurance','VERDICT: PASS / FAIL\n[파일 산출 지시]')
        self.assertIn('첫 줄에 VERDICT',prompt.split('Result Format')[-1])
        self.assertNotIn('[Summary]',prompt.split('Result Format')[-1])

    def test_simple_gui_request_does_not_force_extra_designer(self):
        decision=ChiefPlanner().rule_plan('tkinter GUI calculator.py 파일을 만들어 주세요.')
        self.assertNotIn('DESIGN',decision.core_roles)

    def test_migration_is_repeatable_and_preserves_project(self):
        project=self.o.create_project('keep')
        Database(self.root/'test.db')
        self.assertIsNotNone(self.o.project_manager.get_project(project))
        with self.db.connection() as conn:
            self.assertEqual(conn.execute('PRAGMA user_version').fetchone()[0],4)
            self.assertEqual(conn.execute('SELECT count(*) FROM model_responses').fetchone()[0],0)


class AdapterTests(unittest.TestCase):
    def test_explicit_ollama_request_options_survive_stage_configuration(self):
        with patch('app.models.ollama_adapter.requests.post') as post:
            post.return_value.json.return_value={'response':'ok','done':True,'done_reason':'stop'}
            adapter=OllamaAdapter(model='any-supported-model',request_options={'think':False})
            adapter.configure_generation('qa')
            adapter.generate('hello')
            self.assertIs(post.call_args.kwargs['json']['think'],False)

    def test_explicit_vllm_request_options_are_forwarded(self):
        with patch('app.models.vllm_adapter.requests.post') as post:
            post.return_value.json.return_value={'choices':[{'message':{'content':'ok'},'finish_reason':'stop'}]}
            adapter=VllmAdapter(model='any-supported-model',request_options={'chat_template_kwargs':{'enable_thinking':False}})
            adapter.configure_generation('implementation')
            adapter.generate('hello')
            self.assertEqual(post.call_args.kwargs['json']['chat_template_kwargs'],{'enable_thinking':False})

    def test_ollama_metadata_and_truncated_output(self):
        with patch('app.models.ollama_adapter.requests.post') as post:
            post.return_value.json.return_value={'response':'partial','done':True,'done_reason':'length','eval_count':50}
            adapter=OllamaAdapter(model='some-new-model:tag')
            text=BaseAgent('a','DEV','Software Developer',adapter.model).execute('write',adapter)
            self.assertIn('[MODEL_RESPONSE_INCOMPLETE]',text)
            self.assertEqual(adapter.last_response['eval_count'],50)
            self.assertNotIn('think',post.call_args.kwargs['json'])

    def test_vllm_length_metadata(self):
        with patch('app.models.vllm_adapter.requests.post') as post:
            post.return_value.json.return_value={'choices':[{'message':{'content':'partial'},'finish_reason':'length'}]}
            adapter=VllmAdapter(model='other-model')
            adapter.generate('hello')
            self.assertTrue(adapter.last_response['truncated'])

    def test_stage_profiles_are_exact_model_configuration(self):
        config={'model_profiles':{'gemma4:12b':{'ollama':{'options':{'num_predict':6000},
            'request_options':{'think':False},'stages':{'implementation':{'retry_options':[{'num_predict':9000}]}}}}}}
        options,_,request=generation_settings(config,'ollama','gemma4:12b',{},300,'implementation',1)
        self.assertEqual(options['num_predict'],9000)
        self.assertIs(request['think'],False)
        self.assertEqual(generation_settings(config,'ollama','unrelated',{},300,'implementation')[0],{})

    def test_empty_response_is_error(self):
        with patch('app.models.ollama_adapter.requests.post') as post:
            post.return_value.json.return_value={'response':'','done':True,'done_reason':'stop'}
            with self.assertRaisesRegex(ValueError,'빈 응답'):
                OllamaAdapter().generate('hello')


if __name__=='__main__':
    unittest.main()
