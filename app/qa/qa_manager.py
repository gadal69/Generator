from datetime import datetime
import json
import uuid


QA_STATUSES = {"PASS", "PASS_WITH_WARNING", "FAIL", "BLOCKED"}


class QAManager:
    """작업 결과의 품질 검증, 재작업 요청, 검증 이력 관리를 담당한다."""

    def __init__(self, database, task_manager, result_manager):
        self.database = database
        self.task_manager = task_manager
        self.result_manager = result_manager

    def review(self, project_id, task_id, result_id=None, reviewer_agent_id="AGT-QA-001",
               status=None, score=None, summary="", issues=None):
        if status is None:
            result = self.result_manager.get_result(result_id) if result_id else None
            if result is None:
                raise ValueError("QA 대상 Result가 필요합니다.")
            status, score, summary, issues = self._evaluate_result(result[3])
        if status not in QA_STATUSES:
            raise ValueError(f"지원하지 않는 QA 상태: {status}")
        if issues is None:
            issues = []
        qa_id = "QA-" + str(uuid.uuid4())[:8].upper()
        now = datetime.now().isoformat()
        with self.database.connection() as conn:
            conn.execute("""INSERT INTO qa_reviews
                (qa_id, project_id, task_id, result_id, reviewer_agent_id, status, score,
                 summary, issues, created_at, resolved_at, rework_task_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (qa_id, project_id, task_id, result_id, reviewer_agent_id, status, score,
                 summary, json.dumps(issues, ensure_ascii=False), now,
                 now if status in ("PASS", "PASS_WITH_WARNING") else None, None))
            if status in ('FAIL','BLOCKED'):
                invalidated=conn.execute("UPDATE approvals SET status='INVALIDATED',comment=?,decided_at=? WHERE task_id=? AND project_id=? AND status IN ('PENDING','APPROVED')",
                    ('QA 재검증 실패로 자동 무효화되었습니다.',now,task_id,project_id))
                if invalidated.rowcount:
                    conn.execute("UPDATE projects SET status='BLOCKED' WHERE project_id=?",(project_id,))
                    conn.execute("UPDATE team_runs SET status='ERROR',error_message=?,finished_at=? WHERE project_id=? AND status IN ('PENDING_APPROVAL','APPROVED')",
                        ('Final QA revalidation failed; pending approval invalidated.',now,project_id))
        return self.get_review(qa_id)

    def _evaluate_result(self, text):
        text = (text or "").strip()
        if not text:
            return "FAIL", 0.0, "결과가 비어 있습니다.", ["EMPTY_RESULT"]
        upper = text.upper()
        if "[BLOCKED]" in upper:
            return "BLOCKED", 0.0, "결과가 차단 상태를 명시했습니다.", ["BLOCKED"]
        if "[QA:FAIL]" in upper:
            return "FAIL", 40.0, "Agent 결과가 QA 실패를 명시했습니다.", ["AGENT_MARKED_FAIL"]
        if "[QA:WARNING]" in upper:
            return "PASS_WITH_WARNING", 80.0, "경고가 있으나 결과는 사용 가능합니다.", ["AGENT_MARKED_WARNING"]
        return "PASS", 100.0, "기본 QA 검증을 통과했습니다.", []

    def request_rework(self, qa_id, agent_id=None, rework_instruction=None):
        review = self.get_review(qa_id)
        if not review:
            raise ValueError(f"QA를 찾을 수 없습니다: {qa_id}")
        if review[5] not in ("FAIL", "BLOCKED"):
            raise ValueError("FAIL 또는 BLOCKED 상태에서만 REWORK할 수 있습니다.")
        task = self.task_manager.get_task(review[2])
        rework_task_id = self.task_manager.create_task(
            project_id=review[1],
            name=f"REWORK: {task[3]}",
            description=rework_instruction or f"{task[4]}\n\nQA {qa_id} 재작업. Issues: {review[8]}",
            agent_id=agent_id or task[2]
        )
        self.task_manager.update_status(rework_task_id, "READY")
        self._set_rework_task(qa_id, rework_task_id)
        return rework_task_id

    def _set_rework_task(self, qa_id, task_id):
        conn=self.database.connect()
        conn.execute("UPDATE qa_reviews SET rework_task_id=? WHERE qa_id=?", (task_id,qa_id))
        conn.commit(); conn.close()

    def get_review(self, qa_id):
        conn=self.database.connect()
        row=conn.execute("""SELECT qa_id,project_id,task_id,result_id,reviewer_agent_id,status,
            score,summary,issues,created_at,resolved_at,rework_task_id FROM qa_reviews WHERE qa_id=?""",
            (qa_id,)).fetchone()
        conn.close()
        return row

    def get_reviews_by_task(self, task_id):
        conn=self.database.connect()
        rows=conn.execute("SELECT qa_id,project_id,task_id,result_id,reviewer_agent_id,status,score,summary,issues,created_at,resolved_at,rework_task_id FROM qa_reviews WHERE task_id=? ORDER BY created_at", (task_id,)).fetchall()
        conn.close(); return rows

    def latest_review(self, task_id):
        rows=self.get_reviews_by_task(task_id)
        return rows[-1] if rows else None
