# 요구사항별 검증과 최종 승인 — 2026-10-27

분석에 등록된 요구사항과 검사 ID를 연결하고, 정상 입력·잘못된 입력·경계값의 실행 결과를 각각 집계한다. 자연어 요청 전체를 자동으로 빠짐없이 해석했다고 보장하지 않으므로 대표는 분석 단계에서 요구사항 목록과 합격 기준을 확인해야 한다.

## 기준의 출처

- `USER`: 대표가 명시적으로 제공한 검증 계약. 모델 제안보다 우선한다.
- `PROPOSED`: 모델이 제안한 예상 결과. 실제 검사 및 통과 판정에 사용하지 않는다.
- `REPRESENTATIVE_CONFIRMED`: 모델 제안을 대표가 분석 화면에서 확인한 기준. 원래 제안의 출처는 `expectation_origin: MODEL`로 유지한다.
- `LEGACY_EXPLICIT`: 대표가 입력한 기존 함수 예제. 개별 예제 확인에 사용할 수 있지만 요구사항 연결이 없는 예제만으로 최종 승인하지 않는다.

모델이 JSON 안에 `source: USER`나 사례별 승인 표시를 넣어도 확인 경계에서 덮어쓴다. 분석 화면에는 사람이 읽을 수 있는 기준 상태와 입력 유형 설명을 기존 영역에 표시한다.

## 검사 형식

```json
{
  "version": 1,
  "requirements": [
    {"id": "range", "description": "0~10 정수만 처리하고 두 배 값을 반환", "required_categories": ["normal", "invalid", "boundary"]}
  ],
  "checks": [
    {
      "id": "range_cases", "kind": "python_function", "target": "sample.py",
      "function": "bounded", "required": true, "requirements": ["range"],
      "cases": [
        {"args": [3], "expected": 6, "category": "normal"},
        {"args": ["x"], "error": true, "error_type": "TypeError", "category": "invalid"},
        {"args": [0], "expected": 0, "category": "boundary"},
        {"args": [10], "expected": 20, "category": "boundary"}
      ]
    }
  ]
}
```

함수 사례는 각각 `category`를 지정하고, CLI 실행 검사는 검사 항목에 `category`를 지정한다. 기존 사례는 분류가 없으면 일반 기대값은 normal, 예외 기대는 invalid로 해석한다. boundary는 명시해야 한다. 함수·CLI 동작 요구사항은 기본적으로 세 유형이 필요하다. 파일 존재 같은 정적 요구사항은 대표가 확인한 `required_categories: ["normal"]`과 `category_rationale`로 적용 범위를 설명할 수 있다.

검사 또는 사례에 `enabled: false`를 지정하면 실행을 생략하고 NOT_RUN으로 기록한다. `skip_reason`으로 이유를 남길 수 있다. 비활성 사례를 실행기에 전달하지 않으며 다른 사례가 성공해도 해당 생략을 통과로 바꾸지 않는다.

## 판정 및 승인

- 요구사항에 필수 검사가 연결되지 않았거나 입력 유형이 빠졌으면 해당 COVERAGE는 NOT_RUN이다.
- 선택 검사는 필수 검증 범위를 대신 충족하지 못한다.
- 예상값 불일치·예외 종류 불일치·필수 실행 실패는 FAIL이다.
- 실행 환경 없음, 미지원 검사, 생략, 결과 누락은 NOT_RUN이다. 알 수 없는 상태도 미검증으로 처리한다.
- 빈 텍스트 조건만으로 통과할 수 없도록 거부한다.
- 프로그램 최종 검사에는 요구사항 목록과 필수 검사 연결을 요구한다. 기존의 무제한 호스트 실행 미실행 표시는 참고용이며, 실제 필수 실행 범위는 명시된 계약으로 판단한다.
- 필수 실패는 승인 요청을 중단한다. 필수 미검증은 상태와 사유를 표시하고 자동·수동 최종 승인을 모두 차단한다. 대표 확인만으로 미실행 검사를 PASS로 바꾸지 않는다.

체크포인트의 `final_verification`에 정확한 최종 결과 ID·본문 해시, 승인 기준 해시, 산출 파일 목록·내용 해시, 의존성 환경 해시, 검사 기록을 저장한다. 승인 직전에 최신 QA와 이 증거를 다시 확인한다. 결과·기준·파일 목록·파일 내용·환경이 바뀌거나 새 재검증이 미완료이면 이전 통과를 재사용하지 못한다. 기존 프로그램에 증거가 없으면 재검증이 필요하다. 프로그램이 아닌 기존 일반 업무의 QA 승인 흐름은 유지한다.

기존 프로그램의 기준에 요구사항 연결이나 경계값 사례가 없다면 재분석·대표 확인 후 재검증해야 한다. Docker 재검증 스크립트도 같은 강화된 최종 검증 정책으로 새 승인 증거를 저장한다.

## 검증 결과

- `tests/test_requirement_verification.py`: 입력 유형별 판정, 제안 출처 위조 거부, 대표 기준 우선, 생략·누락·실패, Docker 사례 매핑, 수동·자동 승인, 검증 후 결과·파일·기준 변경, 이전 형식 프로그램의 증거 누락을 검사한다.
- [실제 Docker 검사 결과](requirement_verification_20261027.json): 결정적 fixture를 사용했으며 AI 호출은 0회다. 정상·오류·경계 사례 전체는 PASS, 경계 누락과 정상 사례 생략은 NOT_RUN, 잘못된 예상값은 FAIL, 미확인 모델 제안은 NOT_RUN이었다.
- 최종 승인은 임시 DB를 사용하는 작업 흐름 통합 테스트에서 검사했다. 실제 Docker 검사 결과 파일은 검증 엔진의 실제 실행을 확인한 기록이며 실제 사용자 프로젝트를 승인한 기록은 아니다.

```powershell
.\.venv\Scripts\python.exe -B -m unittest discover -s tests -q
.\.venv\Scripts\python.exe -B -X utf8 tests/manual_requirement_acceptance.py
```

수동 Docker 테스트는 기존 결과 파일 덮어쓰기를 거부한다.
