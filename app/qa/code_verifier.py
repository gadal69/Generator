"""Evidence-based verification for generated code; execution is opt-in and sandbox-aware."""
from __future__ import annotations

import ast
from datetime import datetime
from pathlib import Path
import uuid
import json
from copy import deepcopy
from app.qa.function_verifier import FunctionVerifier
from app.qa.functional_cases import verification_cases_from_request
from app.qa.project_verifier import ProjectVerifier
from app.qa.verification_contract import build_contract


class CodeVerifier:
    def __init__(self, database):
        self.database = database

    def verify_contract(self,project_id,artifacts,contract,settings=None,control=None,partial=False):
        records=[]
        for row in ProjectVerifier(settings,control).verify(artifacts,contract,partial):
            kind='FUNCTIONAL_TEST' if row['type']=='python_function' else row['type'].upper()
            record=self._record(project_id,row['path'],kind,row['status'],json.dumps(row,ensure_ascii=False))
            record.update(required=row['required'],check_id=row['check_id']);records.append(record)
        return records

    def verify_artifacts(self, project_id, artifacts, cases, task_id=None, result_id=None):
        records=[]
        python_files=[item for item in artifacts if Path(item['path']).suffix.casefold()=='.py']
        for item in python_files:
            applicable=[case for case in cases if not case.get('file') or case['file']==item['path']]
            if len(python_files)>1 and any(not case.get('file') for case in applicable):
                result={'status':'NOT_RUN','reason':'Multiple files require explicit test-to-file ownership.','cases':[]}
            else:
                result=FunctionVerifier().verify(item['content'],applicable)
            result.update(task_id=task_id,result_id=result_id)
            records.append(self._record(project_id,item['path'],'FUNCTIONAL_TEST',result['status'],
                                        json.dumps(result,ensure_ascii=False)))
        return records

    def verify_files(self, project_id, files, request='',contract=None,settings=None,control=None):
        results = []
        for item in files:
            path = Path(item["path"])
            if path.suffix.casefold() != ".py":
                if path.suffix.casefold() in {'.js','.ts','.jsx','.tsx','.html','.css','.sh','.bat','.ps1','.sql'}:
                    results.append(self._record(project_id,path.name,'FUNCTIONAL_TEST','NOT_RUN',
                        'No trusted language-specific execution/test runner is configured.'))
                continue
            try:
                ast.parse(path.read_text(encoding="utf-8-sig"), filename=path.name)
                syntax_status, details = "PASS", "Python AST syntax validation passed."
            except (SyntaxError, UnicodeError, OSError) as exc:
                syntax_status, details = "FAIL", f"{type(exc).__name__}: {exc}"
            results.append(self._record(project_id, path.name, "PYTHON_SYNTAX", syntax_status, details))
            results.append(self._record(
                project_id, path.name, "EXECUTION", "NOT_RUN",
                "Unrestricted application/GUI execution was not performed. Explicit Docker checks are recorded separately with their own scope.",
            ))
            # The explicit contract governs required runtime checks; this legacy
            # unrestricted-execution placeholder is informational only.
            results[-1]['required']=False
        python_files=[item for item in files if Path(item['path']).suffix.casefold()=='.py']
        artifacts=[]
        for item in python_files:
            try:
                artifacts.append({'path':item.get('relative_path',Path(item['path']).name),
                                  'content':Path(item['path']).read_text(encoding='utf-8-sig')})
            except (OSError,UnicodeError):
                pass  # Already reported by the syntax check above.
        if contract is not None:
            # A final program cannot claim requirement completeness from a few
            # standalone examples. Partial checks keep their original scope.
            contract=deepcopy(contract)
            contract['coverage_policy']='requirements'
            all_artifacts=[]
            for item in files:
                try:all_artifacts.append({'path':item.get('relative_path',Path(item['path']).name),'content':Path(item['path']).read_text(encoding='utf-8-sig')})
                except (OSError,UnicodeError):continue
            results.extend(self.verify_contract(project_id,all_artifacts,contract,settings,control))
        else:results.extend(self.verify_artifacts(project_id,artifacts,verification_cases_from_request(request)))
        return results

    def _record(self, project_id, relative_path, kind, status, details):
        verification_id = "VER-" + uuid.uuid4().hex[:8].upper()
        with self.database.connection() as conn:
            conn.execute("""INSERT INTO code_verifications
                (verification_id,project_id,relative_path,verification_type,status,details,created_at)
                VALUES (?,?,?,?,?,?,?)""", (verification_id,project_id,relative_path,kind,status,details,datetime.now().isoformat()))
        return {"verification_id": verification_id, "path": relative_path,
                "type": kind, "status": status, "details": details}
