"""Docker-only execution. Missing isolation is NOT_RUN, never host execution."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
import time
import uuid
from app.orchestrator.work_contract import file_key
from app.runtime.run_control import RunCancelled
from app.qa.docker_environment import inspect_environment


class IsolatedRunner:
    def __init__(self,settings=None,control=None):
        self.settings=settings or {};self.control=control

    def available(self,runtime):
        dependency_state=self.settings.get('project_dependencies',{})
        if dependency_state and dependency_state.get('status')!='READY':
            return None,dependency_state.get('reason','프로젝트 패키지 환경 미준비')+'\n'+dependency_state.get('remedy','')
        state=inspect_environment(self.settings,runtime)
        if state['status']!='READY':return None,state['reason']
        return (state['docker'],state['image']),None

    def run(self,artifacts,check):
        runtime=check.get('runtime','gui' if check['kind']=='gui' else 'python');ready,reason=self.available(runtime)
        if not ready:return {'status':'NOT_RUN','reason':reason}
        docker,image=ready;name='itni-verify-'+uuid.uuid4().hex
        if self.control:self.control.check()
        # Command lines and answers never become Docker flags or host commands.
        payload={'check':{k:v for k,v in check.items() if k in {'kind','target','argv','stdin','timeout','function','factory','actions'}}}
        if check['kind'] in {'gui','image_output'}:
            payload['check']['outputs']=[{'path':s['path'],'pixels':[{'xy':p['xy']} for p in s.get('pixels',[])]} for s in check.get('outputs',[])]
            payload['check']['widgets']=[{k:v for k,v in s.items() if k!='expected'} for s in check.get('widgets',[])]
        if check['kind']=='python_function':
            payload['check']['cases']=[{'args':case.get('args',[])} for case in check['cases']]
        timeout=min(max(float(check.get('timeout',15)),1),60)
        flags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0
        with tempfile.TemporaryDirectory(prefix='itni_bundle_') as directory:
            root=Path(directory);bundle=root/'inputs';runner=root/'runner';bundle.mkdir();runner.mkdir()
            total=0
            if len(artifacts)>256:return {'status':'NOT_RUN','reason':'실행 묶음 파일 개수 제한(256)을 초과했습니다.'}
            for item in artifacts:
                file_key(item['path']);path=item['path'].strip().replace('\\','/');data=item['content'].encode('utf-8');total+=len(data)
                if len(data)>2*1024*1024 or total>16*1024*1024:return {'status':'NOT_RUN','reason':'실행 묶음 크기 제한을 초과했습니다.'}
                destination=bundle/path;destination.parent.mkdir(parents=True,exist_ok=True);destination.write_bytes(data)
            shutil.copyfile(Path(__file__).with_name('container_worker.py'),runner/'worker.py')
            shutil.copyfile(Path(__file__).with_name('media_worker.py'),runner/'media_worker.py')
            argv=[docker,'run','--rm','-i','--name',name,'--network','none','--read-only',
                  '--cap-drop','ALL','--security-opt','no-new-privileges','--pids-limit','64',
                  '--memory','256m','--memory-swap','256m','--cpus','1','--user','65534:65534',
                  '--tmpfs','/tmp:rw,nosuid,noexec,size=64m,mode=1777',
                  '--mount',f'type=bind,source={bundle},target=/inputs,readonly',
                  '--mount',f'type=bind,source={runner},target=/runner,readonly',
                  '--entrypoint','python',image,'-I','/runner/worker.py']
            output=bytearray();process=None
            try:
                process=subprocess.Popen(argv,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,creationflags=flags)
                def drain():
                    while True:
                        data=process.stdout.read(4096)
                        if not data:break
                        if len(output)<65536:output.extend(data[:65536-len(output)])
                thread=threading.Thread(target=drain,daemon=True);thread.start()
                process.stdin.write(json.dumps(payload).encode());process.stdin.close();start=time.monotonic()
                while process.poll() is None:
                    if self.control:self.control.check()
                    if time.monotonic()-start>timeout:return {'status':'FAIL','reason':'격리 실행 시간 제한을 초과했습니다.'}
                    time.sleep(.05)
                thread.join(timeout=2)
                if process.returncode:return {'status':'FAIL','reason':'격리 실행이 비정상 종료되었습니다.','output':output.decode(errors='replace')[:1000]}
                try:return {'status':'OBSERVED','observation':json.loads(output)}
                except ValueError:return {'status':'FAIL','reason':'격리 실행 결과 형식이 잘못되었습니다.'}
            except (OSError,subprocess.SubprocessError) as exc:
                return {'status':'FAIL','reason':'격리 실행 오류: '+str(exc)[:500]}
            finally:
                try:subprocess.run([docker,'rm','-f',name],capture_output=True,timeout=10,creationflags=flags)
                except (OSError,subprocess.TimeoutExpired):
                    self.cleanup_error='격리 컨테이너 정리 상태 확인이 필요합니다: '+name
                if process and process.poll() is None:process.kill();process.wait(timeout=5)
                if getattr(self,'cleanup_error',None):
                    if self.control and self.control.cancelled.is_set():
                        if self.control.callback:self.control.callback({'stage':'cleanup_warning','reason':self.cleanup_error})
                    else:raise RuntimeError(self.cleanup_error)
