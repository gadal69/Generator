"""Live model + Docker acceptance for a three-file SQLite/API sample."""
import argparse
from datetime import datetime
import json
from pathlib import Path
import sys
import time
import traceback
import sqlite3
import tempfile

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from app.gui.qt_dashboard import make_database
from app.models.adapter_factory import create_local_adapter
from app.orchestrator.orchestrator import Orchestrator
from app.qa.project_verifier import ProjectVerifier
from app.qa.verification_contract import build_contract,blocking_failure,required_unverified
from app.runtime.run_control import RunControl
from app.runtime.checkpoints import Checkpoints
from app.orchestrator.work_contract import read_contract
from app.storage.database import Database

REQUEST='''Python 표준 라이브러리만 사용하는 작은 할 일 관리 프로그램을 store.py, api_client.py, main.py 세 파일로 작성하세요. 세 파일 외 소스·테스트·문서 파일은 생성하지 마세요.
store.py: TaskStore(db_path) 클래스. 생성 시 sqlite3로 tasks 테이블(id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT, done INTEGER)을 준비합니다. 모든 DB 작업은 SQL 파라미터 바인딩을 사용하고 commit 및 연결 종료를 보장하세요.
add_task(title): str 이외는 TypeError. strip()한 제목 길이가 1~80이 아니면 ValueError. 저장하고 양의 정수 id 반환. done 초기값은 False.
list_tasks(): id 오름차순으로 [{"id":int,"title":str,"done":bool}] 반환. 비어 있으면 []. 새 TaskStore 인스턴스와 별도 Python 프로세스에서도 같은 DB 자료가 조회되어야 합니다.
mark_done(task_id): bool을 포함해 int 이외는 TypeError, 0 이하 id는 ValueError. 완료 상태로 변경 후 존재하면 True, 없는 id면 False 반환.
api_client.py: fetch_label(task_id, transport). task_id 검증은 mark_done과 동일. 주입받은 transport(task_id)를 호출합니다. 응답은 dict이고 label은 strip() 후 비어 있지 않은 str이어야 하며 그 문자열을 반환합니다. 잘못된 응답은 ValueError, transport 자체 예외는 그대로 전파합니다.
선택적 실제 연결 경로인 http_transport(endpoint, task_id, timeout=3)도 작성하세요. urllib.request.urlopen으로 endpoint.rstrip('/')+'/'+str(task_id)를 GET하고 UTF-8 JSON 객체를 반환합니다. 실제 주소나 키를 하드코딩하지 않습니다.
main.py: from store import TaskStore 및 from api_client import fetch_label을 재사용합니다. summarize(db_path, transport)는 DB 행마다 fetch_label을 호출하여 기존 id/title/done과 label을 포함한 dict 목록을 반환합니다. 빈 DB는 []이고 transport를 호출하지 않습니다. 오류는 숨기지 말고 전파합니다.
main 가드에서는 sys.argv[1]의 DB 경로를 받아 TaskStore(...).list_tasks()를 JSON으로 한 번 출력합니다. CLI에서는 네트워크를 호출하지 않습니다.
외부 통신은 이 테스트에서 실제 호출하지 않습니다. 주입 transport의 정상·오류·경계 모의 응답만 검사하고 실제 외부 연결은 별도 미실행으로 표시하세요. GUI, pip 패키지, 다른 파일은 필요 없습니다. 최소 개발 담당자를 사용하되 파일별 책임과 세 모듈의 인터페이스를 명확히 하세요.'''

