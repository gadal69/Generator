from app.agents.base_agent import BaseAgent
from app.chief.chief_planner import ChiefPlanner
from app.chief.chief_decision import ChiefDecision
import json
import re
from app.models.generation_policy import prepare_adapter
from app.runtime.run_control import RunCancelled
from app.chief.allocation_policy import ALLOCATION_INSTRUCTION

class ChiefAgent(BaseAgent):
    """대표와 실행조직 사이에서 조직 구성과 운영 판단을 담당하는 Chief."""
    def __init__(self, model="gemma4:12b", planner=None):
        super().__init__("AGT-CHIEF-001","Chief","Chief",model)
        self.planner=planner or ChiefPlanner()

    def analyze_request(self, request, model_adapter=None, verification_contract=None):
        fallback=self.planner.rule_plan(request)
        from app.qa.verification_contract import explicit_contract,validate_contract
        provided=verification_contract if verification_contract is not None else explicit_contract(request)
        if provided is not None:
            provided=validate_contract(provided)
            fallback.verification_contract=provided
        if model_adapter is None: return fallback
        verification_instruction=(
            "The representative already supplied authoritative verification criteria separately. Omit verification_contract from your response; do not recreate or alter those expected results. Focus on analysis, explicit file ownership and interface compatibility. "
            if provided is not None else
            "Propose verification_contract={version:1,requirements:[{id,description,required_categories:[normal,invalid,boundary]}],checks:[{id,kind,target,requirements,required:true,...}]}. Cover every acceptance requirement with linked required checks. "
            "For input-processing behavior provide normal, invalid-input and boundary cases with category=normal|invalid|boundary on each case (or command check). For static artifacts only, use required_categories:[normal] and explain why other categories are inapplicable in category_rationale. Never silently omit difficult requirements; unsupported checks stay unverified. Disabled checks/cases (enabled:false) are NOT_RUN, not PASS. "
            "Supported checks: python_function (function,cases:[{args,expected} or {args,error:true,error_type:optional exact exception class name}]), json_value (path array,expected), text (contains/not_contains lists or equals), file, command (argv list,expected exit_code/stdout,runtime). Use error_type when the representative requires a specific exception. "
            "For PySide6 GUI use gui: target module, factory callable returning a window, actions [{action:click|input|open_file|save_file,widget:objectName,text or path}], widgets [{widget,property,expected}], outputs optional. File dialogs are injected selections; native dialog and screen review are NOT_RUN separately. Give widgets stable objectName values. Other GUI frameworks remain unsupported/NOT_RUN. "
            "For edited images use image_output with argv and outputs:[{path,format:PNG,size:[width,height],pixels:[{xy:[x,y],rgb:[r,g,b]}]}]. Include multiple representative pixels that distinguish the requested edit, not only file existence. Use gui outputs with the same schema for GUI saves. Add required visual_review checks for visual requirements: these remain NOT_RUN pending screen review. GUI/Pillow dependencies and OS libraries must exist in the verified Docker image; never install on host. ")
        prompt=("You are the Chief of Staff for the representative. " + ALLOCATION_INSTRUCTION +
                "The only permanent organizational roles are Chief of Staff and QA. "
                "Use local Ollama by default. Set cloud_ai_required=true only when the task cannot be completed adequately with available local models or requires an unavailable cloud capability; complexity or polished writing alone is not sufficient. "
                "The Chief model itself must not call a cloud provider. When public external research is requested, the execution layer may use the configured Gemini Google Search adapter; keep that search limited to public query text and provide citations. For any other cloud-model use, specify purpose, narrow task scope, data scope, and estimated cost so the representative can approve it first. "
                "For recurring automation requests, identify schedule, source, delivery channel, and required credentials; create suitable specialists. Do not claim it is installed, scheduled, or sending until configuration and an explicit installation step are complete. "
                "Return only a valid JSON object with double quotes. core_roles must be an array of role-name strings, not objects. specialists must be an array of {name,specialty,capabilities,role}. Compose the smallest necessary temporary implementation team without generic management agents. Each role should state a clear deliverable and capability in task_assignments. "
                "Write all user-facing analysis in Korean: rationale, work_summary, work_steps, specialist names, and specialist roles. Organize the explanation by relevant work domain and keep names concise. "
                "Include library_requirements as a list of {package, version, purpose, runtime}. Use an exact version only when required and known; otherwise version must be an empty string for environment resolution. Use plain PyPI distribution names for runtime python, plain npm package names for runtime node; explain purpose in Korean. List only external packages actually needed by the chosen implementation, not standard library modules or speculative alternatives. Use [] when none are needed. Never claim installation occurred. "
                + verification_instruction +
                "Use representative-supplied expectations first. Label unverified requirements honestly; never claim model review is program execution. Do not add unauthorized output files or select host executables, Docker images, credentials or network permissions. "
                "Fields: core_roles, specialists, integration_role (one assigned developer role for code requests, empty otherwise), required_capabilities, rationale, priority, execution_mode (model provider only: local or cloud), cloud_ai_required (boolean), cloud_purpose, cloud_scope, cloud_data_scope, estimated_cost, work_summary, work_steps, acceptance_criteria (list of testable requirements), task_assignments (list of {role, deliverable, files}). Each output file must have exactly one owner. Never invent additional output files when the representative names the required files; a single-file request must stay a single file. Designers may implement UI code when assigned those files. Supporting agents deliver bounded handoff information rather than duplicating the entire program. For a small single-file program, prefer one implementation owner; add other roles only for a distinct necessary deliverable. Include a software developer for integration. "
                "execution_mode describes where AI inference runs, not where task data comes from. A locally generated program may still call an external public API or website. Keep execution_mode local whenever cloud_ai_required is false. "
                "For every instruction that names a public website/API and requests search, retrieval, or collection, explicitly state the source and preferred access method (official API first), whether this task requests a generated integration program or an immediate lookup, and that no data was actually retrieved unless an available tool executed the request.\nRepresentative instruction:\n"+request)
        try:
            prepare_adapter(model_adapter, 'analysis')
            text=model_adapter.generate(prompt)
            if getattr(model_adapter, 'last_response', {}).get('truncated'):
                fallback.rationale += ' 모델 분석 응답이 중단되어 규칙 기반 분석을 사용했습니다.'
                return fallback
            return self.planner.parse_llm(text,fallback,verification_contract=provided)
        except RunCancelled:
            raise
        except Exception as exc:
            fallback.rationale += f' 모델 분석 호출 실패({type(exc).__name__})로 규칙 기반 분석을 사용했습니다.'
            return fallback

    def synthesize_analyses(self, request, local_decision, gemini_review, model_adapter):
        local_review = {
            "library_requirements": local_decision.library_requirements,
            "work_summary": local_decision.work_summary,
            "rationale": local_decision.rationale,
            "core_roles": local_decision.core_roles,
            "specialists": local_decision.specialists,
            "integration_role": local_decision.integration_role,
            "task_assignments": local_decision.task_assignments,
            "required_capabilities": local_decision.required_capabilities,
            "verification_contract": local_decision.verification_contract,
            "work_steps": local_decision.work_steps,
        }
        prompt = (
            ALLOCATION_INSTRUCTION + "당신은 최종 업무를 결정하는 로컬 비서실장입니다. 대표 요구사항을 직접 다시 확인하고, "
            "서로 독립적으로 작성된 로컬 1차 분석과 Gemini 검토를 비교하십시오. "
            "공통으로 확인된 요구사항, 서로 다른 판단, 누락·모호함, 위험을 comparison_summary에 구분해 요약하고, "
            "근거가 있는 요구사항만 반영한 최종 작업 범위·담당 역할·수행 단계를 결정하십시오. "
            "Gemini 의견을 무조건 채택하지 말고, 최종 판단과 클라우드 실행 필요 여부는 로컬 비서실장이 결정합니다. "
            "반드시 기존 ChiefDecision JSON 필드(core_roles, specialists, required_capabilities, rationale, priority, "
            "cloud_ai_required, cloud_purpose, cloud_scope, cloud_data_scope, estimated_cost, work_summary, work_steps)에 "
            "library_requirements(필요한 외부 패키지의 package, version, purpose, runtime 목록), 요구사항별 정상·오류·경계 사례를 연결한 verification_contract와 comparison_summary 문자열을 추가한 JSON 객체만 반환하십시오.\n\n"
            f"대표 요구사항:\n{request}\n\n"
            "로컬 1차 분석(JSON):\n"
            + json.dumps(local_review, ensure_ascii=False)
            + "\n\nGemini 독립 검토 결과:\n"
            + str(gemini_review)
        )
        text = model_adapter.generate(prompt)
        match = re.search(r"\{.*\}", text or "", re.S)
        if not match:
            raise RuntimeError("로컬 비서실장의 Gemini·로컬 분석 종합 결과가 유효한 JSON이 아닙니다.")
        try:
            response = json.loads(match.group(0))
        except json.JSONDecodeError as exc:
            raise RuntimeError("로컬 비서실장의 분석 종합 JSON을 해석할 수 없습니다.") from exc
        if not isinstance(response, dict) or not str(response.get("comparison_summary", "")).strip():
            raise RuntimeError("최종 분석에 두 모델의 공통점·차이점 비교 요약이 없습니다.")
        decision = self.planner.parse_llm(text, local_decision)
        decision.cloud_ai_required = local_decision.cloud_ai_required
        decision.cloud_purpose = local_decision.cloud_purpose
        decision.cloud_scope = local_decision.cloud_scope
        decision.cloud_data_scope = local_decision.cloud_data_scope
        decision.estimated_cost = local_decision.estimated_cost
        decision.execution_mode = local_decision.execution_mode
        decision.model_reviews = {"local": local_review, "gemini": str(gemini_review)}
        decision.comparison_summary = str(response["comparison_summary"]).strip()[:4000]
        return decision

    def review_final(self, final_result, qa_status):
        if qa_status not in ("PASS","PASS_WITH_WARNING"):
            return False,"QA 통과 전 최종 보고 불가"
        if not (final_result or "").strip():
            return False,"최종 결과가 비어 있음"
        return True,"대표 보고 가능한 상태"
