"""Explain incomplete verification plans without inventing authoritative expectations."""
def verification_feedback(decision, request=''):
    if any(item.get('kind') == 'PLAN_ERROR' for item in decision.get('analysis_issues', [])):
        return ['', '검증 계획 생성 실패로 실행을 보류했습니다.',
                '추가 질문이 별도로 없다면 같은 요구사항을 반복해서 붙여 넣지 않아도 됩니다.',
                '위 원인과 다음 조치를 확인하세요. 실패·미실행 검증은 그대로 유지됩니다.']
    contract = decision.get('verification_contract') or {}
    requirements = contract.get('requirements') or []
    checks = contract.get('checks') or []
    criteria = decision.get('acceptance_criteria') or []
    issues = []
    if not requirements:
        issues.append('분석된 요구사항 목록이 없습니다.')
    if not criteria:
        issues.append('프로젝트별 합격 기준이 없습니다.')
    if not checks:
        issues.append('실행할 검증 항목이 없습니다.')
    for requirement in requirements:
        linked = [c for c in checks if requirement['id'] in c.get('requirements', [])
                  and c.get('enabled', True) and c.get('required', True)]
        if not linked:
            issues.append(f"{requirement['id']}: 연결된 필수 검증 항목이 없습니다.")
        else:
            from app.qa.verification_contract import case_category
            covered = set()
            for check in linked:
                if check.get('cases'):
                    covered.update(case_category(case, check) for case in check['cases'] if case.get('enabled', True))
                else:
                    covered.add(check.get('category', 'normal'))
            missing = set(requirement.get('required_categories') or ['normal', 'invalid', 'boundary']) - covered
            if missing:
                issues.append(f"{requirement['id']}: 입력 유형별 검사 부족 ({', '.join(sorted(missing))}).")
    if not issues:
        return []
    lines = ['', '분석 보완 필요:'] + ['• ' + issue for issue in issues]
    lines += ['', '요구사항 목록 작성·보완안 (대표 확인 전 초안):']
    if requirements:
        lines += [f"• {r['id']}: {r['description']}" for r in requirements]
    else:
        lines += ['• R1: ' + str(decision.get('work_summary') or request or '원하는 기능과 산출물을 작성해 주세요.'),
                  '• 서로 다른 필수 기능은 R2, R3처럼 나누어 주세요.']
    lines += ['', '프로젝트별 합격 기준 작성·보완안:',
              '• 각 R번호별로 입력 조건 → 기대 결과를 구체적으로 작성해 주세요.',
              '• 오류 처리, 결과 형식, 허용 오차·제한이 있다면 함께 정해 주세요.']
    lines += ['• 기존 기준: ' + str(item) for item in criteria]
    lines += ['', '검증 항목 작성·보완안:',
              '• 각 R번호에 정상 입력·잘못된 입력·경계값 검사와 예상 결과를 연결해 주세요.',
              '• 각 검사의 대상 파일·함수 또는 버튼과 실행 방법을 지정해야 합니다.',
              '• 적용되지 않는 입력 유형은 이유를 명시하고, 지원되지 않는 검사는 미검증으로 남깁니다.',
              '', '재분석 요청: 위 초안을 확인하고 대표자 요구사항 입력란에 기능·입력·예상 결과를 보완한 뒤',
              '「비서실장 업무 분석」을 다시 눌러 주세요. 비서실장이 요구사항과 실행 가능한 검증 항목을 다시 구성합니다.',
              '이 안내는 검증 통과 기록이 아니며 기존 실패·미실행 항목을 통과시키지 않습니다.']
    lines += ['', '복사해서 사용할 보완 문장 — 대표 확인 전 초안:',
              reanalysis_draft(decision, request),
              '위 제안을 확인·수정한 뒤 필요한 문장을 대표자 요구사항에 반영하고 다시 분석해 주세요.']
    return lines


