"""Deterministic artifact checks shared by rule and model QA."""
import ast
import re
from pathlib import PurePosixPath
from app.orchestrator.work_contract import file_key


def validate_artifacts(artifacts, expected, request):
    actual = []
    for artifact in artifacts:
        try:
            key = file_key(artifact['path'])
        except ValueError as exc:
            return str(exc)
        if key in actual:
            return f'동일 파일이 중복 제출되었습니다: {key}'
        actual.append(key)
        content = artifact.get('content', '')
        if not content.strip():
            return f'빈 파일입니다: {key}'
        if PurePosixPath(key).suffix == '.py':
            try:
                tree = ast.parse(content, filename=key)
            except SyntaxError as exc:
                return f'{key} 문법 오류: {exc.msg} (줄 {exc.lineno})'
            if any(isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant)
                   and node.value.value is Ellipsis for node in ast.walk(tree)):
                return f'{key}: 생략된 구현(Ellipsis)이 있습니다.'
            # Enforce explicitly requested bans, without imposing them on unrelated requests.
            prohibition = re.search(r'(?:eval|exec).{0,100}(?:금지|사용하지|쓰지|사용\s*없이)|(?:without|do not use|no)\s+(?:eval|exec)', request, re.I | re.S)
            if prohibition:
                for node in ast.walk(tree):
                    if isinstance(node, ast.Name) and node.id in {'eval', 'exec'}:
                        return f'{key}: 요구사항에서 금지한 {node.id} 사용 (줄 {node.lineno})'
                    if isinstance(node, ast.Attribute) and node.attr in {'eval', 'exec'}:
                        return f'{key}: 요구사항에서 금지한 {node.attr} 사용 (줄 {node.lineno})'
                    if isinstance(node, ast.ImportFrom) and node.module == 'builtins' and any(alias.name in {'eval','exec'} for alias in node.names):
                        return f'{key}: 요구사항에서 금지한 eval/exec import'
    expected_keys = [file_key(path) for path in expected]
    if expected and set(actual) != set(expected_keys):
        return f'배정 파일과 제출 파일이 다릅니다. 배정={expected_keys}, 제출={actual}'
    return ''
