# 생성 프로그램 GUI·이미지 검증

개발기 GUI 디자인은 수정하지 않았다. 검증 계약과 기존 QA 기록 경로를 확장했다.

## 실제 자동 실행 범위

`kind: gui`는 Linux Docker 안에서 PySide6 창을 실제 생성하고 Qt Test의 키·마우스 이벤트를 전달한다. 호스트에서 생성 코드를 실행하지 않는다. 지원 항목은 입력, 버튼 클릭, 파일 열기·저장 핸들러 호출과 이후 위젯 속성·이미지 결과 비교다. 모듈 import는 이벤트 루프를 시작하면 안 되며, factory는 QWidget을 반환해야 한다. 위젯에는 고유 objectName을 지정한다.

파일 선택은 QFileDialog의 반환 경로를 주입한다. 실제 파일 읽기·저장 핸들러는 실행하지만 운영체제 기본 파일 대화상자를 직접 조작한 것은 아니다. 결과에는 `native_dialogs: NOT_RUN`과 `screen_review: NOT_RUN`을 별도로 남긴다. 다른 GUI 프레임워크, 미지정 factory·동작·기대값, 준비되지 않은 환경은 통과로 간주하지 않는다. 현재 입력 이벤트는 ASCII만 지원한다. 한글 IME·드래그·운영체제 대화상자는 별도 검증이 필요하다.

`kind: image_output`은 Docker 명령 실행 후 Pillow로 결과 파일을 실제 디코딩한다. 출력 존재, 디코딩 성공, 실제 형식, 가로·세로 크기, 대표가 지정한 좌표의 RGB 값을 비교한다. 파일 확장자만 보고 형식을 판단하지 않는다. GUI 검사의 outputs에도 같은 조건을 적용한다. 좌표 검사만으로 이미지 전체의 미적 품질을 보장하지 않으므로, 편집 효과를 구분할 수 있는 여러 좌표를 지정하고 외관 요구사항은 화면 검토로 남긴다.

## 계약 예시

```json
{
  "version": 1,
  "requirements": [{"id":"save","description":"저장 결과","required_categories":["normal"],"category_rationale":"저장 결과 관찰. 입력 오류·취소는 별도 요구사항으로 연결"}],
  "checks": [{
    "id":"save-result","kind":"gui","target":"editor.py","requirements":["save"],"factory":"Window",
    "actions":[{"action":"open_file","widget":"openButton","path":"input.png"},{"action":"input","widget":"colorInput","text":"red"},{"action":"click","widget":"editButton"},{"action":"save_file","widget":"saveButton","path":"output.png"}],
    "widgets":[{"widget":"statusLabel","property":"text","expected":"saved"}],
    "outputs":[{"path":"output.png","format":"PNG","size":[4,3],"pixels":[{"xy":[0,0],"rgb":[255,0,0]},{"xy":[3,2],"rgb":[255,0,0]}]}]
  }]
}
```

입력 이미지는 프로젝트의 실행 절차로 준비해야 한다. 파일 선택 취소는 빈 path로 지정한다. `visual_review`는 화면 검토 전용 항목이며 현재 자동으로 승인하지 않고 NOT_RUN을 반환한다. 필수 visual_review나 실행하지 못한 필수 항목은 기존 최종 승인 차단 규칙에 반영된다. 모델이 제안한 기대값은 대표 확인 전에는 실행 통과로 인정하지 않는다.

## 실행 환경

검토된 `scripts/gui_verification.Dockerfile`은 PySide6 6.8.3, Pillow 11.3.0과 필요한 Linux 라이브러리를 Docker 안에 설치하고 창 생성·패키지 검사를 수행한다. `scripts/setup_gui_verification.py --build`는 이미지를 빌드한 뒤 실제 비권한·네트워크 차단·읽기 전용 컨테이너에서 입력·클릭·저장 결과를 재확인하고, 성공한 경우에만 고정 이미지 ID를 `verification.images.gui`에 연결한다. 기존 python 이미지와 모델 설정은 유지한다. 프로젝트가 PySide6 의존성을 요구하면 이 GUI 기본 이미지를 상속해 프로젝트별 버전 설치·확인 후 전용 이미지를 사용한다. 런타임 오류 시 호스트 설치나 실행으로 우회하지 않는다.

## 2026-10-28 실측

Docker 엔진 29.9.2 연결 확인. 두 파일(editor.py, store.py)로 구성한 결정적 테스트 샘플에서 실제 GUI 열기→입력→편집→저장, 잘못된 색상 입력, 파일 선택 취소, SQLite 저장 후 별도 Python 프로세스 조회, PNG 디코딩·크기·형식·두 좌표 편집 결과 검사 통과. 잘못된 크기 기준은 FAIL로 검출했다. 결과는 `docs/gui_media_acceptance.json`에 기록했다.

이 샘플은 검증기를 시험하기 위해 작성한 테스트 픽스처다. 이전 로컬 모델이 생성했던 할 일 관리 프로그램의 전체 개발 흐름 성공을 의미하지 않는다. 실제 화면 외관, 네이티브 파일 대화상자, 실제 외부 API는 NOT_RUN이다.

전체 회귀 테스트 176개 통과(39.853초). 최종 변경본의 실제 Docker 샘플 검사도 재실행하여 PASS를 확인했다. 모델 기본 설정과 개발기 GUI 디자인은 유지했다.
