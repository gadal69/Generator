"""Resume the recorded live acceptance project after a verified engine repair."""
from collections import Counter
from datetime import datetime
import json
import time
import manual_final_acceptance as live
from app.runtime.checkpoints import Checkpoints


def main():
    live.report=json.loads(live.REPORT.read_text(encoding='utf-8'))
    r=live.report;o=live.Orchestrator(live.make_database());project_id=r['project_id']
    _,state=Checkpoints(o.database).load(project_id)
    r['initial_failure']={'error':r.pop('workflow_error',None),'checkpoint_status':r['checkpoint_status'],
                          'evidence':'report_initial_failure.json'}
    r.pop('traceback',None);r['resume_started_at']=datetime.now().astimezone().isoformat();r['phase']='resuming'
    before=len(r['calls']);start=time.monotonic();live.save()
    result=o.run_user_request(state['request'],model_override=state['model'],qa_mode=state['qa_mode'],
        approval_mode=state['approval_mode'],max_reworks=state['max_reworks'],verbose=False,
        adapter_factory=lambda name:live.TracedAdapter(live.create_local_adapter(model=name)),
        resume=True,resume_project_id=project_id,control=live.RunControl(live.event))
    r['result']=result;r['resume_elapsed_seconds']=round(time.monotonic()-start,2)
    r['resume_model_calls']=len(r['calls'])-before
    config=json.loads((live.ROOT/'config'/'app_config.json').read_text(encoding='utf-8'))
    artifact_root=live.ROOT/'projects'/project_id/'artifacts'
    artifacts=[{'path':p.relative_to(artifact_root).as_posix(),'content':p.read_text(encoding='utf-8-sig')}
               for p in artifact_root.rglob('*') if p.is_file()]
    r['artifact_paths']=[item['path'] for item in artifacts]
    r['independent_initial_contract']=live.ProjectVerifier(config.get('verification',{})).verify(artifacts,live.build_contract(state['request']))
    inputs=[' A  a B ','\n\t Mixed\r\nCASE  ','punctuation, punctuation.','한글 ENGLISH 한글','one\u00a0two\u00a0one',
            '',' \t\n ','123 123 45','café CAFÉ','A-B a-b C-D','single',' x '*30]
    checks=[]
    for function in ('normalize_text','word_counts'):
        cases=[]
        for value in inputs:
            normalized=' '.join(value.split()).lower()
            expected=normalized if function=='normalize_text' else dict(Counter(normalized.split()))
            cases.append({'args':[value],'expected':expected})
        cases.extend({'args':[value],'error':True} for value in (None,False,[],{}))
        checks.append({'id':'independent:'+function,'kind':'python_function','target':'text_tools.py','function':function,'cases':cases})
    extra=live.ProjectVerifier().verify(artifacts,live.build_contract('',{'version':1,'checks':checks}))
    cases=[case for row in extra for case in row.get('cases',[])]
    r['independent_additional_checks']=extra
    type_errors=[case for case in cases if case['case'].get('error')]
    r['independent_additional_summary']={'cases':len(cases),'passed':sum(case['status']=='PASS' for case in cases),
        'failed':sum(case['status']=='FAIL' for case in cases),'not_run':sum(case['status']=='NOT_RUN' for case in cases),
        'strict_type_error_cases':len(type_errors),'strict_type_error_passed':sum(case.get('exception')=='TypeError' for case in type_errors)}
    if live.blocking_failure(extra) or any(case.get('exception')!='TypeError' for case in type_errors):
        final=o.result_manager.get_result(result['final_result_id'])
        o.qa_manager.review(project_id,final[1],final[0],'AGT-QA-001','FAIL',0.0,
            '독립 인수 검사 실패: '+json.dumps(r['independent_additional_summary']),['INDEPENDENT_ACCEPTANCE_FAIL'])
        r['independent_acceptance']='FAIL; approval invalidated'
    elif live.required_unverified(extra):r['independent_acceptance']='NOT_RUN'
    else:r['independent_acceptance']='PASS_FUNCTION_SCOPE_ONLY'
    for item in artifacts:
        (live.OUT/('generated_'+live.Path(item['path']).name+'.txt')).write_text(item['content'],encoding='utf-8')
    with o.database.connection() as conn:
        r['database_status']={'project_id':project_id,'status':conn.execute('SELECT status FROM projects WHERE project_id=?',(project_id,)).fetchone()[0]}
        r['approval_rows']=[{'approval_id':row[0],'status':row[1]} for row in conn.execute('SELECT approval_id,status FROM approvals WHERE project_id=?',(project_id,))]
        r['checkpoint_status']=conn.execute('SELECT status FROM workflow_checkpoints WHERE project_id=?',(project_id,)).fetchone()[0]
    r['phase']='finished';r['finished_at']=datetime.now().astimezone().isoformat();live.save()
    print('RESUME_FINAL '+json.dumps({'project_id':project_id,'status':result['status'],'elapsed':r['resume_elapsed_seconds'],
          'model_calls':r['resume_model_calls'],'independent':r['independent_additional_summary']},ensure_ascii=False),flush=True)


if __name__=='__main__':main()
