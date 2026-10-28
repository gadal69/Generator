"""Persistent measured repair history; model opinions never become execution evidence."""
from datetime import datetime
import difflib
import hashlib


def record(state,phase,task_id,result_id,status,reason,content='',checks=None,review=None):
    ledger=state.setdefault('qa_audit',[])
    prior=next((r for r in reversed(ledger) if r['phase']==phase and r['task_id']==task_id),None)
    digest=hashlib.sha256(content.encode()).hexdigest()
    snapshots=state.setdefault('qa_audit_snapshots',{})
    key=phase+':'+task_id
    old=snapshots.get(key)
    delta=list(difflib.ndiff((old or '').splitlines(),content.splitlines())) if old is not None else []
    row={'sequence':len(ledger)+1,'at':datetime.now().isoformat(),'phase':phase,'task_id':task_id,
         'result_id':result_id,'status':status,'cause':str(reason)[:6000],
         'content_hash':digest,'previous_hash':prior.get('content_hash') if prior else None,
         'change':{'kind':'INITIAL' if old is None else 'UNCHANGED' if old==content else 'MODIFIED',
                   'added_lines':sum(x.startswith('+ ') for x in delta),'removed_lines':sum(x.startswith('- ') for x in delta),
                   'diff_excerpt':'\n'.join(x for x in delta if x.startswith(('+ ','- ')))[:4000]},
         'checks':checks or [],'gemini_review':review,
         'execution_evidence':phase=='INTEGRATION'}
    ledger.append(row);snapshots[key]=content
    return row


def stop_notice(state,reason):
    return {'reason':str(reason),'next_steps':[
        '실패한 요구사항·실제 실행 결과·수정 전후 차이를 확인합니다.',
        '검증 기준을 완화하지 않고 작업 분할·파일 인터페이스·실행 환경을 재검토합니다.',
        '필요하면 모델을 명시적으로 선택하고 대표가 분석을 확인한 뒤 새 실행을 시작합니다.'],
        'automatic_resume':False,'history_file':'qa_recovery.json',
        'review_count':len(state.get('qa_audit',[]))}
