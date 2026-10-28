from datetime import datetime
class AgentManager:
    def __init__(self,database): self.database=database
    def register_agent(self,agent_id,project_id,name,role,model,agent_type="CORE",capabilities=None,created_by="AGT-CHIEF-001",parent_agent_id=None):
        now=datetime.now().isoformat(); import json
        conn=self.database.connect(); conn.execute("""INSERT OR REPLACE INTO agents
        (agent_id,project_id,name,role,status,model,created_at,agent_type,capabilities,created_by,parent_agent_id)
        VALUES (?,?,?,?,?,?,?,?,?,?,?)""",(agent_id,project_id,name,role,"IDLE",model,now,agent_type,json.dumps(capabilities or [],ensure_ascii=False),created_by,parent_agent_id)); conn.commit(); conn.close()
    def update_status(self,agent_id,status):
        conn=self.database.connect(); conn.execute("UPDATE agents SET status=? WHERE agent_id=?",(status,agent_id)); conn.commit(); conn.close()
    def get_agent(self,agent_id):
        conn=self.database.connect(); row=conn.execute("SELECT agent_id,project_id,name,role,status,model,created_at FROM agents WHERE agent_id=?",(agent_id,)).fetchone(); conn.close(); return row
    def get_agent_detail(self,agent_id):
        conn=self.database.connect(); row=conn.execute("SELECT agent_id,project_id,name,role,status,model,created_at,agent_type,capabilities,created_by,parent_agent_id FROM agents WHERE agent_id=?",(agent_id,)).fetchone(); conn.close(); return row
