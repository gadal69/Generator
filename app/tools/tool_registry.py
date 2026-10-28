from datetime import datetime
import uuid

class ToolRegistry:
    def __init__(self,database):
        self.database=database
        self._tools={}

    def register(self,name,func,description=""):
        tid="TOOL-"+str(uuid.uuid4())[:8].upper()
        self._tools[name]=func
        conn=self.database.connect()
        conn.execute("INSERT OR REPLACE INTO tool_registry(tool_id,name,description,enabled,created_at) VALUES (?,?,?,?,?)",
                     (tid,name,description,1,datetime.now().isoformat()))
        conn.commit(); conn.close()
        return tid

    def enable(self,name,enabled=True):
        conn=self.database.connect(); conn.execute("UPDATE tool_registry SET enabled=? WHERE name=?",(1 if enabled else 0,name)); conn.commit(); conn.close()

    def execute(self,name,**kwargs):
        if name not in self._tools: raise KeyError(f"등록되지 않은 Tool: {name}")
        conn=self.database.connect(); row=conn.execute("SELECT enabled FROM tool_registry WHERE name=?",(name,)).fetchone(); conn.close()
        if not row or not row[0]: raise RuntimeError(f"비활성화된 Tool: {name}")
        return self._tools[name](**kwargs)

    def list_tools(self):
        conn=self.database.connect(); rows=conn.execute("SELECT tool_id,name,description,enabled,created_at FROM tool_registry ORDER BY name").fetchall(); conn.close()
        return rows
