"""Domain-aware analysis. Requests describe preparation, never grant tool permissions."""
import re

DOMAIN_RULES = {
    '개발': (('프로그램', '소프트웨어', '코드', '웹사이트', '앱 개발'), '소프트웨어 개발자'),
    '콘텐츠': (('콘텐츠', '컨텐츠', '동영상', '영상', '대본', '블로그', '음성'), '콘텐츠 기획·제작자'),
    '기획·분석': (('기획', '시장', '전략', '조사', '사업계획', '요구사항 분석'), '기획·분석 담당자'),
    '문서·사무': (('문서', '보고서', '제안서', '발표자료', '엑셀', 'ppt', 'docx'), '문서·사무 담당자'),
    '데이터': (('데이터', '통계', 'csv', 'sqlite', '시각화'), '데이터 분석가'),
    '디자인': (('디자인', '로고', '브랜딩', '포스터', 'ui', 'ux'), '디자이너'),
    '운영·관리': (('운영', '일정', '예약', '모니터링', '관리', '메일'), '운영·관리 담당자'),
    '마케팅·영업': (('마케팅', '영업', '광고', '고객', 'sns', '상품 소개'), '마케팅·영업 담당자'),
}

DOMAIN_INSTRUCTION = (
    'Analyze development, content, planning/research, office/documents, data, design, '
    'operations and marketing/sales, including mixed requests. Assign domain-specific workers '
    'with concrete deliverables; do not use Developer as a generic worker. '
    'Separate analysis capability from available execution tools. Describe missing API/library '
    'requirements, prerequisites, cost/data-transfer approval and local/manual alternatives. '
    'Never claim tools are installed or connected without evidence. Ask for missing output '
    'format, input assets and acceptance criteria. Do not reject planning merely because '
    'a media generation tool is unavailable. Software integration roles apply only to code work. '
)


def enrich_analysis(decision, request):
    text = (request or '').casefold()
    domains = [name for name, (words, _) in DOMAIN_RULES.items()
               if any(word in text for word in words)]
    if re.search(r"\.(?:py|js|ts|tsx|jsx|html|css|sql|cs|java)\b", text) and "개발" not in domains:
        domains.insert(0, "개발")
    if "개발" in domains:
        decision.file_creation_required = True
    promised_code = any(re.search(r"\.(?:py|js|ts|html|css|cs|java)$", path, re.I)
        for assignment in decision.task_assignments for path in assignment.get('files', []))
    if promised_code:
        decision.file_creation_required = True
        if '개발' not in domains:
            domains.insert(0, '개발')
    if '계산기' in text and '개발' not in domains:
        domains.insert(0, '개발')
        decision.file_creation_required = True
    underspecified = ('계산기' in text and not any(word in text for word in
        ('python', '파이썬', '웹', '데스크톱', 'gui', 'cli', '명령줄', 'html', '기본값', '알아서')))
    decision.analysis_issues = [item for item in decision.analysis_issues if item.get('kind') != 'NEEDS_INPUT']
    if underspecified:
        decision.analysis_issues.append({'kind': 'NEEDS_INPUT', 'reason': '계산기의 실행 형태가 지정되지 않았습니다.',
            'action': '웹·데스크톱·명령줄 중 원하는 형태를 요구사항에 추가해 주세요.'})
    decision.requirements_ready = not underspecified and not decision.analysis_issues
    if underspecified:
        decision.clarification_questions = ['계산기를 웹·데스크톱·명령줄 중 어떤 형태로 만들까요?',
            '필요한 연산과 입력·오류 처리 기준을 알려 주세요. 기본 사칙연산으로 진행해도 되나요?']
    decision.work_domains = domains
    if not domains:
        return decision
    non_code = '개발' not in domains and decision.workflow_type == 'standard'
    if non_code:
        decision.core_roles = [DOMAIN_RULES[name][1] for name in domains]
        decision.specialists = []
        decision.integration_role = ''
        decision.direct_execution = False
        decision.file_creation_required = False
        decision.task_assignments = [
            {'role': role, 'deliverable': request, 'files': []}
            for role in decision.core_roles]
        decision.rationale = '요청 분야에 맞는 담당자를 구성했습니다. 도구 준비와 실행 결과 확인은 별도로 필요합니다.'
        decision.work_summary = request
        decision.work_steps = ['요구사항·입력 자료·완료 기준 확인', '담당자별 산출물 작성',
                               '필요 도구·권한 준비 후 실행', '산출물 검증 및 대표 승인']
    if not non_code:
        for domain in domains:
            role = DOMAIN_RULES[domain][1]
            if domain != "개발" and role not in decision.core_roles:
                decision.core_roles.append(role)
    requests = []
    def need(kind, name, purpose, alternative, runtime='', package=''):
        requests.append(dict(kind=kind, name=name, purpose=purpose, status='준비 확인 필요',
            runtime=runtime, package=package, version='', alternative=alternative,
            approval='설치·외부 전송·비용·게시 실행 전 대표 승인',
            next_action='환경·연결·실행 검증 후 사용. 호스트에 임의 설치하지 않음'))
    if '콘텐츠' in domains:
        if any(w in text for w in ('영상', '동영상')):
            need('api_or_local_model', '영상 생성 모델/API', '요청 장면 생성',
                 '기존 촬영 영상 제공 또는 스토리보드·대본만 우선 제작')
            need('program', 'FFmpeg', '영상 편집·인코딩', '장면별 편집 지시서를 만들어 수동 편집')
            decision.clarification_questions = ['영상 길이·비율·스타일은 무엇인가요?',
                '사용할 이미지·영상과 음성·자막 요구사항, 비용 한도는 무엇인가요?']
        elif any(w in text for w in ('음성', '나레이션')):
            need('api_or_local_model', '음성 합성 도구', '음성 파일 생성', '녹음 파일 제공 또는 대본만 작성')
    if '문서·사무' in domains:
        for words, package in [(('엑셀', 'xlsx'), 'openpyxl'), (('docx', '워드'), 'python-docx'),
                               (('ppt', '발표자료'), 'python-pptx')]:
            if any(w in text for w in words):
                need('library', package, '문서 산출물 생성', 'Markdown·CSV·텍스트로 우선 작성', 'python', package)
    if '데이터' in domains and any(w in text for w in ('통계', '시각화')):
        need('library', 'pandas', '표 데이터 분석', '표준 라이브러리 csv·statistics 사용', 'python', 'pandas')
    if '디자인' in domains and any(w in text for w in ('이미지', '로고', '포스터')):
        need('api_or_local_model', '이미지 생성·디자인 도구', '시각 산출물 제작', 'SVG 또는 디자인 명세 작성')
    if any(w in text for w in ('최신', '웹 검색', '외부 api', '공공데이터')):
        need('api', '검색·공식 데이터 API', '최신 외부 자료 조회', '대표가 제공한 자료로 분석하되 최신성 미검증 표시')
    if any(w in text for w in ('메일 발송', '이메일 발송', 'sns 게시', '광고 집행', '캘린더 연동')):
        need('api', '대상 서비스 API/SMTP', '외부 서비스 실행', '초안·일정 파일만 작성하고 수동 처리')
    decision.tool_requests = requests
    for tool in requests:
        if tool['kind'] == 'library' and not any(x.get('package') == tool['package'] for x in decision.library_requirements):
            decision.library_requirements.append({k: tool[k] for k in ('package', 'version', 'purpose', 'runtime')})
    if not decision.clarification_questions:
        decision.clarification_questions = ['입력 자료·결과 형식·완료 기준을 확인해 주세요.']
    return decision
