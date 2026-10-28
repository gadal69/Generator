"""Daily G2B DB-construction notice digest.

Configuration is read from environment variables. This module never schedules itself
or sends mail unless invoked after all required settings are present.
"""
from __future__ import annotations

import json
import logging
import os
import re
import smtplib
import ssl
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from email.message import EmailMessage
from pathlib import Path
from urllib.parse import quote, quote_plus, urlparse
from zoneinfo import ZoneInfo

import requests

LOGGER = logging.getLogger("g2b_digest")
STATE_FILE = Path(__file__).resolve().parents[2] / "data" / "g2b_digest_state.json"


def _redact_api_error(error: Exception, api_key: str) -> str:
    message = str(error)
    for candidate in (api_key, quote(api_key, safe=""), quote_plus(api_key)):
        if candidate:
            message = message.replace(candidate, "[REDACTED]")
    return re.sub(r"(?i)((?:service|api|auth)[_-]?key=)[^&\s]+", r"\1[REDACTED]", message)


def _required(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"필수 환경변수가 없습니다: {name}")
    return value


def _config() -> dict:
    config = {
        "api_key": _required("G2B_API_KEY"),
        "api_url": _required("G2B_API_URL"),
        "recipient": _required("G2B_EMAIL_TO"),
        "sender": _required("G2B_SMTP_FROM"),
        "smtp_host": _required("G2B_SMTP_HOST"),
        "smtp_user": _required("G2B_SMTP_USER"),
        "smtp_password": _required("G2B_SMTP_PASSWORD"),
        "smtp_port": int(os.getenv("G2B_SMTP_PORT", "465")),
        "keywords": [x.strip() for x in os.getenv("G2B_KEYWORDS", "DB 구축,데이터베이스 구축").split(",") if x.strip()],
    }
    parsed = urlparse(config["api_url"])
    if parsed.scheme != "https" or not parsed.netloc or parsed.query:
        raise RuntimeError("G2B_API_URL은 서비스키 등 쿼리 문자열이 없는 HTTPS API 주소여야 합니다.")
    return config


def _as_items(payload) -> list[dict]:
    """Accept the usual data.go.kr JSON envelope or XML envelope."""
    if isinstance(payload, dict):
        response = payload.get("response", payload)
        body = response.get("body", response) if isinstance(response, dict) else {}
        items = body.get("items", []) if isinstance(body, dict) else []
        if isinstance(items, dict):
            items = items.get("item", [])
        if isinstance(items, dict):
            items = [items]
        return [item for item in items if isinstance(item, dict)] if isinstance(items, list) else []
    if isinstance(payload, str):
        root = ET.fromstring(payload)
        rows = []
        for node in root.findall(".//item"):
            rows.append({child.tag.split("}")[-1]: (child.text or "").strip() for child in node})
        return rows
    return []


def _validate_api_payload(payload) -> None:
    header = None
    if isinstance(payload, dict):
        response = payload.get("response", payload)
        if isinstance(response, dict):
            header = response.get("header")
    elif isinstance(payload, str):
        root = ET.fromstring(payload)
        header_node = root.find(".//header")
        if header_node is not None:
            header = {child.tag.split("}")[-1]: (child.text or "").strip() for child in header_node}
    if not isinstance(header, dict):
        return
    code = str(header.get("resultCode", "")).strip()
    message = str(header.get("resultMsg", "")).strip()
    if code and code not in {"00", "0000", "NORMAL_SERVICE"}:
        raise RuntimeError(f"나라장터 OpenAPI 오류 {code}: {message or '오류 설명 없음'}")


def fetch_notices(config: dict) -> list[dict]:
    """Fetch the API's current notice page and filter title/description locally."""
    now = datetime.now(ZoneInfo("Asia/Seoul"))
    begin = os.getenv("G2B_INQRY_BGN_DATE", (now - timedelta(days=1)).strftime("%Y%m%d%H%M"))
    end = os.getenv("G2B_INQRY_END_DATE", now.strftime("%Y%m%d%H%M"))
    params = {"serviceKey": config["api_key"], "pageNo": 1, "numOfRows": 100,
              "type": "json", "inqryDiv": "1", "inqryBgnDate": begin, "inqryEndDate": end}
    # Operation-specific parameters can be added without changing source code.
    extra = os.getenv("G2B_EXTRA_PARAMS", "").strip()
    if extra:
        custom = json.loads(extra)
        if not isinstance(custom, dict):
            raise RuntimeError("G2B_EXTRA_PARAMS는 JSON 객체여야 합니다.")
        params.update({str(k): v for k, v in custom.items() if str(k).casefold() not in {"servicekey", "pageNo".casefold()}})
    all_rows = []
    for page in range(1, 51):
        params["pageNo"] = page
        page_rows = None
        last_error = None
        for attempt in range(3):
            try:
                response = requests.get(config["api_url"], params=params, timeout=(5, 30))
                response.raise_for_status()
                try:
                    payload = response.json()
                except ValueError:
                    payload = response.text
                _validate_api_payload(payload)
                page_rows = _as_items(payload)
                break
            except (requests.RequestException, ET.ParseError, ValueError) as exc:
                last_error = exc
                if attempt < 2:
                    time.sleep(2 ** attempt)
        if page_rows is None:
            safe_error = _redact_api_error(last_error or RuntimeError("원인 정보 없음"), config["api_key"])
            raise RuntimeError(f"나라장터 OpenAPI 조회 실패(3회 재시도): {safe_error}") from None
        all_rows.extend(page_rows)
        if len(page_rows) < 100:
            break
    else:
        LOGGER.warning("조회 결과가 5,000건을 초과해 앞의 5,000건까지만 검색했습니다.")
    terms = [term.casefold() for term in config["keywords"]]
    return [row for row in all_rows if any(term in " ".join(str(v) for v in row.values()).casefold() for term in terms)]


