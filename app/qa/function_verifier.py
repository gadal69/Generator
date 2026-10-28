"""Run trusted, bounded interpretation of user-specified pure-function examples."""
import ast
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile


class FunctionVerifier:
    def __init__(self, timeout=8):
        self.timeout = timeout

    def verify(self, source, cases):
        if not cases:
            return {'status':'NOT_RUN','reason':'No explicit user input/expected-result examples were supplied.','cases':[]}
        if len(source)>200_000 or len(cases)>32:
            return {'status':'NOT_RUN','reason':'Verification input exceeds the supported size.','cases':[]}
        try:
            ast.parse(source)
        except (SyntaxError,ValueError,RecursionError) as exc:
            return {'status':'FAIL','reason':'Invalid Python source: '+str(exc),'cases':[]}
        payload=json.dumps({'source':source,'cases':cases},ensure_ascii=False)
        if len(payload)>240_000:
            return {'status':'NOT_RUN','reason':'Verification input exceeds the supported size.','cases':[]}
        worker=Path(__file__).with_name('pure_python_worker.py').resolve()
        try:
            with tempfile.TemporaryDirectory(prefix='itni_function_check_') as directory:
                completed=subprocess.run(
                    [sys.executable,'-I','-S','-X','utf8',str(worker)],input=payload,
                    text=True,encoding='utf-8',capture_output=True,timeout=self.timeout,
                    cwd=directory,creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0,
                )
            result=json.loads(completed.stdout)
            rows=result.get('cases',[])
            if completed.returncode or 'error' in result or len(rows)!=len(cases):
                return {'status':'NOT_RUN','reason':'Trusted verifier could not finish: '+str(result.get('error',completed.stderr))[:500],'cases':[]}
            statuses={row['status'] for row in rows}
            status='FAIL' if 'FAIL' in statuses else 'NOT_RUN' if 'NOT_RUN' in statuses else 'PASS'
            return {'status':status,'scope':'PURE_FUNCTION_AST_INTERPRETATION',
                    'reason':'User examples checked with a bounded interpreter; application startup and GUI are not executed.',
                    'cases':rows}
        except subprocess.TimeoutExpired:
            return {'status':'NOT_RUN','reason':'Trusted verifier did not finish within its wall-clock limit. Host load or verifier execution must be checked before concluding the generated function is wrong.','cases':[]}
        except (OSError,ValueError,KeyError) as exc:
            return {'status':'NOT_RUN','reason':'Verifier unavailable: '+type(exc).__name__+': '+str(exc)[:300],'cases':[]}
