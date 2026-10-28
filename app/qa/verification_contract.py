"""Versioned verification contracts; authority comes from the confirmation boundary."""
import hashlib
import json
import re
from copy import deepcopy
from app.orchestrator.work_contract import file_key

CATEGORIES = ('normal', 'invalid', 'boundary')


def case_category(case, check=None):
    return case.get('category', (check or {}).get('category', 'invalid' if case.get('error') else 'normal'))


def validate_contract(value):
    if not isinstance(value,dict) or value.get('version',1)!=1:
        raise ValueError('검증 계약은 version=1인 JSON 객체여야 합니다.')
    result=deepcopy(value);result['version']=1
    requirements=result.setdefault('requirements',[]);checks=result.setdefault('checks',[])
    if not isinstance(requirements,list) or not isinstance(checks,list) or len(checks)>64 or len(requirements)>64:
        raise ValueError('검증 항목 형식 또는 최대 개수(64)가 올바르지 않습니다.')
    ids=set();requirement_ids=set()
    for requirement in requirements:
        if not isinstance(requirement,dict) or not isinstance(requirement.get('id'),str) or not isinstance(requirement.get('description'),str):
            raise ValueError('요구사항에는 id와 description이 필요합니다.')
        if not requirement['id'].strip() or not requirement['description'].strip():raise ValueError('요구사항 ID와 설명은 비어 있을 수 없습니다.')
        if requirement['id'] in requirement_ids:raise ValueError('요구사항 ID가 중복되었습니다.')
        categories=requirement.get('required_categories')
        if categories is not None and (not isinstance(categories,list) or not categories or
                not all(isinstance(item,str) and item in CATEGORIES for item in categories) or len(set(categories))!=len(categories)):
            raise ValueError('required_categories는 normal/invalid/boundary 중 중복 없는 목록이어야 합니다.')
        requirement_ids.add(requirement['id'])
    for check in checks:
        if not isinstance(check,dict) or not isinstance(check.get('id'),str) or not check['id'] or not isinstance(check.get('kind'),str):
            raise ValueError('검증 항목에는 고유 id와 kind가 필요합니다.')
        if check['id'] in ids:raise ValueError('검증 ID가 중복되었습니다.')
        ids.add(check['id'])
        if not isinstance(check.get('target'),str):raise ValueError('검증 대상 target이 필요합니다.')
        file_key(check['target'])
        check['target']=check['target'].strip().replace('\\','/');check.setdefault('required',True);check.setdefault('requirements',[])
        if not isinstance(check['required'],bool):raise ValueError('required는 boolean이어야 합니다.')
        if not isinstance(check.get('enabled',True),bool):raise ValueError('enabled는 boolean이어야 합니다.')
        if 'category' in check and check['category'] not in CATEGORIES:raise ValueError('검사 category가 올바르지 않습니다.')
        if not isinstance(check['requirements'],list) or not all(isinstance(item,str) and item in requirement_ids for item in check['requirements']):
            raise ValueError('검증 항목이 알 수 없는 요구사항을 참조합니다.')
        if check['kind'] in {'gui','image_output'}:
            outputs=check.get('outputs',[])
            if not isinstance(outputs,list) or len(outputs)>16:raise ValueError('이미지 출력은 최대 16개입니다.')
            if check['kind']=='image_output' and not outputs:raise ValueError('이미지 결과 기준이 필요합니다.')
            for output in outputs:
                file_key(output['path'])
                if not isinstance(output.get('format'),str) or not output['format']:raise ValueError('이미지 형식이 필요합니다.')
                size=output.get('size')
                if not isinstance(size,list) or len(size)!=2 or not all(type(n) is int and 0<n<=16000 for n in size):raise ValueError('이미지 크기가 올바르지 않습니다.')
                pixels=output.get('pixels',[])
                if not isinstance(pixels,list) or not 1<=len(pixels)<=256:raise ValueError('편집 결과 RGB 픽셀 기준이 필요합니다.')
                for pixel in pixels:
                    xy=pixel.get('xy');rgb=pixel.get('rgb')
                    if not isinstance(xy,list) or len(xy)!=2 or not all(type(n) is int and 0<=n<size[i] for i,n in enumerate(xy)):raise ValueError('픽셀 좌표 오류')
                    if not isinstance(rgb,list) or len(rgb)!=3 or not all(type(n) is int and 0<=n<=255 for n in rgb):raise ValueError('RGB 기준 오류')
            if check['kind']=='gui' and ('factory' in check or 'actions' in check):
                if not re.fullmatch(r'[A-Za-z_]\w*',check.get('factory','')):raise ValueError('GUI factory 이름이 필요합니다.')
                actions=check.get('actions')
                if not isinstance(actions,list) or not 1<=len(actions)<=64:raise ValueError('GUI actions가 필요합니다.')
                for action in actions:
                    if action.get('action') not in {'click','input','open_file','save_file'} or not isinstance(action.get('widget'),str):raise ValueError('GUI 동작 오류')
                    if action['action']=='input' and (not isinstance(action.get('text'),str) or len(action['text'])>1024 or not action['text'].isascii()):raise ValueError('현재 입력 이벤트는 ASCII 1024자까지 지원합니다.')
                    if action['action'] in {'open_file','save_file'}:
                        if not isinstance(action.get('path'),str):raise ValueError('파일 선택 경로가 필요합니다.')
                        if action['path']:file_key(action['path'])
                widgets=check.get('widgets',[])
                if not isinstance(widgets,list) or len(widgets)>64:raise ValueError('GUI widget 기준 오류')
                for widget in widgets:
                    if not isinstance(widget.get('widget'),str) or 'expected' not in widget or not isinstance(widget.get('property','text'),str):raise ValueError('위젯 예상 결과가 필요합니다.')
                if not widgets and not outputs:raise ValueError('동작 후 위젯 또는 출력 결과 기준이 필요합니다.')
        if check['kind']=='python_function':
            if not isinstance(check.get('function'),str) or not re.fullmatch(r'[A-Za-z_]\w*',check['function']):raise ValueError('함수 이름이 올바르지 않습니다.')
            if not isinstance(check.get('cases'),list) or not 1<=len(check['cases'])<=32:raise ValueError('함수 검증에는 1~32개 cases가 필요합니다.')
            for case in check['cases']:
                if not isinstance(case,dict) or not isinstance(case.get('args',[]),list):raise ValueError('함수 인자 형식이 잘못되었습니다.')
                if 'category' in case and case['category'] not in CATEGORIES:raise ValueError('사례 category가 올바르지 않습니다.')
                if not isinstance(case.get('enabled',True),bool):raise ValueError('사례 enabled는 boolean이어야 합니다.')
                if 'error' in case and not isinstance(case['error'],bool):raise ValueError('error는 boolean이어야 합니다.')
                if ('expected' in case)==bool(case.get('error')):raise ValueError('expected 또는 error 하나가 필요합니다.')
                if 'error_type' in case and (not case.get('error') or not isinstance(case['error_type'],str)
                    or not re.fullmatch(r'[A-Za-z_]\w*',case['error_type'])):raise ValueError('error_type은 error=true와 예외 클래스 이름으로 지정하세요.')
        elif check['kind']=='text':
            if 'equals' in check and not isinstance(check['equals'],str):raise ValueError('equals는 문자열이어야 합니다.')
            if 'equals' not in check and not (check.get('contains') or check.get('not_contains')):raise ValueError('비어 있지 않은 텍스트 검증 조건이 필요합니다.')
            for key in ('contains','not_contains'):
                if key in check and (not isinstance(check[key],list) or not all(isinstance(item,str) for item in check[key])):raise ValueError('포함 조건은 문자열 목록이어야 합니다.')
        elif check['kind']=='json_value':
            if 'expected' not in check or not isinstance(check.get('path',[]),list):raise ValueError('JSON 경로와 예상값이 필요합니다.')
            if not all(type(key) in (str,int) for key in check.get('path',[])):raise ValueError('JSON 경로는 문자열 또는 정수여야 합니다.')
        elif check['kind'] in {'command','image_output'}:
            if not isinstance(check.get('argv'),list) or not check['argv'] or not all(isinstance(arg,str) for arg in check['argv']):raise ValueError('실행 검증에는 argv가 필요합니다.')
            if 'exit_code' in check and type(check['exit_code']) is not int:raise ValueError('exit_code는 정수여야 합니다.')
            if 'stdout' in check and not isinstance(check['stdout'],str):raise ValueError('stdout은 문자열이어야 합니다.')
            if not isinstance(check.get('stdout_contains',[]),list) or not all(isinstance(item,str) for item in check.get('stdout_contains',[])):raise ValueError('stdout_contains는 문자열 목록이어야 합니다.')
        if 'timeout' in check and (type(check['timeout']) not in (int,float) or not 1<=check['timeout']<=60):raise ValueError('timeout은 1~60초여야 합니다.')
    if len(json.dumps(result,ensure_ascii=False,allow_nan=False))>100_000:raise ValueError('검증 계약이 너무 큽니다.')
    return result


