# 나라장터 DB 구축 공고 이메일 자동화

Chief가 “매일 아침 8시에 www.g2b.go.kr에 있는 DB구축 공고 취합해서 메일로 보내줘”와 같은 지시를 받으면, 자동화 요청으로 분류하고 공개 데이터 수집 및 일정/메일 담당 에이전트를 구성합니다. 처리 결과는 `AUTOMATION_SETUP_REQUIRED`로 표시합니다. 필요한 인증정보가 아직 없으므로 팀 대시보드가 예약 등록이나 발송 성공으로 오인하지 않습니다.

## 데이터 원천

웹페이지 화면을 파싱하지 않고 조달청의 [나라장터 입찰공고정보서비스](https://www.data.go.kr/data/15129394/openapi.do)를 사용합니다. 이 서비스는 REST JSON/XML을 제공하며, 물품·용역·공사 등 공고 유형별로 오퍼레이션이 나뉘어 있습니다. 공공데이터포털에서 활용 신청 후 발급한 서비스 키와 원하는 공고 유형에 맞는 API 오퍼레이션 URL을 준비해야 합니다.

## 환경 설정

PowerShell에서 `$env:`로 지정한 값은 현재 PowerShell과 수동 실행에서만 유효합니다. 예약 작업까지 쓰려면 Windows의 **사용자 환경 변수**에 값을 등록한 뒤 작업을 등록하고, 작업 스케줄러가 같은 사용자 계정으로 실행되는지 확인합니다. 비밀번호나 서비스 키를 소스 코드, 프로젝트 지시문 또는 Git에 넣지 않습니다. 회사 SMTP 정책이 허용한다면 전용 앱 비밀번호를 사용합니다.

```powershell
$env:G2B_API_KEY = "발급받은_서비스키"
$env:G2B_API_URL = "공고유형에_맞는_공식_API_오퍼레이션_HTTPS_URL"
$env:G2B_EMAIL_TO = "받는사람@example.com"
$env:G2B_SMTP_FROM = "보내는사람@example.com"
$env:G2B_SMTP_HOST = "smtp.example.com"
$env:G2B_SMTP_PORT = "465"
$env:G2B_SMTP_USER = "SMTP 계정"
$env:G2B_SMTP_PASSWORD = "SMTP 앱 비밀번호"
$env:G2B_KEYWORDS = "DB 구축,데이터베이스 구축"
```

기본 조회 범위는 한국 시간 기준 최근 24시간이며, `G2B_INQRY_BGN_DATE`와 `G2B_INQRY_END_DATE`로 바꿀 수 있습니다. 공고 유형에 따라 추가 검색 파라미터가 필요하면 JSON 객체로 설정합니다. (API 문서에서 해당 오퍼레이션 파라미터 형식을 확인하십시오.)

```powershell
$env:G2B_INQRY_BGN_DATE = "202610050000"
$env:G2B_INQRY_END_DATE = "202610060800"
$env:G2B_EXTRA_PARAMS = '{"inqryDiv":"1"}'
```

## 수동 실행과 예약

프로젝트 루트(`D:\ITNI_AI_TEAM`)에서 설정 직후 한 번 수동 실행해 API 응답, 키워드 결과, 메일을 확인합니다.

```powershell
python -m app.automations.g2b_daily_email_digest
```

정상 수동 실행을 확인한 다음에만 매일 08:00 Windows 작업을 등록합니다.

```powershell
python -m app.automations.g2b_daily_email_digest --install-daily
```

Windows 작업 스케줄러는 실행 계정으로 프로그램을 시작합니다. 예약 시에도 위 환경변수가 보이는지 확인하십시오. 서비스 키 또는 메일 인증에 실패하면 프로그램은 오류 코드와 로그를 남기고, 성공 메일로 오인하지 않습니다. 수집된 공고 ID는 `data/g2b_digest_state.json`에 저장하여 이미 발송한 공고의 중복 발송을 줄입니다.

## 적용 한계

현재 프로그램은 OpenAPI 호출과 메일 발송 코드를 제공하며, 실제 API 서비스 키·공고 유형 오퍼레이션·수신자·SMTP 자격증명은 실행 환경에서 설정해야 합니다. 나라장터 공고 유형에 따라 API 주소와 검색 파라미터가 달라질 수 있으므로, 대상 업무(물품/용역/공사 등)에 맞게 `G2B_API_URL`과 날짜 파라미터를 확인한 뒤 활성화합니다.
