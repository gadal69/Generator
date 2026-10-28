from pathlib import Path

from app.orchestrator.project_manager import ProjectManager
from app.orchestrator.agent_manager import AgentManager
from app.orchestrator.result_manager import ResultManager
from app.errors.error_manager import ErrorManager
from app.errors.retry_manager import RetryManager
from app.task_manager.task_manager import TaskManager

from app.agents.base_agent import BaseAgent
from app.models.adapter_factory import create_local_adapter
from app.qa.qa_manager import QAManager
from app.approval.approval_manager import ApprovalManager
from app.knowledge.knowledge_manager import KnowledgeManager
from app.tools.tool_registry import ToolRegistry
from app.tools.project_file_tool import ProjectFileTool
from app.runtime.agent_runtime import AgentRuntime

from app.task_manager.dependency_manager import DependencyManager
from app.workflow.workflow_manager import WorkflowManager

from app.context.context_manager import ContextManager

class Orchestrator:

    def __init__(self, database):

        self.database = database

        self.project_manager = ProjectManager(database)
        self.agent_manager = AgentManager(database)
        self.task_manager = TaskManager(database)        

        self.dependency_manager = DependencyManager(database)

        self.workflow_manager = WorkflowManager(
            database=database,
            task_manager=self.task_manager,
            dependency_manager=self.dependency_manager
        )

        self.result_manager = ResultManager(database)
        self.context_manager = ContextManager(
            database
        )
        self.error_manager = ErrorManager(
            database
        )
        self.retry_manager = RetryManager()
        self.qa_manager = QAManager(
            database=database,
            task_manager=self.task_manager,
            result_manager=self.result_manager
        )
        self.approval_manager = ApprovalManager(
            database=database,
            qa_manager=self.qa_manager
        )
        self.knowledge_manager = KnowledgeManager(database)
        self.tool_registry = ToolRegistry(database)
        self.project_file_tool = ProjectFileTool(Path(__file__).resolve().parents[2])
        self.tool_registry.register(
            "write_project_file", self.project_file_tool.write_file,
            "Write generated files to projects/<project_id>/artifacts; keep any requested relative subdirectory inside that folder.",
        )
        self.agent_runtime = AgentRuntime(
            database=database,
            orchestrator=self,
            tool_registry=self.tool_registry,
            knowledge_manager=self.knowledge_manager
        )

        from app.orchestrator.team_runner import TeamRunner
        self.team_runner = TeamRunner(self)
        self.chief = __import__("app.chief.chief_agent", fromlist=["ChiefAgent"]).ChiefAgent(model=self._default_model())
        self.approval_gate = __import__("app.chief.approval_gate", fromlist=["ApprovalGate"]).ApprovalGate(database)

    def _default_model(self):
        try:
            import json
            from pathlib import Path
            cfg = json.loads((Path(__file__).resolve().parents[2] / "config" / "app_config.json").read_text(encoding="utf-8"))
            return cfg["ai"]["default_model"]
        except Exception:
            return "gemma4:12b"

    def run_user_request(self, request, **kwargs):
        return self.team_runner.run_user_request(request, **kwargs)
    def can_execute_task(self, task_id):

        return self.dependency_manager.can_execute(
            task_id
        )

    def create_project(
        self,
        name,
        description=""
    ):

        project_id = self.project_manager.create_project(
            name=name,
            description=description
        )

        return project_id

    def register_agent(
        self, agent_id, project_id, name, role, model,
        agent_type="CORE", capabilities=None, created_by="AGT-CHIEF-001", parent_agent_id=None
    ):
        self.agent_manager.register_agent(
            agent_id=agent_id, project_id=project_id, name=name, role=role, model=model,
            agent_type=agent_type, capabilities=capabilities, created_by=created_by, parent_agent_id=parent_agent_id
        )

    def create_task(
        self,
        project_id,
        name,
        description="",
        agent_id=None
    ):

        task_id = self.task_manager.create_task(
            project_id=project_id,
            name=name,
            description=description,
            agent_id=agent_id
        )

        # Task 생성 직후 Workflow 상태를 초기화한다.
        self.workflow_manager.initialize_task_status(
            task_id
        )

        return task_id

    def update_workflow(self, project_id):

        updated_tasks = self.workflow_manager.update_waiting_tasks(
            project_id
        )

        return updated_tasks

    def execute_pm_task(
        self,
        project_id,
        task_id,
        task_description
    ):
        """Compatibility entry point routed through the current assigned-agent workflow."""
        return self.execute_task(
            project_id=project_id,
            task_id=task_id,
            task_description=task_description,
        )

    def qa_task(self, project_id, task_id, result_id=None, reviewer_agent_id="AGT-QA-001",
                 status=None, score=None, summary="", issues=None):
        """완료 Result를 QA하고 필요 시 REWORK를 요청할 수 있게 한다."""
        return self.qa_manager.review(
            project_id=project_id,
            task_id=task_id,
            result_id=result_id,
            reviewer_agent_id=reviewer_agent_id,
            status=status,
            score=score,
            summary=summary,
            issues=issues
        )

    def request_approval(self, project_id, task_id, result_id=None,
                         approver_agent_id="AGT-CHIEF-001"):
        return self.approval_manager.request(
            project_id=project_id,
            task_id=task_id,
            result_id=result_id,
            approver_agent_id=approver_agent_id
        )

    def approve(self, approval_id, status, comment=""):
        return self.approval_manager.decide(
            approval_id=approval_id,
            status=status,
            comment=comment
        )

    def retry_task(
        self,
        project_id,
        task_id,
        error_id
    ):

        print("\n==============================")
        print("TASK RETRY")
        print("==============================")

        error = self.error_manager.get_error(
            error_id
        )

        if error is None:
            raise ValueError(
                f"Error를 찾을 수 없습니다: {error_id}"
            )

        current_retry_count = error[9]

        print(
            "Current Retry Count :",
            current_retry_count
        )

        # Retry 가능 여부 확인
        if not self.retry_manager.can_retry(
            current_retry_count
        ):

            print(
                "Retry 불가 → Chief Escalation"
            )

            return {
                "status": "ESCALATE",
                "error_id": error_id,
                "retry_count": current_retry_count
            }

        # Retry 횟수 증가
        next_retry_count = (
            self.retry_manager.get_next_retry_count(
                current_retry_count
            )
        )

        strategy = (
            self.retry_manager.get_retry_strategy(
                current_retry_count
            )
        )

        print(
            "Next Retry Count :",
            next_retry_count
        )

        print(
            "Strategy :",
            strategy
        )

        # DB Retry Count 업데이트
        self.error_manager.update_retry_count(
            error_id=error_id,
            retry_count=next_retry_count
        )

        print(
            "Retry Count DB 업데이트 완료"
        )

        return {
            "status": "RETRY",
            "error_id": error_id,
            "retry_count": next_retry_count,
            "strategy": strategy
        }

    def execute_retry(
        self,
        project_id,
        task_id,
        error_id
    ):

        print("\n==============================")
        print("AUTOMATIC RETRY PROCESS")
        print("==============================")

        max_retry = self.retry_manager.MAX_RETRIES

        while True:

            retry_result = self.retry_task(
                project_id=project_id,
                task_id=task_id,
                error_id=error_id
            )

            # --------------------------------
            # Retry 불가
            # --------------------------------

            if retry_result["status"] != "RETRY":

                print("\n==============================")
                print("CHIEF ESCALATION")
                print("==============================")

                print(
                    "Error ID :",
                    error_id
                )

                print(
                    "Retry Count :",
                    retry_result["retry_count"]
                )

                print(
                    "Status : ESCALATE_TO_CHIEF"
                )

                return {
                    "status": "ESCALATE_TO_CHIEF",
                    "error_id": error_id,
                    "retry_count": retry_result["retry_count"]
                }

            # --------------------------------
            # Retry 정보
            # --------------------------------

            retry_count = retry_result["retry_count"]
            strategy = retry_result["strategy"]

            print("\n==============================")
            print(
                f"RETRY {retry_count}"
            )
            print("==============================")

            print(
                "Retry Count :",
                retry_count
            )

            print(
                "Strategy :",
                strategy
            )

            # --------------------------------
            # 실제 Task 재실행
            # --------------------------------

            try:

                result = self.execute_task(
                    project_id=project_id,
                    task_id=task_id,
                    retry_parent_error_id=error_id
                )

                # Retry 성공 시 원본 Error 및 Retry Chain을 RESOLVED 처리한다.
                self._resolve_retry_chain(error_id)

                print("\n==============================")
                print("RETRY SUCCESS")
                print("==============================")

                return {
                    "status": "RETRY_SUCCESS",
                    "error_id": error_id,
                    "retry_count": retry_count,
                    "result": result
                }

            except Exception as e:

                print("\n==============================")
                print("RETRY FAILED")
                print("==============================")

                print(
                    "Retry Count :",
                    retry_count
                )

                print(
                    "Error :",
                    str(e)
                )

                # 다음 while에서
                # retry_task()가 retry_count를 확인하고
                # 다음 Retry 또는 Chief Escalation을 결정합니다.

                continue


    def _resolve_retry_chain(self, root_error_id):
        """Retry 성공 시 해당 Retry Chain의 미해결 Error를 RESOLVED 처리한다."""
        chain = self.error_manager.get_retry_chain(root_error_id)

        for error in chain:
            if error[8] != "RESOLVED":
                self.error_manager.update_status(
                    error_id=error[0],
                    status="RESOLVED"
                )

    def execute_task(
        self,
        project_id,
        task_id,
        task_description=None,
        retry_parent_error_id=None,
        model_adapter=None,
    ):

        print("\n==============================")
        print("GENERIC TASK EXECUTION")
        print("==============================")

        # ==========================================
        # Task 조회
        # ==========================================

        task = self.task_manager.get_task(
            task_id
        )

        if task is None:
            raise ValueError(
                f"Task를 찾을 수 없습니다: {task_id}"
            )

        task_project_id = task[1]
        task_agent_id = task[2]
        task_name = task[3]
        task_db_description = task[4]
        task_status = task[5]

        # ==========================================
        # Project 확인
        # ==========================================

        if task_project_id != project_id:

            raise ValueError(
                "Task가 현재 Project에 속하지 않습니다."
            )

        # ==========================================
        # Agent 확인
        # ==========================================

        if not task_agent_id:

            raise ValueError(
                "Task에 Agent가 지정되어 있지 않습니다."
            )

        agent_info = self.agent_manager.get_agent(
            task_agent_id
        )

        if agent_info is None:

            raise ValueError(
                f"Agent를 찾을 수 없습니다: {task_agent_id}"
            )

        agent_id = agent_info[0]
        agent_name = agent_info[2]
        agent_role = agent_info[3]
        agent_model = agent_info[5]

        print(f"Project : {project_id}")
        print(f"Task    : {task_id}")
        print(f"Task    : {task_name}")
        print(f"Agent   : {agent_id}")
        print(f"Role    : {agent_role}")
        print(f"Model   : {agent_model}")

        # ==========================================
        # Dependency 확인
        # ==========================================

        can_execute = self.can_execute_task(
            task_id
        )

        if not can_execute:

            self.task_manager.update_status(
                task_id=task_id,
                status="WAITING"
            )

            print("\n==============================")
            print("TASK WAITING")
            print("==============================")

            print(
                "선행 Task가 완료되지 않아 "
                "현재 Task를 실행할 수 없습니다."
            )

            return None

        # ==========================================
        # Task 시작
        # ==========================================

        self.task_manager.update_status(
            task_id=task_id,
            status="IN_PROGRESS"
        )

        self.agent_manager.update_status(
            agent_id=agent_id,
            status="IN_PROGRESS"
        )

        try:

            # ==========================================
            # Agent 생성
            # ==========================================

            agent = BaseAgent(
                agent_id=agent_id,
                name=agent_name,
                role=agent_role,
                model=agent_model
            )

            # ==========================================
            # Model Adapter
            # ==========================================

            if model_adapter is None:
                model_adapter = create_local_adapter(model=agent_model)

            # ==========================================
            # Task 내용 결정
            # ==========================================

            if task_description is None:

                task_description = task_db_description

            # ==========================================
            # Agent 실행
            # ==========================================

            context = self.context_manager.build_task_context(
                task_id
            )

            print("\n==============================")
            print("TASK CONTEXT")
            print("==============================")

            print(context)

            result = agent.execute(
                task=task_description,
                model_adapter=model_adapter,
                context=context
)
            from app.models.generation_policy import record_response
            record_response(self.database,model_adapter,'work',project_id,task_id)

            # ==========================================
            # Result 저장
            # ==========================================

            result_id = self.result_manager.save_result(
                task_id=task_id,
                agent_id=agent_id,
                summary=result,
                status="COMPLETED"
            )

            # ==========================================
            # 상태 변경
            # ==========================================

            self.task_manager.update_status(
                task_id=task_id,
                status="COMPLETED"
            )

            self.agent_manager.update_status(
                agent_id=agent_id,
                status="COMPLETED"
            )

            print("\n==============================")
            print("GENERIC TASK COMPLETED")
            print("==============================")

            print(f"Result ID : {result_id}")

            return result_id

        except Exception as e:

            from app.runtime.run_control import RunCancelled
            if isinstance(e,RunCancelled):
                self.task_manager.update_status(task_id=task_id,status='READY')
                self.agent_manager.update_status(agent_id=agent_id,status='IDLE')
                raise

            error_id = self.error_manager.record_error(
                project_id=project_id,
                task_id=task_id,
                agent_id=agent_id,
                error_type="AGENT_ERROR",
                severity="MAJOR",
                message=str(e),
                details=repr(e),
                status="OPEN",
                retry_count=0,
                parent_error_id=retry_parent_error_id
)

            self.task_manager.update_status(
                task_id=task_id,
                status="ERROR"
            )

            self.agent_manager.update_status(
                agent_id=agent_id,
                status="ERROR"
            )

            print("\n==============================")
            print("TASK EXECUTION ERROR")
            print("==============================")
            print("Error ID :", error_id)
            print("Message  :", str(e))

            raise
        
        
    
