"""Independent mocked HTTP-adapter checks; no real API connection is claimed."""
import argparse
from datetime import datetime
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from app.qa.project_verifier import ProjectVerifier
from app.qa.verification_contract import build_contract,blocking_failure,required_unverified

PRELUDE='''import json
from unittest.mock import patch
class Response:
    status=200
    headers={}
    def __init__(self,body): self.body=body
    def read(self,*args): return self.body
    def close(self): pass
    def getcode(self): return 200
    def getheader(self,*args): return 'application/json'
    def __enter__(self): return self
    def __exit__(self,*args): pass
calls=[]
body=b'{}'
def fake(request,timeout=3):
    calls.append((getattr(request,'full_url',request),timeout))
    return Response(body)
with patch('urllib.request.urlopen',side_effect=fake):
    import api_client
'''
DRIVERS={
    'normal':'''body=b'{"label":"mock"}'
with patch('urllib.request.urlopen',side_effect=fake):
    assert api_client.http_transport('https://mock.invalid/tasks/',7,timeout=2)=={'label':'mock'}
assert calls==[('https://mock.invalid/tasks/7',2)]
''',
    'invalid':'''body=b'not-json'
with patch('urllib.request.urlopen',side_effect=fake):
    try: api_client.http_transport('https://mock.invalid/tasks',1)
    except Exception: pass
    else: raise AssertionError('invalid JSON must not be accepted')
assert len(calls)==1
''',
    'boundary':'''body=json.dumps({'label':'한글 경계'},ensure_ascii=False).encode('utf-8')
with patch('urllib.request.urlopen',side_effect=fake):
    assert api_client.http_transport('https://mock.invalid/tasks///',999999,timeout=0.5)=={'label':'한글 경계'}
assert calls==[('https://mock.invalid/tasks/999999',0.5)]
'''}

DB_LIFECYCLE='''import sqlite3
from unittest.mock import patch
opened={}
original_connect=sqlite3.connect
class TrackedConnection(sqlite3.Connection):
    def close(self):
        opened.pop(id(self),None)
        return super().close()
def tracked_connect(*args,**kwargs):
    kwargs['factory']=TrackedConnection
    connection=original_connect(*args,**kwargs)
    opened[id(connection)]=connection
    return connection
with patch('sqlite3.connect',side_effect=tracked_connect):
    from store import TaskStore
    store=TaskStore('/tmp/lifecycle.sqlite')
    assert not opened, 'connection left open after init'
    key=store.add_task('lifecycle')
    assert not opened, 'connection left open after insert'
    rows=store.list_tasks()
    assert rows[0]['done'] is False and type(rows[0]['id']) is int
    assert not opened, 'connection left open after select'
    assert store.mark_done(key) is True
    assert not opened, 'connection left open after update'
    assert TaskStore('/tmp/lifecycle.sqlite').list_tasks()[0]['done'] is True
    assert not opened, 'connection left open after reopen'
print('DB_LIFECYCLE_OK')
'''


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--report',type=Path,required=True)
    args=parser.parse_args();original=json.loads(args.report.read_text(encoding='utf-8'))
    output=args.report.parent/'api_http_probe.json'
    if output.exists():raise RuntimeError('기존 검사 기록을 덮어쓰지 않습니다.')
    project=original['project_id'];folder=ROOT/'projects'/project/'artifacts'
    artifacts=[{'path':p.name,'content':p.read_text(encoding='utf-8')} for p in folder.glob('*.py')]
    checks=[{'id':'http:'+category,'kind':'command','target':'api_client.py','category':category,
             'requirements':['http'],'argv':['python','-c',PRELUDE+driver+"\nprint('HTTP_MOCK_OK')"],
             'stdout':'HTTP_MOCK_OK','timeout':20} for category,driver in DRIVERS.items()]
    checks.append({'id':'db:lifecycle','kind':'command','target':'store.py','category':'normal',
        'requirements':['db_lifecycle'],'argv':['python','-c',DB_LIFECYCLE],
        'stdout':'DB_LIFECYCLE_OK','timeout':20})
    contract=build_contract('',{'version':1,'requirements':[
        {'id':'http','description':'HTTP 어댑터의 URL·JSON·UTF-8·예외 처리를 모의 urlopen으로 검사'},
        {'id':'db_lifecycle','description':'SQLite 연결 종료와 id/done 자료형을 확인',
         'required_categories':['normal'],'category_rationale':'입력 분류와 별개인 연결 생명주기·출력 자료형 검사'}],'checks':checks})
    config=json.loads((ROOT/'config/app_config.json').read_text(encoding='utf-8'))
    rows=ProjectVerifier(config['verification']).verify(artifacts,contract)
    status='FAIL' if blocking_failure(rows) else 'NOT_RUN' if required_unverified(rows) else 'PASS'
    report={'status':status,'project_id':project,'scope':'MOCKED_URLOPEN_AND_SQLITE_LIFECYCLE_IN_DOCKER',
            'checked_at':datetime.now().astimezone().isoformat(),'real_external_connection':'NOT_RUN','checks':rows}
    if status=='FAIL' and original.get('result',{}).get('final_result_id'):
        from app.gui.qt_dashboard import make_database
        from app.orchestrator.orchestrator import Orchestrator
        o=Orchestrator(make_database());result=o.result_manager.get_result(original['result']['final_result_id'])
        o.qa_manager.review(project,result[1],result[0],'AGT-QA-001','FAIL',0.0,'추가 HTTP 어댑터 모의 검사 실패',['HTTP_MOCK_PROBE_FAIL'])
        report['approval_invalidated']=True
    output.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'status':status,'report':str(output)},ensure_ascii=False))
    return 0 if status=='PASS' else 1


if __name__=='__main__':raise SystemExit(main())
