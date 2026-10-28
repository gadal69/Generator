"""Explicit live local-model acceptance run; never executes the generated application."""
import argparse
from contextlib import redirect_stdout, redirect_stderr
from datetime import datetime
import json
from pathlib import Path
import sys
import time
import uuid
import shutil
from zoneinfo import ZoneInfo

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))

from app.orchestrator.orchestrator import Orchestrator
from app.storage.database import Database
from app.utils.path_manager import PathManager
from app.models.adapter_factory import create_local_adapter


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--model')
    parser.add_argument('--qa',choices=['agent','rule'],default='agent')
    parser.add_argument('--timeout',type=int,default=300)
    parser.add_argument('--no-thinking',action='store_true',help='Explicit test override; only for Ollama models reporting thinking support.')
    parser.add_argument('--reuse-analysis',type=Path,help='Reuse a prior test analysis only when its stored project has identical requirements.')
    args=parser.parse_args()
    config=json.loads((ROOT/'config/app_config.json').read_text(encoding='utf-8'))
    if config.get('ai',{}).get('provider','ollama') not in {'ollama','vllm'}:
        raise RuntimeError('This test uses a configured local provider only.')
    model=args.model or config['ai']['default_model']
    request=(ROOT/'docs/CALCULATOR_TEST_PROMPT.txt').read_text(encoding='utf-8').strip()
    database=Database(PathManager().database/'itni_ai_team.db')
    orchestrator=Orchestrator(database)
    name='계산기 기능검증 재시험 '+uuid.uuid4().hex[:8]
    started=time.monotonic()
    report={'model':model,'qa_mode':args.qa,'started_at':datetime.now(ZoneInfo('Asia/Seoul')).isoformat(),
            'workflow':'same backend as the desktop app; analysis confirmation automated for this authorized test',
            'application_execution':'NOT_RUN'}
    report['test_overrides']={'timeout':args.timeout,'disable_thinking':args.no_thinking}
    logs=ROOT/'logs';logs.mkdir(exist_ok=True)
    try:
        adapter=create_local_adapter(model=model)
        if not adapter.has_model():
            raise RuntimeError('The selected local model is unavailable: '+model)
        if args.no_thinking:
            if config['ai']['provider']!='ollama':
                raise RuntimeError('--no-thinking requires an Ollama model reporting thinking support.')
            metadata=next((item for item in adapter.health().get('models',[]) if item.get('name')==model),{})
            if 'thinking' not in metadata.get('capabilities',[]):
                raise RuntimeError('The server did not report thinking support; no unsupported option was sent.')
        def factory(name):
            return create_local_adapter(model=name,timeout=args.timeout,
                request_options={'think':False} if args.no_thinking else None)
        print('ANALYSIS_STARTED',model,flush=True)
        if args.reuse_analysis:
            previous=json.loads(args.reuse_analysis.read_text(encoding='utf-8'))
            with database.connection() as conn:
                row=conn.execute('SELECT description FROM projects WHERE project_id=?',(previous.get('project_id'),)).fetchone()
            if not row or row[0]!=request or not isinstance(previous.get('analysis'),dict):
                raise ValueError('Analysis reuse requires the same recorded requirements and a saved analysis.')
            decision=previous['analysis']
            report['reused_analysis_from']=str(args.reuse_analysis)
        else:
            decision=orchestrator.team_runner.analyze_user_request(request,model_override=model,adapter_factory=factory)
        report['analysis_elapsed_seconds']=round(time.monotonic()-started,2)
        report['analysis']=decision
        if decision.get('cloud_ai_required'):
            raise RuntimeError('Local-only test stopped because the analysis requested cloud AI.')
        print('ANALYSIS_COMPLETE',report['analysis_elapsed_seconds'],decision.get('core_roles'),flush=True)
        with (logs/'calculator_verification.log').open('a',encoding='utf-8',buffering=1) as log:
            log.write('\n=== '+report['started_at']+' '+name+' ===\n')
            with redirect_stdout(log),redirect_stderr(log):
                result=orchestrator.run_user_request(request,project_name=name,
                    analysis_override=decision,model_override=model,qa_mode=args.qa,
                    approval_mode='manual',verbose=True,adapter_factory=factory)
        report.update(status=result['status'],project_id=result['project_id'],
            code_verifications=result.get('code_verifications'),qa_reviews=result['qa_reviews'])
        print('WORKFLOW_COMPLETE',result['status'],result['project_id'],flush=True)
    except Exception as exc:
        report.update(status='ERROR',error=type(exc).__name__+': '+str(exc))
        with database.connection() as conn:
            row=conn.execute('SELECT project_id FROM projects WHERE name=?',(name,)).fetchone()
        if row:report['project_id']=row[0]
        print('WORKFLOW_ERROR',report['error'],flush=True)
    finally:
        report['elapsed_seconds']=round(time.monotonic()-started,2)
        report['finished_at']=datetime.now(ZoneInfo('Asia/Seoul')).isoformat()
        history=ROOT/'docs/calculator_retests';history.mkdir(exist_ok=True)
        latest=ROOT/'docs/CALCULATOR_RETEST_RESULT.json'
        if latest.exists():
            old=json.loads(latest.read_text(encoding='utf-8'))
            old_key=old.get('project_id') or 'previous-'+uuid.uuid4().hex[:8]
            shutil.copy2(latest,history/(old_key+'.json'))
        latest.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        (history/((report.get('project_id') or uuid.uuid4().hex[:8])+'.json')).write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        print('REPORT',str(ROOT/'docs/CALCULATOR_RETEST_RESULT.json'),flush=True)
    return 0 if report['status']=='PENDING_APPROVAL' else 1


if __name__=='__main__':
    raise SystemExit(main())
