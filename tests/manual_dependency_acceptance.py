"""Explicit live Docker test using a deterministic fixture, not a model generation benchmark."""
import contextlib
import io
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from app.chief.chief_decision import ChiefDecision
from app.orchestrator.orchestrator import Orchestrator
from app.orchestrator.work_contract import read_contract
from app.qa.project_environment import prepare_project
from app.qa.project_verifier import ProjectVerifier
from app.qa.verification_contract import build_contract, required_unverified
from app.storage.database import Database

SOURCE='''from PIL import Image
from io import BytesIO
def image_info():
    image = Image.new('RGB', (8, 6), (12, 34, 56)).resize((4, 3))
    buffer = BytesIO()
    image.save(buffer, format='PNG')
    buffer.seek(0)
    with Image.open(buffer) as result:
        return {'size': list(result.size), 'format': result.format, 'pixel': list(result.getpixel((0, 0)))}
if __name__ == '__main__':
    assert image_info() == {'size': [4, 3], 'format': 'PNG', 'pixel': [12, 34, 56]}
    print('image-ok')
'''


def main():
    output=ROOT/'docs'/'dependency_acceptance_20261027'
    output.mkdir(parents=True,exist_ok=True)
    report_path=output/'report.json'
    if report_path.exists(): raise RuntimeError('기존 테스트 결과를 덮어쓰지 않습니다.')
    settings=json.loads((ROOT/'config/app_config.json').read_text(encoding='utf-8'))['verification']
    database=ROOT/'data'/'acceptance'/'dependencies-20261027.db'
    database.parent.mkdir(parents=True,exist_ok=True)
    o=Orchestrator(Database(database))
    class Adapter:
        model='deterministic-fixture'
        def generate(self,prompt):
            c=read_contract(prompt)
            if c and c.get('files'): return '<artifact path="image_sample.py">\n'+SOURCE+'</artifact>'
            return '이미지 편집 검사 fixture 완료'
    contract=build_contract('',{'version':1,'checks':[
        {'id':'cli','kind':'command','target':'image_sample.py','argv':['python','image_sample.py'],'stdout':'image-ok'},
        {'id':'image','kind':'python_function','engine':'docker','target':'image_sample.py','function':'image_info',
         'cases':[{'args':[],'expected':{'size':[4,3],'format':'PNG','pixel':[12,34,56]}}]}]})
    libraries=[{'package':'Pillow','version':'11.3.0','runtime':'python','purpose':'이미지 생성·크기 변경·PNG 저장 확인'}]
    decision=ChiefDecision(core_roles=['Software Developer'],file_creation_required=True,
                           library_requirements=libraries,verification_contract=contract).to_dict()
    with contextlib.redirect_stdout(io.StringIO()):
        result=o.run_user_request('image_sample.py 파일로 이미지 편집 검사 프로그램을 만들어 주세요.',
            analysis_override=decision,adapter_factory=lambda model:Adapter(),model_override='deterministic-fixture',
            qa_mode='rule',approval_mode='manual',verbose=False)
    checks=[row for row in result['code_verifications'] if row.get('check_id') in {'cli','image'}]
    if result['status']!='PENDING_APPROVAL' or len(checks)!=2 or any(row['status']!='PASS' for row in checks):
        raise RuntimeError(json.dumps(result,ensure_ascii=False))
    project=result['project_id']
    original=json.loads((ROOT/'projects'/project/'environment/dependencies.json').read_text(encoding='utf-8'))
    _,reused=prepare_project(ROOT,project,libraries,settings)
    assert reused['status']=='READY' and reused['image']==original['image']
    failed_project=o.create_project('패키지 설치 실패 검증','존재하지 않는 Pillow 버전의 실패 차단 확인')
    bad_settings,failed=prepare_project(ROOT,failed_project,[dict(libraries[0],version='9999999.0')],settings)
    assert failed['status']=='NOT_READY' and not bad_settings['images']
    rows=ProjectVerifier(bad_settings).verify([{'path':'image_sample.py','content':SOURCE}],contract)
    assert required_unverified(rows)
    report={'scope':'DETERMINISTIC_FIXTURE_FULL_WORKFLOW_WITH_LIVE_DOCKER', 'model_calls':0,
            'status':'PASS','positive_project':project,'image':original['image'],
            'checks':checks,'cache_recheck':'PASS','failure_project':failed_project,
            'failure_preparation':failed,'failure_verifications':rows,
            'approval_status':result['status'],'host_package_installation':False}
    report_path.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'status':'PASS','positive_project':project,'failure_project':failed_project,
                      'report':str(report_path)},ensure_ascii=False))


if __name__=='__main__': main()
