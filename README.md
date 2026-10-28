# ITNI AI TEAM 1.5.0

로컬 AI로 업무 지시를 분석하고, 필요한 작업 에이전트를 구성한 뒤 QA와 승인을 거쳐 결과를 저장하는 데스크톱 프로그램입니다.

## 실행

프로젝트 폴더에서 PowerShell로 실행합니다.

```powershell
cd D:\Dev\cs\ITNI_AI_TEAM
.\.venv\Scripts\python.exe run_dashboard.py
```

새 컴퓨터에서는 Python 3.11 이상을 설치하고 가상환경을 새로 만듭니다.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe run_dashboard.py
```

## 모델 및 사용 흐름

- 현재 기본 엔진과 모델은 `config/app_config.json`의 `ai.provider`, `ai.default_model`로 설정합니다.
- Ollama를 사용하는 경우 서버를 실행하고 설정된 모델을 설치해야 합니다.
- 업무 입력 → 분석 → 분석 확인 후 실행 → QA 및 재작업 → 최종 승인 순서로 진행합니다.
- 화면의 오프라인 전체 점검은 실제 모델 없이 임시 DB로 실행 흐름을 확인합니다.
- Gemini 검색·교차 분석·코드 검토는 SDK, API 키와 클라우드 사용 승인이 필요합니다. 키는 `GEMINI_API_KEY` 또는 `GOOGLE_API_KEY` 환경변수로 지정합니다.

## 데이터 및 산출물

- 운영 DB: `data/database/itni_ai_team.db`
- 실행 결과: `projects/<project_id>/team_run_result.json`, `final_result.txt`
- 생성 파일: `projects/<project_id>/artifacts/`
- 기존 소스 수정 요청은 작업공간 안의 지정된 소스 파일을 교체할 수 있습니다.
- 생성된 Python 코드의 문법은 검사하지만 자동 실행·기능 테스트는 수행하지 않습니다.
- PPTX/XLSX/DOCX/PDF 요청은 문서를 생성하는 Python 스크립트를 산출합니다.
- 나라장터 자동화 설정은 `docs/G2B_DAILY_EMAIL_AUTOMATION.md`를 참고하세요.

운영 DB와 기존 프로젝트는 보존해야 할 사용자 데이터입니다. 정리 내역은 `docs/CLEANUP_REPORT.md`에 기록되어 있습니다.

실행 로직 수정과 모델별 설정 안내: `docs/RUNTIME_RELIABILITY_UPDATE.md`.
회귀 검증은 `tests/test_runtime_reliability.py`에서 수행합니다.

사용자 예제 기반 함수 검사와 실제 모델 재시험 안내: `docs/FUNCTIONAL_VERIFICATION_UPDATE.md`.
