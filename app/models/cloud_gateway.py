"""Single service-layer gate for every external AI call."""
from __future__ import annotations

from datetime import datetime
import uuid

from app.models.gemini_search_adapter import GeminiSearchAdapter


class CloudApprovalRequired(PermissionError):
    def __init__(self, approval_id, message="대표의 클라우드 사용 승인이 필요합니다."):
        super().__init__(message)
        self.approval_id = approval_id


class ApprovedCloudAdapter:
    def __init__(self, gateway, approval_id, adapter):
        self.gateway, self.approval_id, self.adapter = gateway, approval_id, adapter
        self.model = getattr(adapter, "model", "gemini")

    @property
    def last_response(self):
        return getattr(self.adapter, 'last_response', {})

    def configure_generation(self, stage, attempt=0):
        configure=getattr(self.adapter, 'configure_generation', None)
        if callable(configure):
            configure(stage,attempt)

    def generate(self, prompt, images=None):
        self.gateway.assert_approved(self.approval_id)
        try:
            result = self.adapter.generate(prompt) if images is None else self.adapter.generate(prompt, images=images)
            self.gateway.log(self.approval_id, "COMPLETED")
            return result
        except Exception:
            self.gateway.log(self.approval_id, "ERROR")
            raise

    def search(self, prompt):
        self.gateway.assert_approved(self.approval_id)
        try:
            result = self.adapter.search(prompt)
            self.gateway.log(self.approval_id, "COMPLETED")
            return result
        except Exception:
            self.gateway.log(self.approval_id, "ERROR")
            raise


class CloudGateway:
    def __init__(self, database, approval_gate, adapter_factory=None, approval_required=True):
        self.database = database
        self.approval_gate = approval_gate
        self.adapter_factory = adapter_factory or GeminiSearchAdapter
        self.approval_required = approval_required

    def request_or_adapter(self, project_id, purpose, scope, data_scope, cost="미산정", approval_id=None):
        if not self.approval_required:
            return ApprovedCloudAdapter(self, None, self.adapter_factory())
        if approval_id is None:
            approval_id = self.approval_gate.request_cloud(
                project_id, "AGT-CHIEF-001", purpose, scope, data_scope, cost
            )
            raise CloudApprovalRequired(approval_id)
        row = self.approval_gate.get(approval_id)
        if row is None:
            raise PermissionError(f"클라우드 승인 요청을 찾을 수 없습니다: {approval_id}")
        expected = (project_id, "cloud", purpose, scope, data_scope)
        actual = (row[1], row[3], row[4], row[5], row[6])
        if actual != expected:
            raise PermissionError("클라우드 승인 범위가 현재 호출과 일치하지 않습니다.")
        if row[8] != "APPROVED":
            raise PermissionError(f"클라우드 승인이 허용되지 않았습니다: {row[8]}")
        return ApprovedCloudAdapter(self, approval_id, self.adapter_factory())

    def assert_approved(self, approval_id):
        if not self.approval_required and approval_id is None:
            return
        row = self.approval_gate.get(approval_id)
        if row is None or row[8] != "APPROVED":
            raise PermissionError("외부 전송 직전 승인 검사가 실패했습니다.")

    def log(self, approval_id, status):
        if approval_id is None:
            return
        row = self.approval_gate.get(approval_id)
        if row is None:
            return
        with self.database.connection() as conn:
            conn.execute("""INSERT INTO runtime_runs
                (run_id,project_id,task_id,agent_id,status,input_text,output_text,started_at,finished_at,error_message)
                VALUES (?,?,?,?,?,?,?,?,?,?)""",
                ("RUN-"+uuid.uuid4().hex[:8].upper(),row[1],None,row[2],status,
                 f"cloud_approval_id={approval_id}; purpose={row[4]}",None,
                 datetime.now().isoformat(),datetime.now().isoformat(),None))
