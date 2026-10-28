class WorkflowManager:

    def __init__(self, database, task_manager, dependency_manager):
        self.database = database
        self.task_manager = task_manager
        self.dependency_manager = dependency_manager

    def initialize_task_status(self, task_id):
        """
        Task 생성 직후 Dependency 상태를 확인하여
        CREATED 상태를 WAITING 또는 READY로 변경한다.
        """

        task = self.task_manager.get_task(task_id)

        if task is None:
            raise ValueError(
                f"Task를 찾을 수 없습니다: {task_id}"
            )

        current_status = task[5]

        # 이미 다른 상태로 변경된 Task는 건드리지 않는다.
        if current_status != "CREATED":
            return current_status

        can_execute = self.dependency_manager.can_execute(
            task_id
        )

        if can_execute:

            self.task_manager.update_status(
                task_id=task_id,
                status="READY"
            )

            return "READY"

        self.task_manager.update_status(
            task_id=task_id,
            status="WAITING"
        )

        return "WAITING"

    def update_waiting_tasks(self, project_id):
        """
        선행 Task가 모두 완료된 WAITING Task를
        READY 상태로 변경한다.
        """

        conn = self.database.connect()
        cursor = conn.cursor()

        cursor.execute("""
            SELECT task_id
            FROM tasks
            WHERE project_id = ?
            AND status = 'WAITING'
        """, (project_id,))

        waiting_tasks = cursor.fetchall()

        conn.close()

        updated_tasks = []

        for row in waiting_tasks:

            task_id = row[0]

            can_execute = self.dependency_manager.can_execute(
                task_id
            )

            if can_execute:

                self.task_manager.update_status(
                    task_id=task_id,
                    status="READY"
                )

                updated_tasks.append(task_id)

        return updated_tasks