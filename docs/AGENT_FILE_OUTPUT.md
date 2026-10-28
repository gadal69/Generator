# 에이전트 파일 산출물

코드 또는 파일 생성 지시가 들어오면 Chief Planner는 `file_creation_required`를 설정하고 프로젝트별 Software Developer를 팀에 포함합니다. 개발 에이전트는 결과에 `<artifact path="파일명.py"> ... </artifact>` 형식으로 완전한 파일 내용을 포함합니다. TeamRunner는 QA와 Chief 검토가 통과한 뒤 등록된 `write_project_file` 도구를 호출해 파일을 씁니다.

생성 파일은 해당 프로젝트의 artifacts 아래에 저장합니다. 상대 폴더를 지정하면 artifacts 내부에 해당 폴더를 만듭니다.

```text
projects/<project_id>/artifacts/<requested-file>      # 폴더 미지정
projects/<project_id>/artifacts/<relative-folder>/... # 프로젝트 내 상대 폴더 지정
```

경로 없이 `calu.py`를 요청하면 해당 프로젝트의 `artifacts/calu.py`에 생성됩니다. 에이전트는 workspace 바깥, 드라이브 루트, 상위 디렉터리에는 쓸 수 없습니다. 파일은 UTF-8로 원자적으로 저장되고 파일당 최대 2MB입니다. 생성 코드는 자동 실행하지 않습니다.

PPTX/XLSX 생성 스크립트 등 결과물을 실행할 때 별도 라이브러리가 필요한 경우, 분석창에 설치 여부와 설치 명령을 표시합니다. 이 라이브러리는 코드 작성 단계가 아니라 생성된 스크립트를 실행할 때 필요한 것으로 구분합니다.

개발 에이전트가 파일 내용을 산출하지 않거나 저장 도구가 실패하면 실행은 오류 상태가 되어 성공/승인 완료로 처리되지 않습니다.
