from datetime import datetime
import uuid
from app.qa.approval_evidence import check_approval_evidence

APPROVAL_STATUSES={"APPROVED","REJECTED","PENDING","INVALIDATED"}

class ApprovalManager:
    def __init__(self,database,qa_manager):
        self.database=database; self.qa_manager=qa_manager

    def request(self,project_id,task_id,result_id=None,approver_agent_id="AGT-CHIEF-001"):
        aid="APR-"+str(uuid.uuid4())[:8].upper()
        now=datetime.now().isoformat()
        with self.database.connection() as conn:
            conn.execute('BEGIN IMMEDIATE')
            latest=conn.execute('SELECT result_id,status FROM qa_reviews WHERE task_id=? AND project_id=? ORDER BY created_at DESC LIMIT 1',
                                (task_id,project_id)).fetchone()
            if not latest or latest[1] not in ('PASS','PASS_WITH_WARNING'):
                raise ValueError('QA 통과 결과만 승인 요청할 수 있습니다.')
            if result_id is None:result_id=latest[0]
            if result_id!=latest[0]:raise ValueError('승인 요청 결과와 최신 QA 검토 결과가 다릅니다.')
            conn.execute("""INSERT INTO approvals
            (approval_id,project_id,task_id,result_id,approver_agent_id,status,comment,created_at)
            VALUES (?,?,?,?,?,?,?,?)""",(aid,project_id,task_id,result_id,approver_agent_id,"PENDING","",now))
        return aid

    def decide(self,approval_id,status,comment=""):
        if status not in {"APPROVED","REJECTED"}: raise ValueError("승인 상태는 APPROVED/REJECTED만 가능합니다.")
        now=datetime.now().isoformat()
        with self.database.connection() as conn:
            conn.execute('BEGIN IMMEDIATE')
            existing=conn.execute("SELECT project_id,status,task_id,result_id FROM approvals WHERE approval_id=?",(approval_id,)).fetchone()
            if existing is None:
                raise ValueError(f"승인 요청을 찾을 수 없습니다: {approval_id}")
            if existing[1] != "PENDING":
                if existing[1]=='INVALIDATED':raise ValueError('QA 재검증 실패로 무효화된 승인 요청입니다. 수정 및 QA 재검토가 필요합니다.')
                raise ValueError(f"이미 결정된 승인 요청입니다: {approval_id} ({existing[1]})")
            if status=='APPROVED':
                latest=conn.execute('SELECT result_id,status FROM qa_reviews WHERE task_id=? AND project_id=? ORDER BY created_at DESC LIMIT 1',
                                    (existing[2],existing[0])).fetchone()
                if not latest or latest[1] not in ('PASS','PASS_WITH_WARNING') or latest[0]!=existing[3]:
                    raise ValueError('최신 QA를 통과한 동일 결과만 승인할 수 있습니다.')
                check_approval_evidence(conn,existing[0],existing[3])
            cur=conn.execute(
                "UPDATE approvals SET status=?,comment=?,decided_at=? WHERE approval_id=? AND status='PENDING'",
                (status,comment,now,approval_id),
            )
            if cur.rowcount != 1:
                raise RuntimeError("다른 실행에서 승인 상태가 먼저 변경되었습니다.")
            project_status="COMPLETED" if status=="APPROVED" else "REJECTED"
            updated=conn.execute("UPDATE projects SET status=? WHERE project_id=?",(project_status,existing[0]))
            if updated.rowcount != 1:
                raise RuntimeError(f"승인 대상 프로젝트를 찾을 수 없습니다: {existing[0]}")
            row=conn.execute("SELECT approval_id,project_id,task_id,result_id,approver_agent_id,status,comment,created_at,decided_at FROM approvals WHERE approval_id=?",(approval_id,)).fetchone()
        return row

    def get(self,approval_id):
        conn=self.database.connect()
        row=conn.execute("SELECT approval_id,project_id,task_id,result_id,approver_agent_id,status,comment,created_at FROM approvals WHERE approval_id=?",(approval_id,)).fetchone()
        conn.close(); return row

    def get_by_task(self,task_id):
        conn=self.database.connect()
        rows=conn.execute("SELECT approval_id,project_id,task_id,result_id,approver_agent_id,status,comment,created_at FROM approvals WHERE task_id=? ORDER BY created_at",(task_id,)).fetchall()
        conn.close(); return rows
