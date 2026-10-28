from app.organization.agent_registry import AgentRegistry

class OrganizationManager:
    """프로젝트별 Chief 생성 에이전트 등록/조회 계층."""
    def __init__(self, database, registry=None):
        self.database=database
        self.registry=registry or AgentRegistry()
        self._ensure_persistent_pool()

    def _ensure_persistent_pool(self):
        conn=self.database.connect()
        # Remove legacy seeded agents; project-specific historical agents remain intact.
        conn.executemany("DELETE FROM organization_agents WHERE agent_id=?", [(agent_id,) for agent_id in ("CORE-PM", "CORE-DESIGN", "CORE-DEV", "CORE-QA", "CORE-RESEARCH")])
        conn.commit(); conn.close()

    def list_basic_agents(self): return self.registry.all()
    def get_basic_agent(self, role): return self.registry.find_by_role(role)

    def list_project_agents(self, project_id):
        conn=self.database.connect()
        rows=conn.execute("""SELECT agent_id,project_id,name,role,status,model,created_at,agent_type,capabilities,created_by,parent_agent_id
            FROM agents WHERE project_id=? ORDER BY created_at""",(project_id,)).fetchall()
        conn.close(); return rows