PRELUDE='''import json, sqlite3, subprocess, sys
from pathlib import Path
def raises(call, exception):
    try: call()
    except exception: return
    raise AssertionError('expected ' + exception.__name__)
'''
DRIVERS={
('store','normal'):'''from store import TaskStore
p='/tmp/tasks.sqlite'; s=TaskStore(p)
i=s.add_task(' first '); assert type(i) is int and i>0
assert s.mark_done(i) is True
assert TaskStore(p).list_tasks()==[{'id':i,'title':'first','done':True}]
child=subprocess.run([sys.executable,'-c',"import json; from store import TaskStore; print(json.dumps(TaskStore('/tmp/tasks.sqlite').list_tasks()))"],capture_output=True,text=True,check=True)
assert json.loads(child.stdout)==[{'id':i,'title':'first','done':True}]
''',
('store','invalid'):'''from store import TaskStore
s=TaskStore('/tmp/tasks.sqlite')
for value in (None,5,False): raises(lambda:s.add_task(value),TypeError)
for value in ('','   ','x'*81): raises(lambda:s.add_task(value),ValueError)
for value in (None,'1',True): raises(lambda:s.mark_done(value),TypeError)
for value in (0,-1): raises(lambda:s.mark_done(value),ValueError)
raises(lambda:TaskStore('/tmp/does-not-exist/tasks.sqlite'),sqlite3.Error)
assert s.list_tasks()==[]
''',
('store','boundary'):'''from store import TaskStore
s=TaskStore('/tmp/tasks.sqlite'); assert s.list_tasks()==[]
i=s.add_task('한'*80); j=s.add_task("x'); DROP TABLE tasks;--")
assert s.mark_done(999999) is False
rows=TaskStore('/tmp/tasks.sqlite').list_tasks()
assert [r['id'] for r in rows]==[i,j] and rows[0]['title']=='한'*80
assert rows[1]['title']=="x'); DROP TABLE tasks;--" and rows[1]['done'] is False
''',
('api','normal'):'''from api_client import fetch_label
calls=[]
def transport(i): calls.append(i); return {'label':' remote '}
assert fetch_label(3,transport)=='remote' and calls==[3]
''',
('api','invalid'):'''from api_client import fetch_label
for response in (None,{}, {'label':2},{'label':''},{'label':'   '}):
    raises(lambda:fetch_label(1,lambda i:response),ValueError)
for value in (None,True,'1'): raises(lambda:fetch_label(value,lambda i:{'label':'a'}),TypeError)
for value in (0,-1): raises(lambda:fetch_label(value,lambda i:{'label':'a'}),ValueError)
def failed(i): raise RuntimeError('mock failure')
raises(lambda:fetch_label(1,failed),RuntimeError)
''',
('api','boundary'):'''from api_client import fetch_label
assert fetch_label(1,lambda i:{'label':' 한글 '})=='한글'
assert fetch_label(999999,lambda i:{'label':'a'})=='a'
''',
('integration','normal'):'''from store import TaskStore
from main import summarize
p='/tmp/tasks.sqlite'; s=TaskStore(p); i=s.add_task('one'); s.mark_done(i)
assert summarize(p,lambda key:{'label':'mock-'+str(key)})==[{'id':i,'title':'one','done':True,'label':'mock-'+str(i)}]
child=subprocess.run([sys.executable,'main.py',p],capture_output=True,text=True,check=True)
assert json.loads(child.stdout)==[{'id':i,'title':'one','done':True}]
''',
('integration','invalid'):'''from store import TaskStore
from main import summarize
p='/tmp/tasks.sqlite'; s=TaskStore(p); s.add_task('one')
raises(lambda:summarize(p,lambda i:{}),ValueError)
assert len(TaskStore(p).list_tasks())==1
child=subprocess.run([sys.executable,'main.py','/tmp/absent/tasks.sqlite'],capture_output=True,text=True)
assert child.returncode!=0
''',
('integration','boundary'):'''from store import TaskStore
from main import summarize
p='/tmp/tasks.sqlite'; s=TaskStore(p)
def forbidden(i): raise AssertionError('transport must not be called')
assert summarize(p,forbidden)==[]
child=subprocess.run([sys.executable,'main.py',p],capture_output=True,text=True,check=True)
assert json.loads(child.stdout)==[]
i=s.add_task('한'*80)
assert summarize(p,lambda key:{'label':'경계'})==[{'id':i,'title':'한'*80,'done':False,'label':'경계'}]
'''}


