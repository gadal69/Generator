"""Explicit live Docker category checks; deterministic fixture, no model calls."""
from datetime import datetime
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from tests.test_requirement_verification import sample, SOURCE
from app.qa.project_verifier import ProjectVerifier
from app.qa.verification_contract import build_contract, blocking_failure, required_unverified


def main():
    output=ROOT/'docs'/'requirement_verification_20261027.json'
    if output.exists():raise RuntimeError('기존 결과를 덮어쓰지 않습니다.')
    settings=json.loads((ROOT/'config/app_config.json').read_text(encoding='utf-8'))['verification']
    artifacts=[{'path':'sample.py','content':SOURCE}]
    reports={}
    for name in ('complete','missing_boundary','disabled_normal','wrong_expected','model_proposal'):
        raw=sample();raw['checks'][0]['engine']='docker'
        if name=='missing_boundary':raw['checks'][0]['cases']=raw['checks'][0]['cases'][:3]
        if name=='disabled_normal':raw['checks'][0]['cases'][0]['enabled']=False
        if name=='wrong_expected':raw['checks'][0]['cases'][0]['expected']=999
        contract=build_contract('',proposed=raw) if name=='model_proposal' else build_contract('',raw)
        rows=ProjectVerifier(settings).verify(artifacts,contract)
        outcome='FAIL' if blocking_failure(rows) else 'NOT_RUN' if required_unverified(rows) else 'PASS'
        expected='PASS' if name=='complete' else 'FAIL' if name=='wrong_expected' else 'NOT_RUN'
        if outcome!=expected:raise RuntimeError(name+': '+json.dumps(rows,ensure_ascii=False))
        reports[name]={'outcome':outcome,'expected':expected,'checks':rows}
    output.write_text(json.dumps({'status':'PASS','checked_at':datetime.now().astimezone().isoformat(),
        'scope':'LIVE_DOCKER_REQUIREMENT_CATEGORIES_WITH_DETERMINISTIC_FIXTURE',
        'model_calls':0,'scenarios':reports},ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'status':'PASS','scenarios':{name:r['outcome'] for name,r in reports.items()},
                      'report':str(output)},ensure_ascii=False))


if __name__=='__main__':main()
