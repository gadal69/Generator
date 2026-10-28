"""Trusted container entry point. Receives inputs, never the host's expected answers."""
import contextlib
import importlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
sys.path.insert(0,str(Path(__file__).resolve().parent))
from media_worker import gui,images


def main():
    request=json.load(sys.stdin)
    work=Path('/tmp/project');shutil.copytree('/inputs',work);os.chdir(work);sys.path.insert(0,str(work))
    check=request['check'];kind=check['kind']
    if kind=='gui':
        # Qt writes native diagnostics directly to stderr, outside redirect_stderr.
        saved=os.dup(2)
        with tempfile.TemporaryFile() as diagnostic:
            try:
                os.dup2(diagnostic.fileno(),2)
                with contextlib.redirect_stdout(io.StringIO()),contextlib.redirect_stderr(io.StringIO()):
                    try:result=gui(check)
                    except ImportError as exc:result={'unavailable':str(exc)}
                    except Exception as exc:result={'error':type(exc).__name__+': '+str(exc)[:500]}
            finally:os.dup2(saved,2);os.close(saved)
            diagnostic.seek(0);result['runtime_stderr']=diagnostic.read(2048).decode(errors='replace')
    elif kind in {'command','image_output'}:
        try:
            run=subprocess.run(check['argv'],input=check.get('stdin',''),text=True,capture_output=True,timeout=check.get('timeout',10),cwd=work)
            result={'exit_code':run.returncode,'stdout':run.stdout[:32768],'stderr':run.stderr[:32768]}
            if kind=='image_output' and run.returncode==0:
                try:result['images']=images(check['outputs'])
                except ImportError as exc:result['unavailable']=str(exc)
        except subprocess.TimeoutExpired:result={'timeout':True}
    else:
        outputs=[]
        with contextlib.redirect_stdout(io.StringIO()),contextlib.redirect_stderr(io.StringIO()):
            module=importlib.import_module('.'.join(Path(check['target']).with_suffix('').parts))
            function=getattr(module,check['function'])
            for case in check['cases']:
                try:outputs.append({'value':function(*case.get('args',[]))})
                except Exception as exc:outputs.append({'exception':type(exc).__name__,'message':str(exc)[:500]})
        result={'cases':outputs}
    sys.stdout.write(json.dumps(result,allow_nan=False))


if __name__=='__main__':
    main()
