"""Project-scoped, Docker-only dependency preparation. Never installs on the host."""
from copy import deepcopy
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import threading
import time
import uuid

from app.qa.docker_environment import inspect_environment
from app.qa.isolated_runner import IsolatedRunner
from app.runtime.run_control import RunCancelled
from app.tools.dependency_preflight import normalize_libraries


PROBE = '''import contextlib, importlib, importlib.metadata as m, io, json, pathlib, re, subprocess
packages = PACKAGES
checked = []
for item in packages:
    dist = m.distribution(item['package'])
    if item['version'] and dist.version != item['version']:
        raise RuntimeError('Requested version mismatch: ' + item['package'])
    modules = set((dist.read_text('top_level.txt') or '').split())
    if not modules:
        for f in dist.files or []:
            parts = pathlib.PurePosixPath(str(f)).parts
            if not parts: continue
            if len(parts) > 1 and parts[1] == '__init__.py': modules.add(parts[0])
            elif len(parts) == 1 and parts[0].endswith('.py'): modules.add(parts[0][:-3])
    modules = sorted(x for x in modules if re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', x) and x not in {'tests', 'test'})
    if not modules or len(modules) > 32: raise RuntimeError('Cannot establish import smoke check: ' + item['package'])
    with contextlib.redirect_stdout(io.StringIO()):
        for name in modules: importlib.import_module(name)
    checked.append({'package': item['package'], 'version': dist.version, 'imports': modules})
result = subprocess.run(['python', '-I', '-m', 'pip', 'check'], capture_output=True, text=True, timeout=15)
if result.returncode: raise RuntimeError(result.stdout + result.stderr)
resolved = sorted([{'package': d.metadata['Name'], 'version': d.version} for d in m.distributions()], key=lambda x: x['package'].lower())
print(json.dumps({'checked': checked, 'resolved': resolved, 'pip_check': 'PASS'}))
'''


def _hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def _command(argv, timeout, control=None):
    """Bound output and allow cooperative cancellation of Docker CLI operations."""
    flags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
    output = bytearray()
    process = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, creationflags=flags)
    def drain():
        while True:
            part = process.stdout.read(4096)
            if not part: break
            output.extend(part)
            if len(output) > 65536: del output[:-65536]
    reader = threading.Thread(target=drain, daemon=True)
    reader.start()
    start = time.monotonic()
    try:
        while process.poll() is None:
            if control: control.check()
            if time.monotonic() - start > timeout: raise TimeoutError('Docker 이미지 준비 시간 제한 초과')
            time.sleep(.1)
        reader.join(timeout=2)
        text = output.decode('utf-8', errors='replace')
        if process.returncode: raise RuntimeError(text[-6000:])
        return text
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)
        reader.join(timeout=2)
        process.stdout.close()


def _save_text(folder, name, content):
    path = folder / name
    temporary = folder / ('.dependencies-' + uuid.uuid4().hex + '.tmp')
    try:
        temporary.write_text(content, encoding='utf-8')
        os.replace(temporary, path)
    finally:
        if temporary.exists(): temporary.unlink()


def _save(folder, record):
    _save_text(folder, 'dependencies.json', json.dumps(record, ensure_ascii=False, indent=2) + '\n')


def smoke(image, packages, control=None):
    source = PROBE.replace('PACKAGES', repr(packages), 1)
    row = IsolatedRunner({'images': {'python': image}}, control).run(
        [{'path': 'dependencies_probe.py', 'content': source}],
        {'kind': 'command', 'target': 'dependencies_probe.py',
         'argv': ['python', 'dependencies_probe.py'], 'timeout': 60})
    observation = row.get('observation', {})
    if row.get('status') != 'OBSERVED' or observation.get('exit_code') != 0:
        raise RuntimeError('패키지 실행 확인 실패: ' + json.dumps(row, ensure_ascii=False)[-6000:])
    report = json.loads(observation['stdout'])
    if report.get('pip_check') != 'PASS' or len(report.get('checked', [])) != len(packages):
        raise RuntimeError('패키지 설치·실행 검사 결과가 불완전합니다.')
    return report


