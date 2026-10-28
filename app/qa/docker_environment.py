"""Read-only Docker diagnostics and discovery, including fresh Windows installs."""
import os
from pathlib import Path
import shutil
import subprocess


def resolve_docker():
    found=shutil.which('docker')
    if found:return found
    if os.name=='nt':
        roots=[Path(os.environ.get('ProgramFiles','C:/Program Files'))/'Docker'/'Docker']
        if os.environ.get('LOCALAPPDATA'):roots.append(Path(os.environ['LOCALAPPDATA'])/'Programs'/'DockerDesktop')
        for root in roots:
            candidate=root/'resources'/'bin'/'docker.exe'
            if candidate.is_file():return str(candidate)
    return None


def inspect_environment(settings=None,runtime='python',require_image=True):
    docker=resolve_docker();settings=settings or {}
    result={'status':'NOT_READY','docker':docker,'runtime':runtime}
    if not docker:return dict(result,code='DOCKER_MISSING',reason='Docker가 설치되지 않았습니다. Docker Desktop 설치와 최초 실행을 완료하세요.')
    flags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0
    try:
        engine=subprocess.run([docker,'info','--format','{{.OSType}}'],capture_output=True,text=True,
                              encoding='utf-8',errors='replace',timeout=10,creationflags=flags)
        if engine.returncode:return dict(result,code='ENGINE_UNAVAILABLE',reason='Docker 엔진에 연결할 수 없습니다. Docker Desktop과 WSL 상태를 확인하세요.')
        if engine.stdout.strip()!='linux':return dict(result,code='LINUX_ENGINE_REQUIRED',reason='Docker를 Linux 컨테이너 모드로 실행해야 합니다.')
        if not require_image:return dict(result,status='READY',code='ENGINE_READY',reason='Linux Docker 엔진 연결 완료')
        image=settings.get('images',{}).get(runtime)
        if not image:return dict(result,code='IMAGE_UNCONFIGURED',reason='실행 이미지가 연결되지 않았습니다. scripts/setup_docker_verification.py --build를 실행하세요.')
        probe=subprocess.run([docker,'image','inspect',image],capture_output=True,timeout=10,creationflags=flags)
        if probe.returncode:return dict(result,code='IMAGE_MISSING',reason='설정된 로컬 실행 이미지가 없습니다. 준비 스크립트로 다시 구성하세요.')
        return dict(result,status='READY',code='READY',image=image,reason='Docker 엔진과 로컬 실행 이미지 준비 완료')
    except (OSError,subprocess.TimeoutExpired) as exc:
        return dict(result,code='DOCKER_PROBE_FAILED',reason='Docker 상태 확인 실패: '+str(exc)[:300])
