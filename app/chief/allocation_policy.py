"""Shared allocation rules for Chief analysis and implementation execution."""
import re
from app.tools.project_file_tool import is_file_creation_request

ALLOCATION_INSTRUCTION = (
    "The Chief owns requirements analysis, work allocation and result consolidation, and never authors program code. "
    "Start a simple program with one Software Developer who implements and integrates it. "
    "Judge complexity by feature dependencies, external API/database integration and verification difficulty, not file count. "
    "Add UI/frontend, backend, database or other specialists only for distinct necessary deliverables. "
    "Designers may implement assigned UI files. Each output file has exactly one owner. "
    "Set integration_role to exactly one assigned developer role, which integrates interfaces after the other owners finish. "
    "Never invent output files just to split work. QA is an independent fixed reviewer, never an author or integration owner. "
    "Do not assign implementation tasks to Chief or QA. Empty core_roles/specialists are allowed only for "
    "non-implementation requests the Chief can answer directly. "
)

def is_developer(name,role=''):
    text=f'{name or ""} {role or ""}'.casefold()
    return str(name or '').strip().casefold() in {'dev','developer','coder'} or any(term in text for term in
        ('software developer','developer','programmer','coder','coding agent','개발자','소프트웨어 개발','개발 담당'))

def fixed_role(value):
    text=str(value or '').strip().casefold();compact=re.sub(r'[^a-z0-9가-힣]','',text)
    if compact.startswith(('chiefofstaff','비서실장')) or compact in {'chief','chiefself'}:return 'chief'
    if re.search(r'\bqa\b',text) or any(token in compact for token in ('qualityassurance','품질보증','검증에이전트')):return 'qa'
    return None

def developer_key(value):
    """Preserve distinct component developers while normalizing generic aliases."""
    if not is_developer(value):return None
    text=str(value).casefold();compact=re.sub(r'[^a-z0-9가-힣]','',text)
    for key,tokens in (
        ('frontend',('frontend','프론트엔드','클라이언트')),
        ('backend',('backend','백엔드','서버')),
        ('database',('database','데이터베이스')),
        ('mobile',('mobile','모바일')),
        ('fullstack',('fullstack','풀스택')),
        ('integration',('integration','통합'))):
        if any(token in compact for token in tokens):return key+'_developer'
    if re.search(r'\bui\b',text):return 'frontend_developer'
    return 'software_developer'

def enforce_allocation(decision,request):
    """Idempotent policy applied before confirmation and again before execution."""
    from app.chief.domain_analysis import enrich_analysis
    decision = enrich_analysis(decision, request)
    if decision.workflow_type!='standard':return decision
    if is_file_creation_request(request):decision.file_creation_required=True
    if decision.work_domains and "개발" not in decision.work_domains:return decision
    if not decision.file_creation_required:return decision
    decision.direct_execution=False
    roles=decision.core_roles if isinstance(decision.core_roles,list) else [decision.core_roles]
    decision.core_roles=list(dict.fromkeys(role for role in roles if role and not fixed_role(role)))
    decision.specialists=[item for item in decision.specialists
        if not any(fixed_role(item.get(key,'')) for key in ('name','role','specialty'))]
    developers=[role for role in decision.core_roles if is_developer(role)]
    developers.extend(item.get('role') or item.get('name') or item.get('specialty') for item in decision.specialists
        if is_developer(item.get('name'),item.get('role') or item.get('specialty')))
    if not developers:decision.core_roles.append('Software Developer');developers=['Software Developer']
    selected=decision.integration_role
    if selected:
        matches=[role for role in developers if role.casefold()==str(selected).casefold()]
        if not matches:
            aliases=[item.get('role') or item.get('name') or item.get('specialty') for item in decision.specialists
                     if is_developer(item.get('name'),item.get('role') or item.get('specialty'))
                     and str(selected).casefold() in {str(item.get(key,'')).casefold() for key in ('name','role','specialty')}]
            matches=list(dict.fromkeys(aliases))
            if len(matches)!=1:matches=[]
        if not matches:raise ValueError('통합 담당자는 배정된 개발 역할 중 하나여야 합니다: '+str(selected))
        selected=matches[0]
    else:selected=developers[-1]
    decision.integration_role=selected
    assignments=[];adjusted=False
    for item in decision.task_assignments:
        kind=fixed_role(item.get('role'))
        if kind=='qa':
            if item.get('files'):raise ValueError('QA는 파일 작성 담당자로 배정할 수 없습니다.')
            continue
        if kind=='chief':
            if not item.get('files'):continue
            item=dict(item,role=selected);adjusted=True
        assignments.append(item)
    decision.task_assignments=assignments
    if adjusted:
        decision.rationale='비서실장은 분석·업무 분장·결과 취합을 담당하고 코드 작성은 개발 담당자에게 배정했습니다.'
        decision.work_steps=[step for step in decision.work_steps if not fixed_role(step.split(':',1)[0])]
    for capability in ('implementation','file_output'):
        if capability not in decision.required_capabilities:decision.required_capabilities.append(capability)
    step=f'개발 구현·통합 담당: {selected}. 다른 담당자의 산출물 이후 인터페이스를 통합하고 독립 QA로 검증합니다.'
    if step not in decision.work_steps:decision.work_steps.append(step)
    return decision
