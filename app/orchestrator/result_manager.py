from datetime import datetime
import uuid


class ResultManager:

    def __init__(self, database):
        self.database = database

    def save_result(
        self,
        task_id,
        agent_id,
        summary,
        status="COMPLETED"
    ):

        result_id = "RESULT-" + str(uuid.uuid4())[:8].upper()

        created_at = datetime.now().isoformat()

        conn = self.database.connect()
        cursor = conn.cursor()

        cursor.execute("""
            INSERT INTO results (
                result_id,
                task_id,
                agent_id,
                summary,
                status,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?)
        """, (
            result_id,
            task_id,
            agent_id,
            summary,
            status,
            created_at
        ))

        conn.commit()
        conn.close()

        return result_id

    def get_latest_result_by_task(self, task_id):
        conn = self.database.connect()
        row = conn.execute("""
            SELECT result_id, task_id, agent_id, summary, status, created_at
            FROM results WHERE task_id=? ORDER BY created_at DESC LIMIT 1
        """, (task_id,)).fetchone()
        conn.close()
        return row

    def get_results_by_task(self, task_id):
        conn = self.database.connect()
        rows = conn.execute("""
            SELECT result_id, task_id, agent_id, summary, status, created_at
            FROM results WHERE task_id=? ORDER BY created_at ASC
        """, (task_id,)).fetchall()
        conn.close()
        return rows

    def get_result(self, result_id):

        conn = self.database.connect()
        cursor = conn.cursor()

        cursor.execute("""
            SELECT
                result_id,
                task_id,
                agent_id,
                summary,
                status,
                created_at
            FROM results
            WHERE result_id = ?
        """, (result_id,))

        result = cursor.fetchone()

        conn.close()

        return result