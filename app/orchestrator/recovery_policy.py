"""Bounded recovery decisions from measured failures, independent of model names."""
import hashlib
import json


class ReplanRequired(RuntimeError):
    pass


def failure_signature(issues,summary):
    category='contract' if 'ARTIFACT_VALIDATION_FAIL' in issues else 'functional' if 'FUNCTIONAL_TEST_FAIL' in issues else 'review'
    return hashlib.sha256((category+'|'+str(summary)[:300]).encode()).hexdigest()


def recovery_action(attempt,limit,history,issues,summary):
    signature=failure_signature(issues,summary)
    repeats=sum(item.get('signature')==signature for item in history)+1
    if attempt>=limit:return {'action':'REPLAN','signature':signature,'reason':'최대 재작업 횟수에 도달했습니다. 작업 분할·인터페이스·모델 적합성을 다시 검토해야 합니다.'}
    if attempt==0:action='TARGETED_REPAIR'
    elif repeats>1:action='REWRITE_WITHIN_SCOPE'
    else:action='REGRESSION_REPAIR'
    return {'action':action,'signature':signature,'reason':
        '실패한 요구사항과 실제 결과를 비교해 해당 파일을 수정하세요.' if action=='TARGETED_REPAIR' else
        '같은 방식이 실패했습니다. 배정 파일과 인터페이스를 유지하고 구현 접근을 바꿔 전체 파일을 다시 작성하세요. 새 파일·외부 서비스·권한을 추가하지 마세요.' if action=='REWRITE_WITHIN_SCOPE' else
        '새로 드러난 결함을 수정하면서 이전 통과 항목도 다시 검증하세요.'}
