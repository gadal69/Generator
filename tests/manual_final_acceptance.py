"""Explicitly invoked live acceptance test; not part of unittest discovery."""
import ast
import argparse
from collections import Counter
from datetime import datetime
import json
from pathlib import Path
import sys
import time
import traceback

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from app.gui.qt_dashboard import make_database
from app.models.adapter_factory import create_local_adapter
from app.orchestrator.orchestrator import Orchestrator
from app.qa.project_verifier import ProjectVerifier
from app.qa.verification_contract import build_contract,blocking_failure,required_unverified
from app.runtime.run_control import RunControl

OUT=ROOT/'docs'/'final_acceptance_20261025'
OUT.mkdir(parents=True,exist_ok=True)
REPORT=OUT/'report.json'
report={'started_at':datetime.now().astimezone().isoformat(),
        'goal':'단일 문장 정리·단어 빈도 분석 프로그램 생성',
        'analysis_confirmation':'이번 최종 테스트 요청에 따라 자동 확인. 사람의 별도 검토를 수행했다고 주장하지 않음.',
        'approval_mode':'manual','configuration_overrides':{},'calls':[],'events':[]}

def save():REPORT.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
def event(item):
    report['events'].append(item);save()
    print('EVENT '+json.dumps(item,ensure_ascii=False),flush=True)


class TracedAdapter:
    def __init__(self,adapter):self.adapter=adapter;self.stage='analysis'
    def __getattr__(self,name):return getattr(self.adapter,name)
    def configure_generation(self,stage,attempt=0):
        self.stage=stage;self.adapter.configure_generation(stage,attempt)
    def generate(self,prompt,images=None):
        index=len(report['calls'])+1;started=time.monotonic()
        call={'index':index,'stage':self.stage,'model':self.adapter.model,'started_at':datetime.now().astimezone().isoformat()}
        report['calls'].append(call);save()
        print(f'MODEL_CALL {index} {self.stage} START',flush=True)
        detail={'prompt':prompt,'configuration':{'options':self.adapter.options,'request_options':self.adapter.request_options,'timeout':self.adapter.timeout}}
        try:
            result=self.adapter.generate(prompt) if images is None else self.adapter.generate(prompt,images=images)
            detail['response']=result;detail['metadata']=self.adapter.last_response
            call['metadata']=self.adapter.last_response;call['response_chars']=len(result)
            return result
        except Exception as exc:
            call['error']=type(exc).__name__+': '+str(exc);detail['error']=call['error'];raise
        finally:
            call['elapsed_seconds']=round(time.monotonic()-started,2)
            (OUT/f'call_{index:02d}.json').write_text(json.dumps(detail,ensure_ascii=False,indent=2),encoding='utf-8')
            save();print(f'MODEL_CALL {index} END {call["elapsed_seconds"]}s',flush=True)


