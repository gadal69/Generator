"""Deterministic real Docker integration; no claim of AI-generated sample success."""
import json
from pathlib import Path
import subprocess
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from app.qa.project_verifier import ProjectVerifier
from app.qa.verification_contract import build_contract,blocking_failure,required_unverified

STORE='''import sqlite3
def save_event():
    db=sqlite3.connect('events.db')
    try:
        db.execute('CREATE TABLE IF NOT EXISTS events(name TEXT)')
        db.execute('INSERT INTO events VALUES (?)',('saved',));db.commit()
    finally:db.close()
def count():
    db=sqlite3.connect('events.db')
    try:return db.execute('SELECT count(*) FROM events').fetchone()[0]
    finally:db.close()
'''
EDITOR='''from PySide6.QtWidgets import QWidget,QVBoxLayout,QLineEdit,QPushButton,QLabel,QFileDialog
from PIL import Image
import store
import subprocess,sys
class Window(QWidget):
    def __init__(self):
        super().__init__();layout=QVBoxLayout(self)
        self.color=QLineEdit();self.color.setObjectName('color');layout.addWidget(self.color)
        self.status=QLabel('ready');self.status.setObjectName('status');layout.addWidget(self.status)
        self.image=None
        Image.new('RGB',(4,3),'white').save('input.png')
        for name,handler in [('open',self.open),('paint',self.paint),('save',self.save)]:
            button=QPushButton(name);button.setObjectName(name);button.clicked.connect(handler);layout.addWidget(button)
    def open(self):
        path,_=QFileDialog.getOpenFileName(self,'Open')
        if path:
            with Image.open(path) as source:self.image=source.convert('RGB')
            self.status.setText('opened')
    def paint(self):
        if self.color.text()!='red':self.status.setText('invalid');return
        self.image.paste((255,0,0),(0,0,self.image.width,self.image.height));self.status.setText('edited')
    def save(self):
        path,_=QFileDialog.getSaveFileName(self,'Save')
        if path:
            self.image.save(path);store.save_event()
            count=subprocess.check_output([sys.executable,'-c','import store; print(store.count())'],text=True).strip()
            self.status.setText('saved:'+count)
'''

def main():
    image=subprocess.check_output(['docker','image','inspect','itni-gui-verification:local','--format','{{.Id}}'],text=True).strip()
    settings={'images':{'gui':image,'python':image}}
    outputs=[{'path':'edited.png','format':'PNG','size':[4,3],'pixels':[{'xy':[0,0],'rgb':[255,0,0]},{'xy':[3,2],'rgb':[255,0,0]}]}]
    actions=[{'action':'open_file','widget':'open','path':'input.png'},{'action':'input','widget':'color','text':'red'},
             {'action':'click','widget':'paint'},{'action':'save_file','widget':'save','path':'edited.png'}]
    checks=[{'id':'gui:save','kind':'gui','target':'editor.py','factory':'Window','actions':actions,
             'widgets':[{'widget':'status','expected':'saved:1'}],'outputs':outputs,'timeout':45},
            {'id':'gui:invalid','kind':'gui','target':'editor.py','factory':'Window','actions':actions[:1]+[{'action':'input','widget':'color','text':'bad'},{'action':'click','widget':'paint'}],
             'widgets':[{'widget':'status','expected':'invalid'}],'timeout':45}]
    checks.append({'id':'gui:cancel','kind':'gui','target':'editor.py','factory':'Window',
        'actions':[{'action':'open_file','widget':'open','path':''}],
        'widgets':[{'widget':'status','expected':'ready'}],'category':'boundary','timeout':45})
    for check,category in zip(checks,['normal','invalid','boundary']):
        check.update(requirements=['gui'],category=category)
    checks.append({'id':'image:decode','kind':'image_output','target':'editor.py',
        'argv':['python','-c',"from PIL import Image; Image.new('RGB',(4,3),'red').save('edited.png')"],
        'outputs':outputs,'requirements':['image'],'timeout':30})
    requirements=[{'id':'gui','description':'열기·입력·편집·저장과 DB 별도 프로세스 재조회'},
        {'id':'image','description':'이미지 디코딩·크기·형식·픽셀 기준','required_categories':['normal'],
         'category_rationale':'고정 이미지 출력에 대한 검사. 잘못된 크기 기준은 별도 음성 대조로 확인.'}]
    artifacts=[{'path':'store.py','content':STORE},{'path':'editor.py','content':EDITOR}]
    contract=build_contract('',{'version':1,'checks':checks,'requirements':requirements})
    rows=ProjectVerifier(settings).verify(artifacts,contract)
    # A visible success string cannot conceal a wrong image edit.
    wrong=build_contract('',{'version':1,'checks':[dict(checks[0],id='negative:wrong-edit',requirements=[])]})
    wrong['checks'][0]['outputs']=[dict(outputs[0],size=[9,9])]
    negative=ProjectVerifier(settings).verify(artifacts,wrong)
    report={'checks':rows,'negative_control':negative,'image':image,
            'sample_origin':'DETERMINISTIC_TEST_FIXTURE_NOT_MODEL_OUTPUT',
            'screen_review':'NOT_RUN','native_dialogs':'NOT_RUN','real_external_api':'NOT_RUN',
            'status':'PASS' if not blocking_failure(rows) and not required_unverified(rows) and blocking_failure(negative) else 'FAIL'}
    output=ROOT/'docs'/'gui_media_acceptance.json';output.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False));return 0 if report['status']=='PASS' else 1

if __name__=='__main__':raise SystemExit(main())
