"""Explicit task deliverables and file ownership shared by workers and QA."""
import json
import re
from pathlib import PurePosixPath, PureWindowsPath


def file_key(path):
    path = str(path).strip().replace('\\', '/')
    if (not path or PureWindowsPath(path).drive or PurePosixPath(path).is_absolute()
            or '..' in PurePosixPath(path).parts or any(c in path for c in '\x00\n\r<>')):
        raise ValueError(f'유효하지 않은 산출 파일 경로: {path!r}')
    return PurePosixPath(path).as_posix().casefold()


def attach_contract(description, owner, files, deliverable, acceptance, tests=None,verification=None):
    contract = {'owner': owner, 'files': files, 'deliverable': deliverable,
                'acceptance': acceptance, 'tests': tests or [], 'verification':verification or {}}
    return (description + '\n\n[작업 계약]\n'
            + json.dumps(contract, ensure_ascii=False) + '\n[/작업 계약]')


def read_contract(description):
    matches = re.findall(r'^\[작업 계약\]\s*\n(.*?)\n\[/작업 계약\]\s*$', description or '', re.S | re.M)
    if not matches:
        return None
    try:
        data = json.loads(matches[-1])
        return data if isinstance(data, dict) else None
    except (ValueError, TypeError):
        return None