def main():
    config=json.loads((ROOT/'config'/'app_config.json').read_text(encoding='utf-8'))
    model=config['ai']['default_model'];report['model']=model;report['configuration']=config['ai'];save()
    criteria={'version':1,'requirements':[
        {'id':'normalize','description':'문자열의 앞뒤 공백을 없애고 연속 공백을 한 칸으로 만들며 소문자로 바꾼다.'},
        {'id':'counts','description':'정리된 문장을 공백으로 나눈 단어별 개수를 dict로 반환한다. 구두점은 보존한다.'},
        {'id':'cli','description':'직접 실행하면 입력 한 줄을 받아 정리 문장과 단어 빈도를 두 줄로 출력한다.'}],
        'checks':[
            {'id':'normalize','kind':'python_function','target':'text_tools.py','function':'normalize_text','requirements':['normalize'],
             'cases':[{'args':['  Hello   WORLD  '],'expected':'hello world'},
                      {'args':['\t안녕\n세상  '],'expected':'안녕 세상'},
                      {'args':['   '],'expected':''},{'args':[123],'error':True,'error_type':'TypeError'}]},
            {'id':'counts','kind':'python_function','target':'text_tools.py','function':'word_counts','requirements':['counts'],
             'cases':[{'args':['Hello hello WORLD'],'expected':{'hello':2,'world':1}},
                      {'args':['안녕  안녕 세상'],'expected':{'안녕':2,'세상':1}},
                      {'args':[''],'expected':{}},{'args':[None],'error':True,'error_type':'TypeError'}]},
            {'id':'startup','kind':'command','target':'text_tools.py','runtime':'python','requirements':['cli'],
             'argv':['python','text_tools.py'],'stdin':'Hello hello WORLD\n','exit_code':0,
             'stdout_contains':['hello hello world',"'hello': 2","'world': 1"],'timeout':10}]}
    request=('문장 정리와 단어 빈도 분석을 하는 작은 터미널 프로그램을 text_tools.py 단일 파일로 작성해 주세요.\n'
        'normalize_text(text)는 str만 허용하고 다른 타입에는 TypeError를 발생시킵니다. '
        '앞뒤 공백 및 탭·줄바꿈 등 모든 연속 공백을 한 칸으로 정리하고 lower() 소문자로 변환합니다. 빈 입력은 빈 문자열입니다.\n'
        'word_counts(text)는 normalize_text를 재사용하여 공백으로 구분한 각 단어의 빈도를 dict로 반환합니다. '
        '구두점은 제거하지 않으며 빈 입력은 빈 dict입니다. str 이외 입력은 TypeError입니다.\n'
        '직접 실행하는 main 가드에서는 input() 한 번으로 입력 한 줄을 받고, 정리 문장과 빈도 dict를 print()로 각각 한 줄에 출력하세요. '
        '외부 패키지, 네트워크, 파일 읽기/쓰기, 다른 산출 파일은 필요 없습니다. 대표자 요구 범위 안에서 최소 역할로 분장하세요.\n'
        '[검증 계약]\n'+json.dumps(criteria,ensure_ascii=False,indent=2)+'\n[/검증 계약]')
    (OUT/'prompt.txt').write_text(request,encoding='utf-8');report['request']=request
    o=Orchestrator(make_database());control=RunControl(event)
    factory=lambda name:TracedAdapter(create_local_adapter(model=name))
    report['database']=o.database.db_path;report['phase']='analysis';save()
    decision=o.team_runner.analyze_user_request(request,model_override=model,adapter_factory=factory,control=control)
    report['analysis']=decision;report['phase']='analysis_confirmed_for_test';save()
    (OUT/'analysis.json').write_text(json.dumps(decision,ensure_ascii=False,indent=2),encoding='utf-8')
    report['phase']='execution';save()
    try:
        result=o.run_user_request(request,project_name='최종 인수 테스트 — 문장 정리·단어 빈도',
            model_override=model,qa_mode='agent',approval_mode='manual',verbose=False,
            analysis_override=decision,adapter_factory=factory,control=control)
        report['result']=result
    except Exception as exc:
        report['workflow_error']=type(exc).__name__+': '+str(exc);report['traceback']=traceback.format_exc()
    project_id=next((item['project_id'] for item in reversed(report['events']) if item.get('project_id')),None)
    report['project_id']=project_id
    if project_id:
        with o.database.connection() as conn:
            report['database_status']=dict(zip(['project_id','status'],conn.execute('SELECT project_id,status FROM projects WHERE project_id=?',(project_id,)).fetchone()))
            report['qa_reviews']=[dict(zip(['qa_id','task_id','result_id','status','summary','issues'],row)) for row in conn.execute('SELECT qa_id,task_id,result_id,status,summary,issues FROM qa_reviews WHERE project_id=? ORDER BY created_at',(project_id,))]
            report['approval_rows']=[dict(zip(['approval_id','status'],row)) for row in conn.execute('SELECT approval_id,status FROM approvals WHERE project_id=?',(project_id,))]
            checkpoint=conn.execute('SELECT status,state_json FROM workflow_checkpoints WHERE project_id=?',(project_id,)).fetchone()
            report['checkpoint_status']=checkpoint[0] if checkpoint else None
        artifact_root=ROOT/'projects'/project_id/'artifacts'
        artifacts=[{'path':p.relative_to(artifact_root).as_posix(),'content':p.read_text(encoding='utf-8-sig')} for p in artifact_root.rglob('*') if p.is_file()] if artifact_root.exists() else []
        # Also inspect the latest attempted output if the workflow correctly blocked saving.
        if not artifacts and checkpoint:
            from app.tools.project_file_tool import extract_agent_artifacts
            state=json.loads(checkpoint[1]);pending=state.get('pending_result_id')
            if pending:
                row=o.result_manager.get_result(pending)
                if row:artifacts=extract_agent_artifacts(row[3],'text_tools.py')
                report['independent_target']='latest rejected attempt; not an approved/saved program'
        report['artifact_paths']=[item['path'] for item in artifacts]
        if artifacts:
            report['independent_initial_contract']=ProjectVerifier(config.get('verification',{})).verify(artifacts,build_contract(request))
            inputs=[' A  a B ','\n\t Mixed\r\nCASE  ','punctuation, punctuation.','한글 ENGLISH 한글','one\u00a0two\u00a0one',
                    '',' \t\n ','123 123 45','café CAFÉ','A-B a-b C-D','single',' x '*30]
            checks=[]
            for function in ('normalize_text','word_counts'):
                cases=[]
                for value in inputs:
                    normalized=' '.join(value.split()).lower()
                    expected=normalized if function=='normalize_text' else dict(Counter(normalized.split()))
                    cases.append({'args':[value],'expected':expected})
                for value in (None,False,[],{}):cases.append({'args':[value],'error':True,'error_type':'TypeError'})
                checks.append({'id':'independent:'+function,'kind':'python_function','target':'text_tools.py','function':function,'cases':cases})
            extra=ProjectVerifier().verify(artifacts,build_contract('',{'version':1,'checks':checks}))
            report['independent_additional_checks']=extra
            report['independent_additional_summary']={'cases':sum(len(row.get('cases',[])) for row in extra),
                'failed':sum(case['status']=='FAIL' for row in extra for case in row.get('cases',[])),
                'not_run':sum(case['status']=='NOT_RUN' for row in extra for case in row.get('cases',[]))}
            report['independent_acceptance']='FAIL' if blocking_failure(extra) else 'NOT_RUN' if required_unverified(extra) else 'PASS_FUNCTION_SCOPE_ONLY'
            if blocking_failure(extra) and report.get('result',{}).get('final_result_id'):
                final=o.result_manager.get_result(report['result']['final_result_id'])
                o.qa_manager.review(project_id,final[1],final[0],'AGT-QA-001','FAIL',0.0,
                    '추가 독립 검사에서 요구사항 불일치 발견',['INDEPENDENT_ACCEPTANCE_FAIL'])
                report['approval_rows']=[{'approval_id':item[0],'status':item[5]} for item in o.approval_manager.get_by_task(final[1])]
                report['database_status']={'project_id':project_id,'status':o.project_manager.get_project(project_id)[3]}
            for item in artifacts:
                (OUT/('generated_'+Path(item['path']).name+'.txt')).write_text(item['content'],encoding='utf-8')
    report['phase']='finished';report['finished_at']=datetime.now().astimezone().isoformat();save()
    print('FINAL '+json.dumps({'project_id':project_id,'status':report.get('result',{}).get('status'),
          'workflow_error':report.get('workflow_error'),'additional':report.get('independent_additional_summary')},ensure_ascii=False),flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--output-dir',type=Path,default=ROOT/'docs'/datetime.now().strftime('final_acceptance_%Y%m%d_%H%M%S'))
    options=parser.parse_args();OUT=options.output_dir.resolve();REPORT=OUT/'report.json'
    if REPORT.exists():parser.error('기존 테스트 기록은 덮어쓰지 않습니다. 다른 output-dir를 지정하세요.')
    OUT.mkdir(parents=True,exist_ok=True)
    try:main()
    except BaseException as exc:
        report['phase']='fatal';report['fatal_error']=type(exc).__name__+': '+str(exc);report['traceback']=traceback.format_exc();save();raise
