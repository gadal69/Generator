import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from app.chief.chief_decision import ChiefDecision
from app.orchestrator.orchestrator import Orchestrator
from app.orchestrator.work_contract import read_contract
from app.qa.approval_evidence import make_evidence
from app.qa.project_verifier import ProjectVerifier
from app.qa.verification_contract import build_contract,validate_contract,blocking_failure,required_unverified
from app.runtime.checkpoints import Checkpoints
from app.storage.database import Database
from app.tools.project_file_tool import ProjectFileTool


SOURCE='''def bounded(value):
    if not isinstance(value, int): raise TypeError('integer required')
    if value < 0 or value > 10: raise ValueError('outside range')
    return value * 2
'''


def sample():
    return {'version':1,'requirements':[{'id':'double','description':'0~10 정수를 두 배로 반환하고 잘못된 입력을 거부한다.'}],
            'checks':[{'id':'double_cases','kind':'python_function','target':'sample.py','function':'bounded',
                       'requirements':['double'],'cases':[
                           {'args':[3],'expected':6,'category':'normal'},
                           {'args':['x'],'error':True,'error_type':'TypeError','category':'invalid'},
                           {'args':[-1],'error':True,'error_type':'ValueError','category':'invalid'},
                           {'args':[0],'expected':0,'category':'boundary'},
                           {'args':[10],'expected':20,'category':'boundary'}]}]}


