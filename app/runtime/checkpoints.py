"""Atomic checkpoints and stage events for one project execution."""
from datetime import datetime
import hashlib
import json


def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False).encode()).hexdigest()


class Checkpoints:
    def __init__(self,database):self.database=database

    def save(self,project_id,state,status='RUNNING'):
        with self.database.connection() as conn:
            conn.execute('''INSERT INTO workflow_checkpoints(project_id,status,state_json,updated_at)
                VALUES(?,?,?,?) ON CONFLICT(project_id) DO UPDATE SET
                status=excluded.status,state_json=excluded.state_json,updated_at=excluded.updated_at''',
                (project_id,status,json.dumps(state,ensure_ascii=False),datetime.now().isoformat()))

    def load(self,project_id):
        with self.database.connection() as conn:
            row=conn.execute('SELECT status,state_json FROM workflow_checkpoints WHERE project_id=?',(project_id,)).fetchone()
        if not row:raise ValueError('재개할 실행 체크포인트가 없습니다.')
        return row[0],json.loads(row[1])

    def event(self,project_id,team_run_id,event):
        with self.database.connection() as conn:
            conn.execute('INSERT INTO workflow_events(project_id,team_run_id,stage,event_json,created_at) VALUES(?,?,?,?,?)',
                (project_id,team_run_id,event['stage'],json.dumps(event,ensure_ascii=False),datetime.now().isoformat()))
