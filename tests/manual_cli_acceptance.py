"""Explicit manual execution of the reviewed sample, never an automatic Docker fallback."""
import ast
import argparse
from collections import Counter
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from app.qa.pure_python_worker import values_equal
REPORT=ROOT/'docs/final_acceptance_20261025/report.json'


def main(report_path):
    report=json.loads(report_path.read_text(encoding='utf-8'))
    path=ROOT/'projects'/report['project_id']/'artifacts'/'text_tools.py'
    source=path.read_text(encoding='utf-8-sig');tree=ast.parse(source)
    # Fail closed if the previously inspected sample gained other capabilities.
    allowed_names={'isinstance','TypeError','normalize_text','word_counts','main','input','print'}
    allowed_methods={'split','join','lower','get'}
    for node in ast.walk(tree):
        if isinstance(node,ast.ImportFrom):
            if node.module!='typing' or node.level or any(alias.name!='Dict' or alias.asname for alias in node.names):
                raise RuntimeError('Import needs a new manual review.')
        if isinstance(node,(ast.Import,ast.ClassDef,ast.Global,ast.Nonlocal,ast.While)):
            raise RuntimeError('Reviewed sample scope changed; manual review required.')
        if isinstance(node,ast.Call):
            if isinstance(node.func,ast.Name) and node.func.id in allowed_names:continue
            if isinstance(node.func,ast.Attribute) and node.func.attr in allowed_methods:continue
            raise RuntimeError('Unreviewed call; refusing manual execution.')
        if isinstance(node,ast.Attribute) and node.attr.startswith('_'):
            raise RuntimeError('Private attribute access; refusing manual execution.')
        if isinstance(node,ast.FunctionDef) and node.decorator_list:
            raise RuntimeError('Decorators need a new manual review.')
    inputs=['Hello hello WORLD','  A  a B  ','안녕 안녕 세상','','punctuation, punctuation.','CAFÉ café','one\u00a0two one']
    cases=[]
    with tempfile.TemporaryDirectory(prefix='itni_manual_cli_') as directory:
        for value in inputs:
            normalized=' '.join(value.split()).lower();expected_counts=dict(Counter(normalized.split()))
            completed=subprocess.run([sys.executable,'-I','-B','-X','utf8',str(path)],input=value+'\n',
                text=True,encoding='utf-8',capture_output=True,timeout=3,cwd=directory,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
            lines=completed.stdout.splitlines()
            try:actual_counts=ast.literal_eval(lines[1]) if len(lines)==2 else None
            except (ValueError,SyntaxError):actual_counts=None
            passed=(completed.returncode==0 and not completed.stderr and len(lines)==2
                    and lines[0]==normalized and values_equal(actual_counts,expected_counts))
            cases.append({'input':value,'status':'PASS' if passed else 'FAIL','exit_code':completed.returncode,
                          'stdout':completed.stdout,'stderr':completed.stderr,'expected_normalized':normalized,'expected_counts':expected_counts})
    report['manual_cli_execution']={'scope':'EXPLICIT_MANUAL_REVIEWED_SAMPLE_HOST_EXECUTION',
        'automatic_docker_result_unchanged':True,'source_sha256':hashlib.sha256(source.encode()).hexdigest(),
        'executed_at':datetime.now().astimezone().isoformat(),'cases':cases,
        'status':'PASS' if all(case['status']=='PASS' for case in cases) else 'FAIL'}
    for item in report.get('findings',[]):
        if item['id']=='F4':
            item['title']='Docker를 통한 자동 격리 실행 미검증'
            item['evidence']='자동 COMMAND/CLI COVERAGE는 NOT_RUN 유지. 별도로 전체 코드를 검토하고 수동 CLI 7개 입력의 실제 실행을 확인.'
    if report['manual_cli_execution']['status']=='FAIL':
        from app.orchestrator.orchestrator import Orchestrator
        from app.storage.database import Database
        o=Orchestrator(Database(report['database']))
        result=o.result_manager.get_result(report['result']['final_result_id'])
        if o.task_manager.get_task(result[1])[1]!=report['project_id']:raise RuntimeError('Project/result mismatch')
        o.qa_manager.review(report['project_id'],result[1],result[0],'AGT-QA-001','FAIL',0.0,
                            '별도 실제 CLI 인수 검사 실패',['MANUAL_CLI_ACCEPTANCE_FAIL'])
        report['approval_rows']=[{'approval_id':row[0],'status':row[5]} for row in o.approval_manager.get_by_task(result[1])]
        report['database_status']={'project_id':report['project_id'],'status':o.project_manager.get_project(report['project_id'])[3]}
    report['conclusion']='FUNCTION_AND_MANUAL_CLI_TESTS_COMPLETE; AUTOMATIC_DOCKER_NOT_RUN'
    report_path.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'manual_cli_status':report['manual_cli_execution']['status'],'cases':len(cases),'passed':sum(case['status']=='PASS' for case in cases)},ensure_ascii=False))
    if report['manual_cli_execution']['status']!='PASS':raise SystemExit(1)


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--report',type=Path,required=True)
    main(parser.parse_args().report)
