from datetime import datetime
import uuid

class AgentRuntime:
    """Agent 실행을 공통 런타임으로 감싸고 실행 이력을 남긴다."""

    def __init__(self,database,orchestrator,tool_registry=None,knowledge_manager=None):
        self.database=database; self.orchestrator=orchestrator
        self.tool_registry=tool_registry; self.knowledge_manager=knowledge_manager

    def run(self,project_id,task_id,task_description=None,model_adapter=None):
        task=self.orchestrator.task_manager.get_task(task_id)
        if not task: raise ValueError(f"Task를 찾을 수 없습니다: {task_id}")
        run_id="RUN-"+str(uuid.uuid4())[:8].upper()
        start=datetime.now().isoformat()
        conn=self.database.connect()
        conn.execute("INSERT INTO runtime_runs(run_id,project_id,task_id,agent_id,status,input_text,output_text,started_at,finished_at,error_message) VALUES (?,?,?,?,?,?,?,?,?,?)",
                     (run_id,project_id,task_id,task[2],"RUNNING",task_description or task[4],None,start,None,None))
        conn.commit(); conn.close()
        try:
            result_id=self.orchestrator.execute_task(project_id,task_id,task_description,model_adapter=model_adapter)
            if result_id is None:
                message = "선행 작업이 완료되지 않아 작업이 보류되었습니다."
                self._finish(run_id,"WAITING",None,message)
                return {"run_id":run_id,"status":"WAITING","result_id":None,"message":message}
            result=self.orchestrator.result_manager.get_result(result_id)
            if result is None:
                raise RuntimeError(f"작업 결과를 찾을 수 없습니다: {result_id}")
            self._finish(run_id,"COMPLETED",result[3] if result else str(result_id))
            return {"run_id":run_id,"status":"COMPLETED","result_id":result_id}
        except Exception as e:
            self._finish(run_id,"ERROR",None,str(e)); raise

    def _finish(self,run_id,status,output=None,error=None):
        conn=self.database.connect()
        conn.execute("UPDATE runtime_runs SET status=?,output_text=?,finished_at=?,error_message=? WHERE run_id=?",
                     (status,output,datetime.now().isoformat(),error,run_id))
        conn.commit(); conn.close()

    def get_run(self,run_id):
        conn=self.database.connect(); row=conn.execute("SELECT * FROM runtime_runs WHERE run_id=?",(run_id,)).fetchone(); conn.close(); return row
