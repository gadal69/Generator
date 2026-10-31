"""Strict, bounded local synthesis; never silently accept planner fallback."""
import json
from datetime import datetime, timezone
from pathlib import Path
from app.runtime.run_control import RunCancelled
from app.models.generation_policy import prepare_adapter

SYNTHESIS_SCHEMA = {'type': 'object', 'required': ['comparison_summary', 'work_summary', 'rationale'],
    'properties': {key: {'type': 'string', 'minLength': 1}
                   for key in ('comparison_summary', 'work_summary', 'rationale')}}
SYNTHESIS_SCHEMA['properties'].update({
    key: {'type': 'array', 'items': {'type': 'string'}}
    for key in ('core_roles', 'required_capabilities', 'work_steps', 'acceptance_criteria')})
SYNTHESIS_SCHEMA['properties'].update({
    key: {'type': 'array', 'items': {'type': 'object'}}
    for key in ('specialists', 'task_assignments', 'library_requirements')})
SYNTHESIS_SCHEMA['properties']['verification_contract'] = {'type': 'object'}


def synthesize_json(adapter, prompt, parse):
    retry = prompt
    for attempt in range(2):
        text = ''
        try:
            prepare_adapter(adapter, 'analysis', attempt)
            method = getattr(type(adapter), 'generate_json', None)
            text = adapter.generate_json(retry, SYNTHESIS_SCHEMA) if callable(method) else adapter.generate(retry)
            if getattr(adapter, 'last_response', {}).get('truncated'):
                raise ValueError('분석 종합 응답이 출력 한도로 중단되었습니다.')
            # Only permit an optional enclosing Markdown fence, not arbitrary prose/braces.
            content = text.strip()
            if content.startswith('```') and content.endswith('```'):
                content = content.split('\n', 1)[1].rsplit('```', 1)[0].strip()
            response = json.loads(content)
            if not isinstance(response, dict):
                raise ValueError('분석 종합은 JSON 객체여야 합니다.')
            for key in ('comparison_summary', 'work_summary', 'rationale'):
                if not isinstance(response.get(key), str) or not response[key].strip():
                    raise ValueError(f'분석 종합의 {key} 문자열이 없거나 비어 있습니다.')
            for key in ('core_roles', 'required_capabilities', 'work_steps', 'acceptance_criteria'):
                if key in response and (not isinstance(response[key], list) or not all(isinstance(x, str) for x in response[key])):
                    raise ValueError(f'{key}는 문자열 목록이어야 합니다.')
            decision = parse(response)
            return response, decision
        except RunCancelled:
            raise
        except Exception as exc:
            reason = f'{type(exc).__name__}: {exc}'[:500]
            from app.orchestrator.team_runner import TeamRunner
            sample = TeamRunner._redact_for_external_review(str(text)[:16000])
            try:
                folder = Path(__file__).resolve().parents[2] / 'data' / 'diagnostics'
                folder.mkdir(parents=True, exist_ok=True)
                with (folder / 'synthesis_failures.jsonl').open('a', encoding='utf-8') as log:
                    log.write(json.dumps({'time': datetime.now(timezone.utc).isoformat(), 'attempt': attempt + 1,
                        'reason': reason, 'response_sample': sample}, ensure_ascii=False) + '\n')
            except OSError:
                reason += ' (진단 파일 저장 실패)'
            retry = prompt + '\n이전 종합 응답 오류: ' + reason + '\n원래 요구사항과 두 검토 의견을 유지하고 유효한 JSON 객체만 다시 작성하세요. 주석·생략부호는 금지합니다.'
    raise RuntimeError('분석 종합을 2회 시도했지만 실패했습니다. 실행하지 않았습니다. 원인: ' + reason)
