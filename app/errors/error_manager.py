from datetime import datetime
import uuid


class ErrorManager:

    def __init__(self, database):
        self.database = database

    # ============================================================
    # Error 생성
    # ============================================================
    def record_error(
        self,
        project_id,
        task_id,
        agent_id,
        error_type,
        severity,
        message,
        details="",
        status="OPEN",
        retry_count=0,
        parent_error_id=None
    ):

        error_id = (
            "ERROR-" +
            str(uuid.uuid4())[:8].upper()
        )

        created_at = datetime.now().isoformat()

        conn = self.database.connect()
        cursor = conn.cursor()

        cursor.execute("""
            INSERT INTO errors (
                error_id,
                project_id,
                task_id,
                agent_id,
                error_type,
                severity,
                message,
                details,
                status,
                retry_count,
                created_at,
                resolved_at,
                parent_error_id
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            error_id,
            project_id,
            task_id,
            agent_id,
            error_type,
            severity,
            message,
            details,
            status,
            retry_count,
            created_at,
            None,
            parent_error_id
        ))

        conn.commit()
        conn.close()

        return error_id

    # ============================================================
    # Error 조회
    # ============================================================
    def get_error(self, error_id):

        conn = self.database.connect()
        cursor = conn.cursor()

        cursor.execute("""
            SELECT
                error_id,
                project_id,
                task_id,
                agent_id,
                error_type,
                severity,
                message,
                details,
                status,
                retry_count,
                created_at,
                resolved_at,
                parent_error_id
            FROM errors
            WHERE error_id = ?
        """, (error_id,))

        result = cursor.fetchone()

        conn.close()

        return result

    # ============================================================
    # Retry Count 업데이트
    # ============================================================
    def update_retry_count(
        self,
        error_id,
        retry_count
    ):

        conn = self.database.connect()
        cursor = conn.cursor()

        cursor.execute("""
            UPDATE errors
            SET retry_count = ?
            WHERE error_id = ?
        """, (
            retry_count,
            error_id
        ))

        conn.commit()
        conn.close()

        return retry_count

    # ============================================================
    # Error 상태 업데이트
    # ============================================================
    def update_status(
        self,
        error_id,
        status
    ):

        conn = self.database.connect()
        cursor = conn.cursor()

        resolved_at = None

        if status == "RESOLVED":
            resolved_at = datetime.now().isoformat()

        cursor.execute("""
            UPDATE errors
            SET
                status = ?,
                resolved_at = ?
            WHERE error_id = ?
        """, (
            status,
            resolved_at,
            error_id
        ))

        conn.commit()
        conn.close()

        return status

    # ============================================================
    # Error 목록 조회
    # ============================================================
    def get_errors_by_task(
        self,
        task_id
    ):

        conn = self.database.connect()
        cursor = conn.cursor()

        cursor.execute("""
            SELECT
                error_id,
                project_id,
                task_id,
                agent_id,
                error_type,
                severity,
                message,
                details,
                status,
                retry_count,
                created_at,
                resolved_at,
                parent_error_id
            FROM errors
            WHERE task_id = ?
            ORDER BY created_at ASC
        """, (task_id,))

        results = cursor.fetchall()

        conn.close()

        return results

    # ============================================================
    # Retry Chain 조회
    # ============================================================
    def get_retry_chain(
        self,
        error_id
    ):

        conn = self.database.connect()
        cursor = conn.cursor()

        cursor.execute("""
            SELECT
                error_id,
                project_id,
                task_id,
                agent_id,
                error_type,
                severity,
                message,
                details,
                status,
                retry_count,
                created_at,
                resolved_at,
                parent_error_id
            FROM errors
            WHERE error_id = ?
               OR parent_error_id = ?
            ORDER BY created_at ASC
        """, (
            error_id,
            error_id
        ))

        results = cursor.fetchall()

        conn.close()

        return results