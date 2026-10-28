"""Bounded integration repair context with exact failed checks and sibling interfaces."""
import ast
import json
import re
from app.orchestrator.work_contract import file_key


def review_task_description(description):
    """Review the requirements and new output, without repeating the old code bundle."""
    match=re.match(r'원래 작업의 요구사항과 대상 파일을 유지해 다시 수행하세요\..*?\n\n원래 작업 지시:\n(.*?)(?=\n\nQA 검토 내용:)',description or '',re.S)
    if not match:return description
    original=match.group(1).strip()
    marker='[통합 수정 근거 — 대표 기준과 대상 파일 유지]\n'
    if marker in description:
        try:
            packet=json.loads(description.split(marker,1)[1])
            notes={'failed_check_ids':[check['id'] for check in packet.get('failed_checks',[])],
                   'sibling_interfaces':packet.get('sibling_interfaces',[]),
                   'instruction':'수정본과 원래 요구사항의 일치 여부를 평가하세요. 이전 코드 묶음은 새 코드로 오인하지 마세요.'}
            original+='\n\n[이전 통합 실패 범위]\n'+json.dumps(notes,ensure_ascii=False)
        except (ValueError,TypeError,KeyError):pass
    return original


def interfaces(content):
    try:tree=ast.parse(content)
    except (SyntaxError,ValueError):return ['구문 분석 불가; 다른 파일 내용을 추정하지 마세요.']
    result=[]
    def signature(node):
        suffix=' -> '+ast.unparse(node.returns) if node.returns else ''
        return node.name+'('+ast.unparse(node.args)+')'+suffix
    for node in tree.body:
        if isinstance(node,(ast.Import,ast.ImportFrom)):result.append(ast.unparse(node))
        elif isinstance(node,(ast.FunctionDef,ast.AsyncFunctionDef)):
            result.append('def '+signature(node))
        elif isinstance(node,ast.ClassDef):
            result.append('class '+node.name)
            for method in node.body:
                if isinstance(method,(ast.FunctionDef,ast.AsyncFunctionDef)) and (not method.name.startswith('_') or method.name=='__init__'):
                    result.append('  def '+signature(method))
        if len(result)>=64:break
    return result[:64]


def repair_context(artifacts,target,failures,contract):
    key=file_key(target)
    related=[row for row in failures if file_key(row['path'])==key]
    ids={row['check_id'] for row in related}
    checks=[check for check in contract.get('checks',[]) if check['id'] in ids]
    current=None;siblings=[]
    for item in artifacts:
        if file_key(item['path'])==key:
            content=item['content'];truncated=len(content)>10000
            current={'path':item['path'],'content':content if not truncated else content[:2000]+'\n[중간 내용 생략]\n'+content[-6000:],
                     'truncated':truncated,'interfaces':interfaces(content)}
        else:siblings.append({'path':item['path'],'interfaces':interfaces(item['content'])})
    observations=[]
    for row in related:
        observation=row.get('observation',{})
        observations.append({'check_id':row['check_id'],'status':row['status'],'reason':row.get('reason',''),
            'runtime_observation':json.dumps({k:observation[k] for k in ('error','widgets','images','steps','native_dialogs','screen_review') if k in observation},ensure_ascii=False)[:3000],
            'exit_code':observation.get('exit_code'),'stdout':str(observation.get('stdout',''))[:800],
            'stderr':str(observation.get('stderr',''))[-1800:]})
    return {'target':target,'failed_checks':checks,'observations':observations,
            'current_file':current,'sibling_interfaces':siblings,
            'instruction':'대표 요구사항과 위의 모든 실패 항목을 함께 수정하세요. 다른 파일의 인터페이스를 보존하고 배정 파일 하나만 전체 artifact로 제출하세요. 예제 main·임의 mock·추가 파일을 만들지 마세요. 생략 표시가 있는 현재 파일은 원래 요구사항에 맞게 완전히 재작성하세요.'}