def prepare_project(workspace, project_id, libraries, settings=None, control=None):
    """Return only a smoke-tested immutable image, or a fail-closed verification setting."""
    if not re.fullmatch(r'PRJ-[A-Z0-9]{8}', project_id): raise ValueError('프로젝트 ID 오류')
    root = Path(workspace).resolve()
    folder = root / 'projects' / project_id / 'environment'
    if folder.resolve() != folder: raise ValueError('프로젝트 환경 경로의 링크/경로 이탈')
    folder.mkdir(parents=True, exist_ok=True)
    if any(path.is_symlink() for path in folder.iterdir()): raise ValueError('프로젝트 환경 파일의 심볼릭 링크를 허용하지 않습니다.')
    packages = normalize_libraries(libraries)
    result_settings = deepcopy(settings or {})
    record = {'version': 1, 'project_id': project_id, 'requested': packages,
              'status': 'PREPARING', 'checked_at': datetime.now().astimezone().isoformat()}
    old = None
    try:
        old = json.loads((folder / 'dependencies.json').read_text(encoding='utf-8'))
    except (OSError, ValueError): pass
    _save(folder, record)
    try:
        # Invalid entries must never silently disappear into the standard-library image.
        if not isinstance(libraries, list) or len(packages) != len(libraries):
            raise ValueError('패키지 이름·버전이 유효하지 않거나 중복됩니다. 분석의 의존성 목록을 수정하세요.')
        if not packages:
            _save_text(folder, 'requirements.requested.txt', '')
            _save_text(folder, 'requirements.lock.txt', '')
            record['status'] = 'NOT_NEEDED'
            _save(folder, record)
            return result_settings, record
        if any(p['runtime'] != 'python' for p in packages):
            raise ValueError('자동 패키지 준비는 Python만 지원합니다. 해당 런타임의 검토된 이미지 준비가 필요합니다.')
        if control: control.emit('dependency_preparation', message='프로젝트 Docker 패키지 설치·실행 확인 중')
        gui_packages=any(p['package'].lower() in {'pyside6','pyside6-essentials','pyside6-addons'} for p in packages)
        base_runtime='gui' if gui_packages and (settings or {}).get('images',{}).get('gui') else 'python'
        state = inspect_environment(settings, base_runtime)
        if state['status'] != 'READY': raise RuntimeError(state['reason'])
        docker, base = state['docker'], state['image']
        if not re.fullmatch(r'sha256:[a-f0-9]{64}', base):
            raise ValueError('고정 SHA256 기본 이미지가 필요합니다. 기본 Docker 준비를 먼저 실행하세요.')
        fingerprint = _hash({'packages': packages, 'base': base, 'probe': PROBE, 'policy': 1})
        record.update(fingerprint=fingerprint, base_image=base)
        image = None
        if old and old.get('status') == 'READY' and old.get('fingerprint') == fingerprint:
            candidate = old.get('image', '')
            if re.fullmatch(r'sha256:[a-f0-9]{64}', candidate) and inspect_environment({'images': {'python': candidate}})['status'] == 'READY':
                image = candidate
        if not image:
            specs = [p['package'] + ('==' + p['version'] if p['version'] else '') for p in packages]
            if old and old.get('status') == 'READY' and old.get('fingerprint') == fingerprint:
                locked = normalize_libraries([dict(p, runtime='python') for p in old.get('resolved', [])])
                if not locked or any(not p['version'] for p in locked) or len(locked) != len(old['resolved']):
                    raise ValueError('저장된 버전 잠금 목록을 확인하지 못했습니다. 의존성 재분석이 필요합니다.')
                specs = [p['package'] + '==' + p['version'] for p in locked]
            # The build context contains only trusted Dockerfile and validated requirements.
            # BuildKit treats a bare sha256 ID in FROM as a registry name. Give the
            # already-inspected local image a unique temporary tag instead.
            base_tag = 'itni-qa-base:' + uuid.uuid4().hex
            dockerfile = (f'FROM {base_tag}\nUSER 0:0\nCOPY requirements.txt /opt/itni-requirements.txt\n'
                'RUN python -I -m pip --isolated install --index-url https://pypi.org/simple '
                '--only-binary=:all: --no-cache-dir --disable-pip-version-check --retries 1 --timeout 30 '
                '-r /opt/itni-requirements.txt && python -I -m pip check\nUSER 65534:65534\n')
            with tempfile.TemporaryDirectory(prefix='itni_dependencies_') as temporary:
                context = Path(temporary)
                (context / 'requirements.txt').write_text('\n'.join(specs) + '\n', encoding='utf-8')
                (context / 'Dockerfile').write_text(dockerfile, encoding='utf-8')
                _save_text(folder, 'requirements.requested.txt', '\n'.join(p['package'] + ('==' + p['version'] if p['version'] else '') for p in packages) + '\n')
                # No generated program, credentials, Docker socket or host folders enter the build.
                try:
                    _command([docker, 'image', 'tag', base, base_tag], 15, control)
                    _command([docker, 'build', '--pull=false', '--progress=plain', '--iidfile', str(context / 'image.id'), str(context)], 600, control)
                    image = (context / 'image.id').read_text(encoding='utf-8').strip()
                finally:
                    try: _command([docker, 'image', 'rm', base_tag], 15)
                    except (OSError, RuntimeError, TimeoutError, subprocess.SubprocessError):
                        record['cleanup_warning'] = '임시 이미지 태그 정리 확인 필요: ' + base_tag
            if not re.fullmatch(r'sha256:[a-f0-9]{64}', image): raise RuntimeError('이미지 고정 ID 확인 실패')
        report = smoke(image, packages, control)
        resolved = normalize_libraries([dict(p, runtime='python') for p in report['resolved']])
        if len(resolved) != len(report['resolved']): raise RuntimeError('설치 버전 목록 형식 오류')
        _save_text(folder, 'requirements.lock.txt', '\n'.join(p['package'] + '==' + p['version'] for p in resolved) + '\n')
        record.update(status='READY', image=image, resolved=report['resolved'], smoke=report)
        _save(folder, record)
        result_settings.setdefault('images', {})['python'] = image
        if gui_packages:result_settings['images']['gui']=image
        result_settings['project_dependencies'] = {'status': 'READY', 'manifest': str(folder / 'dependencies.json')}
        if control: control.emit('dependency_ready', message='패키지 설치·실행 확인 통과')
        return result_settings, record
    except RunCancelled:
        record.update(status='CANCELLED', reason='패키지 환경 준비 중 사용자 중단')
        _save(folder, record)
        raise
    except (OSError, ValueError, RuntimeError, TimeoutError, subprocess.SubprocessError) as exc:
        _save_text(folder, 'requirements.lock.txt', '')
        record.update(status='NOT_READY', reason=str(exc)[-6000:],
                      remedy='Docker 엔진·인터넷 연결과 패키지 이름/버전·버전 충돌을 확인하세요. Linux/Python 3.11용 wheel이 없거나 OS 라이브러리가 필요하면 검토된 별도 이미지를 준비한 후 재시도하세요. 호스트 설치로 대체하지 않습니다.')
        _save(folder, record)
        result_settings['images'] = {}  # Never fall back to a shared image without the required packages.
        result_settings['project_dependencies'] = record
        if control: control.emit('dependency_failed', message=record['reason'] + '\n' + record['remedy'])
        return result_settings, record
