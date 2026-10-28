# Docker 자동 실행 검증 — 2026-10-27

## 현재 상태

후속 기능: [프로젝트별 패키지·버전 및 Docker 환경 준비](PROJECT_DEPENDENCY_ENVIRONMENTS.md)를 추가했다. 선언된 Python 외부 패키지는 프로젝트 이미지에 설치·검사하며, 다른 런타임은 별도 환경이 필요하다.

2026-10-27 사용자 재설치 후 Linux Docker 엔진 연결과 실제 컨테이너 실행을 확인했다. 검증 이미지를 빌드하고 격리 검사 및 함수 검사에 통과한 고정 SHA256 이미지 ID를 `config/app_config.json`에 연결했다.

읽기 전용 루트, UID 65534, 외부 네트워크 경로 없음, 임시 공간 쓰기와 실제 함수 실행이 모두 PASS다. 기존 샘플 `PRJ-DFD03CDA`의 함수 8개 사례와 Docker CLI 실행 및 요구사항 커버리지도 모두 PASS다. 함수 사례는 제한된 AST 검사기에서, CLI 실행은 실제 Docker 프로세스에서 검사했다. 대표 승인은 자동 처리하지 않았다.

프로젝트 안에서는 다음 연결을 준비했다.

- 미설치·엔진 연결 실패·Linux 컨테이너 모드 필요·미설정/누락 이미지 상태를 구분한다.
- Windows 설치 직후 현재 프로세스의 PATH에 Docker가 없어도 기본 설치 위치에서 찾는다.
- Docker 검사가 필요한 요청은 기존 분석 화면의 준비사항에 환경 상태와 준비 방법을 표시한다. 코드 작성은 가능하며 필수 미실행 검사의 자동 승인 차단은 유지한다.
- 명시적 준비 스크립트가 공식 Python 기반 이미지를 빌드하고, 읽기 전용 루트·비특권 사용자·외부 경로 없음·임시 공간 쓰기와 실제 함수/프로세스 실행을 검사한다.
- 검사가 모두 통과해야 실행 이미지의 고정 SHA256 ID를 설정에 연결한다. 실패 시 기존 설정을 유지한다. 다른 런타임과 모델 설정도 보존한다.
- 준비 후 저장 프로젝트의 검증 계약을 모델 재호출 없이 다시 실행하고 `docker_revalidation.json` 및 검증 DB 기록을 남긴다. 실패하면 해당 결과의 QA 실패로 대기 승인을 무효화하며, 성공하더라도 대표 승인을 자동 결정하지 않는다.

## 향후 환경 준비 및 재검사 방법

실제 PC 진단 결과는 [DOCKER_SETUP_STATUS.json](DOCKER_SETUP_STATUS.json), 샘플 재검증 결과는 `projects/PRJ-DFD03CDA/docker_revalidation.json`에 기록했다. 아래 설치·재시작 단계는 이미 완료했으므로 재설치가 필요한 경우에만 수행한다.

1. WSL 설치는 완료했다. 사용자 작업을 저장한 뒤 Windows를 재시작하여 VirtualMachinePlatform 기능을 적용한다.
2. Docker Desktop 설치도 완료했다. 재시작 후 Docker Desktop을 실행하고 최초 실행·약관 확인을 완료한다. Linux 컨테이너 엔진이 실행되어야 한다.
3. 프로젝트 폴더에서 아래 진단을 실행한다. 진단만으로 설치·다운로드·설정 변경을 하지 않는다.

```powershell
.\.venv\Scripts\python.exe -B -X utf8 scripts/setup_docker_verification.py
```

4. 실행 이미지 준비와 기존 샘플 프로젝트 재검사를 명시적으로 실행한다. 이 단계에서는 공식 Python 기반 이미지를 다운로드한다. 컨테이너에서 생성 프로그램을 실행할 때는 네트워크가 차단된다.

```powershell
.\.venv\Scripts\python.exe -B -X utf8 scripts/setup_docker_verification.py --build --project PRJ-DFD03CDA
```

해당 명령이 성공해야 실제 Docker 실행 검증 완료라고 판단한다. 기존 샘플은 문장 정리·단어 빈도 분석 프로그램이다. 준비 스크립트와 Dockerfile은 Python 표준 라이브러리 환경을 제공한다. 외부 패키지나 다른 언어가 필요한 프로그램은 검토된 별도 런타임 이미지가 필요하며 자동으로 패키지를 설치하지 않는다.

설정 연결 후 같은 환경에서 프로젝트만 다시 검사하려면:

```powershell
.\.venv\Scripts\python.exe -B -X utf8 scripts/setup_docker_verification.py --project PRJ-DFD03CDA
```

`PASS`는 명시된 검증 계약의 통과를 뜻한다. 프로그램 전체의 모든 기능이나 GUI를 검증했다는 뜻은 아니다. 기존 실행 기록은 보존되고 새 재검증 결과가 별도 파일과 DB에 추가된다.
