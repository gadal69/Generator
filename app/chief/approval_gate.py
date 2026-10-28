from datetime import datetime
import json, uuid

class ApprovalGate:
    """Cloud AI/중요 변경 등 대표 승인 대상의 정책 게이트."""
    def __init__(self,database): self.database=database
    def request_cloud(self,project_id,chief_id,purpose,scope,data_scope,cost="0"):
        aid="CAP-"+str(uuid.uuid4())[:8].upper(); now=datetime.now().isoformat()
        conn=self.database.connect(); conn.execute("""INSERT INTO model_approvals
          (approval_id,project_id,chief_agent_id,provider,purpose,scope,data_scope,estimated_cost,status,created_at,decided_at,comment)
          VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",(aid,project_id,chief_id,"cloud",purpose,scope,data_scope,cost,"PENDING",now,None,"")); conn.commit(); conn.close(); return aid
    def decide(self,approval_id,status,comment=""):
        if status not in {"APPROVED","REJECTED"}: raise ValueError("model approval status는 APPROVED/REJECTED만 가능합니다.")
        with self.database.connection() as conn:
            existing=conn.execute("SELECT status FROM model_approvals WHERE approval_id=?",(approval_id,)).fetchone()
            if existing is None: raise ValueError(f"클라우드 승인 요청을 찾을 수 없습니다: {approval_id}")
            if existing[0] != "PENDING": raise ValueError(f"이미 결정된 클라우드 승인 요청입니다: {approval_id} ({existing[0]})")
            cur=conn.execute("UPDATE model_approvals SET status=?,decided_at=?,comment=? WHERE approval_id=? AND status='PENDING'",(status,datetime.now().isoformat(),comment,approval_id))
            if cur.rowcount != 1: raise RuntimeError("다른 실행에서 클라우드 승인 상태가 먼저 변경되었습니다.")
        return self.get(approval_id)
    def get(self,approval_id):
        conn=self.database.connect(); row=conn.execute("SELECT approval_id,project_id,chief_agent_id,provider,purpose,scope,data_scope,estimated_cost,status,created_at,decided_at,comment FROM model_approvals WHERE approval_id=?",(approval_id,)).fetchone(); conn.close(); return row
    def approved_for(self,project_id,scope=None,purpose=None,data_scope=None,provider="cloud"):
        conn=self.database.connect(); rows=conn.execute("SELECT scope,purpose,data_scope FROM model_approvals WHERE project_id=? AND provider=? AND status='APPROVED'",(project_id,provider)).fetchall(); conn.close()
        return any((scope is None or scope==r[0]) and (purpose is None or purpose==r[1]) and (data_scope is None or data_scope==r[2]) for r in rows)
