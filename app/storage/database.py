import sqlite3
from contextlib import contextmanager
from pathlib import Path


class Database:

    def __init__(self, db_path):
        self.db_path = str(db_path)
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self.initialize()
        self.migrate_v1_tables()
        self.migrate_errors_table()
        self.migrate()

    def connect(self):
        conn = sqlite3.connect(self.db_path)
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 5000")
        return conn

    @contextmanager
    def connection(self):
        """Commit on success, roll back on failure, and always close the connection."""
        conn = self.connect()
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def initialize(self):
        conn = self.connect()
        cursor = conn.cursor()

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS projects (
                project_id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                description TEXT,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS agents (
                agent_id TEXT PRIMARY KEY,
                project_id TEXT,
                name TEXT NOT NULL,
                role TEXT NOT NULL,
                status TEXT NOT NULL,
                model TEXT,
                created_at TEXT NOT NULL
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS tasks (
                task_id TEXT PRIMARY KEY,
                project_id TEXT,
                agent_id TEXT,
                name TEXT NOT NULL,
                description TEXT,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS results (
                result_id TEXT PRIMARY KEY,
                task_id TEXT,
                agent_id TEXT,
                summary TEXT,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS task_dependencies (
                dependency_id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL,
                task_id TEXT NOT NULL,
                depends_on_task_id TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS errors (
                error_id TEXT PRIMARY KEY,
                project_id TEXT,
                task_id TEXT,
                agent_id TEXT,
                error_type TEXT NOT NULL,
                severity TEXT NOT NULL,
                message TEXT NOT NULL,
                details TEXT,
                status TEXT NOT NULL,
                retry_count INTEGER DEFAULT 0,
                created_at TEXT NOT NULL,
                resolved_at TEXT,
                parent_error_id TEXT
            )
        """)
        
        conn.commit()
        conn.close()

    def migrate_v1_tables(self):
        """ITNI_AI_TEAM V1.0 확장 테이블을 안전하게 생성한다."""
        conn = self.connect()
        cursor = conn.cursor()

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS qa_reviews (
                qa_id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL,
                task_id TEXT NOT NULL,
                result_id TEXT,
                reviewer_agent_id TEXT,
                status TEXT NOT NULL,
                score REAL,
                summary TEXT,
                issues TEXT,
                created_at TEXT NOT NULL,
                resolved_at TEXT,
                rework_task_id TEXT
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS approvals (
                approval_id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL,
                task_id TEXT NOT NULL,
                result_id TEXT,
                approver_agent_id TEXT,
                status TEXT NOT NULL,
                comment TEXT,
                created_at TEXT NOT NULL
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS knowledge_documents (
                document_id TEXT PRIMARY KEY,
                project_id TEXT,
                source_path TEXT NOT NULL,
                title TEXT NOT NULL,
                content TEXT NOT NULL,
                metadata TEXT,
                created_at TEXT NOT NULL
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS knowledge_chunks (
                chunk_id TEXT PRIMARY KEY,
                document_id TEXT NOT NULL,
                chunk_index INTEGER NOT NULL,
                content TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS tool_registry (
                tool_id TEXT PRIMARY KEY,
                name TEXT NOT NULL UNIQUE,
                description TEXT,
                enabled INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS team_runs (
                team_run_id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL,
                user_request TEXT NOT NULL,
                status TEXT NOT NULL,
                final_result TEXT,
                approval_id TEXT,
                started_at TEXT NOT NULL,
                finished_at TEXT,
                error_message TEXT
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS organization_agents (
                agent_id TEXT PRIMARY KEY, name TEXT NOT NULL, role TEXT NOT NULL, capabilities TEXT,
                description TEXT, model_policy TEXT, permanent INTEGER NOT NULL DEFAULT 1, status TEXT NOT NULL, created_at TEXT NOT NULL
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS model_approvals (
                approval_id TEXT PRIMARY KEY, project_id TEXT NOT NULL, chief_agent_id TEXT, provider TEXT NOT NULL,
                purpose TEXT NOT NULL, scope TEXT NOT NULL, data_scope TEXT, estimated_cost TEXT, status TEXT NOT NULL,
                created_at TEXT NOT NULL, decided_at TEXT, comment TEXT
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS runtime_runs (
                run_id TEXT PRIMARY KEY,
                project_id TEXT,
                task_id TEXT,
                agent_id TEXT,
                status TEXT NOT NULL,
                input_text TEXT,
                output_text TEXT,
                started_at TEXT NOT NULL,
                finished_at TEXT,
                error_message TEXT
            )
        """)

        # V1.3 agents metadata migration
        cursor.execute("PRAGMA table_info(agents)")
        columns={r[1] for r in cursor.fetchall()}
        additions={"agent_type":"TEXT DEFAULT 'CORE'","capabilities":"TEXT DEFAULT '[]'","created_by":"TEXT DEFAULT 'AGT-CHIEF-001'","parent_agent_id":"TEXT"}
        for name,typ in additions.items():
            if name not in columns: cursor.execute(f"ALTER TABLE agents ADD COLUMN {name} {typ}")

        conn.commit()
        conn.close()

    def migrate(self):
        """Idempotent, versioned and non-destructive schema hardening."""
        with self.connection() as conn:
            version = conn.execute("PRAGMA user_version").fetchone()[0]
            if version < 1:
                columns = {row[1] for row in conn.execute("PRAGMA table_info(approvals)")}
                if "decided_at" not in columns:
                    conn.execute("ALTER TABLE approvals ADD COLUMN decided_at TEXT")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_projects_status ON projects(status)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_agents_project ON agents(project_id)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_tasks_project_status ON tasks(project_id,status)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_results_task ON results(task_id)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_approvals_project_status ON approvals(project_id,status)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_model_approvals_lookup ON model_approvals(project_id,provider,purpose,scope,status)")
                conn.execute("PRAGMA user_version = 1")
                version = 1
            if version < 2:
                conn.execute("""CREATE TABLE IF NOT EXISTS code_verifications (
                    verification_id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    relative_path TEXT NOT NULL,
                    verification_type TEXT NOT NULL,
                    status TEXT NOT NULL,
                    details TEXT,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(project_id) REFERENCES projects(project_id)
                )""")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_code_verifications_project ON code_verifications(project_id,created_at)")
                conn.execute("PRAGMA user_version = 2")
                version = 2
            if version < 3:
                conn.execute('''CREATE TABLE IF NOT EXISTS model_responses (
                    call_id TEXT PRIMARY KEY, project_id TEXT, task_id TEXT,
                    stage TEXT NOT NULL, model TEXT NOT NULL, metadata TEXT NOT NULL,
                    created_at TEXT NOT NULL)''')
                conn.execute('CREATE INDEX IF NOT EXISTS idx_model_responses_project ON model_responses(project_id,created_at)')
                conn.execute('PRAGMA user_version = 3')
                version=3
            if version<4:
                conn.execute('''CREATE TABLE IF NOT EXISTS workflow_checkpoints (
                    project_id TEXT PRIMARY KEY,status TEXT NOT NULL,state_json TEXT NOT NULL,updated_at TEXT NOT NULL)''')
                conn.execute('''CREATE TABLE IF NOT EXISTS workflow_events (
                    event_id INTEGER PRIMARY KEY AUTOINCREMENT,project_id TEXT NOT NULL,team_run_id TEXT,
                    stage TEXT NOT NULL,event_json TEXT NOT NULL,created_at TEXT NOT NULL)''')
                conn.execute('CREATE INDEX IF NOT EXISTS idx_workflow_events_project ON workflow_events(project_id,event_id)')
                conn.execute('PRAGMA user_version = 4')

    def migrate_errors_table(self):
        conn = self.connect()
        cursor = conn.cursor()

        cursor.execute("PRAGMA table_info(errors)")
        columns = [row[1] for row in cursor.fetchall()]

        if "parent_error_id" not in columns:
            cursor.execute("""
                ALTER TABLE errors
                ADD COLUMN parent_error_id TEXT
            """)

            conn.commit()
        conn.close()
