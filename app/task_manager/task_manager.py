from datetime import datetime
import uuid


class TaskManager:

    def __init__(self, database):
        self.database = database

    def create_task(
        self,
        project_id,
        name,
        description="",
        agent_id=None
    ):

        task_id = "TASK-" + str(uuid.uuid4())[:8].upper()

        created_at = datetime.now().isoformat()

        conn = self.database.connect()
        cursor = conn.cursor()

        cursor.execute("""
            INSERT INTO tasks (
                task_id,
                project_id,
                agent_id,
                name,
                description,
                status,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            task_id,
            project_id,
            agent_id,
            name,
            description,
            "CREATED",
            created_at
        ))

        conn.commit()
        conn.close()

        return task_id
    
    def update_status(self, task_id, status):

        conn = self.database.connect()
        cursor = conn.cursor()

        cursor.execute("""
            UPDATE tasks
            SET status = ?
            WHERE task_id = ?
        """, (
            status,
            task_id
        ))

        conn.commit()
        conn.close()

    def get_task(self, task_id):

        conn = self.database.connect()
        cursor = conn.cursor()

        cursor.execute("""
            SELECT
                task_id,
                project_id,
                agent_id,
                name,
                description,
                status,
                created_at
            FROM tasks
            WHERE task_id = ?
        """, (task_id,))

        result = cursor.fetchone()

        conn.close()

        return result