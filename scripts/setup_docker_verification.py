"""Explicit setup: build a reviewed runtime, smoke-test it, then connect its immutable ID."""
import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import uuid

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from app.qa.docker_environment import inspect_environment
from app.qa.project_verifier import ProjectVerifier
from app.qa.verification_contract import build_contract,blocking_failure,required_unverified

TAG='itni-qa-python:1'
PROBE='''import json,os
def add(a,b): return a+b
if __name__ == "__main__":
    with open("/proc/mounts") as f:
        root_ro=any(row.split()[1]=="/" and "ro" in row.split()[3].split(",") for row in f)
    with open("/proc/net/route") as f:
        no_routes=len(f.read().strip().splitlines())==1
    with open("/tmp/itni-probe","w") as f: f.write("ok")
    print(json.dumps({"uid":os.getuid(),"root_readonly":root_ro,"no_external_routes":no_routes,"tmp_writable":True},sort_keys=True))
'''


def smoke(settings):
    expected=json.dumps({'uid':65534,'root_readonly':True,'no_external_routes':True,'tmp_writable':True},sort_keys=True)
    contract=build_contract('',{'version':1,'checks':[
        {'id':'isolation','kind':'command','target':'probe.py','argv':['python','probe.py'],'stdout':expected,'timeout':20},
        {'id':'function','kind':'python_function','engine':'docker','target':'probe.py','function':'add',
         'cases':[{'args':[2,3],'expected':5},{'args':[-4,1],'expected':-3}],'timeout':20}]})
    rows=ProjectVerifier(settings).verify([{'path':'probe.py','content':PROBE}],contract)
    if len(rows)!=2 or any(row['status']!='PASS' for row in rows):raise RuntimeError('실행 환경 검사 실패. 설정을 변경하지 않습니다: '+json.dumps(rows,ensure_ascii=False))
    return rows


def prepare(config_path=ROOT/'config'/'app_config.json',run_build=False):
    original=config_path.read_text(encoding='utf-8');config=json.loads(original)
    state=inspect_environment(config.get('verification',{}),require_image=not run_build)
    if state['status']!='READY':return state
    if not run_build:return state
    docker=state['docker'];folder=ROOT/'runtime'/'qa-python'
    subprocess.run([docker,'build','--pull','--tag',TAG,str(folder)],check=True,timeout=900)
    inspected=subprocess.run([docker,'image','inspect','--format','{{.Id}}',TAG],check=True,capture_output=True,
                             text=True,encoding='utf-8',timeout=15)
    image=inspected.stdout.strip()
    if not re.fullmatch(r'sha256:[a-f0-9]{64}',image):raise RuntimeError('실행 이미지의 고정 ID를 확인하지 못했습니다.')
    settings={'images':{'python':image}};rows=smoke(settings)
    # Keep other runtimes and application settings; reject concurrent configuration changes.
    if config_path.read_text(encoding='utf-8')!=original:raise RuntimeError('설정이 변경되었습니다. 최신 설정으로 다시 실행하세요.')
    config.setdefault('verification',{}).setdefault('images',{})['python']=image
    temporary=config_path.with_name('.'+config_path.name+'.'+uuid.uuid4().hex+'.tmp')
    try:
        temporary.write_text(json.dumps(config,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        os.replace(temporary,config_path)
    finally:
        if temporary.exists():temporary.unlink()
    return {'status':'READY','code':'CONFIGURED','image':image,'smoke_checks':rows}


def revalidate(project_id):
    if not re.fullmatch(r'PRJ-[A-Z0-9]{8}',project_id):raise ValueError('프로젝트 ID 형식이 올바르지 않습니다.')
    from app.orchestrator.orchestrator import Orchestrator
    from app.storage.database import Database
    from app.utils.path_manager import PathManager
    from app.runtime.checkpoints import Checkpoints
    from app.qa.code_verifier import CodeVerifier
    paths=PathManager(ROOT/'config'/'app_config.json')
    o=Orchestrator(Database(str(paths.database/'itni_ai_team.db')))
    checkpoint_status,saved=Checkpoints(o.database).load(project_id)
    contract=saved['decision']['verification_contract'];folder=ROOT/'projects'/project_id/'artifacts'
    if not folder.resolve().is_relative_to(ROOT.resolve()):raise ValueError('프로젝트 폴더 밖을 참조하는 산출물 폴더입니다.')
    artifacts=[]
    for path in folder.rglob('*'):
        if not path.is_file():continue
        if not path.resolve().is_relative_to(folder.resolve()):raise ValueError('산출물 폴더 밖을 참조하는 파일입니다.')
        artifacts.append({'path':path.relative_to(folder).as_posix(),'content':path.read_text(encoding='utf-8-sig')})
    if not artifacts:raise ValueError('검사할 저장 산출물이 없습니다.')
    config=json.loads((ROOT/'config'/'app_config.json').read_text(encoding='utf-8'))
    from app.qa.project_environment import prepare_project
    from app.tools.dependency_preflight import requested_libraries
    libraries=requested_libraries(saved.get('request',''),saved['decision'])
    settings,environment=prepare_project(ROOT,project_id,libraries,config.get('verification',{}))
    rows=CodeVerifier(o.database).verify_contract(project_id,artifacts,dict(contract,coverage_policy='requirements'),settings)
    status='FAIL' if blocking_failure(rows) else 'NOT_RUN' if required_unverified(rows) else 'PASS'
    if saved.get('final_result_id'):
        from app.qa.approval_evidence import make_evidence
        files=[{'path':str(folder/item['path'])} for item in artifacts]
        saved['final_verification']=make_evidence(o.database,project_id,saved['final_result_id'],contract,rows,files,ROOT)
        Checkpoints(o.database).save(project_id,saved,checkpoint_status)
    report={'project_id':project_id,'status':status,'scope':'DECLARED_VERIFICATION_CONTRACT',
            'checked_at':datetime.now().astimezone().isoformat(),'checks':rows,'approval_changed':False,
            'dependency_environment':environment}
    if status=='FAIL' and saved.get('final_result_id'):
        result=o.result_manager.get_result(saved['final_result_id'])
        if not result:raise ValueError('재검증 대상의 최종 결과를 찾을 수 없습니다.')
        o.qa_manager.review(project_id,result[1],result[0],'AGT-QA-001','FAIL',0.0,
                            'Docker 환경 준비 후 재검증 실패',['DOCKER_REVALIDATION_FAIL'])
        report['approval_changed']='pending approval invalidated by failed QA'
    (folder.parent/'docker_revalidation.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    return report


def main():
    parser=argparse.ArgumentParser(description='Docker 실행 환경 진단 및 명시적 이미지 준비')
    parser.add_argument('--build',action='store_true',help='공식 Python 기반 이미지를 다운로드·빌드하고 검사 후 설정 연결')
    parser.add_argument('--project',help='준비된 환경으로 저장 프로젝트의 검증 계약 재검사')
    args=parser.parse_args()
    result=prepare(run_build=args.build)
    print(json.dumps(result,ensure_ascii=False,indent=2))
    if result['status']!='READY':return 2
    if args.project:
        report=revalidate(args.project);print(json.dumps(report,ensure_ascii=False,indent=2))
        if report['status']!='PASS':return 1
    return 0


if __name__=='__main__':
    try:raise SystemExit(main())
    except (OSError,ValueError,RuntimeError,subprocess.SubprocessError) as exc:
        print(str(exc),file=sys.stderr);raise SystemExit(1)
