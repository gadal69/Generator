"""Prepare and smoke-test a Docker GUI runtime before connecting its immutable ID."""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import uuid
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from app.qa.project_verifier import ProjectVerifier
from app.qa.verification_contract import build_contract,blocking_failure,required_unverified

PROBE='''from PySide6.QtWidgets import QWidget,QVBoxLayout,QPushButton,QLineEdit
from PIL import Image
class Window(QWidget):
    def __init__(self):
        super().__init__();layout=QVBoxLayout(self)
        self.text=QLineEdit();self.text.setObjectName('input');layout.addWidget(self.text)
        button=QPushButton('save');button.setObjectName('save');button.clicked.connect(self.save);layout.addWidget(button)
    def save(self):
        Image.new('RGB',(2,2),'red').save('probe.png');self.text.setText('saved')
'''

def prepare(build=False):
    path=ROOT/'config/app_config.json';original=path.read_text(encoding='utf-8');config=json.loads(original)
    if build:
        base=config['verification']['images']['python']
        subprocess.run(['docker','tag',base,'itni-gui-base:local'],check=True,timeout=30)
        subprocess.run(['docker','build','--build-arg','BASE=itni-gui-base:local','-f',str(ROOT/'scripts/gui_verification.Dockerfile'),'-t','itni-gui-verification:local',str(ROOT/'scripts')],check=True,timeout=900)
    image=subprocess.check_output(['docker','image','inspect','itni-gui-verification:local','--format','{{.Id}}'],text=True,timeout=15).strip()
    if not re.fullmatch(r'sha256:[a-f0-9]{64}',image):raise ValueError('Immutable image ID missing')
    contract=build_contract('',{'version':1,'checks':[{'id':'runtime:gui','kind':'gui','target':'probe.py','factory':'Window',
        'actions':[{'action':'input','widget':'input','text':'test'},{'action':'click','widget':'save'}],
        'widgets':[{'widget':'input','expected':'saved'}],
        'outputs':[{'path':'probe.png','format':'PNG','size':[2,2],'pixels':[{'xy':[1,1],'rgb':[255,0,0]}]}],'timeout':45}]})
    rows=ProjectVerifier({'images':{'gui':image}}).verify([{'path':'probe.py','content':PROBE}],contract)
    if blocking_failure(rows) or required_unverified(rows):raise RuntimeError(json.dumps(rows,ensure_ascii=False))
    if path.read_text(encoding='utf-8')!=original:raise RuntimeError('설정이 변경되어 연결을 중단합니다.')
    config['verification']['images']['gui']=image
    temporary=path.with_name('.gui-config-'+uuid.uuid4().hex+'.tmp')
    try:
        temporary.write_text(json.dumps(config,ensure_ascii=False,indent=2)+'\n',encoding='utf-8');os.replace(temporary,path)
    finally:
        if temporary.exists():temporary.unlink()
    print(json.dumps({'status':'READY','image':image,'smoke':rows},ensure_ascii=False))

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--build',action='store_true');args=parser.parse_args();prepare(args.build)
