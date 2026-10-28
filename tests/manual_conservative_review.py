"""Read-only product review: reproduce gaps using isolated temporary databases only."""
import json
from pathlib import Path
import sys
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from tests.test_universal_workflow import UniversalFlowTests,contract
from tests.test_requirement_verification import FinalApprovalVerificationTests
from app.orchestrator.team_runner import TeamRunner
from app.tools.dependency_preflight import inspect_verification_environment


def run():
    report={}
    fixture=UniversalFlowTests('test_no_verification_contract_does_not_auto_approve');fixture.setUp()
    try:
        checks=[{'id':name,'kind':'python_function','target':name+'.py','function':'add','requirements':['addition'],
                 'cases':[{'args':[2,3],'expected':5}]} for name in ('left','right')]
        fixture.decision['verification_contract']=contract(checks,[{'id':'addition','description':'정수 덧셈',
            'required_categories':['normal'],'category_rationale':'고정 정수 덧셈 사례 검토'}])
        result=fixture.run_flow(fixture.adapter([]),request='left.py 및 right.py 파일을 만들어 주세요.')
        earlier=result['task_results'][0]
        fixture.o.qa_task(result['project_id'],earlier['task_id'],earlier['result_id'],status='FAIL',score=0,summary='earlier accepted component revalidation failed')
        before=fixture.o.approval_manager.get(result['approval_id'])[5]
        fixture.o.approve(result['approval_id'],'APPROVED')
        report['earlier_component_failure']={'approval_after_failure':before,'final_approval':fixture.o.approval_manager.get(result['approval_id'])[5]}
    finally:fixture.tearDown()
    fixture=FinalApprovalVerificationTests('test_complete_verified_result_can_be_manually_approved');fixture.setUp()
    try:
        def write(**kwargs):
            row=fixture.o.project_file_tool.write_file(**kwargs)
            Path(row['path']).with_name('old_helper.py').write_text('VERSION=1',encoding='utf-8')
            return row
        fixture.o.tool_registry.register('write_project_file',write)
        result=fixture.run_flow()
        path=fixture.root/'projects'/result['project_id']/'artifacts'/'old_helper.py'
        path.write_text('VERSION=2',encoding='utf-8')
        fixture.o.approve(result['approval_id'],'APPROVED')
        report['unhashed_existing_artifact']={'changed_existing_file':'old_helper.py','final_approval':fixture.o.approval_manager.get(result['approval_id'])[5]}
    finally:fixture.tearDown()
    fixture=FinalApprovalVerificationTests('test_complete_verified_result_can_be_manually_approved');fixture.setUp()
    try:
        original=fixture.o.team_runner._write_artifact
        attempts=[]
        def broken_export(project,result):
            attempts.append(result.status)
            if len(attempts)==1:raise OSError('simulated report disk write failure')
            return original(project,result)
        with patch.object(fixture.o.team_runner,'_write_artifact',side_effect=broken_export):
            try:fixture.run_flow(approval='auto')
            except OSError:pass
        with fixture.o.database.connection() as conn:
            report['post_approval_report_failure']={
                'approval':conn.execute('SELECT status FROM approvals').fetchone()[0],
                'project':conn.execute('SELECT status FROM projects').fetchone()[0],
                'checkpoint':conn.execute('SELECT status FROM workflow_checkpoints').fetchone()[0]}
    finally:fixture.tearDown()
    text='API_KEY=demo_value_123456789\nAuthorization: Bearer demo_bearer_123456789\npassword="tiny"'
    masked=TeamRunner._redact_for_external_review(text)
    report['redaction_gaps']={'unquoted_key_remains':'demo_value_123456789' in masked,
        'bearer_remains':'demo_bearer_123456789' in masked,'short_password_remains':'tiny' in masked}
    with patch('app.qa.docker_environment.inspect_environment') as probe:
        notices=inspect_verification_environment({'checks':[{'kind':'gui','target':'editor.py'},{'kind':'image_output','target':'edit.py'}]})
        report['gui_image_preflight']={'notice_count':len(notices),'runtime_probed':probe.called}
    (ROOT/'docs/conservative_review_reproduction.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False,indent=2))

if __name__=='__main__':run()