def criteria():
    checks=[]
    for (area,category),driver in DRIVERS.items():
        filename={'store':'store.py','api':'api_client.py','integration':'main.py'}[area]
        marker=area+'_'+category+'_OK'
        checks.append({'id':area+':'+category,'kind':'command','target':filename,'category':category,
                       'requirements':[area],'argv':['python','-c',PRELUDE+driver+f"\nprint({marker!r})"],
                       'stdout':marker,'timeout':20,'required':True})
    return {'version':1,'requirements':[
        {'id':'store','description':'SQLite 저장·재조회·입력 검증·별도 프로세스 지속성'},
        {'id':'api','description':'주입 transport 모의 응답의 검증·호출·예외 전파'},
        {'id':'integration','description':'세 파일 연동·요약·CLI·빈 데이터·오류 전파'}],'checks':checks}


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--output-dir',type=Path,required=True)
    parser.add_argument('--seed-project',help='동일 샘플의 이전 모델 산출물을 초기 구현에만 재사용')
    parser.add_argument('--analysis-response',type=Path,help='실제 모델의 기존 분석 응답을 수정된 파서로 재검증')
    parser.add_argument('--model',help='이 테스트에만 적용하는 로컬 모델; 기본 설정 파일은 변경하지 않음')
    parser.add_argument('--resume-project',help='중단 상태로 확인된 기존 프로젝트의 체크포인트 재개')
    args=parser.parse_args();out=args.output_dir.resolve();out.mkdir(parents=True,exist_ok=True)
    report_path=out/'report.json'
    if report_path.exists():raise RuntimeError('기존 테스트 결과를 덮어쓰지 않습니다.')
    report={'started_at':datetime.now().astimezone().isoformat(),'scope':'LIVE_MODEL_MULTIFILE_SQLITE_DOCKER',
            'analysis_confirmation':'사용자가 요청한 테스트 절차상 분석 확인. 별도 사람 검토 또는 실제 최종 승인을 주장하지 않음.',
            'external_api':{'mock':'PENDING','real_connection':'NOT_RUN','reason':'실제 API 주소·자격증명 미지정; Docker 실행 네트워크 차단'},
            'calls':[],'events':[]}
    def save():
        temporary=report_path.with_suffix('.json.tmp')
        temporary.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        temporary.replace(report_path)
    def event(item):
        report['events'].append(item);save();print('EVENT '+json.dumps(item,ensure_ascii=False),flush=True)
    seeds={}
    analysis_response=json.loads(args.analysis_response.read_text(encoding='utf-8'))['response'] if args.analysis_response else None
    if args.analysis_response:report['analysis_response_replay']=str(args.analysis_response)
    class Traced:
        def __init__(self,adapter):self.adapter=adapter;self.stage='analysis';self.attempt=0
        def __getattr__(self,name):return getattr(self.adapter,name)
        def configure_generation(self,stage,attempt=0):self.stage=stage;self.attempt=attempt;self.adapter.configure_generation(stage,attempt)
        def generate(self,prompt,images=None):
            index=len(report['calls'])+1;start=time.monotonic()
            row={'index':index,'stage':self.stage,'model':self.adapter.model};report['calls'].append(row);save()
            detail={'prompt':prompt};print(f'MODEL_CALL {index} {self.stage} START',flush=True)
            try:
                if self.stage=='analysis' and analysis_response is not None:
                    row['origin']='REPLAYED_LIVE_MODEL_ANALYSIS'
                    detail.update(response=analysis_response,metadata={},origin=row['origin'])
                    return analysis_response
                task_contract=read_contract(prompt) or {}
                paths=task_contract.get('files',[])
                if self.stage=='implementation' and self.attempt==0 and len(paths)==1 and paths[0] in seeds and '원래 작업의 요구사항과 대상 파일을 유지해 다시 수행하세요.' not in prompt:
                    text=seeds[paths[0]];row['origin']='REUSED_PRIOR_MODEL_ARTIFACT'
                    detail.update(response=text,metadata={},origin=row['origin'])
                    return text
                row['origin']='LIVE_MODEL'
                text=self.adapter.generate(prompt) if images is None else self.adapter.generate(prompt,images=images)
                detail.update(response=text,metadata=self.adapter.last_response);row['metadata']=self.adapter.last_response
                return text
            except Exception as exc:detail['error']=str(exc);row['error']=str(exc);raise
            finally:
                row['elapsed_seconds']=round(time.monotonic()-start,2)
                (out/f'call_{index:02d}.json').write_text(json.dumps(detail,ensure_ascii=False,indent=2),encoding='utf-8')
                save();print(f'MODEL_CALL {index} END {row["elapsed_seconds"]}s',flush=True)
    config=json.loads((ROOT/'config/app_config.json').read_text(encoding='utf-8'))
    report['model']=args.model or config['ai']['default_model'];report['default_model_unchanged']=config['ai']['default_model'];(out/'request.txt').write_text(REQUEST,encoding='utf-8')
    contract=criteria();(out/'criteria.json').write_text(json.dumps(contract,ensure_ascii=False,indent=2),encoding='utf-8')
    o=Orchestrator(make_database());control=RunControl(event)
    if args.seed_project:
        _,previous=Checkpoints(o.database).load(args.seed_project)
        if previous.get('request')!=REQUEST:raise ValueError('동일 샘플 요청의 모델 산출물만 재사용할 수 있습니다.')
        for output in previous.get('agent_outputs',[]):
            if len(output.get('files',[]))==1:seeds[output['files'][0]]=output['output']
        report['replay_of']=args.seed_project;report['seed_files']=sorted(seeds)
    factory=lambda model:Traced(create_local_adapter(model=model))
    save()
    try:
        if args.resume_project:
            _,saved=Checkpoints(o.database).load(args.resume_project)
            analysis=saved['decision'];report['resumed_project']=args.resume_project
        else:
            analysis=o.team_runner.analyze_user_request(REQUEST,adapter_factory=factory,model_override=report['model'],verification_contract=contract,control=control)
        report['analysis']=analysis;save()
        (out/'analysis.json').write_text(json.dumps(analysis,ensure_ascii=False,indent=2),encoding='utf-8')
        result=o.run_user_request(REQUEST,project_name='다중 파일 SQLite·모의 API 최종 검증',
            analysis_override=analysis,verification_contract=contract,adapter_factory=factory,
            model_override=report['model'],
            resume=bool(args.resume_project),resume_project_id=args.resume_project,
            qa_mode='agent',approval_mode='manual',verbose=False,control=control)
        report['result']=result
    except Exception as exc:report.update(workflow_error=type(exc).__name__+': '+str(exc),traceback=traceback.format_exc())
    project=next((e['project_id'] for e in reversed(report['events']) if e.get('project_id')),None)
    report['project_id']=project
    if project:
        with o.database.connection() as conn:
            report['qa']=[dict(zip(['task_id','result_id','status','summary','issues'],r)) for r in conn.execute(
                'SELECT task_id,result_id,status,summary,issues FROM qa_reviews WHERE project_id=? ORDER BY created_at',(project,))]
            report['approvals']=[dict(zip(['id','status'],r)) for r in conn.execute('SELECT approval_id,status FROM approvals WHERE project_id=?',(project,))]
            checkpoint=conn.execute('SELECT status,state_json FROM workflow_checkpoints WHERE project_id=?',(project,)).fetchone()
        if checkpoint:
            report['checkpoint_status']=checkpoint[0]
            (out/'checkpoint.json').write_text(json.dumps(json.loads(checkpoint[1]),ensure_ascii=False,indent=2),encoding='utf-8')
        folder=ROOT/'projects'/project/'artifacts'
        artifacts=[{'path':p.name,'content':p.read_text(encoding='utf-8')} for p in folder.glob('*.py')] if folder.exists() else []
        if artifacts:
            independent=ProjectVerifier(config['verification']).verify(artifacts,build_contract('',contract))
            report['independent_checks']=independent
            report['independent_outcome']='FAIL' if blocking_failure(independent) else 'NOT_RUN' if required_unverified(independent) else 'PASS'
            report['external_api']['mock']=next((r['status'] for r in independent if r['check_id']=='coverage:api'),'NOT_RUN')
            for item in artifacts:(out/('generated_'+item['path']+'.txt')).write_text(item['content'],encoding='utf-8')
            if report['independent_outcome']=='FAIL' and report.get('result',{}).get('final_result_id'):
                final=o.result_manager.get_result(report['result']['final_result_id'])
                o.qa_manager.review(project,final[1],final[0],'AGT-QA-001','FAIL',0.0,
                    '추가 독립 통합 검사 실패',['INDEPENDENT_MULTIFILE_FAIL'])
            if report['independent_outcome']=='PASS' and report.get('result',{}).get('approval_id'):
                # Exercise final approval on an isolated DB copy; never approve the actual project.
                with tempfile.TemporaryDirectory(prefix='itni_approval_copy_') as temporary:
                    copy_path=Path(temporary)/'approval.db'
                    with o.database.connection() as source:
                        target=sqlite3.connect(copy_path)
                        try:source.backup(target)
                        finally:target.close()
                    copied=Orchestrator(Database(copy_path))
                    copied.approve(report['result']['approval_id'],'APPROVED','격리 DB 사본의 승인 경로 테스트')
                    report['final_approval_test']={'scope':'ISOLATED_DATABASE_COPY','status':'PASS',
                        'production_approval':o.approval_manager.get(report['result']['approval_id'])[5]}
    report['finished_at']=datetime.now().astimezone().isoformat();save()
    print('FINAL '+json.dumps({'project_id':project,'status':report.get('result',{}).get('status'),
        'error':report.get('workflow_error'),'independent':report.get('independent_outcome')},ensure_ascii=False),flush=True)
    return 0 if report.get('independent_outcome')=='PASS' and not report.get('workflow_error') else 1


if __name__=='__main__':raise SystemExit(main())
