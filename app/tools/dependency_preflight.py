"""Read-only dependency and credential checks shown before a task is executed."""
from __future__ import annotations

import importlib.util
from importlib.metadata import PackageNotFoundError, version
import json
import os
from pathlib import Path
import re
import sys


def _installed(module_name):
    try:
        return importlib.util.find_spec(module_name) is not None
    except (ImportError, ModuleNotFoundError, ValueError):
        return False


def _gemini_sdk_ready():
    if not _installed("google.genai"):
        return False
    try:
        parts = tuple(int(part) for part in re.findall(r"\d+", version("google-genai"))[:3])
        numbers = (parts + (0, 0, 0))[:3]
        return numbers >= (2, 0, 0)
    except (PackageNotFoundError, ValueError):
        return False


def normalize_libraries(items):
    """Accept package identifiers only; model-supplied shell commands are never used."""
    result, seen = [], set()
    if not isinstance(items, list):
        return result
    for item in items[:30]:
        if not isinstance(item, dict):
            continue
        package, runtime = item.get('package'), item.get('runtime', 'python')
        if not isinstance(package, str) or runtime not in ('python', 'node'):
            continue
        pattern = r'[A-Za-z0-9][A-Za-z0-9._-]{0,99}' if runtime == 'python' else r'(?:@[a-z0-9._-]+/)?[a-z0-9][a-z0-9._-]{0,99}'
        if not re.fullmatch(pattern, package):
            continue
        if runtime == 'python' and package in sys.stdlib_module_names:
            continue
        requested_version = item.get('version') or ''
        if not isinstance(requested_version, str) or (requested_version and not re.fullmatch(r'[0-9][A-Za-z0-9.!+_-]{0,79}', requested_version)):
            continue
        key = (runtime, re.sub(r'[-_.]+', '-', package.lower()))
        if key in seen:
            continue
        seen.add(key)
        result.append({'package': package, 'runtime': runtime, 'version': requested_version,
                       'purpose': str(item.get('purpose') or '생성 프로그램 실행에 필요')[:300]})
    return result


def requested_libraries(request, decision):
    declared = decision.get('library_requirements') or []
    text = (request or '').casefold()
    # A bounded fallback also works when the model omits dependency analysis.
    if not declared and any(word in text for word in ('이미지', '사진', 'image', 'photo')) and any(
            word in text for word in ('편집', '수정', '크기', '자르', '보정', 'edit', 'resize', 'crop')):
        declared = [{'package': 'Pillow', 'purpose': '이미지 편집용 기본 후보; 구현 방식에 따라 변경 가능', 'runtime': 'python'}]
    if not declared:
        formats = {'.pptx':'python-pptx', '.xlsx':'openpyxl', '.docx':'python-docx', '.pdf':'reportlab'}
        suffix = next((ext for ext in formats if re.search(re.escape(ext)+r'(?![a-z0-9_])', text)), '')
        if not suffix and any(word in text for word in ('파워포인트', '프레젠테이션')): suffix='.pptx'
        if not suffix and '엑셀' in text: suffix='.xlsx'
        if suffix: declared=[{'package':formats[suffix], 'purpose':'문서 파일 생성', 'runtime':'python'}]
    return normalize_libraries(declared)


def library_notices(request, decision):
    notices = []
    for item in requested_libraries(request, decision):
        package, runtime = item['package'], item['runtime']
        installed = False
        if runtime == 'python':
            try:
                installed_version = version(package)
                installed = not item['version'] or installed_version == item['version']
            except (PackageNotFoundError, ValueError):
                pass
        spec = package + (('==' if runtime == 'python' else '@') + item['version'] if item['version'] else '')
        command = f'python -m pip install {spec}' if runtime == 'python' else f'npm install {spec}'
        notices.append({'name': f"{package}: {item['purpose']}", 'package': package,
                        'installed': installed, 'blocking': False, 'install_command': command,
                        'when_needed': '현재 Python 환경의 설치 참고 정보. 실행 시 프로젝트별 Docker 환경에서 별도 설치·검사하며 호스트에는 자동 설치하지 않음. Node 등 미지원 런타임은 별도 이미지 필요'})
    return notices


