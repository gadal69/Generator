"""Bind final verification to the exact result, contract and saved artifacts."""
import hashlib
import json
from pathlib import Path

from app.qa.verification_contract import blocking_failure, required_unverified, fingerprint
from app.orchestrator.work_contract import read_contract


def _hash(data):
    return hashlib.sha256(data).hexdigest()


def _file_hash(path):
    if path.stat().st_size>20*1024*1024:raise ValueError('승인 대상 파일 크기 제한 초과')
    return _hash(path.read_bytes())


def make_evidence(database, project_id, result_id, contract, records, written_files, workspace):
    root=Path(workspace).resolve()
    files={}
    for item in written_files:
        path=Path(item['path']).resolve()
        if not path.is_relative_to(root):raise ValueError('승인 대상 파일의 작업공간 이탈')
        files[str(path)]=_file_hash(path)
    folder=root/'projects'/project_id/'artifacts'
    artifact_files=sorted(str(path.resolve()) for path in folder.rglob('*') if path.is_file()) if folder.exists() else []
    manifest=root/'projects'/project_id/'environment'/'dependencies.json'
    if manifest.is_file():files[str(manifest.resolve())]=_file_hash(manifest)
    with database.connection() as conn:
        result=conn.execute('SELECT r.summary FROM results r JOIN tasks t ON t.task_id=r.task_id WHERE r.result_id=? AND t.project_id=?',(result_id,project_id)).fetchone()
    if not result:raise ValueError('검증 결과에 연결할 최종 결과가 없습니다.')
    expected={check['id'] for check in contract.get('checks',[]) if check.get('required',True)}
    expected.update('coverage:'+requirement['id'] for requirement in contract.get('requirements',[]))
    missing=expected-{row.get('check_id') for row in records}
    if not contract.get('requirements'):missing.add('requirements')
    if any(check.get('required',True) and not check.get('requirements') for check in contract.get('checks',[])):
        missing.add('requirement_links')
    return {'version':1,'result_id':result_id,'result_hash':_hash((result[0] or '').encode()),
            'contract_hash':fingerprint(contract),'workspace':str(root),'files':files,
            'artifact_files':artifact_files,
            'status':'FAIL' if blocking_failure(records) else 'NOT_RUN' if missing or not records or required_unverified(records) else 'PASS',
            'missing_evidence':sorted(missing),'records':records}


def check_approval_evidence(conn, project_id, result_id):
    target=conn.execute('SELECT t.description,r.summary FROM results r JOIN tasks t ON t.task_id=r.task_id WHERE r.result_id=? AND t.project_id=?',(result_id,project_id)).fetchone()
    program_result=bool(target and ('<artifact ' in (target[1] or '') or '[파일 산출 지시]' in (target[0] or '')
                                  or (read_contract(target[0] or '') or {}).get('files')))
    recorded_code=conn.execute("SELECT 1 FROM code_verifications WHERE project_id=? AND verification_type IN ('PYTHON_SYNTAX','FUNCTIONAL_TEST','COMMAND') LIMIT 1",(project_id,)).fetchone()
    program_result=program_result or bool(recorded_code)
    checkpoint=conn.execute('SELECT state_json FROM workflow_checkpoints WHERE project_id=?',(project_id,)).fetchone()
    if not checkpoint:
        if program_result:raise ValueError('프로그램의 최종 검증 기록이 없습니다. 요구사항 확인 및 재검증이 필요합니다.')
        return  # Non-program legacy tasks have only the existing QA gate.
    state=json.loads(checkpoint[0]);decision=state.get('decision',{})
    evidence=state.get('final_verification')
    if not decision.get('file_creation_required') and not evidence and not program_result:return
    if not evidence or evidence.get('result_id')!=result_id:
        raise ValueError('최종 결과에 연결된 실행·요구사항 검증 기록이 없습니다. 재검증이 필요합니다.')
    records=evidence.get('records',[])
    if evidence.get('status')!='PASS' or not records or blocking_failure(records) or required_unverified(records):
        raise ValueError('필수 검증에 실패·누락·미실행 항목이 남아 있어 최종 승인할 수 없습니다.')
    if fingerprint(decision.get('verification_contract',{}))!=evidence.get('contract_hash'):
        raise ValueError('승인 기준이 변경되었습니다. 새 기준으로 재검증하세요.')
    result=conn.execute('SELECT r.summary FROM results r JOIN tasks t ON t.task_id=r.task_id WHERE r.result_id=? AND t.project_id=?',(result_id,project_id)).fetchone()
    if not result or _hash((result[0] or '').encode())!=evidence.get('result_hash'):
        raise ValueError('검증 후 최종 결과가 변경되었습니다. 재검증하세요.')
    root=Path(evidence['workspace']).resolve()
    folder=root/'projects'/project_id/'artifacts'
    actual=sorted(str(path.resolve()) for path in folder.rglob('*') if path.is_file()) if folder.exists() else []
    if actual!=evidence.get('artifact_files'):
        raise ValueError('검증 후 산출 파일 목록이 변경되었습니다. 재검증하세요.')
    try:
        for filename,expected in evidence['files'].items():
            path=Path(filename)
            if not path.resolve().is_relative_to(root) or not path.is_file() or _file_hash(path)!=expected:
                raise ValueError('검증 후 파일 또는 실행 환경이 변경되었습니다. 재검증하세요.')
    except OSError as exc:raise ValueError('승인 대상 파일을 확인하지 못했습니다. 재검증하세요.') from exc