def explicit_contract(request):
    matches=re.findall(r'^\[검증 계약\]\s*\n(.*?)\n\[/검증 계약\]\s*$',request or '',re.S|re.M)
    if len(matches)>1:raise ValueError('검증 계약 블록은 하나만 지정하세요.')
    return validate_contract(json.loads(matches[0])) if matches else None


def build_contract(request,supplied=None,proposed=None,confirmed=False):
    user=explicit_contract(request)
    if supplied is not None:
        supplied=validate_contract(supplied)
        if user is not None and {k:v for k,v in user.items() if k!='source'}!={k:v for k,v in supplied.items() if k!='source'}:raise ValueError('입력문과 별도 검증 계약이 다릅니다.')
        user=supplied
    if user is not None:contract=user;source='USER'
    elif proposed:contract=validate_contract(proposed);source='REPRESENTATIVE_CONFIRMED' if confirmed else 'PROPOSED'
    else:
        from app.qa.functional_cases import cases_from_request
        from app.tools.project_file_tool import requested_output_paths
        cases=cases_from_request(request)
        targets=[name for name in requested_output_paths(request) if name.casefold().endswith('.py')]
        checks=[]
        if len(targets)==1:
            for function in dict.fromkeys(case['function'] for case in cases):
                checks.append({'id':'literal:'+function,'kind':'python_function','target':targets[0],
                    'function':function,'required':True,'requirements':[],
                    'cases':[{key:value for key,value in case.items() if key!='function'} for case in cases if case['function']==function]})
        contract={'version':1,'requirements':[],'checks':checks};source='LEGACY_EXPLICIT' if checks else 'UNSPECIFIED'
    contract['source']=source
    if source in {'PROPOSED','REPRESENTATIVE_CONFIRMED'}:contract['coverage_policy']='requirements'
    contract['expectation_origin']='MODEL' if source in {'PROPOSED','REPRESENTATIVE_CONFIRMED'} else 'USER' if source in {'USER','LEGACY_EXPLICIT'} else 'UNSPECIFIED'
    # Authority is assigned here, never accepted from model-authored nested fields.
    for check in contract.get('checks',[]):
        check['expectation_source']=source
        for case in check.get('cases',[]):case['expectation_source']=source
    return contract


def fingerprint(contract):
    return hashlib.sha256(json.dumps(contract,sort_keys=True,ensure_ascii=False).encode()).hexdigest()


def scope_contract(contract,paths):
    result=deepcopy(contract);keys={file_key(path) for path in paths}
    matching=[check for check in contract.get('checks',[]) if file_key(check['target']) in keys]
    runtime_kinds={'command','gui','image_output','visual_review'}
    result['checks']=[check for check in matching if check['kind'] not in runtime_kinds and check.get('engine')!='docker']
    deferred={check['id']:{key:check[key] for key in ('id','kind','target','required','requirements') if key in check}
              for check in matching if check['kind'] in runtime_kinds or check.get('engine')=='docker'}
    for check in contract.get('deferred_checks',[]):
        if isinstance(check,dict) and isinstance(check.get('target'),str) and file_key(check['target']) in keys:
            deferred.setdefault(check['id'],deepcopy(check))
    result['deferred_checks']=list(deferred.values())
    result['requirements']=[]
    return result


def blocking_failure(records):
    return any(row['status']=='FAIL' and row.get('required',True) for row in records)


def required_unverified(records):
    return any(row.get('status') not in {'PASS','FAIL'} and row.get('required',True) for row in records)
