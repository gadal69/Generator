"""Container-only Qt interaction and decoded image observations."""
import importlib
import os
import sys
from pathlib import Path
from unittest.mock import patch


def images(paths):
    from PIL import Image
    observations=[]
    for spec in paths:
        path=Path(spec['path'])
        row={'path':spec['path'],'exists':path.is_file()}
        if row['exists']:
            try:
                if path.stat().st_size>16*1024*1024:raise ValueError('image exceeds 16 MiB')
                with Image.open(path) as image:
                    if image.width*image.height>16_000_000:raise ValueError('image exceeds pixel limit')
                    image.load()
                    row.update(format=image.format,size=list(image.size),mode=image.mode)
                    rgb=image.convert('RGB')
                    row['pixels']=[list(rgb.getpixel(tuple(p['xy']))) for p in spec.get('pixels',[])]
            except Exception as exc:row['error']=type(exc).__name__+': '+str(exc)[:200]
        observations.append(row)
    return observations


def gui(check):
    os.environ['QT_QPA_PLATFORM']='offscreen'
    os.environ['QT_LOGGING_RULES']='*.warning=false'
    os.environ['XDG_CACHE_HOME']='/tmp/itni-gui-cache'
    from PySide6.QtWidgets import QApplication,QFileDialog
    from PySide6.QtCore import QObject,Qt
    from PySide6.QtTest import QTest
    app=QApplication.instance() or QApplication([])
    module=importlib.import_module('.'.join(Path(check['target']).with_suffix('').parts))
    window=getattr(module,check['factory'])()
    window.show();app.processEvents()
    steps=[]
    errors=[];old_hook=sys.excepthook
    sys.excepthook=lambda kind,value,tb:errors.append(kind.__name__+': '+str(value)[:200])
    try:
        for action in check['actions']:
            target=window.findChild(QObject,action['widget'])
            if target is None:raise ValueError('widget not found: '+action['widget'])
            if not target.isEnabled() or not target.isVisible():raise ValueError('widget unavailable: '+action['widget'])
            kind=action['action']
            if kind=='input':
                target.setFocus();QTest.keyClick(target,Qt.Key_A,Qt.ControlModifier)
                QTest.keyClick(target,Qt.Key_Backspace)
                QTest.keyClicks(target,action['text'])
            elif kind in {'open_file','save_file'}:
                method='getOpenFileName' if kind=='open_file' else 'getSaveFileName'
                # Exercise the real handler; native dialog interaction is a separate scope.
                with patch.object(QFileDialog,method,return_value=(action['path'],'')):
                    QTest.mouseClick(target,Qt.LeftButton);app.processEvents()
            else:QTest.mouseClick(target,Qt.LeftButton)
            app.processEvents();steps.append({'action':kind,'widget':action['widget'],'status':'EXECUTED'})
        widgets={}
        for spec in check.get('widgets',[]):
            item=window.findChild(QObject,spec['widget'])
            if item is None:raise ValueError('result widget not found: '+spec['widget'])
            widgets[spec['widget']]=item.property(spec.get('property','text'))
        return {'steps':steps,'widgets':widgets,'images':images(check.get('outputs',[])),
                'error':'; '.join(errors),
                'native_dialogs':'NOT_RUN','screen_review':'NOT_RUN','scope':'QT_OFFSCREEN_WIDGET_EVENTS'}
    finally:
        sys.excepthook=old_hook
        window.close();app.processEvents()
