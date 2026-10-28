from datetime import datetime
import uuid


class ProjectManager:

    def __init__(self, database):
        self.database = database

    def create_project(self, name, description=""):

        project_id = "PRJ-" + str(uuid.uuid4())[:8].upper()

        created_at = datetime.now().isoformat()

        conn = self.database.connect()
        cursor = conn.cursor()

        cursor.execute("""
            INSERT INTO projects (
                project_id,
                name,
                description,
                status,
                created_at
            )
            VALUES (?, ?, ?, ?, ?)
        """, (
            project_id,
            name,
            description,
            "INBOX",
            created_at
        ))

        conn.commit()
        conn.close()

        return project_id

    def get_project(self, project_id):

        conn = self.database.connect()
        cursor = conn.cursor()

        cursor.execute("""
            SELECT
                project_id,
                name,
                description,
                status,
                created_at
            FROM projects
            WHERE project_id = ?
        """, (project_id,))

        result = cursor.fetchone()

        conn.close()

        return result

    def change_status(self, project_id, status):

        conn = self.database.connect()
        cursor = conn.cursor()

        cursor.execute("""
            UPDATE projects
            SET status = ?
            WHERE project_id = ?
        """, (
            status,
            project_id
        ))

        conn.commit()
        conn.close()