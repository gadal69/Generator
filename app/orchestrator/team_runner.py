from dataclasses import dataclass, asdict
from datetime import datetime
import json
import re
from pathlib import Path
import uuid
from app.agents.base_agent import BaseAgent
from app.models.adapter_factory import create_local_adapter
from app.chief.chief_agent import ChiefAgent
from app.chief.allocation_policy import enforce_allocation, is_developer, fixed_role
from app.chief.approval_gate import ApprovalGate
from app.organization.organization_manager import OrganizationManager
from app.organization.team_composer import TeamComposer, TeamMemberSpec
from app.organization.agent_registry import AgentRegistry
from app.tools.project_file_tool import requested_output_paths, extract_agent_artifacts, is_file_creation_request, requested_filename, requested_filenames, requested_source_path
from app.chief.chief_planner import ChiefPlanner
from app.tools.dependency_preflight import inspect_request, inspect_verification_environment, requested_libraries
from app.models.gemini_search_adapter import GeminiSearchAdapter
from app.models.cloud_gateway import CloudGateway, CloudApprovalRequired
from app.qa.code_verifier import CodeVerifier
from app.orchestrator.work_contract import attach_contract, read_contract, file_key
from app.qa.artifact_validation import validate_artifacts
from app.models.generation_policy import prepare_adapter, record_response
from app.qa.verification_contract import build_contract, scope_contract, blocking_failure, required_unverified
from app.runtime.run_control import RunControl, RunCancelled, ControlledAdapter
from app.runtime.checkpoints import Checkpoints, digest
from app.orchestrator.recovery_policy import recovery_action, ReplanRequired

@dataclass
class TeamRunResult:
    team_run_id:str; project_id:str; status:str; approval_id:str|None; final_result_id:str|None; final_result:str; task_results:list; qa_reviews:list; errors:list; chief_decision:dict|None=None; team:list|None=None; cloud_approval_id:str|None=None; code_verifications:list|None=None
    def to_dict(self): return asdict(self)