def inspect_request(request, decision):
    requirements = []
    if decision.get("external_data_access") or decision.get("cloud_ai_required"):
        config_path = Path(__file__).resolve().parents[2] / "config" / "app_config.json"
        try:
            config = json.loads(config_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            config = {}
        search_enabled = bool(config.get("gemini", {}).get("external_search_enabled", True))
        requirements.extend([
            {
                "name": "Gemini Google Search 활성화",
                "package": "config/app_config.json",
                "installed": search_enabled,
                "install_command": 'config/app_config.json에서 "gemini.external_search_enabled"를 true로 설정',
                "when_needed": "외부 자료 수집 실행 전",
                "blocking": True,
            },
            {
                "name": "Gemini 웹 검색 SDK",
                "package": "google-genai",
                "installed": _gemini_sdk_ready(),
                "install_command": "python -m pip install --upgrade google-genai",
                "when_needed": "외부 자료 수집 실행 전",
                "blocking": True,
            },
            {
                "name": "Gemini API 키",
                "package": "GEMINI_API_KEY 환경변수",
                "installed": bool(os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")),
                "install_command": 'setx GEMINI_API_KEY "발급받은_API_키"  (등록 후 프로그램 재시작)',
                "setup_url": "https://aistudio.google.com/app/apikey",
                "when_needed": "외부 자료 수집 실행 전",
                "blocking": True,
            },
        ])
    if decision.get("cloud_ai_required") and not decision.get("external_data_access") and requirements:
        requirements[0]["installed"] = True
        requirements[0]["blocking"] = False
        requirements[0]["when_needed"] = "Only needed when Gemini Google Search is selected"
    text = (request or "").casefold()
    binary_formats = {
        ".pptx": ("PowerPoint 생성 라이브러리", "python-pptx", "pptx"),
        ".xlsx": ("Excel 생성 라이브러리", "openpyxl", "openpyxl"),
        ".docx": ("Word 생성 라이브러리", "python-docx", "docx"),
        ".pdf": ("PDF 생성 라이브러리", "reportlab", "reportlab"),
    }
    match = re.search(r"\.(pptx|xlsx|docx|pdf)(?![a-z0-9_])", text)
    suffix = "." + match.group(1) if match else ""
    if not suffix and any(word in text for word in ("파워포인트", "프레젠테이션")):
        suffix = ".pptx"
    elif not suffix and "엑셀" in text:
        suffix = ".xlsx"
    if suffix in binary_formats:
        label, package, module_name = binary_formats[suffix]
        requirements.append({
            "name": label,
            "package": package,
            "installed": _installed(module_name),
            "install_command": f"python -m pip install {package}",
            "when_needed": f"생성된 {suffix[1:].upper()} 스크립트 실행 전 (분석·코드 작성에는 불필요)",
            "blocking": False,
        })
    existing = {re.sub(r'[-_.]+', '-', item['package'].lower()) for item in requirements}
    requirements.extend(item for item in library_notices(request, decision)
                        if re.sub(r'[-_.]+', '-', item['package'].lower()) not in existing)
    return requirements


def inspect_verification_environment(contract,settings=None):
    """Explain missing runtime prerequisites without preventing code generation."""
    from app.qa.docker_environment import inspect_environment
    runtimes={check.get('runtime','python') for check in contract.get('checks',[])
              if (check.get('kind')=='command' or check.get('engine')=='docker')
              and isinstance(check.get('runtime','python'),str)}
    requirements=[]
    for runtime in sorted(runtimes):
        state=inspect_environment(settings,runtime)
        requirements.append({'name':'Docker 격리 실행 환경 ('+runtime+')','package':'docker',
            'installed':state['status']=='READY','blocking':False,'when_needed':state['reason'],
            'install_command':'.\\.venv\\Scripts\\python.exe -B scripts/setup_docker_verification.py --build',
            'setup_url':'https://docs.docker.com/desktop/setup/install/windows-install/'})
    return requirements
