import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest

from app.chief.allocation_policy import ALLOCATION_INSTRUCTION,enforce_allocation
from app.chief.chief_agent import ChiefAgent
from app.chief.chief_decision import ChiefDecision
from app.chief.chief_planner import ChiefPlanner
from app.orchestrator.orchestrator import Orchestrator
from app.orchestrator.work_contract import read_contract
from app.organization.team_composer import TeamComposer
from app.storage.database import Database
from app.tools.project_file_tool import ProjectFileTool


class AllocationPolicyTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.o=Orchestrator(Database(self.root/'test.db'))
        self.o.project_file_tool=ProjectFileTool(self.root)
        self.o.tool_registry.register('write_project_file',self.o.project_file_tool.write_file)

    def tearDown(self):self.temp.cleanup()

    def plan(self,decision,request):
        decision=enforce_allocation(decision,request)
        members=TeamComposer('PRJ-TEST',model='any-model').compose(decision.core_roles,decision.specialists)
        return self.o.team_runner._build_plan(request,decision,members),members

    def test_previous_chief_self_assignment_is_normalized_without_plan_recovery(self):
        class Adapter:
            def generate(self,prompt):
                self.prompt=prompt
                return json.dumps({'core_roles':[],'direct_execution':True,
                    'task_assignments':[{'role':'Chief of Staff (Self)','files':['sample.py'],'deliverable':'implementation'}]})
        adapter=Adapter()
        result=self.o.team_runner.analyze_user_request('sample.py 파일을 만들어 주세요.',adapter_factory=lambda model:adapter)
        self.assertEqual(result['source'],'LLM')
        self.assertEqual(result['core_roles'],['Software Developer'])
        self.assertEqual(result['task_assignments'][0]['role'],'Software Developer')
        self.assertFalse(result['direct_execution'])
        self.assertEqual(result['integration_role'],'Software Developer')
        self.assertIn(ALLOCATION_INSTRUCTION,adapter.prompt)
        self.assertIn('not file count',adapter.prompt)

    def test_simple_program_has_one_developer(self):
        d=ChiefPlanner().rule_plan('sample.py 프로그램을 만들어 주세요.')
        plan,members=self.plan(d,'sample.py 프로그램을 만들어 주세요.')
        self.assertEqual(len(members),1);self.assertEqual(len(plan),1)
        self.assertEqual(read_contract(plan[0]['description'])['files'],['sample.py'])
        self.assertIn('[통합 담당]',plan[0]['description'])

    def test_sqlite_usage_alone_does_not_add_database_handoff_role(self):
        decision=ChiefPlanner().rule_plan('SQLite와 SQL 파라미터 바인딩으로 store.py 파일을 작성하세요.')
        self.assertEqual(decision.specialists,[])
        explicit=ChiefPlanner().rule_plan('DB 설계 전문가가 스키마 설계를 하고 store.py를 작성하세요.')
        self.assertTrue(explicit.specialists)

    def test_each_file_retains_its_own_deliverable_for_same_owner(self):
        decision=ChiefDecision(core_roles=['Software Developer'],file_creation_required=True,
            task_assignments=[{'role':'Software Developer','files':['store.py'],'deliverable':'DB 저장'},
                              {'role':'Software Developer','files':['main.py'],'deliverable':'CLI 통합'}])
        plan,_=self.plan(decision,'store.py 및 main.py 파일을 작성하세요.')
        contracts=[read_contract(item['description']) for item in plan]
        self.assertEqual({c['files'][0]:c['deliverable'] for c in contracts},
                         {'store.py':'DB 저장','main.py':'CLI 통합'})

    def test_policy_is_idempotent_and_preserves_file_scope(self):
        d=ChiefDecision(core_roles=['Chief of Staff'],file_creation_required=True,
            task_assignments=[{'role':'Chief of Staff (Self)','files':['src/main.py'],'deliverable':'implementation'}])
        enforce_allocation(d,'src/main.py 파일을 작성해 주세요.');before=d.to_dict()
        enforce_allocation(d,'src/main.py 파일을 작성해 주세요.')
        self.assertEqual(before,d.to_dict());self.assertEqual(d.task_assignments[0]['files'],['src/main.py'])

    def test_non_implementation_reply_can_remain_chief_direct(self):
        d=ChiefPlanner().rule_plan('안녕하세요. 간단히 답해 주세요.')
        self.assertTrue(d.direct_execution);self.assertEqual(d.core_roles,[])
        self.assertEqual(d.integration_role,'')

    def test_qa_cannot_author_files(self):
        d=ChiefDecision(file_creation_required=True,core_roles=['QA','Software Developer'],
            task_assignments=[{'role':'QA','files':['sample.py'],'deliverable':'code'}])
        with self.assertRaisesRegex(ValueError,'QA'):enforce_allocation(d,'sample.py 파일 작성')

    def test_qa_is_not_a_temporary_implementation_role(self):
        d=ChiefDecision(file_creation_required=True,core_roles=['QA','Chief of Staff','Software Developer'])
        enforce_allocation(d,'sample.py 파일 작성')
        self.assertEqual(d.core_roles,['Software Developer'])

    def test_component_developers_are_distinct_and_integrator_is_last(self):
        request='view.js, api.py, schema.sql 파일을 작성해 주세요.'
        proposal={'core_roles':['Backend Developer','Frontend Developer','Database Specialist'],
            'integration_role':'Backend Developer','task_assignments':[
                {'role':'Backend Developer','files':['api.py'],'deliverable':'API and integration'},
                {'role':'Frontend Developer','files':['view.js'],'deliverable':'UI implementation'},
                {'role':'Database Specialist','files':['schema.sql'],'deliverable':'database schema'}]}
        d=ChiefPlanner().parse_llm(json.dumps(proposal),ChiefPlanner().rule_plan(request))
        self.assertEqual(d.source,'LLM');self.assertEqual(len(d.core_roles),3)
        plan,members=self.plan(d,request)
        self.assertEqual(len(members),3)
        integrator=next(m for m in members if m.name=='Backend Developer')
        self.assertEqual(plan[-1]['agent_id'],integrator.agent_id)
        self.assertEqual(plan[-1]['depends_on'],[0,1])
        self.assertIn('[통합 담당]',plan[-1]['description'])
        self.assertEqual({p for task in plan for p in read_contract(task['description'])['files']},
                         {'view.js','api.py','schema.sql'})

    def test_integrator_without_files_reviews_after_ui_implementation(self):
        d=ChiefDecision(core_roles=['DESIGN','Software Developer'],file_creation_required=True,
            task_assignments=[{'role':'DESIGN','files':['view.py'],'deliverable':'UI implementation'}])
        plan,members=self.plan(d,'view.py 파일을 작성해 주세요.')
        self.assertEqual(read_contract(plan[0]['description'])['files'],['view.py'])
        self.assertEqual(read_contract(plan[-1]['description'])['files'],[])
        self.assertIn('[통합 검토 지침]',plan[-1]['description']);self.assertEqual(plan[-1]['depends_on'],[0])

    def test_integrator_must_be_an_assigned_developer(self):
        d=ChiefDecision(core_roles=['DESIGN','Software Developer'],file_creation_required=True,integration_role='DESIGN')
        with self.assertRaisesRegex(ValueError,'통합 담당자'):enforce_allocation(d,'view.py 파일 작성')

    def test_generic_developer_alias_still_deduplicates(self):
        proposal={'core_roles':['DEV','Software Developer'],'task_assignments':[{'role':'DEV','files':['sample.py']}]}
        d=ChiefPlanner().parse_llm(json.dumps(proposal),ChiefPlanner().rule_plan('sample.py 파일 작성'))
        plan,members=self.plan(d,'sample.py 파일 작성')
        self.assertEqual(d.source,'LLM');self.assertEqual(len(members),1);self.assertEqual(len(plan),1)

    def test_confirmed_chief_author_plan_cannot_bypass_execution_policy(self):
        calls=[]
        class Adapter:
            model='any-model'
            def generate(self,prompt):
                contract=read_contract(prompt);calls.append(contract)
                return '<artifact path="sample.py">\ndef add(a,b): return a+b\n</artifact>'
        decision={'core_roles':[],'direct_execution':True,'file_creation_required':False,
                  'task_assignments':[{'role':'Chief of Staff (Self)','files':['sample.py'],'deliverable':'code'}]}
        with contextlib.redirect_stdout(io.StringIO()):
            result=self.o.run_user_request('sample.py 파일을 만들어 주세요.',model_override='any-model',
                analysis_override=decision,qa_mode='rule',approval_mode='manual',verbose=False,
                adapter_factory=lambda model:Adapter())
        self.assertEqual(result['status'],'PENDING_APPROVAL')
        self.assertEqual(calls[0]['owner'],'Software Developer')
        self.assertFalse(result['chief_decision']['direct_execution'])
        self.assertEqual([m['name'] for m in result['team'] if m['agent_type']=='FIXED'],['Chief','QA'])


if __name__=='__main__':unittest.main()
