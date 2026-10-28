class ContextManager:

    def __init__(self, database):
        self.database = database

    def build_task_context(self, task_id):

        conn = self.database.connect()
        cursor = conn.cursor()

        # ==========================================
        # 1. 현재 Task
        # ==========================================

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

        task = cursor.fetchone()

        if task is None:
            conn.close()

            raise ValueError(
                f"Task를 찾을 수 없습니다: {task_id}"
            )

        project_id = task[1]

        # ==========================================
        # 2. 선행 Task 확인
        # ==========================================

        cursor.execute("""
            SELECT
                depends_on_task_id
            FROM task_dependencies
            WHERE task_id = ?
        """, (task_id,))

        dependencies = cursor.fetchall()

        # ==========================================
        # 3. 선행 Task의 Result 조회
        # ==========================================

        previous_results = []

        for dependency in dependencies:

            depends_on_task_id = dependency[0]

            cursor.execute("""
                SELECT
                    result_id,
                    task_id,
                    agent_id,
                    summary,
                    status,
                    created_at
                FROM results
                WHERE task_id = ?
                ORDER BY created_at DESC
            """, (depends_on_task_id,))

            results = cursor.fetchall()

            for result in results:

                previous_results.append({
                    "result_id": result[0],
                    "task_id": result[1],
                    "agent_id": result[2],
                    "summary": result[3],
                    "status": result[4],
                    "created_at": result[5]
                })

        conn.close()

        # ==========================================
        # 4. Context 생성
        # ==========================================

        context = {

            "project": {
                "project_id": project_id
            },

            "task": {
                "task_id": task[0],
                "project_id": task[1],
                "agent_id": task[2],
                "name": task[3],
                "description": task[4],
                "status": task[5],
                "created_at": task[6]
            },

            "previous_results": previous_results
        }

        return context