def reanalysis_draft(decision, request=''):
    """Draft only: never modify the contract or treat suggestions as consent."""
    original = request or decision.get('work_summary') or '요청한 산출물을 작성해 주세요.'
    contract = decision.get('verification_contract') or {}
    requirements = contract.get('requirements') or []
    criteria = decision.get('acceptance_criteria') or []
    lines = ['[기존 업무지시 — 원문]', str(original),
             '', '[요구사항 목록 — 기존 분석 내용, 원문과 일치하는지 확인 필요]']
    if decision.get('analysis_issues'):
        lines += ['【이번 분석에서 보완할 부분 — 실행 조건으로 확정된 내용 아님】']
        for index, issue in enumerate(decision['analysis_issues'], 1):
            lines += [f"수정 대상 {index}: {issue.get('reason', '')}", '보완 방향: ' + issue.get('action', '')]
        lines += ['검증 계획 오류는 사용자 기능 요구사항과 구분해 해결하고, 함수 이름·대상 파일·입력·예상 결과를 빠짐없이 작성해 주세요.']
    lines += ([f"{r['id']}: {r['description']}" for r in requirements]
              or ['R1: 위 원문의 작업을 수행하고 요청한 결과물을 제공해 주세요.'])
    lines += ['', '[합격 기준 — 기존 분석 내용, 대표 확인 필요]']
    lines += [str(item) for item in criteria] or ['기존 분석에 구체적인 합격 기준이 없습니다. 아래 제안을 확인해 주세요.']
    lines += ['', '[대표 확인이 필요한 제안 — 아직 확정된 조건이 아닙니다]']
    if '계산기' in str(original):
        lines += ['요구사항 제안: 기본 사칙연산과 소수점 입력, 초기화 기능을 제공해 주세요.',
                  '합격 기준 제안: 2+3=5, 5-8=-3, 4*3=12, 8/2=4, 0.5+0.5=1이어야 합니다.',
                  '오류 처리 제안: 0으로 나누면 오류를 안내하고 종료되지 않으며, 이어서 정상 계산할 수 있어야 합니다.',
                  '검증 항목 제안: 각 연산의 정상 입력, 5/0의 잘못된 입력, 0+0의 경계값을 실제 실행해 확인해 주세요.',
                  '초기화 검증 제안: 계산 후 초기화하면 이전 입력·결과가 지워지는지 확인해 주세요.']
    else:
        lines += ['요구사항 제안: 각 필수 기능을 R번호로 나누고, 원문에서 지정한 산출물에 연결해 주세요.',
                  '합격 기준 제안: 각 기능은 대표가 제공하거나 확인한 입력 예시에서 지정한 예상 결과와 일치해야 합니다.',
                  '오류 처리 제안: 사용할 수 없는 입력·자료는 원인을 안내하고 성공으로 표시하지 않아야 합니다.',
                  '검증 항목 제안: 각 R번호에 정상 사례, 잘못된 입력 사례, 허용 범위의 경계 사례와 예상 결과를 연결해 주세요.',
                  '비적용 항목 제안: 입력이나 경계값이 없는 산출물은 비적용 이유를 명시하고 실제 적용되는 검사를 수행해 주세요.']
    lines += ['공통 검증 제안: 약속한 파일은 실제 존재하고 내용이 비어 있지 않아야 합니다. 파일이 없는 작업은 비적용 이유를 표시해 주세요.',
              '검증 방식 제안: 실행·검토하지 못한 항목은 미검증으로 남기고, 모델 의견만으로 통과시키지 마세요.',
              '', '[확인 후 재분석 요청]',
              '위 제안 중 원문과 충돌하는 내용은 제외하고, 채택할 조건과 구체적인 입력·예상 결과를 확인한 뒤 다시 분석해 주세요.',
              '확인되지 않은 제안은 합격 기준으로 확정하지 말고 추가 질문으로 남겨 주세요.']
    return '\n'.join(lines)
