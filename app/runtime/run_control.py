"""Cooperative cancellation and observable execution without killing application threads."""
import threading
import time
from queue import Queue, Empty


class RunCancelled(Exception):
    pass


class RunControl:
    def __init__(self,callback=None):
        self.cancelled=threading.Event();self.callback=callback;self.started=time.monotonic()

    def cancel(self):self.cancelled.set()

    def check(self):
        if self.cancelled.is_set():raise RunCancelled('사용자가 실행을 중단했습니다.')

    def emit(self,stage,**details):
        self.check()
        event=dict(stage=stage,elapsed_seconds=round(time.monotonic()-self.started,2),**details)
        if self.callback:self.callback(event)

    def call(self,operation):
        self.check();queue=Queue(maxsize=1)
        def work():
            try:queue.put((True,operation()))
            except BaseException as exc:queue.put((False,exc))
        thread=threading.Thread(target=work,daemon=True);thread.start()
        while True:
            self.check()
            try:ok,value=queue.get(timeout=.1)
            except Empty:continue
            self.check()
            if ok:return value
            raise value


class ControlledAdapter:
    def __init__(self,adapter,control):self.adapter=adapter;self.control=control
    def __getattr__(self,name):return getattr(self.adapter,name)
    def generate(self,prompt,images=None):
        return self.control.call(lambda:self.adapter.generate(prompt) if images is None else self.adapter.generate(prompt,images=images))
    def generate_json(self, prompt, schema):
        operation = getattr(self.adapter, 'generate_json', None)
        return self.control.call(lambda: operation(prompt, schema) if callable(operation) else self.adapter.generate(prompt))
    def search(self,prompt):return self.control.call(lambda:self.adapter.search(prompt))
