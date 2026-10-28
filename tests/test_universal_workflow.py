import contextlib
import io
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from app.chief.chief_decision import ChiefDecision
from app.orchestrator.orchestrator import Orchestrator
from app.orchestrator.work_contract import read_contract
from app.qa.verification_contract import build_contract,validate_contract,scope_contract,blocking_failure,required_unverified
from app.qa.project_verifier import ProjectVerifier
from app.qa.isolated_runner import IsolatedRunner
from app.orchestrator.recovery_policy import recovery_action,ReplanRequired
from app.runtime.run_control import RunControl,RunCancelled
from app.runtime.checkpoints import Checkpoints
from app.storage.database import Database
from app.tools.project_file_tool import ProjectFileTool,requested_output_paths


def contract(checks,requirements=None):
    return build_contract('',{'version':1,'checks':checks,'requirements':requirements or []})


class ContractTests(unittest.TestCase):
    def test_text_json_and_file_registry(self):
        checks=[{'id':'text','kind':'text','target':'Views/Page.html','contains':['Welcome'],'not_contains':['placeholder']},
                {'id':'json','kind':'json_value','target':'Config.json','path':['items',0,'enabled'],'expected':True},
                {'id':'file','kind':'file','target':'Views/Page.html'}]
        rows=ProjectVerifier().verify([{'path':'Views/Page.html','content':'<h1>Welcome</h1>'},
                                      {'path':'Config.json','content':'{"items":[{"enabled":true}]}'}],contract(checks))
        self.assertEqual([r['status'] for r in rows],['PASS']*3)

    def test_json_boolean_does_not_equal_integer(self):
        c=contract([{'id':'j','kind':'json_value','target':'x.json','expected':1}])
        self.assertTrue(blocking_failure(ProjectVerifier().verify([{'path':'x.json','content':'true'}],c)))

    def test_unknown_check_and_uncovered_requirement_are_not_run(self):
        c=contract([{'id':'gui','kind':'gui','target':'view.py'}],[{'id':'r','description':'button behavior'}])
        rows=ProjectVerifier().verify([{'path':'view.py','content':'x=1'}],c)
        self.assertEqual([row['status'] for row in rows],['NOT_RUN','NOT_RUN'])
        self.assertTrue(required_unverified(rows))

    def test_required_coverage_and_optional_failure(self):
        c=contract([{'id':'optional','kind':'file','target':'absent.txt','required':False},
                    {'id':'required','kind':'file','target':'yes.txt','requirements':['r']}],
                   [{'id':'r','description':'deliverable'}])
        rows=ProjectVerifier().verify([{'path':'yes.txt','content':'ok'}],c)
        self.assertFalse(blocking_failure(rows));self.assertFalse(required_unverified(rows))
        self.assertEqual(rows[-1]['status'],'PASS')

    def test_multiple_requirements_do_not_treat_coverage_rows_as_checks(self):
        c=contract([{'id':'file','kind':'file','target':'main.py','requirements':['r1']},
                    {'id':'text','kind':'text','target':'main.py','contains':['ready'],'requirements':['r2']},
                    {'id':'run','kind':'command','target':'main.py','argv':['python','main.py'],'requirements':['r3']}],
                   [{'id':'r1','description':'file exists'},{'id':'r2','description':'ready output'},
                    {'id':'r3','description':'program runs'}])
        with patch.object(IsolatedRunner,'run',return_value={'status':'NOT_RUN','reason':'Docker unavailable'}):
            rows=ProjectVerifier().verify([{'path':'main.py','content':'print("ready")'}],c)
        self.assertEqual([row['status'] for row in rows if row['type']=='COVERAGE'],['PASS','PASS','NOT_RUN'])
        self.assertFalse(blocking_failure(rows));self.assertTrue(required_unverified(rows))

    def test_model_cannot_confirm_its_own_contract(self):
        proposal={'version':1,'source':'USER','checks':[{'id':'f','kind':'file','target':'x.txt'}]}
        proposed=build_contract('',proposed=proposal)
        self.assertEqual(proposed['source'],'PROPOSED')
        self.assertEqual(ProjectVerifier().verify([{'path':'x.txt','content':'ok'}],proposed)[0]['status'],'NOT_RUN')
        self.assertEqual(build_contract('',proposed=proposal,confirmed=True)['source'],'REPRESENTATIVE_CONFIRMED')

    def test_user_contract_wins_over_model(self):
        supplied=contract([{'id':'user','kind':'file','target':'x.txt'}])
        request='[검증 계약]\n'+json.dumps(supplied)+'\n[/검증 계약]'
        result=build_contract(request,supplied,{'version':1,'checks':[]},True)
        self.assertEqual(result['checks'][0]['id'],'user')

    def test_rejects_invalid_contracts(self):
        for check in [{'id':'x','kind':'file','target':'../secret'},
                      {'id':'x','kind':'command','target':'x.py','argv':[]},
                      {'id':'x','kind':'json_value','target':'x.json','path':[{}],'expected':1},
                      {'id':'x','kind':'command','target':'x.py','argv':['python'],'timeout':float('inf')}]:
            with self.subTest(check=check),self.assertRaises((ValueError,TypeError)):
                validate_contract({'checks':[check]})

    def test_paths_and_partial_scope_preserve_case_and_directories(self):
        c=contract([{'id':'x','kind':'file','target':'src/Rules.py'},
                    {'id':'run','kind':'command','target':'Main.py','argv':['python','Main.py']}])
        self.assertEqual(scope_contract(c,['src/rules.py'])['checks'][0]['target'],'src/Rules.py')
        self.assertEqual(len(scope_contract(c,['src/Rules.py','Main.py'])['checks']),1)
        request='src/Rules.py 및 Main.py 파일을 만들어 주세요.\n[검증 계약]\n'+json.dumps(c)+'\n[/검증 계약]'
        self.assertEqual(requested_output_paths(request),['src/Rules.py','Main.py'])

    def test_docker_functions_wait_for_full_project_bundle(self):
        c=contract([{'id':'f','kind':'python_function','engine':'docker','target':'Main.py','function':'run','cases':[{'args':[],'expected':1}]}])
        self.assertEqual(scope_contract(c,['Main.py'])['checks'],[])
        self.assertEqual(build_contract('src/Rules.py 파일을 작성하세요. add(2,3)=5')['checks'][0]['target'],'src/Rules.py')

    def test_multi_file_checks_have_explicit_function_ownership(self):
        c=contract([{'id':'a','kind':'python_function','target':'a.py','function':'convert','cases':[{'args':[2],'expected':4}]},
                    {'id':'b','kind':'python_function','target':'b.py','function':'convert','cases':[{'args':[2],'expected':3}]}])
        rows=ProjectVerifier().verify([{'path':'a.py','content':'def convert(x): return x*2'},
                                      {'path':'b.py','content':'def convert(x): return x+1'}],c)
        self.assertEqual([r['status'] for r in rows],['PASS','PASS'])

    def test_missing_docker_never_executes_host_command(self):
        with patch('app.qa.docker_environment.resolve_docker',return_value=None),patch('app.qa.isolated_runner.subprocess.Popen') as popen:
            result=IsolatedRunner().run([],{'kind':'command','argv':['dangerous-host-command']})
        self.assertEqual(result['status'],'NOT_RUN');popen.assert_not_called()

    def test_file_qa_reports_deferred_runtime_checks_instead_of_silently_omitting(self):
        c=contract([{'id':'run','kind':'command','target':'x.py','argv':['python','x.py']}])
        scoped=scope_contract(c,['x.py'])
        with patch.object(IsolatedRunner,'run') as run:
            rows=ProjectVerifier().verify([{'path':'x.py','content':'pass'}],scoped,partial=True)
        run.assert_not_called()
        self.assertEqual(rows[0]['status'],'NOT_RUN')
        self.assertEqual(rows[0]['scope'],'INTEGRATION_DEFERRED')
        self.assertEqual(scope_contract(scoped,['x.py'])['deferred_checks'],scoped['deferred_checks'])

    def test_duplicate_paths_and_malformed_observations_fail(self):
        c=contract([{'id':'run','kind':'command','target':'x.py','argv':['python','x.py']}])
        with self.assertRaises(ValueError):
            ProjectVerifier().verify([{'path':'X.py','content':''},{'path':'x.py','content':''}],c)
        with patch.object(IsolatedRunner,'run',return_value={'status':'OBSERVED','observation':[]}):
            self.assertEqual(ProjectVerifier().verify([{'path':'x.py','content':''}],c)[0]['status'],'FAIL')

    def test_isolated_results_are_compared_by_host(self):
        c=contract([{'id':'run','kind':'command','target':'Main.py','argv':['python','Main.py'],'stdout':'expected'}])
        with patch.object(IsolatedRunner,'run',return_value={'status':'OBSERVED','observation':{'exit_code':0,'stdout':'wrong','status':'PASS'}}):
            self.assertEqual(ProjectVerifier().verify([{'path':'Main.py','content':'print(1)'}],c)[0]['status'],'FAIL')

    def test_container_arguments_and_payload_are_separate(self):
        class Process:
            def __init__(self,argv,**kwargs):
                self.argv=argv;self.stdin=io.BytesIO();self.stdout=io.BytesIO(b'{"exit_code":0,"stdout":"ok"}');self.returncode=0
                self.payload=None
                self.stdin.close=lambda:None
            def poll(self):return 0
        seen=[]
        def launch(argv,**kwargs):
            process=Process(argv,**kwargs);seen.append(process);return process
        with patch.object(IsolatedRunner,'available',return_value=(('docker','trusted-image'),None)),\
             patch('app.qa.isolated_runner.subprocess.Popen',side_effect=launch),\
             patch('app.qa.isolated_runner.subprocess.run'):
            result=IsolatedRunner().run([{'path':'Main.py','content':'print("ok")'}],
                {'kind':'command','target':'Main.py','argv':['python','Main.py','--privileged'],'stdout':'secret-expected'})
        self.assertEqual(result['status'],'OBSERVED')
        argv=seen[0].argv;payload=seen[0].stdin.getvalue().decode()
        for flag in ['--network','none','--read-only','--cap-drop','--pids-limit','--memory','--user']:self.assertIn(flag,argv)
        self.assertNotIn('--privileged',argv);self.assertNotIn('secret-expected',payload)
        self.assertIn('Main.py',payload)

    def test_cancel_waiting_model_does_not_accept_late_result(self):
        control=RunControl();release=threading.Event()
        timer=threading.Timer(.1,control.cancel);timer.start();started=time.monotonic()
        try:
            with self.assertRaises(RunCancelled):control.call(lambda:release.wait(2))
            self.assertLess(time.monotonic()-started,1)
        finally:release.set();timer.join()

    def test_recovery_changes_strategy_then_requires_replan(self):
        first=recovery_action(0,2,[],['X'],'same')
        second=recovery_action(1,2,[first],['X'],'same')
        self.assertEqual(first['action'],'TARGETED_REPAIR')
        self.assertEqual(second['action'],'REWRITE_WITHIN_SCOPE')
        self.assertEqual(recovery_action(2,2,[first,second],['X'],'same')['action'],'REPLAN')


class UniversalFlowTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.o=Orchestrator(Database(self.root/'test.db'));self.o.project_file_tool=ProjectFileTool(self.root)
        self.o.tool_registry.register('write_project_file',self.o.project_file_tool.write_file)
        self.decision=ChiefDecision(core_roles=['Software Developer'],file_creation_required=True).to_dict()

    def tearDown(self):self.temp.cleanup()

    def run_flow(self,adapter,request='sample.py 파일을 만들어 주세요.',**kwargs):
        settings=dict(model_override='any-model:v1',qa_mode='rule',approval_mode='manual',max_reworks=2,
                      verbose=False,analysis_override=self.decision,adapter_factory=lambda model:adapter)
        settings.update(kwargs)
        with contextlib.redirect_stdout(io.StringIO()):return self.o.run_user_request(request,**settings)

    def adapter(self,calls):
        class Adapter:
            model='any-model:v1'
            def generate(self,prompt):
                c=read_contract(prompt)
                if c and c['files']:
                    calls.append(c['files'][0]);return '<artifact path="'+c['files'][0]+'">\ndef add(a,b): return a+b\n</artifact>'
                return 'complete'
        return Adapter()

    def test_cancel_then_resume_reuses_accepted_output(self):
        calls=[];control=RunControl()
        control.callback=lambda event:control.cancel() if event['stage']=='task_complete' else None
        result=self.run_flow(self.adapter(calls),control=control)
        self.assertEqual(result['status'],'CANCELLED');self.assertEqual(calls,['sample.py'])
        resumed=self.run_flow(self.adapter(calls),resume=True,resume_project_id=result['project_id'],analysis_override=None)
        self.assertEqual(resumed['status'],'PENDING_APPROVAL');self.assertEqual(calls,['sample.py'])
        status,state=Checkpoints(self.o.database).load(result['project_id']);self.assertEqual(status,'COMPLETE')
        with self.o.database.connection() as conn:
            stages=[row[0] for row in conn.execute('SELECT stage FROM workflow_events WHERE project_id=?',(result['project_id'],))]
        self.assertIn('integration',stages);self.assertIn('complete',stages)

    def test_cancel_after_generation_reuses_pending_result(self):
        calls=[];control=RunControl()
        control.callback=lambda event:control.cancel() if event['stage']=='qa' else None
        result=self.run_flow(self.adapter(calls),control=control)
        self.assertEqual(result['status'],'CANCELLED')
        self.run_flow(self.adapter(calls),resume=True,resume_project_id=result['project_id'],analysis_override=None)
        self.assertEqual(calls,['sample.py'])

    def test_resume_rejects_changed_request_model_and_settings(self):
        control=RunControl();control.cancel();calls=[]
        result=self.run_flow(self.adapter(calls),control=control)
        original=self.o.team_runner.adapter_factory
        for changed in [{'request':'other.py 파일을 만들어 주세요.'},{'model_override':'other-model'}, {'approval_mode':'auto'}]:
            with self.subTest(changed=changed),self.assertRaises(ValueError):
                self.run_flow(self.adapter(calls),resume=True,resume_project_id=result['project_id'],analysis_override=None,**changed)
            self.assertIs(self.o.team_runner.adapter_factory,original)

    def test_no_verification_contract_does_not_auto_approve(self):
        result=self.run_flow(self.adapter([]),approval_mode='auto')
        self.assertEqual(result['status'],'PENDING_APPROVAL')
        self.assertTrue(required_unverified(result['code_verifications']))

    def test_dependency_failure_is_visible_and_blocks_automatic_approval(self):
        self.decision['library_requirements']=[{'package':'Pillow','runtime':'python','version':''}]
        self.decision['verification_contract']=contract([{'id':'exists','kind':'file','target':'sample.py'}])
        blocked={'status':'NOT_READY','requested':self.decision['library_requirements'],
                 'reason':'No matching distribution','remedy':'패키지 버전 확인'}
        with patch('app.qa.project_environment.prepare_project',return_value=(
                {'images':{},'project_dependencies':blocked},blocked)):
            result=self.run_flow(self.adapter([]),approval_mode='auto')
        self.assertEqual(result['status'],'PENDING_APPROVAL')
        self.assertIn('No matching distribution',result['final_result'])
        self.assertTrue(required_unverified(result['code_verifications']))
        self.assertIsNone(self.o.team_runner._project_environment)

    def test_summary_counts_same_file_once_for_multiple_checks(self):
        self.decision['verification_contract']=contract([
            {'id':'first','kind':'python_function','target':'sample.py','function':'add','cases':[{'args':[2,3],'expected':5}]},
            {'id':'second','kind':'python_function','target':'sample.py','function':'add','cases':[{'args':[7,8],'expected':15}]}])
        result=self.run_flow(self.adapter([]))
        expected='1개 파일에서 2개 항목 통과'
        self.assertIn(expected,result['final_result'])
        stored=self.o.result_manager.get_result(result['final_result_id'])
        self.assertIn(expected,stored[3])
        artifact=self.root/'projects'/result['project_id']/'final_result.txt'
        self.assertIn(expected,artifact.read_text(encoding='utf-8'))

    def test_summary_counts_same_basename_in_distinct_folders_separately(self):
        self.decision['verification_contract']=contract([
            {'id':'left','kind':'python_function','target':'left/sample.py','function':'add','cases':[{'args':[2,3],'expected':5}]},
            {'id':'right','kind':'python_function','target':'right/sample.py','function':'add','cases':[{'args':[7,8],'expected':15}]}])
        result=self.run_flow(self.adapter([]),request='left/sample.py 및 right/sample.py 파일을 만들어 주세요.')
        self.assertIn('2개 파일에서 2개 항목 통과',result['final_result'])

    def test_integration_repair_is_bounded_and_saved(self):
        calls=[]
        self.decision['verification_contract']=contract([{'id':'run','kind':'command','target':'sample.py','argv':['python','sample.py']}])
        with patch.object(IsolatedRunner,'run',side_effect=[{'status':'FAIL','reason':'integration mismatch'},
                     {'status':'OBSERVED','observation':{'exit_code':0,'stdout':''}},
                     {'status':'OBSERVED','observation':{'exit_code':0,'stdout':''}}]):
            result=self.run_flow(self.adapter(calls))
        self.assertEqual(result['status'],'PENDING_APPROVAL');self.assertEqual(len(calls),2)
        status,saved=Checkpoints(self.o.database).load(result['project_id'])
        self.assertEqual(saved['integration_attempts']['sample.py'],1)
        self.assertIsNone(saved['integration_pending'])

    def test_repeated_integration_failure_requires_new_plan(self):
        calls=[]
        self.decision['verification_contract']=contract([{'id':'run','kind':'command','target':'sample.py','argv':['python','sample.py']}])
        with patch.object(IsolatedRunner,'run',return_value={'status':'FAIL','reason':'same mismatch'}),self.assertRaises(ReplanRequired):
            self.run_flow(self.adapter(calls))
        with self.o.database.connection() as conn:
            self.assertEqual(conn.execute('SELECT status FROM workflow_checkpoints').fetchone()[0],'REPLAN_REQUIRED')
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM approvals').fetchone()[0],0)
        self.assertEqual(len(calls),3)
        with self.o.database.connection() as conn:
            project=conn.execute('SELECT project_id FROM workflow_checkpoints').fetchone()[0]
        _,saved=Checkpoints(self.o.database).load(project)
        self.assertEqual([r['status'] for r in saved['qa_audit'] if r['phase']=='INTEGRATION'],['FAIL']*3)
        self.assertFalse(saved['replan_recommendation']['automatic_resume'])
        notice=self.root/'projects'/project/'final_result.txt'
        self.assertIn('다음 조치',notice.read_text(encoding='utf-8'))
        with self.assertRaises(ValueError):
            self.run_flow(self.adapter(calls),resume=True,resume_project_id=project,analysis_override=None)

    def test_repair_audit_survives_cancel_resume_and_records_actual_change(self):
        calls=[]
        class Adapter:
            model='any-model:v1'
            def generate(self,prompt):
                calls.append(prompt)
                return '<artifact path="sample.py">\ndef add(a,b): return '+('0' if len(calls)==1 else 'a+b')+'\n</artifact>'
        self.decision['verification_contract']=contract([{'id':'add','kind':'python_function','target':'sample.py',
            'function':'add','cases':[{'args':[2,3],'expected':5}]}])
        control=RunControl();control.callback=lambda e:control.cancel() if e['stage']=='rework' else None
        stopped=self.run_flow(Adapter(),control=control)
        self.assertEqual(stopped['status'],'CANCELLED')
        resumed=self.run_flow(Adapter(),resume=True,resume_project_id=stopped['project_id'],analysis_override=None)
        _,state=Checkpoints(self.o.database).load(stopped['project_id'])
        history=[r for r in state['qa_audit'] if r['phase']=='LOCAL_QA']
        self.assertEqual(history[0]['status'],'FAIL');self.assertEqual(history[-1]['status'],'PASS_WITH_WARNING')
        self.assertEqual(history[-1]['change']['kind'],'MODIFIED')
        self.assertIn('a+b',history[-1]['change']['diff_excerpt'])
        self.assertEqual(len(calls),2)
        self.assertEqual(resumed['status'],'PENDING_APPROVAL')

    def test_gemini_option_and_explicit_approval_cannot_be_bypassed(self):
        calls=[]
        def factory():calls.append('cloud');raise AssertionError('must not transmit')
        self.run_flow(self.adapter([]),gemini_adapter_factory=factory)
        self.assertEqual(calls,[])
        with patch.object(self.o.team_runner,'_cloud_approval_required',return_value=False):
            result=self.run_flow(self.adapter([]),gemini_code_review=True,gemini_adapter_factory=factory)
        self.assertEqual(result['status'],'CLOUD_APPROVAL_PENDING');self.assertEqual(calls,[])

    def test_gemini_pass_does_not_override_execution_failure(self):
        cloud_calls=[]
        class Gemini:
            model='mock-gemini'
            def generate(self,prompt):
                cloud_calls.append(prompt)
                return json.dumps({'verdict':'PASS','summary':'review only','issues':[],
                    'optimized_artifacts':[{'path':'sample.py','content':'def add(a,b): return a+b'}]})
        class Local:
            model='any-model:v1'
            def generate(self,prompt):
                if prompt.startswith('당신은 독립 QA Agent'):return 'VERDICT: PASS\ncode review only'
                return '<artifact path="sample.py">\ndef add(a,b): return a+b\n</artifact>'
        self.decision['verification_contract']=contract([{'id':'run','kind':'command','target':'sample.py','argv':['python','sample.py']}])
        pending=self.run_flow(Local(),qa_mode='agent',gemini_code_review=True,gemini_adapter_factory=Gemini)
        self.o.team_runner.approval_gate.decide(pending['cloud_approval_id'],'APPROVED')
        with patch.object(IsolatedRunner,'run',return_value={'status':'FAIL','reason':'measured crash'}),self.assertRaises(ReplanRequired):
            self.run_flow(Local(),qa_mode='agent',gemini_code_review=True,gemini_adapter_factory=Gemini,
                resume_project_id=pending['project_id'],cloud_approval_ids={'code_qa':pending['cloud_approval_id']})
        self.assertTrue(cloud_calls)
        with self.o.database.connection() as conn:self.assertEqual(conn.execute('SELECT count(*) FROM approvals').fetchone()[0],0)

    def test_cloud_scope_and_pretransmission_recheck(self):
        from app.models.cloud_gateway import CloudGateway
        from unittest.mock import Mock
        project=self.o.create_project('scope-check');gate=self.o.team_runner.approval_gate
        approval=gate.request_cloud(project,'AGT-CHIEF-001','review','code_qa','masked code')
        gate.decide(approval,'APPROVED')
        adapter=Mock();factory=Mock(return_value=adapter)
        gateway=CloudGateway(self.o.database,gate,adapter_factory=factory)
        with self.assertRaises(PermissionError):gateway.request_or_adapter(project,'review','other','masked code',approval_id=approval)
        factory.assert_not_called()
        wrapper=gateway.request_or_adapter(project,'review','code_qa','masked code',approval_id=approval)
        with self.o.database.connection() as conn:conn.execute("UPDATE model_approvals SET status='REJECTED' WHERE approval_id=?",(approval,))
        with self.assertRaises(PermissionError):wrapper.generate('must not send')
        adapter.generate.assert_not_called()

    def test_rejected_integration_repair_is_explained_to_next_rewrite(self):
        prompts=[]
        class Adapter:
            model='any-model:v1'
            def generate(self,prompt):
                prompts.append(prompt)
                output='<artifact path="sample.py">\ndef add(a,b): return a+b\n</artifact>'
                return '[MODEL_RESPONSE_INCOMPLETE]\n'+output if len(prompts)==2 else output
        self.decision['verification_contract']=contract([{'id':'run','kind':'command','target':'sample.py','argv':['python','sample.py']}])
        with patch.object(IsolatedRunner,'run',side_effect=[{'status':'FAIL','reason':'same mismatch'},
                {'status':'FAIL','reason':'same mismatch'},
                {'status':'OBSERVED','observation':{'exit_code':0,'stdout':''}},
                {'status':'OBSERVED','observation':{'exit_code':0,'stdout':''}}]):
            result=self.run_flow(Adapter())
        self.assertIn('previous_repair_rejection',prompts[2])
        self.assertIn('rewrite_from_requirements',prompts[2])
        self.assertIn('"content": null',prompts[2])
        _,state=Checkpoints(self.o.database).load(result['project_id'])
        history=state['integration_recovery_history']['sample.py']
        self.assertEqual(history[1]['action'],'REWRITE_WITHIN_SCOPE')
        self.assertIn('응답 중단',history[0]['qa_rejection']['summary'])

    def test_cancel_during_model_call_leaves_task_resumable(self):
        control=RunControl();release=threading.Event();started=threading.Event()
        class SlowAdapter:
            model='any-model:v1'
            def generate(self,prompt):
                started.set();release.wait(3);return 'late output'
        def cancel_when_started():
            started.wait(2);control.cancel()
        helper=threading.Thread(target=cancel_when_started);helper.start()
        try:
            result=self.run_flow(SlowAdapter(),control=control)
            self.assertEqual(result['status'],'CANCELLED')
            with self.o.database.connection() as conn:
                self.assertEqual(conn.execute('SELECT COUNT(*) FROM results').fetchone()[0],0)
                self.assertEqual(conn.execute('SELECT status FROM tasks LIMIT 1').fetchone()[0],'READY')
            resumed=self.run_flow(self.adapter([]),resume=True,resume_project_id=result['project_id'],analysis_override=None)
            self.assertEqual(resumed['status'],'PENDING_APPROVAL')
        finally:release.set();helper.join()

    def test_gui_controls_preserve_cancelled_request_and_show_contract(self):
        import os
        os.environ['QT_QPA_PLATFORM']='offscreen'
        from qtpy import QtWidgets
        from app.gui import qt_dashboard
        app=QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        with patch.object(qt_dashboard,'make_database',return_value=self.o.database):
            window=qt_dashboard.Dashboard()
            window.request_input.setPlainText('keep this request')
            window._run_succeeded({'status':'CANCELLED','project_id':'test'})
            self.assertEqual(window.request_input.toPlainText(),'keep this request')
            self.assertFalse(window.cancel_button.isEnabled())
            self.assertEqual(window.resume_button.text(),'중단 작업 재개')
            window._display_decision({'chief_decision':{'verification_contract':contract([{'id':'f','kind':'file','target':'x.py'}])}})
            self.assertIn('검증 계약',window.decision_summary.toPlainText())
            window.refresh_timer.stop();window.close();app.processEvents()

    def test_gui_worker_cancel_keeps_controls_and_thread_lifecycle_consistent(self):
        import os
        os.environ['QT_QPA_PLATFORM']='offscreen'
        from qtpy import QtWidgets
        from app.gui import qt_dashboard
        app=QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        release=threading.Event();started=threading.Event()
        class SlowAdapter:
            model='any-model:v1'
            def generate(self,prompt):started.set();release.wait(3);return 'late output'
        with patch.object(qt_dashboard,'make_database',return_value=self.o.database),\
             patch('app.orchestrator.team_runner.create_local_adapter',return_value=SlowAdapter()):
            window=qt_dashboard.Dashboard();window.request_input.setPlainText('sample.py 파일을 만들어 주세요.')
            window.worker=qt_dashboard.RunWorker(window.request_input.toPlainText(),'any-model:v1','rule','manual',analysis_override=self.decision,parent=window)
            window.worker.succeeded.connect(window._run_succeeded);window.worker.failed.connect(window._run_failed)
            try:
                window._start_worker()
                self.assertFalse(window.model_selector.isEnabled());self.assertTrue(window.cancel_button.isEnabled())
                self.assertTrue(started.wait(2));window.cancel_run()
                until=time.monotonic()+3
                while window.worker.isRunning() and time.monotonic()<until:
                    app.processEvents();time.sleep(.01)
                app.processEvents()
                self.assertFalse(window.worker.isRunning())
                self.assertTrue(window.model_selector.isEnabled());self.assertFalse(window.cancel_button.isEnabled())
                self.assertIn('sample.py',window.request_input.toPlainText())
                self.assertIn('중단',window.run_state.text())
            finally:
                release.set();window.worker.request_cancel();window.worker.wait(2000)
                app.processEvents();window.refresh_timer.stop();window.close();app.processEvents()


if __name__=='__main__':unittest.main()
