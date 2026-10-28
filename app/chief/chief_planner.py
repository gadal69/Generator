import json,re
from app.chief.chief_decision import ChiefDecision
from app.chief.allocation_policy import enforce_allocation, developer_key
from app.tools.project_file_tool import is_file_creation_request

class ChiefPlanner:
    """요청에서 필요한 기본 역할/전문성을 추론하는 정책 계층."""
    KEYWORDS={
        "proposal":("제안서","입찰","RFP","사업계획","제안"),
        "website":("웹사이트","홈페이지","웹페이지","사이트"),
        "development":("개발","프로그램","코드","구현","소프트웨어","API"),
        "ocr":("OCR","문자인식","스캔","VLM","문서이미지"),
        "gis":("GIS","공간정보","지도","좌표"),
        "db":("DB","데이터베이스","데이터 구축","데이터관리"),
        "security":("보안","개인정보","취약점"),
        "design":("디자인","UI","UX","화면","PPT","발표자료"),
        "research":("조사","분석","자료","시장","동향")
    }
    def rule_plan(self, request):
        text=request.lower()
        external_data_access = self._requires_external_data(request)
        external_data_plan = self._external_data_plan(request)
        hit=lambda key:any(k.lower() in text for k in self.KEYWORDS[key])
        automation = self._automation_spec(request)
        if automation:
            specs = [
                {"specialty":"Public procurement data","name":"G2B_Data_Specialist","capabilities":["나라장터 공식 OpenAPI 분석","공고 검색 및 중복 제거","DB 구축 공고 분류"]},
                {"specialty":"Scheduled workflow automation","name":"Automation_Engineer","capabilities":["매일 예약 실행","재시도 및 로그","Windows 작업 스케줄러"]},
                {"specialty":"Email delivery","name":"Email_Delivery_Specialist","capabilities":["SMTP 설정 검증","요약 메일 발송","비밀정보 환경변수 관리"]},
            ]
            return ChiefDecision(
                core_roles=["Data Engineer"], specialists=specs,
                required_capabilities=["공식 조달청 OpenAPI 연동","DB 구축 키워드 검색","예약 수집","중복 방지","메일 요약 발송","설정 및 오류 상태 보고"],
                rationale="정기 수집 자동화로 분류했습니다. 공개 조달 데이터는 로컬에서 처리하며, API 키와 메일 설정이 확인될 때까지 실행/발송 완료로 표시하지 않습니다.",
                priority="NORMAL", cloud_ai_required=False, source="RULE", execution_mode="local",
                external_data_access=external_data_access,
                external_data_plan=external_data_plan,
                workflow_type="scheduled_automation", automation=automation)
        roles=[]; caps=[]; specs=[]
        file_creation_required = is_file_creation_request(request)
        if file_creation_required:
            roles=["Software Developer"]; caps=["requirements","implementation","file_output"]
        elif hit("proposal"):
            roles=["Proposal Specialist"]; caps=['research','planning']
        elif hit("website"):
            roles=["Software Developer"]; caps+=['planning','design','implementation']
        elif hit("development"):
            roles=["Software Developer"]; caps+=['planning','implementation','testing']
        elif hit("research"):
            roles=["Research Specialist"]; caps+=['research','planning']
        else:
            roles=[]; caps+=['chief_direct_execution']
        if hit("ocr"):
            if not roles: roles=["Document Data Specialist"]
            specs.append({"specialty":"ocr","name":"AI OCR Specialist","role":"문서 OCR 및 메타데이터 분석","capabilities":["ocr","vlm","document_analysis","metadata_extraction"]})
        if hit("gis"):
            if not roles: roles=["GIS Specialist"]
            specs.append({"specialty":"gis","name":"GIS Specialist","capabilities":["gis","spatial_data","coordinate_analysis"]})
        db_explicit=any(k.lower() in text for k in ("db 설계","db 전문가","데이터베이스 설계","데이터 모델링","sql 설계","스키마 설계"))
        if db_explicit:
            if not roles: roles=["Database Specialist"]
            specs.append({"specialty":"db","name":"DB Specialist","capabilities":["database","sql","data_modeling","data_quality"]})
        if hit("security"):
            if not roles: roles=["Security Specialist"]
            specs.append({"specialty":"security","name":"Security Specialist","capabilities":["security","privacy","risk_assessment"]})
        needs_design = hit('design') and (not file_creation_required or any(
            token in text for token in ('디자인 담당', '디자이너', 'ui 설계', '화면 설계', 'ux 설계')))
        if needs_design and "DESIGN" not in roles:
            roles.insert(-1 if roles else 0,"DESIGN"); caps.append('design')
        priority="HIGH" if any(k in text for k in ("긴급","중요","마감","즉시")) else "NORMAL"
        direct_execution = not roles and not specs
        roles = list(dict.fromkeys(roles))
        return enforce_allocation(ChiefDecision(core_roles=roles,specialists=specs,required_capabilities=sorted(set(caps)),
            rationale=("비서실장이 직접 처리할 수 있는 요청으로 판단했습니다." if direct_execution else "요청에 필요한 최소 작업 에이전트를 구성했습니다."),priority=priority,
            cloud_ai_required=False,cloud_purpose="",cloud_scope="",cloud_data_scope="",estimated_cost="0",
            source="RULE",execution_mode="local",file_creation_required=file_creation_required,
            external_data_access=external_data_access,
            external_data_plan=external_data_plan,
            direct_execution=direct_execution, work_summary=request,
            work_steps=[] if direct_execution else [f"{role} 역할로 요청 산출물 작성" for role in roles]),request)

    @staticmethod
    def _requires_external_data(request):
        text = (request or "").casefold()
        compact = re.sub(r"\s+", "", text)
        mock_request=any(token in text for token in ('모의', 'mock', 'fixture', 'stub', '주입 transport'))
        no_live_access=bool(re.search(r'실제\s*(?:외부\s*)?(?:api\s*)?(?:호출|조회|접속|연결|통신)(?:은|는|을|를)?\s*하지\s*않',text))
        if mock_request and no_live_access:
            return False
        has_source = any(token in text for token in (
            "g2b", "나라장터", "조달청", "data.go.kr", "http://", "https://", "외부 사이트", "외부 api",
            "웹 검색", "웹에서", "인터넷", "온라인 자료", "네이버에서", "구글에서", "검색엔진",
        )) or "외부사이트" in compact or bool(re.search(r"\bwww\.[a-z0-9.-]+\.[a-z]{2,}\b", text))
        has_action = any(token in text for token in (
            "접속", "조회", "검색", "수집", "취합", "가져와", "크롤링", "api 연동", "openapi", "찾아줘", "찾아봐", "웹조사",
        ))
        return has_source and has_action

    @classmethod
    def _external_data_plan(cls, request):
        text = (request or "").casefold()
        required = cls._requires_external_data(request)
        g2b = any(token in text for token in ("g2b", "나라장터", "조달청"))
        url_match = re.search(r"https?://[^\s<>\"']+|\bwww\.[a-z0-9.-]+\.[a-z]{2,}\b", text)
        if g2b:
            source = "나라장터 조달 데이터"
        elif url_match:
            source = url_match.group(0).rstrip(".,;)")
        elif required:
            source = "공개 웹 검색 (구체 도메인 미지정)"
        else:
            source = ""
        api_requested = any(token in text for token in ("api", "openapi", "공공데이터")) or g2b
        file_output = is_file_creation_request(request)
        return {
            "required": required,
            "source": source if required else "",
            "method": "Gemini Google Search (공식 기관·API 원문 우선)" if api_requested else ("Gemini Google Search" if required else "해당 없음"),
            "operation": "연동 프로그램 작성" if required and file_output else ("실시간 외부 조회 요청" if required else "외부 조회 불필요"),
            "execution_status": "실행 확인 후 수집 예정",
            "execution_note": "실행 단계에서 Gemini가 공개 웹 자료를 검색합니다. 로그인이 필요한 데이터나 서비스키가 필요한 API는 별도 인증이 필요합니다." if required else "",
        }

    @staticmethod
    def _role_key(value):
        developer=developer_key(value)
        if developer:return developer
        text = re.sub(r"[^a-z0-9가-힣]+", " ", str(value or "").casefold()).strip()
        compact = text.replace(" ", "")
        if any(token in compact for token in ("softwaredeveloper", "developer", "programmer", "coder", "개발자", "소프트웨어개발")):
            return "software_developer"
        if any(token in compact for token in ("database", "dbspecialist", "db전문가", "데이터베이스", "데이터엔지니어")):
            return "data_engineering"
        if any(token in compact for token in ("researchspecialist", "researcher", "조사전문가", "자료조사")):
            return "research"
        return compact

    @classmethod
    def _deduplicate_roles(cls, roles, specialists):
        seen = set()
        clean_roles = []
        for role in roles:
            key = cls._role_key(role)
            if key and key not in seen:
                seen.add(key)
                clean_roles.append(role)
        clean_specialists = []
        for specialist in specialists:
            key = cls._role_key(" ".join((specialist.get("name", ""), specialist.get("role", ""), specialist.get("specialty", ""))))
            if key and key in seen:
                continue
            if key:
                seen.add(key)
            clean_specialists.append(specialist)
        return clean_roles, clean_specialists

    @staticmethod
    def _automation_spec(request):
        text = request.casefold()
        recurring = any(word in text for word in ("매일", "매주", "매월", "정기적으로"))
        g2b = any(word in text for word in ("g2b.go.kr", "나라장터", "조달청"))
        collect = any(word in text for word in ("공고", "취합", "수집", "모아"))
        email = any(word in text for word in ("메일", "이메일", "email"))
        if not (recurring and g2b and collect and email):
            return {}
        match = re.search(r"(?:아침|오전)\s*(\d{1,2})(?:시|:00)", text)
        hour = int(match.group(1)) if match else 8
        if not 0 <= hour <= 23:
            hour = 8
        return {
            "source_url": "https://www.g2b.go.kr",
            "source_api": "조달청 나라장터 입찰공고정보서비스 (data.go.kr)",
            "search_terms": ["DB 구축", "데이터베이스 구축"],
            "schedule": {"frequency": "daily", "time": f"{hour:02d}:00", "timezone": "Asia/Seoul"},
            "delivery": "email",
            "required_configuration": ["조달청 OpenAPI 서비스키와 공고분류별 오퍼레이션 URL", "메일 수신자", "SMTP 서버/포트/발신자/계정 비밀번호"],
            "agent_creation_required": True,
            "execution_ready": False,
        }

    def parse_llm(self,text, fallback,verification_contract=None):
        if fallback.workflow_type == "scheduled_automation":
            return fallback
        try:
            m=re.search(r"\{.*\}",text or "",re.S)
            if not m: raise ValueError('모델 분석에 JSON 객체가 없습니다.')
            data=json.loads(m.group(0))
            d=fallback.to_dict()
            # Workflow modes are assigned by the deterministic planner, not free-form model output.
            d["workflow_type"] = fallback.workflow_type
            d["automation"] = fallback.automation
            d["file_creation_required"] = fallback.file_creation_required
            d["direct_execution"] = fallback.direct_execution
            d["work_summary"] = fallback.work_summary
            d["work_steps"] = fallback.work_steps
            for k in d:
                if k not in {"workflow_type", "automation", "file_creation_required"} and k in data: d[k]=data[k]
            if not isinstance(d["cloud_ai_required"], bool):
                d["cloud_ai_required"] = fallback.cloud_ai_required
            d["execution_mode"] = "cloud" if d["cloud_ai_required"] else "local"
            roles=d.get("core_roles")
            if isinstance(roles,str):
                roles=[roles]
            if isinstance(roles, list):
                generic_roles={"pm","project manager","qa","quality assurance","chief","chief of staff"}
                d["core_roles"]=list(dict.fromkeys(r.strip()[:80] for r in roles if isinstance(r,str) and r.strip() and r.strip().casefold() not in generic_roles))[:8]
            else:
                d["core_roles"]=fallback.core_roles
            # A named specialist may be the sole implementation owner. The shared
            # allocation policy adds a developer only if no developer exists anywhere.
            raw_specialists=d.get("specialists")
            if not isinstance(raw_specialists,list):
                raw_specialists=fallback.specialists
            specialists=[]
            for specialist in raw_specialists:
                if not isinstance(specialist,dict):
                    continue
                specialty=specialist.get("specialty") or ""
                name=specialist.get("name") or specialty
                capabilities=specialist.get("capabilities") or [specialty or name]
                if isinstance(capabilities,str):
                    capabilities=[capabilities]
                if not isinstance(name,str) or not name.strip() or not isinstance(specialty,str):
                    continue
                role=specialist.get("role")
                normalized={"name":name.strip(),"specialty":specialty.strip(),"capabilities":[c.strip() for c in capabilities if isinstance(c,str) and c.strip()]}
                if isinstance(role,str) and role.strip(): normalized["role"]=role.strip()[:80]
                specialists.append(normalized)
            d["specialists"]=specialists
            d["external_data_access"] = self._requires_external_data(fallback.work_summary)
            d["external_data_plan"] = self._external_data_plan(fallback.work_summary)
            d["core_roles"], d["specialists"] = self._deduplicate_roles(d["core_roles"], d["specialists"])
            if not isinstance(d.get("required_capabilities"),list):
                d["required_capabilities"]=fallback.required_capabilities
            else:
                d["required_capabilities"]=[c.strip() for c in d["required_capabilities"] if isinstance(c,str) and c.strip()]
            for field in ("cloud_purpose","cloud_scope","cloud_data_scope","estimated_cost"):
                if not isinstance(d.get(field),str):
                    d[field]=str(d.get(field) or "")
            if d["cloud_ai_required"] and not (str(d.get("cloud_purpose", "")).strip() and str(d.get("cloud_scope", "")).strip() and str(d.get("cloud_data_scope", "")).strip()):
                return fallback
            d["direct_execution"] = not bool(d["core_roles"] or d["specialists"])
            d["work_summary"] = str(data.get("work_summary") or fallback.work_summary)
            steps = data.get("work_steps", fallback.work_steps)
            d["work_steps"] = [str(step).strip() for step in steps if str(step).strip()] if isinstance(steps, list) else fallback.work_steps
            from app.tools.dependency_preflight import normalize_libraries
            d['library_requirements'] = normalize_libraries(data.get('library_requirements', fallback.library_requirements))
            comparison_summary = data.get("comparison_summary", "")
            d["comparison_summary"] = comparison_summary.strip()[:4000] if isinstance(comparison_summary, str) else ""
            d["source"]="LLM"
            d['integration_role']=data.get('integration_role') or ''
            from app.qa.verification_contract import validate_contract
            proposed=data.get('verification_contract')
            d['verification_contract']=(validate_contract(verification_contract) if verification_contract is not None
                else validate_contract(proposed) if isinstance(proposed,dict) else {})
            d["acceptance_criteria"] = [str(item).strip()[:500] for item in data.get("acceptance_criteria", []) if isinstance(item, str) and item.strip()][:20] if isinstance(data.get("acceptance_criteria"), list) else []
            assignments = data.get("task_assignments", [])
            d["task_assignments"] = []
            if isinstance(assignments, list):
                for item in assignments[:16]:
                    if not isinstance(item, dict) or not isinstance(item.get("role"), str):
                        continue
                    files = item.get("files", [])
                    if not isinstance(files, list) or not all(isinstance(path, str) for path in files):
                        continue
                    d["task_assignments"].append({"role": item["role"].strip(), "deliverable": str(item.get("deliverable") or "")[:600], "files": files})
            decision=enforce_allocation(ChiefDecision(**d),fallback.work_summary)
            for item in decision.task_assignments:
                step=f"{item['role']}: {item['deliverable']} / 산출 파일: {', '.join(item['files']) or '역할별 인계 자료'}"
                if step not in decision.work_steps:decision.work_steps.append(step)
            return decision
        except Exception as exc:
            fallback.source='RULE_ANALYSIS_RECOVERY'
            fallback.rationale+=' 모델 분석 형식 오류로 규칙 기반 배정을 사용했습니다: '+type(exc).__name__+': '+str(exc)[:200]
            return fallback