class TeamRunner:
    """V1.5.0: 비서실장이 지시를 분석하고 필요 시 작업자 구성 후 고정 QA로 검토한다."""
    def __init__(self,orchestrator,adapter_factory=None,planner=None):
        self.orchestrator=orchestrator; self.database=orchestrator.database
        self.adapter_factory=adapter_factory or (lambda model: create_local_adapter(model=model))
        self.approval_gate=ApprovalGate(self.database); self.organization=OrganizationManager(self.database)
        self.registry=AgentRegistry()

    def analyze_user_request(self, request, model_override=None, adapter_factory=None, gemini_review=False, gemini_adapter=None,verification_contract=None,control=None):
        """Create the Chief's reviewable work analysis without executing the request."""
        request=(request or "").strip()
        if not request: raise ValueError("사용자 업무 지시를 입력해 주세요.")
        original_request = request
        request = self._include_source_file(request)
        old=self.adapter_factory
        if adapter_factory is not None: self.adapter_factory=adapter_factory
        model=model_override or self._default_model()
        if control:
            factory=self.adapter_factory
            self.adapter_factory=lambda name:ControlledAdapter(factory(name),control)
        try:
            if control:control.emit('analysis')
            chief = ChiefAgent(model=model)
            analysis_adapter=self.adapter_factory(model)
            local_decision = chief.analyze_request(request,analysis_adapter,verification_contract=verification_contract)
            record_response(self.database,analysis_adapter,'analysis')
            decision = local_decision
            if gemini_review:
                purpose, scope, data_scope = "Chief 요청 교차 검토", "chief_cross_review", "민감정보 및 첨부 본문을 제외한 요청문"
                conn=self.database.connect()
                existing=conn.execute("""SELECT m.approval_id,m.project_id,m.status FROM model_approvals m
                    JOIN projects p ON p.project_id=m.project_id
                    WHERE p.description=? AND m.purpose=? AND m.scope=? AND m.data_scope=?
                    ORDER BY m.created_at DESC LIMIT 1""",(original_request,purpose,scope,data_scope)).fetchone()
                conn.close()
                if existing and existing[2]=="PENDING":
                    raise CloudApprovalRequired(existing[0])
                project_id = existing[1] if existing and existing[2]=="APPROVED" else self.orchestrator.create_project(
                    "Gemini 교차 분석 승인", original_request)
                gateway=CloudGateway(self.database,self.approval_gate,
                    adapter_factory=(lambda: gemini_adapter) if gemini_adapter else None,
                    approval_required=self._cloud_approval_required())
                adapter=gateway.request_or_adapter(project_id,purpose,scope,data_scope,
                    approval_id=existing[0] if existing and existing[2]=="APPROVED" else None)
                if gemini_adapter is None:
                    readiness = GeminiSearchAdapter.dependency_status()
                    missing = []
                    if not readiness["sdk_installed"]:
                        missing.append("Gemini SDK: python -m pip install --upgrade google-genai")
                    if not readiness["api_key_ready"]:
                        missing.append("API 키: GEMINI_API_KEY 또는 GOOGLE_API_KEY 환경변수 설정")
                    if missing:
                        raise RuntimeError("Gemini 교차 분석 준비가 완료되지 않았습니다.\n- " + "\n- ".join(missing))
                review_prompt = (
                    "대표자의 업무 요구사항을 독립적으로 검토하십시오. 로컬 모델의 분석은 제공되지 않습니다. "
                    "요구사항, 누락·모호한 점, 전제, 위험, 필요한 담당 역할과 실행 단계를 한국어로 정리하십시오. "
                    "추측은 구분하고, 대표자의 요구를 임의로 확대하지 마십시오.\n\n"
                    "대표자 요구사항(민감정보 일부 마스킹, 첨부 파일 본문 제외):\n"
                    + self._public_search_request(original_request)
                )
                independent_review = adapter.generate(review_prompt)
                decision = chief.synthesize_analyses(
                    original_request, local_decision, independent_review, self.adapter_factory(model)
                )
            decision.external_data_access = ChiefPlanner._requires_external_data(original_request)
            decision.external_data_plan = ChiefPlanner._external_data_plan(original_request)
            decision.runtime_requirements = inspect_request(original_request, decision.to_dict())
            decision=enforce_allocation(decision,original_request)
            analysis_members=TeamComposer('PRJ-ANALYSIS',self.registry,model=model).compose(decision.core_roles,decision.specialists)
            try:
                self._build_plan(original_request,decision,analysis_members,source_request=original_request)
            except ValueError as plan_error:
                # Recovery happens before representative confirmation, never after approval.
                recovered=ChiefPlanner().rule_plan(original_request)
                for field in ('cloud_ai_required','cloud_purpose','cloud_scope','cloud_data_scope',
                              'estimated_cost','execution_mode','external_data_access',
                              'external_data_plan','runtime_requirements'):
                    setattr(recovered,field,getattr(decision,field))
                recovered.source='RULE_PLAN_RECOVERY'
                recovered.rationale+=' 모델의 작업 계획 오류를 감지해 원래 요청 범위로 다시 구성했습니다: '+str(plan_error)
                decision=recovered
                analysis_members=TeamComposer('PRJ-ANALYSIS',self.registry,model=model).compose(decision.core_roles,decision.specialists)
                self._build_plan(original_request,decision,analysis_members,source_request=original_request)
            decision.verification_contract=build_contract(original_request,verification_contract,decision.verification_contract)
            decision.runtime_requirements.extend(inspect_verification_environment(
                decision.verification_contract,self._load_app_config().get('verification',{})))
            for check in decision.verification_contract['checks']:
                decision.acceptance_criteria.append(f"검증 [{check['id']}] {check['kind']}: {check['target']} / 필수={check['required']}")
            return decision.to_dict()
        finally:
            self.adapter_factory=old

    def run_user_request(self,request,project_name=None,approval_mode="auto",qa_mode="agent",max_reworks=None,verbose=True,model_override=None,adapter_factory=None,analysis_override=None,gemini_code_review=False,gemini_adapter_factory=None,cloud_approval_ids=None,resume_project_id=None,verification_contract=None,control=None,resume=False):
        if max_reworks is None:
            max_reworks = self._default_max_reworks()
        if type(max_reworks) is not int or not 0<=max_reworks<=20:raise ValueError('최대 재작업 횟수는 0~20의 정수여야 합니다.')
        request=(request or "").strip()
        if not request: raise ValueError("사용자 요청이 비어 있습니다.")
        original_request = request
        if gemini_code_review and not is_file_creation_request(original_request):
            raise ValueError("Gemini 코드 QA는 코드 또는 파일 작성·수정 요청에서만 사용할 수 있습니다.")
        source_path = requested_source_path(original_request, self.orchestrator.project_file_tool.workspace_root)
        execution_request = self._include_source_file(original_request, source_path)
        if approval_mode not in {"auto","manual"}: raise ValueError("approval_mode는 auto 또는 manual이어야 합니다.")
        if qa_mode not in {"rule","agent"}: raise ValueError("qa_mode는 rule 또는 agent여야 합니다.")
        gemini_reviewer = None
        cloud_approval_ids = dict(cloud_approval_ids or {})
        team_run_id="TEAM-"+str(uuid.uuid4())[:8].upper(); started=datetime.now().isoformat(); old=self.adapter_factory
        model=model_override or self._default_model()
        control=control or RunControl()
        store=Checkpoints(self.database);state={}
        config_hash=digest({key:self._load_app_config().get(key,{}) for key in ('ai','verification')})
        source_hash=digest(execution_request)
        if resume:
            if not resume_project_id:raise ValueError('재개할 프로젝트 ID가 필요합니다.')
            saved_status,state=store.load(resume_project_id)
            if saved_status not in {'CANCELLED','ERROR'}:raise ValueError('완료·실행 중·재계획 상태는 재개할 수 없습니다. 새 분석이 필요합니다.')
            if state.get('request')!=request or state.get('model')!=model:raise ValueError('요구사항 또는 모델이 변경되었습니다. 새 분석이 필요합니다.')
            if state.get('config_hash')!=config_hash:raise ValueError('모델 또는 검증 설정이 바뀌었습니다. 새 분석이 필요합니다.')
            if state.get('source_hash')!=source_hash:raise ValueError('원본 파일이 변경되었습니다. 새 분석이 필요합니다.')
            if verification_contract is not None and digest(build_contract(request,verification_contract))!=digest(state.get('decision',{}).get('verification_contract')):raise ValueError('검증 계약이 변경되었습니다.')
            if (qa_mode,approval_mode,max_reworks,gemini_code_review)!=(state.get('qa_mode'),state.get('approval_mode'),state.get('max_reworks'),state.get('gemini_code_review')):raise ValueError('QA·승인·재작업 설정이 변경되었습니다.')
            if analysis_override and digest({k:v for k,v in analysis_override.items() if k!='source'})!=digest({k:v for k,v in state.get('decision',{}).items() if k!='source'}):
                raise ValueError('확인한 분석이 변경되었습니다. 새 실행을 시작하세요.')
            analysis_override=state.get('decision') or analysis_override
        project_name=project_name or " ".join(request.split())[:60]
        project_id=resume_project_id or self.orchestrator.create_project(project_name,request)
        if resume_project_id and not self.orchestrator.project_manager.get_project(project_id):
            raise ValueError(f"재개할 프로젝트를 찾을 수 없습니다: {project_id}")
        self.orchestrator.project_manager.change_status(project_id,"ANALYSIS")
        self._create_team_run(team_run_id,project_id,request,started)
        prior_callback=control.callback
        def on_event(event):
            event.update(project_id=project_id,team_run_id=team_run_id)
            store.event(project_id,team_run_id,event)
            if prior_callback:prior_callback(event)
        control.callback=on_event
        state.update(request=request,model=model,config_hash=config_hash,source_hash=source_hash,
                     qa_mode=qa_mode,approval_mode=approval_mode,max_reworks=max_reworks,gemini_code_review=gemini_code_review,
                     cloud_approval_ids=cloud_approval_ids)
        store.save(project_id,state)
        self._model_override=model;self._control=control
        factory=adapter_factory or old
        self.adapter_factory=lambda name:ControlledAdapter(factory(name),control)
        try:
            chief=ChiefAgent(model=model)
            chief_adapter=self.adapter_factory(model)
            if analysis_override:
                from app.chief.chief_decision import ChiefDecision
                approved=dict(analysis_override)
                # The UI confirms Chief's analysis, but does not let arbitrary fields bypass safety defaults.
                approved["source"] = "REPRESENTATIVE_CONFIRMED"
                decision=ChiefDecision(**{key:value for key,value in approved.items() if key in ChiefDecision.__dataclass_fields__})
            else:
                decision=chief.analyze_request(execution_request,chief_adapter,verification_contract=verification_contract)
            decision.verification_contract=build_contract(original_request,verification_contract,decision.verification_contract,confirmed=bool(analysis_override))
            decision.external_data_access = ChiefPlanner._requires_external_data(original_request)
            decision.external_data_plan = ChiefPlanner._external_data_plan(original_request)
            decision.runtime_requirements = inspect_request(original_request, decision.to_dict())+inspect_verification_environment(
                decision.verification_contract,self._load_app_config().get('verification',{}))
            decision=enforce_allocation(decision,original_request)
            decision.core_roles, decision.specialists = ChiefPlanner._deduplicate_roles(
                decision.core_roles, decision.specialists
            )
            approval_required = self._cloud_approval_required()
            cloud_gateway = CloudGateway(self.database, self.approval_gate,
                approval_required=approval_required)
            def approved_adapter(key, purpose, scope, data_scope, factory=None,force_approval=False):
                gateway = cloud_gateway if factory is None and not force_approval else CloudGateway(
                    self.database, self.approval_gate, adapter_factory=factory,
                    approval_required=True if force_approval else approval_required)
                return ControlledAdapter(gateway.request_or_adapter(project_id,purpose,scope,data_scope,
                    approval_id=cloud_approval_ids.get(key)),control)
            try:
                cloud_task_adapter = approved_adapter(
                    "task", decision.cloud_purpose or "외부 AI 작업 실행",
                    decision.cloud_scope or "task_execution",
                    decision.cloud_data_scope or "업무 요청 및 작업 문맥",
                ) if decision.cloud_ai_required else None
                if gemini_code_review:
                    gemini_reviewer = approved_adapter(
                        "code_qa", "생성 코드 독립 QA", "code_qa",
                        "요구사항과 생성된 코드(민감정보 마스킹)", gemini_adapter_factory,force_approval=True)
                search_adapter = approved_adapter(
                    "search", "공개 웹 자료 검색", "public_web_search",
                    "민감정보 및 첨부 본문을 제외한 공개 검색어",
                ) if decision.external_data_access else None
            except CloudApprovalRequired as pending:
                self.orchestrator.project_manager.change_status(project_id,"CLOUD_APPROVAL_PENDING")
                result=TeamRunResult(team_run_id,project_id,"CLOUD_APPROVAL_PENDING",None,None,"",[],[],[],decision.to_dict(),[],pending.approval_id,[])
                self._finish_team_run(team_run_id,result.status,"",None,None)
                self._write_artifact(project_id,result)
                store.save(project_id,state,'CLOUD_APPROVAL_PENDING')
                return result.to_dict()
            if decision.cloud_ai_required:
                missing = [item for item in decision.runtime_requirements if item.get("blocking") and not item.get("installed")]
                if missing:
                    details = "\n".join(f"- {item['name']}: {item['install_command']}" for item in missing)
                    raise RuntimeError("Gemini 실행 준비가 완료되지 않았습니다. 분석창의 설치 및 API 키 설정을 마친 뒤 다시 실행하세요.\n" + details)
            if decision.external_data_access and not (resume and state.get('research_complete')):
                missing = [item for item in decision.runtime_requirements if item.get("blocking") and not item.get("installed")]
                if missing:
                    details = "\n".join(f"- {item['name']}: {item['install_command']}" for item in missing)
                    raise RuntimeError("Gemini 외부 자료수집 준비가 완료되지 않았습니다. 아래 항목을 준비한 뒤 다시 실행하세요.\n" + details)
                search_request = self._public_search_request(original_request)
                search_prompt = (
                    "대표의 요청에 답하기 위해 공개 웹 자료를 검색하세요. 공식 기관/API 문서와 원문을 우선 사용하고, "
                    "최근 자료는 날짜를 확인하세요. 출처 링크와 확인 가능한 사실을 포함하고, 찾지 못한 내용은 추측하지 마세요. "
                    "사용자 요청에 포함된 코드·파일 내용·자격증명은 검색에 사용하지 말고, 공개 검색에 필요한 핵심 주제만 처리하세요.\n\n"
                    "공개 검색 요청:\n" + search_request
                )
                research = search_adapter.search(search_prompt)
                execution_request += (
                    "\n\n[Gemini Google Search 공개 자료수집 결과 — 출처 포함]\n"
                    + research["text"]
                )
                decision.external_data_plan["execution_status"] = "Gemini Google Search 자료수집 완료"
                decision.external_data_plan["search_sources"] = research["sources"]
                decision.external_data_plan["gemini_model"] = research["model"]
            if resume and state.get('execution_request'):
                execution_request=state['execution_request']
            decision.library_requirements=requested_libraries(original_request,decision.to_dict())
            if decision.file_creation_required or decision.library_requirements:
                from app.qa.project_environment import prepare_project
                project_settings,environment=prepare_project(
                    self.orchestrator.project_file_tool.workspace_root,project_id,decision.library_requirements,
                    self._load_app_config().get('verification',{}),control)
                self._project_environment=(project_id,project_settings)
                state['dependency_environment']=environment
                if environment['status']!='NOT_NEEDED':
                    decision.runtime_requirements.append({
                        'name':'프로젝트 Docker 패키지 환경', 'package':'project_dependencies',
                        'installed':environment['status']=='READY','blocking':False,
                        'when_needed':str(environment.get('reason') or '패키지 설치·import·의존성 검사 통과'),
                        'install_command':environment.get('remedy','Docker 내부에만 준비 완료')})
                    if not resume:
                        execution_request+='\n\n[프로젝트 의존성 환경]\n'+json.dumps({
                            'requested':environment['requested'],'resolved':environment.get('resolved',[]),
                            'status':environment['status'],'reason':environment.get('reason',''),
                            'instruction':'위 패키지/버전 범위에서 구현하세요. 호스트 설치·추가 패키지 자동 설치를 하지 마세요. 환경 미준비를 실행 성공으로 주장하지 마세요.'},ensure_ascii=False)
            state.update(decision=decision.to_dict(),execution_request=execution_request,research_complete=decision.external_data_access)
            store.save(project_id,state)
            control.emit('planning')
            self.orchestrator.project_manager.change_status(project_id,"PLANNING")
            short=project_id.replace("PRJ-","")[:8]
            fixed_members=[
                TeamMemberSpec(f"AGT-{short}-FIXED-CHIEF",project_id,"Chief","Chief of Staff",model,"FIXED",["analysis","planning","execution"],"SYSTEM",None),
                TeamMemberSpec(f"AGT-{short}-FIXED-QA",project_id,"QA","Quality Assurance",model,"FIXED",["verification","quality"],"SYSTEM",None),
            ]
            work_members=TeamComposer(project_id,self.registry,model=model).compose(decision.core_roles,decision.specialists)
            members=fixed_members+work_members
            for m in members:
                if resume and self.orchestrator.agent_manager.get_agent(m.agent_id):continue
                if cloud_task_adapter and m.agent_type != "FIXED":
                    m.model = cloud_task_adapter.model
                self.orchestrator.register_agent(m.agent_id,m.project_id,m.name,m.role,m.model,agent_type=m.agent_type,capabilities=m.capabilities,created_by=m.created_by,parent_agent_id=m.parent_agent_id)
            if decision.workflow_type == "scheduled_automation":
                result=self._automation_setup_result(team_run_id,project_id,decision,members)
                chief_member=next(member for member in fixed_members if member.name=="Chief")
                review_task=self.orchestrator.create_task(project_id,"비서실장 자동화 준비 결과",result.final_result,chief_member.agent_id)
                self.orchestrator.workflow_manager.initialize_task_status(review_task)
                review_result=self.orchestrator.result_manager.save_result(review_task,chief_member.agent_id,result.final_result)
                qa=self._qa_with_agent(project_id,review_task,review_result,qa_mode=qa_mode)
                result.qa_reviews.append(qa)
                if qa[5] not in ("PASS","PASS_WITH_WARNING"):
                    raise RuntimeError(f"자동화 준비 결과 QA 실패: {qa[5]}")
                self._finish_team_run(team_run_id,result.status,result.final_result,None,None)
                self._write_artifact(project_id,result)
                store.save(project_id,state,'COMPLETE')
                return result.to_dict()
            plan=self._build_plan(execution_request,decision,members,source_request=original_request)
            if not plan:
                chief_member=next(member for member in fixed_members if member.name=="Chief")
                plan=[{"name":"비서실장 직접 수행","description":self._direct_task_description(execution_request,decision),"agent_id":chief_member.agent_id,"depends_on":[]}]
            plan=self._chunk_large_tasks(plan, max_chars=1800)
            direct_execution = len(plan) == 1 and plan[0].get("name") == "비서실장 직접 수행"
            if resume and state.get('task_ids'):
                task_ids=state['task_ids'];plan=state['plan']
            else:
                task_ids=[]
                for idx,item in enumerate(plan):
                    tid=self.orchestrator.create_task(project_id,item['name'],item['description'],item['agent_id']);task_ids.append(tid)
                    for dep in item.get('depends_on',[]):self.orchestrator.dependency_manager.add_dependency(project_id,tid,task_ids[dep])
                    self.orchestrator.workflow_manager.initialize_task_status(tid)
                state.update(task_ids=task_ids,plan=plan,index=0)
            store.save(project_id,state)
            self.orchestrator.project_manager.change_status(project_id,"DEVELOPMENT")
            task_results=state.setdefault('task_results',[]);qa_reviews=state.setdefault('qa_reviews',[])
            errors=[];agent_outputs=state.setdefault('agent_outputs',[])
            final_task_id=state.get('final_task_id');final_result_id=state.get('final_result_id');final_text=state.get('final_text','')
            for idx,tid in enumerate(task_ids):
                if idx<state.get('index',0):continue
                current=state.get('current') or tid;reworks=state.get('reworks',0);history=state.setdefault('recovery_history',[])
                while True:
                    state.update(index=idx,current=current,reworks=reworks);store.save(project_id,state)
                    control.emit('implementation',task_id=current,task_name=plan[idx]['name'],completed=idx,total=len(task_ids),attempt=reworks)
                    self.orchestrator.workflow_manager.update_waiting_tasks(project_id);task=self.orchestrator.task_manager.get_task(current)
                    adapter=prepare_adapter(cloud_task_adapter or self.adapter_factory(self._model_for_task(current)),'implementation',reworks)
                    result_id=state.get('pending_result_id')
                    if not result_id:
                        result_id=self.orchestrator.execute_task(project_id,current,model_adapter=adapter)
                    if not result_id:raise RuntimeError(f'Task 실행 준비 실패: {current}')
                    state['pending_result_id']=result_id;store.save(project_id,state)
                    result=self.orchestrator.result_manager.get_result(result_id)
                    agent_info=self.orchestrator.agent_manager.get_agent(result[2])
                    gemini_review = None
                    if (
                        gemini_reviewer
                        and decision.file_creation_required
                        and agent_info
                        and self._is_developer(agent_info[2], agent_info[3])
                    ):
                        reviewed_output, gemini_review = self._review_artifacts_with_gemini(
                            task, result[3], gemini_reviewer
                        )
                        conn = self.database.connect()
                        conn.execute("UPDATE results SET summary=? WHERE result_id=?", (reviewed_output, result_id))
                        conn.commit(); conn.close()
                        result=self.orchestrator.result_manager.get_result(result_id)
                    # QA is a fixed role and always reviews every completed work output.
                    control.emit('qa',task_id=current,completed=idx,total=len(task_ids))
                    qa=self._qa_with_agent(project_id,current,result_id,gemini_review=gemini_review,qa_mode=qa_mode)
                    from app.orchestrator.qa_audit import record
                    record(state,'LOCAL_QA',tid,result_id,qa[5],qa[7],
                           self.orchestrator.result_manager.get_result(result_id)[3],review=gemini_review)
                    store.save(project_id,state)
                    qa_reviews.append(qa)
                    if qa[5] in ("PASS","PASS_WITH_WARNING"):
                        if current != tid:
                            # Subsequent tasks must receive the accepted revision, not the failed original.
                            with self.database.connection() as conn:
                                for later in task_ids[idx+1:]:
                                    conn.execute('UPDATE task_dependencies SET depends_on_task_id=? WHERE task_id=? AND depends_on_task_id=?',
                                                 (current,later,tid))
                        # QA Agent 자체의 검증 Task는 실행 이력에는 남기되
                        # task_results는 실제 업무 산출 Task만 집계한다.
                        if agent_info and not self._is_qa_agent(agent_info[2],agent_info[3]):
                            task_results.append({"task_id":current,"result_id":result_id,"status":qa[5],"agent_id":result[2]})
                            agent_outputs.append({"name":agent_info[2],"role":agent_info[3],"output":result[3],"files":(read_contract(task[4]) or {}).get('files', []),"task_id":current,"root_task_id":tid,"result_id":result_id})
                        if idx==len(task_ids)-1: final_task_id=current; final_result_id=result_id; final_text=result[3]
                        state.update(index=idx+1,current=None,reworks=0,pending_result_id=None,recovery_history=[],
                            final_task_id=final_task_id,final_result_id=final_result_id,final_text=final_text)
                        store.save(project_id,state);control.emit('task_complete',completed=idx+1,total=len(task_ids),task_id=current)
                        break
                    if reworks>=max_reworks:
                        qa_issues = self._normalize_qa_issues(qa[8])
                        raise ReplanRequired(
                            f"QA 실패 후 최대 재작업 횟수 초과: {current} ({qa[5]}). "
                            f"검토 요약: {qa[7] or '없음'}; 이슈: {', '.join(qa_issues) or '상세 이슈 없음'}"
                        )
                    action=recovery_action(reworks,max_reworks,history,self._normalize_qa_issues(qa[8]),qa[7])
                    history.append(action)
                    control.emit('rework',task_id=current,attempt=reworks+1,strategy=action['action'])
                    reworks+=1
                    rework_instruction = self._build_rework_instruction(
                        self.orchestrator.task_manager.get_task(tid)[4], str(qa[7])+'\n[복구 전략] '+action['reason'], self._normalize_qa_issues(qa[8])
                    )
                    rw=self.orchestrator.qa_manager.request_rework(qa[0],result[2],rework_instruction)
                    self.orchestrator.dependency_manager.add_dependency(project_id,rw,current); self.orchestrator.workflow_manager.initialize_task_status(rw); current=rw
                    state.update(current=current,reworks=reworks,pending_result_id=None,recovery_history=history);store.save(project_id,state)
            control.emit('integration',completed=len(task_ids),total=len(task_ids))
            self._verify_integration(project_id,agent_outputs,decision.verification_contract,qa_mode,max_reworks,control,qa_reviews,state,store,gemini_reviewer)
            if agent_outputs:
                last=agent_outputs[-1];final_task_id=last.get('task_id',final_task_id);final_result_id=last.get('result_id',final_result_id);final_text=last['output']
            state.update(agent_outputs=agent_outputs,qa_reviews=qa_reviews,final_task_id=final_task_id,final_result_id=final_result_id,final_text=final_text)
            store.save(project_id,state)
            self.orchestrator.project_manager.change_status(project_id,"FINAL_REVIEW")
            ok,msg=chief.review_final(final_text,qa_reviews[-1][5] if qa_reviews else "FAIL")
            if not ok: raise RuntimeError(msg)
            if direct_execution:
                agent_outputs.append({"name":"Chief","role":"Chief of Staff","output":final_text})
            control.emit('saving')
            written_files=self._write_requested_files(
                project_id, original_request, agent_outputs, decision.file_creation_required,
                fallback_output=final_text, source_path=source_path,
            )
            code_verifications=CodeVerifier(self.database).verify_files(project_id,written_files,original_request,contract=decision.verification_contract,settings=self._verification_settings(project_id),control=control)
            if blocking_failure(code_verifications):
                from app.qa.approval_evidence import make_evidence
                state['final_verification']=make_evidence(self.database,project_id,final_result_id,decision.verification_contract,
                    code_verifications,written_files,self.orchestrator.project_file_tool.workspace_root)
                store.save(project_id,state)
                raise RuntimeError('저장된 파일의 검증에 실패하여 최종 승인을 중단했습니다.')
            if written_files:
                file_summary="\n\n생성 파일:\n"+"\n".join(f"- {item['path']}" for item in written_files)
                for check in code_verifications:
                    if check['type']=='COVERAGE':
                        evidence=json.loads(check['details'])
                        file_summary+=f"\n요구사항 검증 [{check['check_id']}]: {check['status']} — "+evidence.get('reason','')
                        if evidence.get('categories'):file_summary+=' / 입력 유형: '+json.dumps(evidence['categories'],ensure_ascii=False)
                    if check['type']=='DEPENDENCIES':
                        evidence=json.loads(check['details'])
                        file_summary+='\n패키지 준비 미완료: '+evidence.get('reason','')+'\n조치: '+evidence.get('remedy','')
                    if check['type']!='FUNCTIONAL_TEST':
                        continue
                    file_summary+=f"\n함수 예제 검사 [{check['path']}]: {check['status']}"
                    try:
                        evidence=json.loads(check['details'])
                        for row in evidence.get('cases',[]):
                            case=row['case']
                            file_summary+=f"\n- {case['function']}({', '.join(repr(arg) for arg in case['args'])}): {row['status']}"
                        if check['status']=='NOT_RUN':
                            file_summary+='\n- 미검증 사유: '+str(evidence.get('reason',''))
                    except (ValueError,TypeError,KeyError):
                        pass
                passed_checks=[item for item in code_verifications if item['type']=='FUNCTIONAL_TEST' and item['status']=='PASS']
                passed_files=len({file_key(item['path']) for item in passed_checks})
                file_summary+=f'\n지원 함수 예제 검사: {passed_files}개 파일에서 {len(passed_checks)}개 항목 통과.'
                if required_unverified(code_verifications):
                    file_summary+='\n필수 검증에 누락·미실행 항목이 있어 최종 승인이 차단됩니다. 기준 확인 및 재검증이 필요합니다.'
                    if approval_mode=='auto':
                        approval_mode='manual'
                final_text += file_summary
                if final_result_id:
                    conn=self.database.connect()
                    conn.execute("UPDATE results SET summary=COALESCE(summary,'') || ? WHERE result_id=?",(file_summary,final_result_id))
                    conn.commit(); conn.close()
            if decision.file_creation_required:
                from app.qa.approval_evidence import make_evidence
                state['final_verification']=make_evidence(self.database,project_id,final_result_id,decision.verification_contract,
                    code_verifications,written_files,self.orchestrator.project_file_tool.workspace_root)
                store.save(project_id,state)
            control.check()
            approval_id=self.orchestrator.request_approval(project_id,final_task_id,final_result_id)
            if approval_mode=="auto": self.orchestrator.approve(approval_id,"APPROVED","V1.5.0 자동 승인: 비서실장 최종검토 + QA 통과"); status="APPROVED"
            else: self.orchestrator.project_manager.change_status(project_id,"APPROVAL_PENDING"); status="PENDING_APPROVAL"
            result=TeamRunResult(team_run_id,project_id,status,approval_id,final_result_id,final_text,task_results,qa_reviews,errors,decision.to_dict(),[asdict(m) for m in members],None,code_verifications)
            self._finish_team_run(team_run_id,status,final_text,approval_id,None); self._write_artifact(project_id,result);store.save(project_id,state,'COMPLETE')
            try:
                if control.callback:control.callback({'stage':'complete'})
            except Exception:
                pass  # Completion notification must not reverse an already committed approval.
            return result.to_dict()
        except RunCancelled as exc:
            self.orchestrator.project_manager.change_status(project_id,'CANCELLED')
            self._finish_team_run(team_run_id,'CANCELLED','',None,str(exc));store.save(project_id,state,'CANCELLED')
            cancelled=TeamRunResult(team_run_id,project_id,'CANCELLED',None,None,'',state.get('task_results',[]),state.get('qa_reviews',[]),[],state.get('decision'),[])
            self._write_artifact(project_id,cancelled)
            return cancelled.to_dict()
        except Exception as exc:
            stopped='REPLAN_REQUIRED' if isinstance(exc,ReplanRequired) else 'BLOCKED'
            if isinstance(exc,ReplanRequired):
                from app.orchestrator.qa_audit import stop_notice
                state['replan_recommendation']=stop_notice(state,exc)
                notice=state['replan_recommendation']
                exc.args=(str(exc)+'\n다음 조치:\n'+'\n'.join(notice['next_steps'])+'\n기록: qa_recovery.json',)
            self.orchestrator.project_manager.change_status(project_id,stopped)
            store.save(project_id,state,'REPLAN_REQUIRED' if isinstance(exc,ReplanRequired) else 'ERROR')
            if isinstance(exc,ReplanRequired):
                try:
                    if control.callback:control.callback({'stage':'replan_required',**state['replan_recommendation']})
                except Exception:pass  # Notification cannot erase the persisted stop reason.
            self._finish_team_run(team_run_id,"ERROR","",None,str(exc))
            # Failed runs also get a readable project artifact, not only a DB row.
            failure=TeamRunResult(team_run_id,project_id,'ERROR',None,None,'',
                locals().get('task_results', []),locals().get('qa_reviews', []),
                [str(exc)],locals().get('decision').to_dict() if locals().get('decision') else None,
                [asdict(m) for m in locals().get('members', [])])
            self._write_artifact(project_id,failure)
            raise
        finally:
            self.adapter_factory=old
            self._model_override=None
            control.callback=prior_callback
            self._control=None
            self._project_environment=None

    def _verification_settings(self,project_id):
        environment=getattr(self,'_project_environment',None)
        if environment and environment[0]==project_id:return environment[1]
        return self._load_app_config().get('verification',{})

    def _verify_integration(self,project_id,outputs,contract,qa_mode,limit,control,reviews,state,store,gemini_reviewer=None):
        """Repair only the owner of a failing project check, with a persisted budget."""
        from app.qa.project_verifier import ProjectVerifier
        if not outputs:return
        attempts=state.setdefault('integration_attempts',{})
        histories=state.setdefault('integration_recovery_history',{})
        while True:
            artifacts=[];owners={}
            for index,output in enumerate(outputs):
                if not output.get('files'):continue
                for item in extract_agent_artifacts(output['output'],output['files'][0]):
                    key=file_key(item['path'])
                    if key in owners:raise ReplanRequired('통합 산출 파일의 담당자가 중복되었습니다: '+key)
                    owners[key]=index;artifacts.append(item)
            rows=ProjectVerifier(self._verification_settings(project_id),control).verify(artifacts,contract)
            from app.orchestrator.qa_audit import record
            record(state,'INTEGRATION','<project>',None,
                'FAIL' if blocking_failure(rows) else 'NOT_RUN' if required_unverified(rows) else 'PASS',
                '실제 검증 항목별 관측 결과. 검토 의견으로 대체하지 않습니다.',
                json.dumps(artifacts,ensure_ascii=False,sort_keys=True),checks=rows)
            state['integration_checks']=rows;store.save(project_id,state)
            failures=[row for row in rows if row['status']=='FAIL' and row.get('required',True) and row['type']!='COVERAGE']
            if not failures:
                if blocking_failure(rows):raise ReplanRequired('요구사항 검증 범위를 다시 계획해야 합니다.')
                return
            failure=failures[0];key=file_key(failure['path']);index=owners.get(key)
            if index is None:raise ReplanRequired('실패한 검증 대상의 담당자가 없습니다: '+key)
            output=outputs[index];count=attempts.get(key,0)
            pending=state.get('integration_pending')
            if count>=limit and not pending:raise ReplanRequired('통합 검증 재작업 한도에 도달했습니다. 인터페이스와 검증 계약 재분석이 필요합니다: '+key)
            if not pending:
                task=self.orchestrator.task_manager.get_task(output['task_id'])
                result=self.orchestrator.result_manager.get_result(output['result_id'])
                from app.orchestrator.integration_context import repair_context
                packet=repair_context(artifacts,failure['path'],failures,contract)
                summary=json.dumps(packet['observations'],ensure_ascii=False)
                history=histories.setdefault(key,[])
                previous_rejection=history[-1].get('qa_rejection') if history else None
                action=recovery_action(count,limit,history,['INTEGRATION_FAIL'],summary)
                packet['recovery_strategy']=action
                if previous_rejection:packet['previous_repair_rejection']=previous_rejection
                if packet.get('current_file'):
                    packet['current_file']['content']=None
                    packet['current_file']['rewrite_from_requirements']=True
                    packet['instruction']+=' 기존 구현은 결과 이력에 보존되어 있습니다. 실패한 코드를 복사하지 말고 요구사항·인터페이스·검증 입력으로부터 간결한 전체 파일을 작성하세요. 닫는 </artifact> 태그를 반드시 포함하세요.'
                history.append(action)
                qa=self.orchestrator.qa_manager.review(project_id,task[0],result[0],'AGT-QA-001','FAIL',0.0,summary,['INTEGRATION_FAIL'])
                reviews.append(qa)
                instruction=self._build_rework_instruction(task[4],summary,['INTEGRATION_FAIL'])
                instruction+='\n\n[통합 수정 근거 — 대표 기준과 대상 파일 유지]\n'+json.dumps(packet,ensure_ascii=False)
                retry=self.orchestrator.qa_manager.request_rework(qa[0],result[2],instruction)
                self.orchestrator.workflow_manager.initialize_task_status(retry)
                pending={'key':key,'task_id':retry,'result_id':None};state['integration_pending']=pending
                attempts[key]=count+1;store.save(project_id,state)
            control.emit('integration_rework',task_id=pending['task_id'],attempt=attempts[key],target=key)
            if not pending['result_id']:
                adapter=prepare_adapter(self.adapter_factory(self._model_for_task(pending['task_id'])),'implementation',attempts[key])
                pending['result_id']=self.orchestrator.execute_task(project_id,pending['task_id'],model_adapter=adapter)
                if not pending['result_id']:raise ReplanRequired('통합 재작업을 실행할 수 없습니다.')
                store.save(project_id,state)
            gemini_review=None
            if gemini_reviewer:
                task=self.orchestrator.task_manager.get_task(pending['task_id'])
                result=self.orchestrator.result_manager.get_result(pending['result_id'])
                revised,gemini_review=self._review_artifacts_with_gemini(task,result[3],gemini_reviewer)
                with self.database.connection() as conn:conn.execute('UPDATE results SET summary=? WHERE result_id=?',(revised,pending['result_id']))
            qa=self._qa_with_agent(project_id,pending['task_id'],pending['result_id'],qa_mode=qa_mode,gemini_review=gemini_review);reviews.append(qa)
            record(state,'REPAIR_QA',key,pending['result_id'],qa[5],qa[7],
                   self.orchestrator.result_manager.get_result(pending['result_id'])[3],review=gemini_review)
            if qa[5] in ('PASS','PASS_WITH_WARNING'):
                result=self.orchestrator.result_manager.get_result(pending['result_id'])
                previous_task=output['task_id']
                output.update(output=result[3],task_id=pending['task_id'],result_id=pending['result_id'])
                for record in state.get('task_results',[]):
                    if record['task_id']==previous_task:
                        record.update(task_id=pending['task_id'],result_id=pending['result_id'],status=qa[5])
            else:
                history=histories.setdefault(key,[])
                if history:history[-1]['qa_rejection']={'summary':str(qa[7])[:2000],'issues':self._normalize_qa_issues(qa[8])}
            state['integration_pending']=None;store.save(project_id,state)

    def _split_prompt_into_chunks(self, text, max_chars=1800):
        """큰 작업 프롬프트를 로컬 모델이 안전하게 처리할 수 있는 작은 단위로 분할한다."""
        normal = (text or "").strip()
        if not normal or len(normal) <= max_chars:
            return [normal]

        if "\n\n" in normal:
            sections = [part.strip() for part in normal.split("\n\n") if part.strip()]
            chunks = []
            current = ""
            for section in sections:
                if len(current) + len(section) + 2 <= max_chars:
                    current += ("\n\n" if current else "") + section
                else:
                    if current.strip():
                        chunks.append(current.strip())
                    current = section
            if current.strip():
                chunks.append(current.strip())
            if chunks:
                return chunks
        sentences = re.split(r"(?<=[.!?])\s+", normal)
        chunks = []
        current = ""
        for sentence in sentences:
            candidate = f"{current}\n{sentence}" if current else sentence
            if len(candidate) <= max_chars:
                current = candidate
            else:
                if current.strip():
                    chunks.append(current.strip())
                current = sentence
        if current.strip():
            chunks.append(current.strip())
        return [c for c in chunks if c.strip()]

    def _cloud_approval_required(self):
        try:
            cfg=json.loads((Path(__file__).resolve().parents[2]/"config"/"app_config.json").read_text(encoding="utf-8"))
            return bool(cfg.get("ai",{}).get("cloud_requires_representative_approval",True))
        except (OSError,json.JSONDecodeError):
            return True

    def _chunk_large_tasks(self, plan, max_chars=1800):
        """작업 설명이 과도하게 길면 작은 sub-task로 분할한다."""
        if not plan:
            return plan
        expanded = []
        for item in plan:
            description = str(item.get("description", "") or "")
            if "[파일 산출 지시]" in description or read_contract(description):
                expanded.append(item)
                continue
            chunks = self._split_prompt_into_chunks(description, max_chars=max_chars)
            if len(chunks) <= 1:
                expanded.append(item)
                continue
            start_index = len(expanded)
            for idx, chunk in enumerate(chunks):
                chunked = dict(item)
                chunked["name"] = f"{item.get('name', 'Task')} ({idx + 1}/{len(chunks)})"
                chunked["description"] = chunk
                if idx == 0:
                    chunked["depends_on"] = list(item.get("depends_on", []))
                else:
                    chunked["depends_on"] = [start_index + idx - 1]
                expanded.append(chunked)
        return expanded

    def _build_plan(self,request,decision,members,source_request=None):
        by_role={m.role.lower():m for m in members}; by_name={m.name.lower():m for m in members}; plan=[]
        def member(role):
            for k,v in by_role.items():
                if role.lower() in k: return v
            return next((v for k,v in by_name.items() if role.lower() in k),None)
        ordered=[]
        # Chief decision order is respected; QA is always terminal when selected.
        for role in decision.core_roles:
            m=member(role)
            if m and m.agent_type != "FIXED" and m not in ordered: ordered.append(m)
        specialists=[m for m in members if m.agent_type=="DYNAMIC_SPECIALIST"]
        for m in specialists:
            if m not in ordered: ordered.append(m)
        allocations={m.agent_id: [] for m in ordered}
        integrator=None
        if decision.file_creation_required:
            if any(fixed_role(m.name) or fixed_role(m.role) for m in ordered):
                raise ValueError('비서실장과 QA는 프로그램 구현 담당자가 될 수 없습니다.')
            candidates=[m for m in ordered if decision.integration_role.casefold() in {m.name.casefold(),m.role.casefold()}
                        and self._is_developer(m.name,m.role)]
            if len(candidates)!=1:raise ValueError('개발 구현·통합 담당자를 한 명 지정해야 합니다.')
            integrator=candidates[0]
        deliverables={};file_deliverables={}
        owned={}
        requested_outputs = requested_output_paths(source_request or request) or ['main.py']
        for assignment in decision.task_assignments:
            role_key=TeamComposer._role_key(assignment.get('role', ''))
            matches=[m for m in ordered if role_key in {TeamComposer._role_key(m.name),TeamComposer._role_key(m.role)}]
            if len(matches) != 1:
                raise ValueError(f"작업 계약의 담당 역할을 명확히 지정해야 합니다: {assignment.get('role')}")
            owner=matches[0]
            deliverables[owner.agent_id]=assignment.get('deliverable') or owner.role+' 담당 산출물'
            for path in assignment.get('files', []):
                key=file_key(path)
                if key in owned:
                    raise ValueError(f'산출 파일 소유자가 중복되었습니다: {path}')
                if requested_output_paths(source_request or request) and key not in {file_key(p) for p in requested_outputs}:
                    raise ValueError(f'대표 요청에 없는 파일이 작업 계약에 포함되었습니다: {path}')
                owned[key]=owner.agent_id
                allocations[owner.agent_id].append(path)
                file_deliverables[key]=assignment.get('deliverable') or owner.role+' 담당 파일 구현'
        if decision.file_creation_required:
            developer=integrator
            for path in requested_outputs:
                if file_key(path) not in owned:
                    if developer is None:
                        raise ValueError(f'구현 담당자가 배정되지 않았습니다: {path}')
                    allocations[developer.agent_id].append(path)
                    owned[file_key(path)]=developer.agent_id
            # Component owners finish before the designated integration owner.
            ordered.sort(key=lambda m:(m.agent_id==integrator.agent_id,bool(allocations[m.agent_id]),self._is_developer(m.name,m.role)))
        for m in ordered:
            description=f"대표 요청을 {m.role} 관점에서 수행하십시오.\n\n대표 요청:\n{request}"
            if integrator and m.agent_id==integrator.agent_id:
                description+='\n\n[통합 담당] 다른 담당자의 선행 산출물을 확인하고 파일 간 인터페이스·호출 경로를 통합하세요. 배정 파일만 수정하고 다른 담당자 파일의 결함은 대상 파일과 수정 내용을 명시하세요. 최종 통합 검증과 독립 QA의 판정을 따라야 합니다.'
            if decision.external_data_access:
                data_plan = decision.external_data_plan or {}
                description += (
                    "\n\n[외부 데이터 접근 요구]\n"
                    f"요청 데이터 원천: {data_plan.get('source') or '대표 요청에 지정된 사이트/API'}\n"
                    f"접근 방법: {data_plan.get('method') or '공식 API 우선'}\n"
                    f"요구 유형: {data_plan.get('operation') or '외부 데이터 조회'}\n"
                    "실행 전에 Gemini Google Search를 사용해 공개 자료를 수집합니다. 아래에 첨부된 수집 결과의 출처를 유지하고, "
                    "검색으로 확인되지 않은 데이터를 만들어내지 마십시오. "
                    "연동 프로그램 작성 요청이면 실제 API 호출 코드를 작성하고, 공개 API 키는 환경변수로 읽으십시오. "
                    "즉시 조회 요청이면 확인된 사실과 출처 링크를 결과에 명시하십시오."
                )
            task_descriptions = []
            if decision.file_creation_required and allocations[m.agent_id]:
                requested_outputs = allocations[m.agent_id]
                generators = {
                    ".pptx": ("python-pptx", "from pptx import Presentation", "PowerPoint"),
                    ".xlsx": ("openpyxl", "from openpyxl import Workbook", "Excel"),
                    ".docx": ("python-docx", "from docx import Document", "Word"),
                    ".pdf": ("reportlab", "from reportlab.pdfgen import canvas", "PDF"),
                }
                for filename in requested_outputs:
                    generator = generators.get(Path(filename).suffix.casefold())
                    artifact_filename = (
                        f"{Path(filename).stem}_generator.py" if generator else filename
                    )
                    file_description = description + (
                        "\n\n[파일 산출 지시]\n"
                        f"이번 Task에서 작성할 대상은 '{filename}' 하나뿐입니다. 다른 요청 파일은 작성하지 마십시오. "
                        "최종 응답에는 설명, 요약, Markdown 코드 펜스를 쓰지 말고 artifact 블록 하나만 출력하십시오. "
                        "블록 안에는 해당 파일의 완전하고 실행 가능한 내용만 넣고, 안내 문장이나 자리표시자, 생략 부호, TODO를 넣지 마십시오. "
                        "기능은 요구사항을 충족하는 최소 구현으로 작성하고, 불필요한 설명과 주석은 생략해 출력 한도 안에 파일 전체를 제출하십시오.\n"
                        f'<artifact path="{artifact_filename}">파일 전체 내용</artifact>'
                    )
                    if generator:
                        package, import_line, file_kind = generator
                        file_description += (
                            f"\n\n요청 파일은 {filename} {file_kind} 문서입니다. 실제 바이너리 파일 대신 "
                            f"{package}로 해당 문서를 생성하는 완전한 Python 스크립트 "
                            f"'{artifact_filename}'를 작성하십시오. '{import_line}'을 사용하고, "
                            f"설치 안내와 '{filename}'에 저장하는 코드를 포함하십시오. "
                            "스크립트는 요청된 문서 내용을 실제로 생성해야 합니다."
                        )
                    file_description=attach_contract(file_description,m.role,[artifact_filename],
                        file_deliverables.get(file_key(filename),deliverables.get(m.agent_id,'배정 파일 구현 및 선행 산출물 통합')),decision.acceptance_criteria,
                        verification=scope_contract(decision.verification_contract,[artifact_filename]))
                    task_descriptions.append((f"{m.name} Task: {artifact_filename}", file_description))
            elif integrator and m.agent_id==integrator.agent_id:
                description+='\n\n[통합 검토 지침] 별도 파일 소유권이 없으므로 선행 파일들을 함께 검토한 통합 보고서를 작성하세요. 인터페이스 문제와 담당 파일별 수정 지시를 명확히 쓰세요. 파일이나 artifact를 중복 생성하지 마세요.'
                description=attach_contract(description,m.role,[],'선행 산출물 통합 검토 및 담당 파일별 수정 지시',[])
                task_descriptions.append((f"{m.name} Integration Review",description))
            elif decision.file_creation_required and self._is_qa_agent(m.name,m.role):
                description += "\n\n이전 개발 에이전트의 파일 산출물을 검토하고 요구사항 누락, 명백한 문법 오류, 경로 오류를 확인하십시오. 파일 산출 블록은 직접 만들지 마십시오."
                task_descriptions.append((f"{m.name} Task", description))
            elif decision.file_creation_required:
                description += (
                    "\n\n[역할 지침]\n"
                    "이번 작업에는 파일 소유권이 배정되지 않았습니다. 다음 구현 담당자가 사용할 역할별 인계 자료를 1500자 이내로 작성하세요. "
                    "필요한 UI 구조·인터페이스·구현 지침과 짧은 코드 예시는 작성할 수 있습니다. 전체 프로그램을 중복 작성하거나 파일 생성 완료를 주장하지 마세요. "
                    "다른 담당자가 구현할 기능의 완성 여부를 본인 작업의 합격 조건으로 삼지 마세요."
                )
                description=attach_contract(description,m.role,[],deliverables.get(m.agent_id,m.role+' 관점의 구현 인계 자료'),[])
                task_descriptions.append((f"{m.name} Task", description))
            else:
                task_descriptions.append((f"{m.name} Task", description))
            for task_name, task_description in task_descriptions:
                plan.append({
                    "name": task_name,
                    "description": task_description,
                    "agent_id": m.agent_id,
                    "depends_on": list(range(len(plan))),
                })
        # Make QA terminal if present; if not, add no hidden agent.
        return plan

    @staticmethod
    def _direct_task_description(request, decision):
        steps="\n".join(f"- {step}" for step in decision.work_steps) or "- 요청을 이해하고 필요한 결과물을 직접 작성"
        return ("당신은 고정 비서실장입니다. 대표의 요청을 직접 수행해 최종 산출물을 작성하세요.\n"
                f"업무 분석: {decision.work_summary or decision.rationale}\n수행 단계:\n{steps}\n\n대표 지시:\n{request}")

    @staticmethod
    def _is_qa_agent(name,role):
        text=f"{name or ''} {role or ''}".casefold()
        return any(term in text for term in ("quality assurance","qa reviewer","qa agent","품질 보증","검증 에이전트")) or str(name or "").strip().casefold()=="qa"

    @staticmethod
    def _is_developer(name,role):
        return is_developer(name,role)

    @staticmethod
    def _build_rework_instruction(task_description, qa_summary, qa_issues):
        original_description = str(task_description or "")
        rework_match = re.match(
            r"원래 작업의 요구사항과 대상 파일을 유지해 다시 수행하세요\."
            r".*?\n\n원래 작업 지시:\n(.*?)(?=\n\nQA 검토 내용:)",
            original_description,
            flags=re.S,
        )
        if rework_match:
            original_description = rework_match.group(1).strip()
        original_description = re.sub(
            r"\n*\[재작업 필수 출력 형식\].*$",
            "",
            original_description,
            flags=re.S,
        )
        artifact_paths = list(dict.fromkeys(
            match.group(1).strip()
            for match in re.finditer(
                r"<artifact\s+path=[\"']([^\"']+)[\"']\s*>",
                original_description,
                re.I,
            )
        ))
        task_output_paths = list(dict.fromkeys(
            match.group(1).strip()
            for match in re.finditer(
                r"\[이 Task의 산출 파일:\s*([^\]]+)\]",
                original_description,
                re.I,
            )
        ))
        task_target = re.search(
            r"이번 Task에서 작성할 대상은\s*[\"']([^\"']+)[\"']\s*하나뿐입니다",
            original_description,
            re.I,
        )
        canonical_task_outputs = task_output_paths
        if not canonical_task_outputs and task_target:
            declared_path = task_target.group(1).strip()
            if any(path.casefold() == declared_path.casefold() for path in artifact_paths):
                canonical_task_outputs = [declared_path]
        if canonical_task_outputs:
            artifact_paths = canonical_task_outputs
        elif "[파일 산출 지시]" in original_description and not artifact_paths:
            artifact_paths = requested_filenames(original_description) or ["main.py"]
        artifact_requirement = ""
        if "[파일 산출 지시]" in original_description:
            current_output_paths = {path.casefold() for path in artifact_paths}

            def normalize_artifact_template(match):
                path = match.group(1).strip()
                if canonical_task_outputs and path.casefold() not in current_output_paths:
                    return ""
                return f"[이 Task의 산출 파일: {path}]"

            original_description = re.sub(
                r"<artifact\s+path=[\"']([^\"']+)[\"']\s*>.*?</artifact\s*>",
                normalize_artifact_template,
                original_description,
                flags=re.I | re.S,
            )
            if canonical_task_outputs:
                original_description = re.sub(
                    r"\[이 Task의 산출 파일:\s*[^\]]+\]\s*",
                    "",
                    original_description,
                    flags=re.I,
                ).rstrip()
                original_description += "\n" + "\n".join(
                    f"[이 Task의 산출 파일: {path}]" for path in artifact_paths
                )
            blocks = "\n".join(
                f'<artifact path="{path}">파일 전체 내용</artifact>'
                for path in artifact_paths
            )
            artifact_requirement = (
                "\n\n[재작업 필수 출력 형식]\n"
                "응답에는 설명, 요약, Markdown 코드 펜스를 넣지 말고 아래 각 artifact에 해당하는 블록만 출력하십시오. "
                "각 블록의 경로를 유지하고, 블록 안에는 수정된 파일의 완전하고 실행 가능한 내용만 넣으십시오. "
                "예시의 '파일 전체 내용' 문구를 복사하지 마십시오. "
                "요구사항을 만족하는 최소 구현으로 작성해 출력 한도 안에 각 블록의 닫는 태그까지 완성하십시오.\n"
                + blocks
            )
        diagnosis=str(qa_summary or '')+' '+' '.join(qa_issues)
        recovery=''
        if '금지' in diagnosis and any(word in diagnosis for word in ('eval','exec')):
            recovery=(
                '\n[원인별 수정 전략]\n금지된 eval/exec 호출은 래퍼 함수나 이름 변경으로 우회하지 말고 완전히 제거하세요. '
                '프로젝트의 입력 형식을 해석하는 안전한 라이브러리나 허용된 구문 처리 방식으로 구현하세요. '
                '금지 조건을 임의로 완화하거나 타협했다고 설명하는 구현은 다시 실패합니다.\n'
            )
        elif 'FUNCTIONAL_TEST_FAIL' in diagnosis:
            recovery='\n[원인별 수정 전략]\n검증 기록의 입력·예상 결과·실제 결과를 비교해 계산 로직을 수정하세요. 테스트 예상값을 바꾸거나 입력별 답을 하드코딩하지 마세요. 수정 후 모든 예제를 다시 통과해야 합니다.\n'
        return (
            "원래 작업의 요구사항과 대상 파일을 유지해 다시 수행하세요. 이전 결과를 수정한 전체 산출물을 제출하세요.\n\n"
            f"원래 작업 지시:\n{original_description}{artifact_requirement}\n\n"
            f"QA 검토 내용:\n{qa_summary}\n\nQA 이슈: {', '.join(qa_issues) or '상세 이슈 없음'}\n\n"
            +recovery+
            "[작성 유의사항]\n"
            "출력이 중간에 끊기지 않도록 불필요한 서론/인사말을 생략하고, 완전하고 동작 가능한 핵심 내용 및 산출물을 집중적으로 작성하십시오."
        )

    def _write_requested_files(self,project_id,request,agent_outputs,required,fallback_output=None,source_path=None):
        file_requested = required or is_file_creation_request(request)
        if not file_requested:
            return []
        fallback=requested_filename(request) or "main.py"
        written=[]

        def save_artifacts(output, paths=None):
            for artifact in extract_agent_artifacts(output,paths[0] if paths else fallback):
                if not artifact["content"].strip():
                    continue
                if source_path:
                    artifact["path"] = Path(source_path).name
                    result = self.orchestrator.project_file_tool.replace_existing_file(
                        source_path, artifact["content"], project_id
                    )
                else:
                    result=self.orchestrator.tool_registry.execute(
                        "write_project_file", project_id=project_id,
                        path=artifact["path"], content=artifact["content"],
                    )
                written.append(result)

        for agent in agent_outputs:
            if not agent.get('files') and ('files' in agent or not self._is_developer(agent["name"],agent["role"])):
                continue
            save_artifacts(agent["output"],agent.get('files'))
        if not written and fallback_output:
            save_artifacts(fallback_output)
        if file_requested and not written:
            raise RuntimeError("개발 에이전트가 파일 내용을 산출하지 않아 파일을 저장하지 못했습니다. 파일 생성 단계는 완료로 표시하지 않았습니다.")
        return written

    def _include_source_file(self, request, source_path=None):
        source_path = source_path or requested_source_path(request, self.orchestrator.project_file_tool.workspace_root)
        if not source_path:
            return request
        source = self.orchestrator.project_file_tool.read_existing_file(source_path)
        suffix = Path(source["path"]).suffix.lstrip(".")
        return (
            f"{request}\n\n"
            "[수정 대상 기존 파일의 전체 내용 — 이 내용을 바탕으로 실제 수정본을 작성할 것]\n"
            f"파일 경로: {source['path']}\n"
            f"```{suffix}\n{source['content']}\n```\n"
            "기존 파일 수정 요청이면 설명이나 계획만 출력하지 말고, 수정된 전체 파일을 artifact로 출력하세요."
        )

    def _automation_setup_result(self,run_id,project_id,decision,members):
        project_dir=Path(__file__).resolve().parents[2]/"projects"/project_id
        project_dir.mkdir(parents=True,exist_ok=True)
        self.orchestrator.project_manager.change_status(project_id,"AUTOMATION_SETUP_REQUIRED")
        setup={"workflow_type":decision.workflow_type,"automation":decision.automation,
               "team":[asdict(member) for member in members],
               "program":"app/automations/g2b_daily_email_digest.py",
               "setup_steps":["조달청 OpenAPI 서비스키 발급 및 환경변수 설정","수신자와 SMTP 설정","수동 1회 실행 후 결과 확인","Windows 작업 스케줄러에 매일 예약 등록"]}
        (project_dir/"automation_setup.json").write_text(json.dumps(setup,ensure_ascii=False,indent=2),encoding="utf-8")
        text=("자동화 프로그램 준비 상태: 설정 필요\n"
              "- 수집: 조달청 나라장터 입찰공고정보서비스 공식 OpenAPI\n"
              f"- 검색어: {', '.join(decision.automation.get('search_terms', []))}\n"
              f"- 예약: 매일 {decision.automation.get('schedule',{}).get('time','08:00')} (Asia/Seoul)\n"
              "- 전달: SMTP 이메일\n"
              "- 실행 코드: app/automations/g2b_daily_email_digest.py\n"
              "- 이 실행 환경에서 아직 API 키, 수신자, SMTP 계정이 설정되지 않아 예약 등록과 메일 발송은 하지 않았습니다.\n"
              f"- 프로젝트별 설정 요약: projects/{project_id}/automation_setup.json")
        return TeamRunResult(run_id,project_id,"AUTOMATION_SETUP_REQUIRED",None,None,text,[],[],[],decision.to_dict(),[asdict(member) for member in members],None)

    @staticmethod
    def _redact_for_external_review(value):
        text = str(value or "")
        text = re.sub(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", "[PRIVATE_KEY_REDACTED]", text, flags=re.S)
        text = re.sub(r"(?i)AIza[0-9A-Za-z_-]{30,}", "[API_KEY_REDACTED]", text)
        text = re.sub(
            r"(?i)(\b(?:api[_-]?key|secret|token|password|client[_-]?secret)\b\s*[:=]\s*)([\"'])(.{8,}?)(\2)",
            r"\1\2[REDACTED]\4", text,
        )
        text = re.sub(r"\b\d{6}-[1-4]\d{6}\b", "[PERSONAL_ID_REDACTED]", text)
        text = re.sub(r"\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b", "[EMAIL_REDACTED]", text)
        text = re.sub(r"(?<!\d)(?:\+82[- ]?)?0\d{1,2}[- ]?\d{3,4}[- ]?\d{4}(?!\d)", "[PHONE_REDACTED]", text)
        return text

    def _review_artifacts_with_gemini(self, task, output, adapter):
        artifacts = extract_agent_artifacts(output, requested_filename(task[4]) or "main.py")
        if not artifacts:
            raise RuntimeError("Gemini 코드 QA를 요청했지만 검토할 파일 산출물이 없습니다.")
        safe_task = self._redact_for_external_review(task[4])
        safe_artifacts = [
            {"path": item["path"], "content": self._redact_for_external_review(item["content"])}
            for item in artifacts
        ]
        prompt = (
            "당신은 코드 QA 및 개선 전문가입니다. 아래 대표 요구사항과 개발자가 작성한 전체 코드 파일을 검토하십시오. "
            "요구사항 충족, 논리 오류, 보안, 예외 처리, 성능, 가독성, 유지보수성을 검토하고 필요한 개선을 실제 코드에 반영하십시오. "
            "불필요한 기능 변경은 하지 말고 기존 동작과 공개 인터페이스를 유지하십시오. "
            "각 원본 경로에 대해 개선 후 전체 파일 내용을 반드시 반환하십시오. 요약이나 diff만 반환하지 마십시오. "
            "응답은 다른 설명 없이 유효한 JSON 하나만 반환하십시오. 형식: "
            '{"verdict":"PASS 또는 FAIL","summary":"검토 및 개선 요약","issues":["남은 문제"],'
            '"optimized_artifacts":[{"path":"원본 경로","content":"개선된 파일 전체 내용"}]}. '
            "FAIL은 개선 후에도 요구사항 또는 중요한 문제가 남았음을 뜻합니다.\n\n"
            f"대표 요구사항 및 작업 지시:\n{safe_task}\n\n"
            "개발자 산출물(JSON):\n"
            + json.dumps(safe_artifacts, ensure_ascii=False)
        )
        if len(prompt) > 120_000:
            raise RuntimeError("Gemini 코드 QA 입력이 너무 큽니다(최대 120,000자). 파일을 나눠 검토해 주세요.")
        response_text = str(adapter.generate(prompt) or "").strip()
        json_start = response_text.find("{")
        if json_start < 0:
            raise RuntimeError("Gemini 코드 QA가 구조화된 검토 결과를 반환하지 않았습니다.")
        try:
            payload, _ = json.JSONDecoder().raw_decode(response_text[json_start:])
        except json.JSONDecodeError as exc:
            raise RuntimeError("Gemini 코드 QA 결과 JSON을 해석할 수 없습니다.") from exc
        if not isinstance(payload, dict):
            raise RuntimeError("Gemini 코드 QA 결과 형식이 올바르지 않습니다.")
        verdict = str(payload.get("verdict", "")).strip().upper()
        summary = str(payload.get("summary", "")).strip()
        issues = payload.get("issues", [])
        revised = payload.get("optimized_artifacts")
        if verdict not in {"PASS", "FAIL"} or not summary or not isinstance(issues, list) or not isinstance(revised, list):
            raise RuntimeError("Gemini 코드 QA 결과에 판정, 요약, 이슈 목록 또는 수정된 파일이 없습니다.")
        original_by_path = {item["path"]: item for item in artifacts}
        revised_by_path = {}
        for item in revised:
            if not isinstance(item, dict):
                raise RuntimeError("Gemini 코드 QA 수정 파일 형식이 올바르지 않습니다.")
            path = str(item.get("path") or "")
            content = item.get("content")
            if path not in original_by_path or path in revised_by_path or not isinstance(content, str) or not content.strip():
                raise RuntimeError("Gemini 코드 QA가 원본과 다른 경로 또는 빈 파일을 반환했습니다.")
            revised_by_path[path] = content
        if set(revised_by_path) != set(original_by_path):
            raise RuntimeError("Gemini 코드 QA가 원본 파일 전체의 수정본을 반환하지 않았습니다.")
        review = {
            "verdict": verdict,
            "summary": summary[:4000],
            "issues": [str(issue).strip() for issue in issues if str(issue).strip()][:30],
        }
        output_lines = [
            "[Gemini 코드 QA]",
            f"판정: {verdict}",
            f"검토·개선 요약: {review['summary']}",
        ]
        if review["issues"]:
            output_lines.append("남은 이슈: " + "; ".join(review["issues"]))
        for path, content in revised_by_path.items():
            output_lines.extend(["", f'<artifact path="{path}">', content, "</artifact>"])
        return "\n".join(output_lines), review

    def _qa_with_agent(self,project_id,task_id,result_id,gemini_review=None,qa_mode='agent'):
        result=self.orchestrator.result_manager.get_result(result_id)
        task=self.orchestrator.task_manager.get_task(task_id)
        model=self._model_for_project(project_id)
        conn=self.database.connect()
        qa_row=conn.execute("SELECT agent_id FROM agents WHERE project_id=? AND name='QA' AND agent_type='FIXED' ORDER BY created_at LIMIT 1",(project_id,)).fetchone()
        conn.close()
        qa_agent_id=qa_row[0] if qa_row else "AGT-QA-001"
        deterministic_issue = self._artifact_precheck(task, result[3])
        if deterministic_issue:
            return self.orchestrator.qa_manager.review(
                project_id, task_id, result_id, qa_agent_id, "FAIL", 0.0,
                "자동 산출물 검사에서 실패했습니다: " + deterministic_issue,
                ["ARTIFACT_VALIDATION_FAIL"],
            )
        contract=read_contract(task[4])
        functional_checks=[]
        if contract and contract.get('files'):
            artifacts=extract_agent_artifacts(result[3],contract['files'][0])
            verification=contract.get('verification') or {}
            if verification:
                functional_checks=CodeVerifier(self.database).verify_contract(project_id,artifacts,verification,
                    settings=self._verification_settings(project_id),control=getattr(self,'_control',None),partial=True)
            elif contract.get('tests'):
                functional_checks=CodeVerifier(self.database).verify_artifacts(project_id,artifacts,contract['tests'],task_id,result_id)
            failures=[item for item in functional_checks if item['status']=='FAIL' and item.get('required',True)]
            if failures:
                return self.orchestrator.qa_manager.review(project_id,task_id,result_id,qa_agent_id,
                    'FAIL',0.0,'프로젝트 검증 계약 실패:\n'+'\n'.join(item['details'] for item in failures),['FUNCTIONAL_TEST_FAIL'])
        if qa_mode == 'rule':
            if gemini_review and gemini_review['verdict'] == 'FAIL':
                return self.orchestrator.qa_manager.review(project_id,task_id,result_id,qa_agent_id,
                    'FAIL',0.0,gemini_review['summary'],['GEMINI_CODE_REVIEW_FAIL'])
            return self.orchestrator.qa_manager.review(project_id,task_id,result_id,qa_agent_id,
                'PASS_WITH_WARNING',80.0,'정적 산출물 검사 통과. 함수 예제 검사 상태: '
                +(', '.join(item['status'] for item in functional_checks) or 'NOT_RUN')
                +'. 프로그램 전체 실행·GUI 검증은 미실행입니다.',
                ['APPLICATION_EXECUTION_NOT_RUN'])
        has_file_instruction = "[파일 산출 지시]" in (task[4] or "")
        if has_file_instruction:
            file_qa_note = (
                "이 Task에는 [파일 산출 지시]가 포함되어 있습니다. "
                "artifact 블록 존재, 파일 전체 내용의 완성도, 요청 기능의 충족 여부를 엄격히 검토하십시오. "
                "자리표시자, mock 데이터로 실 API 연동을 대신한 경우, 미완성 코드, 요구사항 누락은 FAIL입니다."
                "요구된 일반 기능 없이 검증 입력의 답만 나열한 구현은 FAIL입니다. "
                "일반 입력을 처리하는 실제 로직과 명시된 금지 조건을 확인하고, 요구사항을 임의로 완화하지 마세요."
            )
        else:
            file_qa_note = (
                "이 Task는 소스코드 파일 직접 생성이 아닌 해당 역할(기획, 설계, 문서화, 분석 등)의 전문 산출물 작성 작업입니다. "
                "코드 artifact 블록 유무로 평가하지 말고, 해당 역할의 산출물 내용이 요청 및 역할 목적에 부합하고 완성도 있는지 검토하십시오."
            )
        from app.orchestrator.integration_context import review_task_description
        review_description=review_task_description(task[4])
        prompt=("당신은 독립 QA Agent입니다. 결과를 검증하고 VERDICT: PASS / PASS_WITH_WARNING / FAIL 중 하나를 반드시 출력하십시오.\n"
                f"{file_qa_note}\n\n"
                +f"Task:{task[3]}\nDescription:{review_description}\nResult:\n{result[3]}")
        if contract:
            prompt += ('\n\n이번 검토는 작업 계약에 명시된 담당 범위만 평가합니다. 파일이 배정되지 않은 인계 작업에 '
                       '전체 프로그램이나 다른 담당자의 파일 구현을 요구하지 마세요. '
                       '파일이 배정된 경우 배정된 파일과 요구사항의 일치 여부를 평가하세요.\n'
                       +json.dumps(contract,ensure_ascii=False))
        prompt += '\n판정과 실제 결함·수정 지시만 10줄 이내로 쓰고, 전체 코드나 장문의 요구사항을 반복하지 마세요.'
        if functional_checks:
            prompt+='\n\n신뢰된 함수 예제 검증 결과(전체 프로그램·GUI 실행과 구분):\n'+json.dumps(functional_checks,ensure_ascii=False)
        if gemini_review:
            prompt += (
                "\n\nGemini 코드 QA 결과도 참고하되 그대로 신뢰하지 말고 코드와 요구사항을 독립적으로 확인하십시오.\n"
                f"Gemini 판정: {gemini_review['verdict']}\nGemini 요약: {gemini_review['summary']}\n"
                f"Gemini 이슈: {'; '.join(gemini_review['issues']) or '없음'}"
            )
        qa_adapter=prepare_adapter(self.adapter_factory(model),'qa')
        text=BaseAgent(qa_agent_id,"QA","Quality Assurance",model).execute(prompt,qa_adapter)
        record_response(self.database,qa_adapter,'qa',project_id,task_id)
        import re
        m=re.search(r"VERDICT\s*:\s*(PASS_WITH_WARNING|PASS|FAIL)",text or "",re.I)
        status=m.group(1).upper() if m else "FAIL"
        if '[MODEL_RESPONSE_INCOMPLETE]' in (text or ''):
            status='FAIL'
        score={"PASS":100.0,"PASS_WITH_WARNING":80.0,"FAIL":40.0}[status]
        issues=[] if status!="FAIL" else ["QA_AGENT_FAIL"]
        if status=='PASS' and any(item['status']=='NOT_RUN' for item in functional_checks):
            status='PASS_WITH_WARNING';score=80.0;issues.append('FUNCTIONAL_TEST_NOT_RUN')
        summary=(text or "")[:4000]
        if gemini_review:
            summary=(f"Gemini 코드 QA {gemini_review['verdict']}: {gemini_review['summary']}\n\n"+summary)[:4000]
            if gemini_review["verdict"] == "FAIL":
                status="FAIL"; score=40.0
                issues.append("GEMINI_CODE_REVIEW_FAIL")
                issues.extend(gemini_review["issues"])
        return self.orchestrator.qa_manager.review(project_id,task_id,result_id,qa_agent_id,status,score,summary,issues)

    @staticmethod
    def _normalize_qa_issues(value):
        """Normalize DB JSON, lists, and legacy plain strings without splitting text into characters."""
        if value is None:
            return []
        if isinstance(value, (list, tuple, set)):
            return [str(item).strip() for item in value if str(item).strip()]
        if isinstance(value, str):
            raw = value.strip()
            if not raw:
                return []
            try:
                decoded = json.loads(raw)
                if isinstance(decoded, list):
                    return [str(item).strip() for item in decoded if str(item).strip()]
            except (json.JSONDecodeError, TypeError):
                pass
            return [raw]
        return [str(value)]

    @staticmethod
    def _public_search_request(request):
        """Keep Gemini search input focused on public query text, excluding attached-file analysis."""
        value = str(request or "")
        for marker in ("\n\n[첨부 이미지 VLM 분석]", "\n\n[수정 대상 기존 파일", "\n\n[수정 대상 파일"):
            if marker in value:
                value = value.split(marker, 1)[0]
        value = re.sub(r"(?i)AIza[0-9A-Za-z_-]{30,}", "[API_KEY_REDACTED]", value)
        value = re.sub(r"(?i)(api[_-]?key|service[_-]?key|token|password)\s*[:=]\s*\S+", r"\1=[REDACTED]", value)
        value = re.sub(r"\b\d{6}-[1-4]\d{6}\b", "[PERSONAL_ID_REDACTED]", value)
        value = re.sub(r"\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b", "[EMAIL_REDACTED]", value)
        value = re.sub(r"(?<!\d)(?:\+82[- ]?)?0\d{1,2}[- ]?\d{3,4}[- ]?\d{4}(?!\d)", "[PHONE_REDACTED]", value)
        return value[:4000].strip()

    def _artifact_precheck(self, task, output):
        """Reject missing/placeholder artifacts and statically parse Python without executing it."""
        description = str(task[4] or "")
        if not (output or '').strip():
            return '작업 응답이 비어 있습니다.'
        if '[MODEL_RESPONSE_INCOMPLETE]' in output:
            return '모델이 응답 중단을 보고했습니다. 완전한 산출물로 재생성해야 합니다.'
        contract=read_contract(description)
        if "[파일 산출 지시]" not in description and not (contract and contract.get('files')):
            return ''
        expected=(contract or {}).get('files', [])
        if re.search(r'<artifact\s+path=',output,re.I) and len(re.findall(r'<artifact\s+path=',output,re.I)) != len(re.findall(r'</artifact\s*>',output,re.I)):
            return 'artifact 블록의 닫는 태그가 없어 응답이 중간에 잘렸습니다.'
        artifacts = extract_agent_artifacts(output, expected[0] if len(expected)==1 else requested_filename(description) or "main.py")
        if not artifacts:
            if re.search(r"<artifact\s+path=[\"'][^\"']+[\"']\s*>", output or "", re.I):
                return "artifact 블록의 닫는 태그가 없어 응답이 중간에 잘렸습니다."
            return "파일 산출 지시가 있으나 artifact 블록이 없습니다."
        if not expected:
            expected=re.findall(r'<artifact\s+path=[\"\']([^\"\']+)[\"\']\s*>',description,re.I)
        issue=validate_artifacts(artifacts,expected,description)
        if issue:
            return issue
        for artifact in artifacts:
            content = str(artifact.get("content") or "").strip()
            if not content:
                return f"{artifact.get('path', '산출 파일')} 내용이 비어 있습니다."
            normalized = re.sub(r"\s+", "", content).casefold()
            placeholders = ("여기에실행가능한파일전체내용을작성", "여기에코드를작성", "todo:구현", "pass#todo", "your_api_key_here")
            if any(token in normalized for token in placeholders):
                return f"{artifact.get('path', '산출 파일')}에 실제 구현 대신 자리표시자가 있습니다."
            if str(artifact.get("path", "")).lower().endswith(".py"):
                try:
                    import ast
                    ast.parse(content, filename=str(artifact.get("path", "<artifact>")))
                except SyntaxError as exc:
                    return f"{artifact.get('path', 'Python 파일')} 문법 오류: {exc.msg} (줄 {exc.lineno})"
        document_contracts = {
            ".pptx": ("pptx", "Presentation", "python-pptx", "PPTX"),
            ".xlsx": ("openpyxl", "Workbook", "openpyxl", "XLSX"),
            ".docx": ("docx", "Document", "python-docx", "DOCX"),
            ".pdf": ("reportlab", "Canvas", "reportlab", "PDF"),
        }
        requested_outputs = requested_filenames(description)
        declared_outputs = {
            match.group(1).strip().casefold()
            for match in re.finditer(
                r"<artifact\s+path=[\"']([^\"']+)[\"']\s*>",
                description,
                re.I,
            )
        }
        declared_outputs.update(
            match.group(1).strip().casefold()
            for match in re.finditer(
                r"\[이 Task의 산출 파일:\s*([^\]]+)\]",
                description,
                re.I,
            )
        )
        for filename in requested_outputs:
            suffix = Path(filename).suffix.casefold()
            contract = document_contracts.get(suffix)
            if not contract:
                continue
            module, factory, package, document_kind = contract
            generator_path = f"{Path(filename).stem}_generator.py".casefold()
            if generator_path not in declared_outputs:
                continue
            generator = next(
                (
                    str(item.get("content") or "")
                    for item in artifacts
                    if str(item.get("path", "")).casefold() == generator_path
                ),
                "",
            )
            if not generator:
                return f"{document_kind} 생성 스크립트 '{generator_path}' 산출물이 없습니다."
            tree = ast.parse(generator)
            module_imported = any(
                (
                    isinstance(node, ast.Import)
                    and any(alias.name == module for alias in node.names)
                )
                or (
                    isinstance(node, ast.ImportFrom)
                    and bool(node.module)
                    and (node.module == module or node.module.startswith(module + "."))
                )
                for node in ast.walk(tree)
            )
            factory_names = {factory}
            factory_names.update(
                alias.asname or alias.name
                for node in ast.walk(tree)
                if isinstance(node, ast.ImportFrom)
                and node.module
                and (node.module == module or node.module.startswith(module + "."))
                for alias in node.names
                if alias.name == factory
            )
            factory_called = any(
                isinstance(node, ast.Call)
                and (
                    (isinstance(node.func, ast.Name) and node.func.id in factory_names)
                    or (isinstance(node.func, ast.Attribute) and node.func.attr == factory)
                )
                for node in ast.walk(tree)
            )
            save_called = any(
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "save"
                for node in ast.walk(tree)
            )
            if not module_imported:
                return f"{document_kind} 생성 스크립트에 {package} import이 없습니다."
            if not factory_called:
                return f"{document_kind} 생성 스크립트에 {factory} 객체 생성이 없습니다."
            if not save_called:
                return f"{document_kind} 생성 스크립트에 문서 저장 호출이 없습니다."
            if not re.search(rf"pip\s+install\s+{re.escape(package)}\b", generator, re.I):
                return f"{document_kind} 생성 스크립트에 {package} 설치 안내가 없습니다."
        return ""
    def _model_for_task(self,task_id):
        t=self.orchestrator.task_manager.get_task(task_id); a=self.orchestrator.agent_manager.get_agent(t[2]) if t else None; return a[5] if a and a[5] else self._default_model()
    def _model_for_project(self,project_id):
        conn=self.database.connect(); r=conn.execute("SELECT model FROM agents WHERE project_id=? AND model IS NOT NULL ORDER BY created_at LIMIT 1",(project_id,)).fetchone(); conn.close(); return r[0] if r else self._default_model()
    def _load_app_config(self):
        try:
            return json.loads((Path(__file__).resolve().parents[2]/"config/app_config.json").read_text(encoding="utf-8"))
        except Exception:
            return {}
    def _default_max_reworks(self):
        return int(self._load_app_config().get("ai", {}).get("max_reworks", 2))
    def _default_model(self):
        if hasattr(self,"_model_override") and self._model_override: return self._model_override
        return self._load_app_config().get("ai", {}).get("default_model", "gemma4:12b")
    def _create_team_run(self,run_id,project_id,request,started):
        conn=self.database.connect(); conn.execute("INSERT INTO team_runs(team_run_id,project_id,user_request,status,started_at) VALUES(?,?,?,?,?)",(run_id,project_id,request,"RUNNING",started)); conn.commit(); conn.close()
    def _finish_team_run(self,run_id,status,final_result,approval_id,error):
        conn=self.database.connect(); conn.execute("UPDATE team_runs SET status=?,final_result=?,approval_id=?,finished_at=?,error_message=? WHERE team_run_id=?",(status,final_result,approval_id,datetime.now().isoformat(),error,run_id)); conn.commit(); conn.close()
    def _write_artifact(self,project_id,result):
        p=Path(self.orchestrator.project_file_tool.workspace_root)/"projects"/project_id; p.mkdir(parents=True,exist_ok=True); (p/"team_run_result.json").write_text(json.dumps(result.to_dict(),ensure_ascii=False,indent=2),encoding="utf-8"); (p/"final_result.txt").write_text(result.final_result or "",encoding="utf-8")
        status,state=Checkpoints(self.database).load(project_id)
        audit={'status':result.status,'checkpoint_status':status,'roles':{'local_qa':'요구사항·코드 검토','gemini':'옵션+외부 전송 승인 시 추가 코드 검토','execution':'Docker 실행 관측. FAIL·필수 NOT_RUN은 승인 차단'},
               'history':state.get('qa_audit',[]),'stop':state.get('replan_recommendation')}
        (p/'qa_recovery.json').write_text(json.dumps(audit,ensure_ascii=False,indent=2),encoding='utf-8')
        if audit['stop']:
            (p/'final_result.txt').write_text('중단 사유: '+audit['stop']['reason']+'\n다음 조치:\n'+'\n'.join(audit['stop']['next_steps'])+'\n기록: qa_recovery.json',encoding='utf-8')