class RequirementVerificationTests(unittest.TestCase):
    def verify(self,raw=None,source=SOURCE):
        return ProjectVerifier().verify([{'path':'sample.py','content':source}],build_contract('',raw or sample()))

    def test_all_three_categories_mapped_and_pass(self):
        rows=self.verify()
        self.assertEqual(rows[-1]['categories'],{'normal':'PASS','invalid':'PASS','boundary':'PASS'})
        self.assertEqual(rows[-1]['linked_checks'],['double_cases'])
        self.assertFalse(required_unverified(rows));self.assertFalse(blocking_failure(rows))

    def test_boundary_omission_does_not_pass_requirement(self):
        raw=sample();raw['checks'][0]['cases']=raw['checks'][0]['cases'][:3]
        rows=self.verify(raw)
        self.assertEqual(rows[0]['status'],'PASS')
        self.assertEqual(rows[-1]['status'],'NOT_RUN')
        self.assertEqual(rows[-1]['missing_categories'],['boundary'])

    def test_wrong_expected_value_is_failure_even_with_full_coverage(self):
        raw=sample();raw['checks'][0]['cases'][0]['expected']=999
        rows=self.verify(raw)
        self.assertTrue(blocking_failure(rows))
        self.assertEqual(rows[-1]['categories']['normal'],'FAIL')

    def test_disabled_case_does_not_run_and_does_not_pass(self):
        raw=sample();raw['checks'][0]['cases'][0]['enabled']=False
        rows=self.verify(raw)
        self.assertEqual(rows[0]['cases'][0]['status'],'NOT_RUN')
        self.assertEqual(rows[-1]['categories']['normal'],'NOT_RUN')

    def test_disabled_check_never_calls_engine(self):
        raw=sample();raw['checks'][0]['enabled']=False
        with patch.object(ProjectVerifier,'_check') as execute:
            rows=self.verify(raw)
        execute.assert_not_called()
        self.assertTrue(required_unverified(rows))

    def test_model_cannot_self_confirm_nested_expectations(self):
        raw=sample();raw['source']='USER';raw['checks'][0]['cases'][0]['expectation_source']='USER'
        proposed=build_contract('',proposed=raw)
        self.assertEqual(proposed['source'],'PROPOSED')
        self.assertEqual(proposed['checks'][0]['cases'][0]['expectation_source'],'PROPOSED')
        with patch.object(ProjectVerifier,'_check') as execute:
            rows=ProjectVerifier().verify([{'path':'sample.py','content':SOURCE}],proposed)
        execute.assert_not_called();self.assertTrue(required_unverified(rows))
        confirmed=build_contract('',proposed=raw,confirmed=True)
        self.assertEqual(confirmed['expectation_origin'],'MODEL')
        self.assertEqual(confirmed['source'],'REPRESENTATIVE_CONFIRMED')

    def test_user_expectations_override_model_proposal(self):
        proposed=sample();proposed['checks'][0]['cases'][0]['expected']=999
        actual=build_contract('',supplied=sample(),proposed=proposed,confirmed=True)
        self.assertEqual(actual['checks'][0]['cases'][0]['expected'],6)
        self.assertEqual(actual['expectation_origin'],'USER')

    def test_missing_requirement_and_unmapped_check_are_not_run(self):
        raw=sample();raw['checks'][0]['requirements']=[]
        rows=ProjectVerifier().verify([{'path':'sample.py','content':SOURCE}],build_contract('',proposed=raw,confirmed=True))
        self.assertTrue(required_unverified(rows))
        self.assertIn('traceability:double_cases',[row['check_id'] for row in rows])

    def test_optional_case_check_cannot_fill_required_coverage(self):
        raw=sample();raw['checks'][0]['required']=False
        self.assertTrue(required_unverified(self.verify(raw)))

    def test_static_requirements_can_confirm_applicable_categories(self):
        raw=sample();raw['requirements'][0]['required_categories']=['normal']
        raw['requirements'][0]['category_rationale']='파일 존재만 확인하는 요구사항'
        raw['checks']=[{'id':'file','kind':'file','target':'sample.py','requirements':['double']}]
        self.assertEqual(self.verify(raw)[-1]['status'],'PASS')

    def test_unknown_status_is_unverified(self):
        self.assertTrue(required_unverified([{'status':'SKIPPED','required':True}]))
        self.assertFalse(required_unverified([{'status':'SKIPPED','required':False}]))

    def test_invalid_category_and_nonboolean_skip_are_rejected(self):
        for field,value in [('category','easy'),('enabled','false')]:
            raw=sample();raw['checks'][0]['cases'][0][field]=value
            with self.assertRaises(ValueError):validate_contract(raw)

    def test_empty_text_predicates_cannot_claim_pass(self):
        with self.assertRaises(ValueError):
            validate_contract({'checks':[{'id':'empty','kind':'text','target':'x.txt','contains':[]}]})

    def test_docker_categories_preserve_case_identity(self):
        raw=sample();raw['checks'][0]['engine']='docker'
        observations=[{'value':6},{'exception':'TypeError'},{'exception':'ValueError'},{'value':0},{'value':20}]
        with patch('app.qa.project_verifier.IsolatedRunner.run',return_value={
                'status':'OBSERVED','observation':{'cases':observations}}):
            rows=self.verify(raw)
        self.assertEqual(rows[0]['scope'],'DOCKER_FUNCTION')
        self.assertEqual([case['category'] for case in rows[0]['cases']],['normal','invalid','invalid','boundary','boundary'])
        self.assertEqual(rows[-1]['status'],'PASS')

    def test_unavailable_runner_and_unsupported_checks_stay_unverified(self):
        raw=sample();raw['checks'][0]['engine']='docker'
        with patch('app.qa.project_verifier.IsolatedRunner.run',return_value={'status':'NOT_RUN','reason':'offline'}):
            self.assertTrue(required_unverified(self.verify(raw)))
        raw['checks']=[{'id':'gui','kind':'gui','target':'sample.py','requirements':['double']}]
        self.assertTrue(required_unverified(self.verify(raw)))


class FinalApprovalVerificationTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.o=Orchestrator(Database(self.root/'test.db'))
        self.o.project_file_tool=ProjectFileTool(self.root)
        self.o.tool_registry.register('write_project_file',self.o.project_file_tool.write_file)

    def tearDown(self):self.temp.cleanup()

    def run_flow(self,raw=None,approval='manual'):
        class Adapter:
            model='verification-fixture'
            def generate(self,prompt):
                c=read_contract(prompt)
                return '<artifact path="sample.py">\n'+SOURCE+'</artifact>' if c and c.get('files') else '완료'
        decision=ChiefDecision(core_roles=['Software Developer'],file_creation_required=True,verification_contract=raw or sample()).to_dict()
        with contextlib.redirect_stdout(io.StringIO()):
            return self.o.run_user_request('sample.py 파일을 만들어 주세요.',analysis_override=decision,
                adapter_factory=lambda model:Adapter(),model_override='verification-fixture',qa_mode='rule',
                approval_mode=approval,max_reworks=0,verbose=False)

    def test_complete_verified_result_can_be_manually_approved(self):
        result=self.run_flow()
        self.o.approve(result['approval_id'],'APPROVED')
        self.assertEqual(self.o.approval_manager.get(result['approval_id'])[5],'APPROVED')

    def test_complete_verified_result_can_be_auto_approved(self):
        self.assertEqual(self.run_flow(approval='auto')['status'],'APPROVED')

    def test_missing_boundary_blocks_auto_and_manual_approval(self):
        raw=sample();raw['checks'][0]['cases']=raw['checks'][0]['cases'][:3]
        result=self.run_flow(raw,approval='auto')
        self.assertEqual(result['status'],'PENDING_APPROVAL')
        with self.assertRaisesRegex(ValueError,'누락|미실행'):self.o.approve(result['approval_id'],'APPROVED')
        self.assertEqual(self.o.approval_manager.get(result['approval_id'])[5],'PENDING')

    def test_skipped_case_blocks_manual_approval(self):
        raw=sample();raw['checks'][0]['cases'][0]['enabled']=False
        result=self.run_flow(raw)
        with self.assertRaises(ValueError):self.o.approve(result['approval_id'],'APPROVED')

    def test_required_failure_creates_no_final_approval(self):
        raw=sample();raw['checks'][0]['cases'][0]['expected']=999
        with self.assertRaises(RuntimeError):self.run_flow(raw)
        with self.o.database.connection() as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM approvals').fetchone()[0],0)

    def test_changed_file_and_contract_cannot_reuse_pass(self):
        result=self.run_flow()
        path=self.root/'projects'/result['project_id']/'artifacts/sample.py'
        original=path.read_text(encoding='utf-8');path.write_text('pass',encoding='utf-8')
        with self.assertRaisesRegex(ValueError,'파일'):self.o.approve(result['approval_id'],'APPROVED')
        path.write_text(original,encoding='utf-8')
        status,state=Checkpoints(self.o.database).load(result['project_id'])
        state['decision']['verification_contract']['requirements'][0]['description']='changed'
        Checkpoints(self.o.database).save(result['project_id'],state,status)
        with self.assertRaisesRegex(ValueError,'기준'):self.o.approve(result['approval_id'],'APPROVED')

    def test_newer_incomplete_revalidation_blocks_old_pass(self):
        result=self.run_flow();status,state=Checkpoints(self.o.database).load(result['project_id'])
        evidence=state['final_verification'];evidence['status']='NOT_RUN'
        evidence['records']=[{'status':'NOT_RUN','required':True}]
        Checkpoints(self.o.database).save(result['project_id'],state,status)
        with self.assertRaises(ValueError):self.o.approve(result['approval_id'],'APPROVED')

    def test_result_edit_and_added_file_need_reverification(self):
        result=self.run_flow()
        path=self.root/'projects'/result['project_id']/'artifacts'/'extra.py'
        path.write_text('pass',encoding='utf-8')
        with self.assertRaisesRegex(ValueError,'목록'):self.o.approve(result['approval_id'],'APPROVED')
        path.unlink()
        with self.o.database.connection() as conn:
            conn.execute('UPDATE results SET summary=? WHERE result_id=?',('changed',result['final_result_id']))
        with self.assertRaisesRegex(ValueError,'결과가 변경'):self.o.approve(result['approval_id'],'APPROVED')

    def test_explicit_examples_without_requirement_mapping_do_not_auto_approve(self):
        raw=sample();raw['requirements']=[];raw['checks'][0]['requirements']=[]
        result=self.run_flow(raw,approval='auto')
        self.assertEqual(result['status'],'PENDING_APPROVAL')
        with self.assertRaises(ValueError):self.o.approve(result['approval_id'],'APPROVED')

    def test_legacy_program_without_checkpoint_cannot_bypass_final_evidence(self):
        result=self.run_flow()
        with self.o.database.connection() as conn:
            conn.execute('DELETE FROM workflow_checkpoints WHERE project_id=?',(result['project_id'],))
        with self.assertRaisesRegex(ValueError,'검증 기록'):self.o.approve(result['approval_id'],'APPROVED')
