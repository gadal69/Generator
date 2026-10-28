"""Generic verification registry operating on immutable project snapshots."""
import json
from copy import deepcopy
from app.orchestrator.work_contract import file_key
from app.qa.function_verifier import FunctionVerifier
from app.qa.isolated_runner import IsolatedRunner
from app.qa.pure_python_worker import values_equal
from app.qa.verification_contract import CATEGORIES, case_category


def aggregate(statuses):
    statuses=list(statuses)
    return 'FAIL' if 'FAIL' in statuses else 'PASS' if statuses and all(s=='PASS' for s in statuses) else 'NOT_RUN'


class ProjectVerifier:
    def __init__(self,settings=None,control=None):
        self.settings=settings or {};self.control=control

    def verify(self,artifacts,contract,partial=False):
        self.artifacts=deepcopy(artifacts)
        files={file_key(item['path']):item['content'] for item in artifacts}
        if len(files)!=len(artifacts):raise ValueError('중복된 프로젝트 파일 경로입니다.')
        rows=[];trusted=contract.get('source') in {'USER','REPRESENTATIVE_CONFIRMED','LEGACY_EXPLICIT'}
        dependencies=self.settings.get('project_dependencies',{})
        if dependencies and dependencies.get('status')!='READY':
            rows.append({'check_id':'environment:dependencies','path':'<project>','type':'DEPENDENCIES',
                         'required':True,'requirements':[],'status':'NOT_RUN',
                         'reason':dependencies.get('reason','프로젝트 패키지 환경 미준비'),
                         'remedy':dependencies.get('remedy','')})
        for check in contract.get('checks',[]):
            if self.control:self.control.emit('verification',check_id=check['id'])
            if partial and file_key(check['target']) not in files:continue
            row={'check_id':check['id'],'path':check['target'],'type':check['kind'],
                 'required':check.get('required',True),'requirements':check.get('requirements',[]),
                 'expectation_source':contract.get('source','UNSPECIFIED'),
                 'expectation_origin':contract.get('expectation_origin','UNSPECIFIED'),
                 'category':check.get('category','normal')}
            if not trusted:result={'status':'NOT_RUN','reason':'검증 계약이 대표 확인 전 제안 상태입니다.'}
            elif not check.get('enabled',True):result={'status':'NOT_RUN','reason':str(check.get('skip_reason') or '검사가 비활성화되어 실행하지 않았습니다.')}
            elif file_key(check['target']) not in files:result={'status':'FAIL','reason':'검증 대상 파일이 없습니다.'}
            else:
                try:result=self._function_cases(files,check) if check['kind']=='python_function' else self._check(files,check)
                except (ValueError,KeyError,TypeError,IndexError,UnicodeError) as exc:
                    result={'status':'FAIL','reason':type(exc).__name__+': '+str(exc)[:500]}
            row.update(result);rows.append(row)
        if partial:
            for check in contract.get('deferred_checks',[]):
                if file_key(check['target']) in files:
                    rows.append({'check_id':check['id'],'path':check['target'],'type':check['kind'],
                        'required':check.get('required',True),'requirements':check.get('requirements',[]),
                        'status':'NOT_RUN','scope':'INTEGRATION_DEFERRED',
                        'reason':'전체 파일 묶음이 필요한 실행 검사는 통합 단계에서 수행합니다. 현재 파일 QA의 실행 통과로 처리하지 않습니다.'})
        if not partial:
            check_rows=list(rows)
            strict=contract.get('coverage_policy')=='requirements'
            if strict:
                if not contract.get('requirements'):
                    rows.append({'check_id':'traceability:requirements','path':'<project>','type':'COVERAGE',
                                 'required':True,'status':'NOT_RUN','reason':'분석된 요구사항 목록이 없어 검증 누락을 확인할 수 없습니다.'})
                for row in check_rows:
                    if row.get('required') and row['type']!='DEPENDENCIES' and not row.get('requirements'):
                        rows.append({'check_id':'traceability:'+row['check_id'],'path':row['path'],'type':'COVERAGE',
                                     'required':True,'status':'NOT_RUN','reason':'필수 검사와 요구사항의 연결이 누락되었습니다.'})
            for requirement in contract.get('requirements',[]):
                coverage=[row for row in check_rows if requirement['id'] in row['requirements'] and row['required']]
                linked=[check for check in contract.get('checks',[]) if requirement['id'] in check.get('requirements',[]) and check.get('required',True)]
                behavioral=strict or any(check['kind'] in {'command','python_function','gui','image_output'} for check in linked)
                categories=requirement.get('required_categories',list(CATEGORIES) if behavioral else ['normal'])
                observations={category:[] for category in CATEGORIES}
                for check,row in zip(linked,coverage):
                    if check['kind']=='python_function':
                        cases=row.get('cases',[])
                        for index,case in enumerate(check['cases']):
                            status=cases[index]['status'] if index<len(cases) else row['status']
                            observations[case_category(case,check)].append(status)
                    else:observations[check.get('category','normal')].append(row['status'])
                category_results={category:aggregate(observations[category]) for category in categories}
                status=aggregate([row['status'] for row in coverage]+list(category_results.values()))
                rows.append({'check_id':'coverage:'+requirement['id'],'path':'<project>','type':'COVERAGE',
                             'required':True,'status':status,'reason':requirement['description'],
                             'categories':category_results,'missing_categories':[c for c in categories if not observations[c]],
                             'linked_checks':[row['check_id'] for row in coverage]})
            if not contract.get('checks'):
                rows.append({'check_id':'coverage:unspecified','path':'<project>','type':'COVERAGE',
                             'required':True,'status':'NOT_RUN','reason':'프로젝트별 합격 기준과 검증 항목이 지정되지 않았습니다.'})
        return rows

    def _function_cases(self,files,check):
        active=[case for case in check['cases'] if case.get('enabled',True)]
        runnable=dict(check,cases=active)
        result=self._check(files,runnable) if active else {'status':'NOT_RUN','reason':'모든 사례가 비활성화되었습니다.'}
        observed=iter(result.get('cases',[]));cases=[]
        for case in check['cases']:
            if not case.get('enabled',True):
                outcome={'status':'NOT_RUN','reason':str(case.get('skip_reason') or '사례 실행 생략')}
            else:outcome=next(observed,{'status':result['status'] if result['status']!='PASS' else 'NOT_RUN',
                                        'reason':result.get('reason','사례 실행 결과가 없습니다.')})
            outcome=dict(outcome,case=dict(case,function=check['function']),category=case_category(case,check))
            cases.append(outcome)
        result.update(cases=cases,status=aggregate([row['status'] for row in cases]))
        return result

    def _check(self,files,check):
        kind=check['kind'];content=files[file_key(check['target'])]
        if kind=='visual_review':return {'status':'NOT_RUN','scope':'SCREEN_REVIEW','reason':'실제 화면 검토 증거가 등록되지 않았습니다. 자동 실행 통과로 대체하지 않습니다.'}
        if kind in {'gui','image_output'}:
            if kind=='gui' and not check.get('factory'):
                return {'status':'NOT_RUN','scope':'DOCKER_GUI_EVENTS','reason':'GUI factory·위젯 동작·예상 결과가 지정되지 않았습니다.'}
            observed=IsolatedRunner(self.settings,self.control).run(self.artifacts,check)
            if observed['status']!='OBSERVED':return observed
            data=observed['observation']
            if not isinstance(data,dict):return {'status':'FAIL','reason':'실행 관측 형식 오류'}
            if data.get('unavailable'):return {'status':'NOT_RUN','reason':data['unavailable'],'observation':data}
            passed=not data.get('error') and not data.get('timeout')
            if kind=='image_output':passed=passed and data.get('exit_code')==0
            else:
                passed=passed and len(data.get('steps',[]))==len(check['actions'])
                passed=passed and all(values_equal(data.get('widgets',{}).get(s['widget']),s['expected']) for s in check.get('widgets',[]))
            actual=data.get('images',[])
            passed=passed and len(actual)==len(check.get('outputs',[]))
            for spec,row in zip(check.get('outputs',[]),actual):
                passed=passed and row.get('exists') is True and not row.get('error') and row.get('format')==spec['format'] and row.get('size')==spec['size'] and row.get('pixels')==[p['rgb'] for p in spec['pixels']]
            return {'status':'PASS' if passed else 'FAIL','scope':'DOCKER_GUI_EVENTS' if kind=='gui' else 'DOCKER_IMAGE_DECODE','observation':data,
                    'reason':'위젯 상태와 디코딩한 이미지 결과를 대표 검증 기준과 비교했습니다. 화면 외관·네이티브 대화상자는 별도 미검증입니다.',
                    'screen_review':'NOT_RUN','native_dialogs':'NOT_RUN' if kind=='gui' else 'NOT_APPLICABLE'}
        if kind=='file':return {'status':'PASS','reason':'요청한 파일이 존재합니다.'}
        if kind=='text':
            passed=(all(value in content for value in check.get('contains',[]))
                    and all(value not in content for value in check.get('not_contains',[]))
                    and ('equals' not in check or content==check['equals']))
            return {'status':'PASS' if passed else 'FAIL','reason':'텍스트 계약 비교 결과'}
        if kind=='json_value':
            value=json.loads(content)
            for key in check.get('path',[]):value=value[key]
            return {'status':'PASS' if values_equal(value,check['expected']) else 'FAIL',
                    'actual':value,'expected':check['expected']}
        if kind=='python_function' and check.get('engine','ast')=='ast':
            cases=[dict(case,function=check['function']) for case in check['cases']]
            return FunctionVerifier().verify(content,cases)
        if kind=='command' or (kind=='python_function' and check.get('engine')=='docker'):
            observed=IsolatedRunner(self.settings,self.control).run(
                self.artifacts,check)
            if observed['status']!='OBSERVED':return observed
            data=observed['observation']
            if not isinstance(data,dict):return {'status':'FAIL','reason':'격리 실행 결과는 객체여야 합니다.'}
            if kind=='command':
                passed=(not data.get('timeout') and data.get('exit_code')==check.get('exit_code',0)
                        and ('stdout' not in check or data.get('stdout','').strip()==check['stdout'].strip())
                        and all(value in data.get('stdout','') for value in check.get('stdout_contains',[])))
                return {'status':'PASS' if passed else 'FAIL','observation':data,'scope':'DOCKER_PROCESS'}
            values=data.get('cases',[])
            if not isinstance(values,list) or len(values)!=len(check['cases']) or not all(isinstance(value,dict) for value in values):return {'status':'FAIL','reason':'격리 함수 결과 형식 또는 개수가 다릅니다.'}
            rows=[]
            for case,value in zip(check['cases'],values):
                if case.get('error'):
                    exception=value.get('exception')
                    passed=isinstance(exception,str) and bool(exception) and (not case.get('error_type') or exception==case['error_type'])
                else:passed='value' in value and values_equal(value['value'],case['expected'])
                rows.append({'case':case,'status':'PASS' if passed else 'FAIL','observation':value})
            return {'status':'PASS' if all(row['status']=='PASS' for row in rows) else 'FAIL','cases':rows,'scope':'DOCKER_FUNCTION'}
        return {'status':'NOT_RUN','reason':'지원되지 않거나 설정되지 않은 검증 종류/실행기입니다: '+kind}
