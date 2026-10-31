"""Bounded repair of missing analysis contracts before representative confirmation."""
import json
import re
from app.qa.verification_contract import validate_contract, explicit_contract
from app.runtime.run_control import RunCancelled


def plan_gaps(contract):
    try:
        validate_contract(contract)
    except (ValueError, TypeError, KeyError, AttributeError) as exc:
        return ['검증 계획 형식 오류: ' + str(exc)]
    requirements = contract.get('requirements') or []
    checks = contract.get('checks') or []
    if not requirements or not checks:
        return ['요구사항 목록과 실행 검증 항목이 모두 필요합니다.']
    linked = {r for c in checks if c.get('required', True) and c.get('enabled', True)
              for r in c.get('requirements', [])}
    return [f"{r['id']}: 연결된 필수 검증 항목이 없습니다."
            for r in requirements if r['id'] not in linked]


def repair_plan(decision, request, adapter, supplied=None):
    if not plan_gaps(decision.verification_contract):
        return decision
    decision.analysis_issues = [item for item in decision.analysis_issues if item.get('kind') != 'PLAN_ERROR']
    failure = '모델이 요구사항 목록 또는 연결된 필수 검사를 반환하지 않았습니다.'
    # Representative-authored contracts must never be silently replaced.
    if supplied is not None or explicit_contract(request) is not None:
        decision.requirements_ready = False
        decision.analysis_issues.append({'kind': 'PLAN_ERROR', 'reason': '직접 지정된 검증 계약에 요구사항 또는 필수 검사 연결이 없습니다.',
            'action': '지정한 검증 계약을 수정해 다시 분석하세요. 사용자 기준은 자동 변경하지 않았습니다.'})
        return decision
    from app.chief.allocation_policy import ALLOCATION_INSTRUCTION
    prompt = (ALLOCATION_INSTRUCTION +
        '요구사항을 실제 실행 가능한 검증 계획으로 변환하세요. JSON 객체만 반환하세요. '
        '필드: version:1, requirements:[{id,description,required_categories}], '
        'checks:[{id,kind,target,required:true,requirements:[요구사항id],...}]. '
        'kind는 python_function(function,cases:[{args,expected,category}]), '
        'gui(factory,actions,widgets), text(contains), file, command(argv) 등을 사용하세요. '
        '정상·오류·경계 사례를 연결하고 비적용은 category_rationale로 설명하세요. '
        '각 검사는 배정한 파일과 실제 함수·위젯을 대상으로 해야 합니다. '
        '예상 결과는 제안이며 실행한 것처럼 쓰지 마세요. 원문의 기준을 변경하지 마세요. '
        '정보가 부족하면 빈 checks를 반환하세요.\n요구사항:\n' + request +
        '\n분석된 작업·파일:\n' + json.dumps(decision.to_dict(), ensure_ascii=False))
    decision.verification_plan_attempts = []
    retry_prompt = prompt
    for attempt in range(2):
        text = ""
        try:
            from app.chief.verification_schema import PLAN_SCHEMA, normalize_categories
            from app.models.generation_policy import prepare_adapter
            prepare_adapter(adapter, 'analysis', attempt)
            instruction = prompt if attempt == 0 else retry_prompt
            structured = getattr(type(adapter), 'generate_json', None)
            text = adapter.generate_json(instruction, PLAN_SCHEMA) if callable(structured) else adapter.generate(instruction)
            if getattr(adapter, 'last_response', {}).get('truncated'):
                raise ValueError('검증 계획 응답 중단')
            match = re.search(r'\{.*\}', text or '', re.S)
            if not match:
                raise ValueError('모델 응답에 검증 계획 JSON이 없습니다.')
            candidate = validate_contract(normalize_categories(json.loads(match.group(0))))
            failure = '; '.join(plan_gaps(candidate))
            if plan_gaps(candidate):
                raise ValueError(failure)
            if not plan_gaps(candidate):
                decision.verification_plan_attempts.append({"attempt": attempt + 1, "status": "VALID"})
                decision.verification_contract = candidate
                if not decision.acceptance_criteria:
                    decision.acceptance_criteria = ["모델 제안·대표 확인 필요: " + r["description"] + " — 연결된 검사의 예상 결과와 일치해야 합니다." for r in candidate["requirements"]]
                break
        except RunCancelled:
            raise
        except Exception as exc:
            failure = "검증 계획 응답 해석·검사 실패: " + type(exc).__name__ + " — " + str(exc)[:300]
            # Local diagnostic only. Do not send failed raw responses to external providers.
            from app.orchestrator.team_runner import TeamRunner
            response_sample = TeamRunner._redact_for_external_review(str(text)[:16000])
            decision.verification_plan_attempts.append({'attempt': attempt + 1, 'status': 'INVALID',
                'reason': failure, 'response_sample': response_sample})
            try:
                from pathlib import Path
                from datetime import datetime, timezone
                folder = Path(__file__).resolve().parents[2] / 'data' / 'diagnostics'
                folder.mkdir(parents=True, exist_ok=True)
                with (folder / 'verification_plan_failures.jsonl').open('a', encoding='utf-8') as log:
                    log.write(json.dumps({'time': datetime.now(timezone.utc).isoformat(),
                        'model': str(getattr(adapter, 'model', 'unknown')),
                        **decision.verification_plan_attempts[-1]}, ensure_ascii=False) + '\n')
            except OSError as log_error:
                decision.verification_plan_attempts[-1]['diagnostic_write_error'] = type(log_error).__name__
            retry_prompt = (prompt + '\n이전 검증 계획의 오류: ' + failure +
                '\nchecks의 각 원소는 객체이며 id는 비어 있지 않은 고유 문자열(C1,C2 등), '
                'kind는 검사 종류 문자열이어야 합니다. id를 생략하거나 check_id/type으로 바꾸지 마세요. '
                'python_function에는 function과 cases가 필수입니다. target은 함수명이 아닌 파일 경로입니다. ' +
                '\n올바른 JSON으로 계획 전체를 다시 작성하세요. 설명·주석·코드 펜스·생략부호·Python 표현식은 금지합니다. '
                '문자열과 키는 큰따옴표, 불리언은 true/false, 빈 값은 null을 사용하세요. '
                '요구사항과 예상 결과의 의미를 바꾸지 마세요. 확신할 수 없는 항목은 추측하지 마세요.')
    if plan_gaps(decision.verification_contract):
        decision.requirements_ready = False
        decision.analysis_issues.append({'kind': 'PLAN_ERROR', 'reason': failure,
            'action': '검증 계획 작성·검사를 최대 2회 시도했지만 실패했습니다. 요구사항 반복 입력 대신 모델·검증 형식과 대상 파일 연결을 점검한 후 다시 분석하세요.'})
    return decision