def _load_state() -> set[str]:
    try:
        data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        return set(data.get("notice_ids", []))
    except (OSError, ValueError, AttributeError):
        return set()


def _notice_id(item: dict) -> str:
    for key in ("bidNtceNo", "bidNtceOrd", "ntceNo", "bidNoticeNo"):
        if item.get(key):
            return f"{item.get('bidNtceNo', item.get('ntceNo', ''))}-{item.get('bidNtceOrd', '')}".strip("-")
    return json.dumps(item, ensure_ascii=False, sort_keys=True)


def _send(config: dict, notices: list[dict]) -> None:
    msg = EmailMessage()
    msg["Subject"] = f"[나라장터] DB 구축 공고 {len(notices)}건"
    msg["From"] = config["sender"]
    msg["To"] = config["recipient"]
    if not notices:
        body = "오늘 확인한 신규 DB 구축 관련 입찰공고가 없습니다."
    else:
        chunks = ["나라장터 신규 DB 구축 관련 입찰공고입니다.", ""]
        for index, item in enumerate(notices, 1):
            title = item.get("bidNtceNm") or item.get("ntceNm") or item.get("title") or "공고 제목 확인 필요"
            url = item.get("bidNtceDtlUrl") or item.get("ntceUrl") or ""
            chunks.extend([f"{index}. {title}", f"기관: {item.get('ntceInsttNm') or item.get('dmndInsttNm') or '정보 없음'}",
                           f"공고번호: {_notice_id(item)}", f"마감: {item.get('bidClseDt') or '정보 없음'}"])
            if url:
                chunks.append(f"상세: {url}")
            chunks.append("")
        body = "\n".join(chunks)
    msg.set_content(body)
    context = ssl.create_default_context()
    if config["smtp_port"] == 465:
        connection = smtplib.SMTP_SSL(config["smtp_host"], config["smtp_port"], timeout=30, context=context)
    else:
        connection = smtplib.SMTP(config["smtp_host"], config["smtp_port"], timeout=30)
    with connection as server:
        if config["smtp_port"] != 465:
            server.starttls(context=context)
        server.login(config["smtp_user"], config["smtp_password"])
        server.send_message(msg)


def run_once() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    config = _config()
    notices = fetch_notices(config)
    known = _load_state()
    fresh = [item for item in notices if _notice_id(item) not in known]
    _send(config, fresh)
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    known.update(_notice_id(item) for item in fresh)
    STATE_FILE.write_text(json.dumps({"notice_ids": sorted(known)}, ensure_ascii=False, indent=2), encoding="utf-8")
    LOGGER.info("조회 %d건, 신규 %d건 메일 발송 완료", len(notices), len(fresh))
    return 0


def install_daily_task(hour: int = 8) -> None:
    if not 0 <= hour <= 23:
        raise ValueError("시간은 0~23 사이여야 합니다.")
    if os.name != "nt":
        raise RuntimeError("예약 설치는 Windows에서만 지원합니다.")
    python = Path(sys.executable).resolve()
    script = Path(__file__).resolve()
    command = f'"{python}" "{script}"'
    subprocess.run(["schtasks", "/Create", "/TN", "ITNI G2B DB Digest", "/SC", "DAILY", "/ST", f"{hour:02d}:00", "/TR", command, "/F"], check=True)
    LOGGER.info("매일 %02d:00 작업을 등록했습니다. 서비스 키/SMTP 환경변수가 작업 실행 계정에서 보이는지 확인하세요.", hour)


def main() -> int:
    if "--install-daily" in sys.argv:
        config = _config()  # Validate credentials before registering a persistent task.
        del config
        hour = int(os.getenv("G2B_SCHEDULE_HOUR", "8"))
        install_daily_task(hour)
        return 0
    return run_once()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        logging.basicConfig(level=logging.ERROR)
        LOGGER.exception("자동화 실행 실패: %s", exc)
        raise SystemExit(1)
