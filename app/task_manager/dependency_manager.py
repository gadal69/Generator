from datetime import datetime
import uuid


class DependencyManager:

    def __init__(self, database):
        self.database = database

    def add_dependency(
        self,
        project_id,
        task_id,
        depends_on_task_id
    ):

        dependency_id = (
            "DEP-" +
            str(uuid.uuid4())[:8].upper()
        )

        created_at = datetime.now().isoformat()

        conn = self.database.connect()
        cursor = conn.cursor()

        cursor.execute("""
            INSERT INTO task_dependencies (
                dependency_id,
                project_id,
                task_id,
                depends_on_task_id,
                created_at
            )
            VALUES (?, ?, ?, ?, ?)
        """, (
            dependency_id,
            project_id,
            task_id,
            depends_on_task_id,
            created_at
        ))

        conn.commit()
        conn.close()

        return dependency_id

    def get_dependencies(self, task_id):

        conn = self.database.connect()
        cursor = conn.cursor()

        cursor.execute("""
            SELECT
                dependency_id,
                project_id,
                task_id,
                depends_on_task_id,
                created_at
            FROM task_dependencies
            WHERE task_id = ?
        """, (task_id,))

        results = cursor.fetchall()

        conn.close()

        return results

    def can_execute(self, task_id):

        dependencies = self.get_dependencies(task_id)

        # 선행 Task가 없으면 바로 실행 가능
        if not dependencies:
            return True

        conn = self.database.connect()
        cursor = conn.cursor()

        for dependency in dependencies:

            depends_on_task_id = dependency[3]

            cursor.execute("""
                SELECT status
                FROM tasks
                WHERE task_id = ?
            """, (depends_on_task_id,))

            result = cursor.fetchone()

            if result is None:
                conn.close()
                return False

            status = result[0]

            if status != "COMPLETED":
                conn.close()
                return False

        conn.close()

        return True