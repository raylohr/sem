"""Hard process deadline around untrusted JavaScript, including hung evaluate calls."""
import multiprocessing
import os
from pathlib import Path
import signal
import time

from .crawl_renderer import render_page
from .crawl_content import pack_content


def _child(pipe,url,configuration,profile,proxy):
    os.environ.pop('CRAWLER_RENDER_TOKEN',None)
    def gate(domain,rate):
        pipe.send(('ready',domain,rate))
        if pipe.recv()!='go':
            raise RuntimeError
    try:
        capture=render_page(url,configuration,profile,proxy,gate)
        pipe.send(('result',{**capture.metadata(),'artifacts':pack_content(capture)}))
    except Exception:
        try:
            pipe.send(('result',{'error':'render_failed'}))
        except Exception:
            pass
    finally:
        pipe.close()


def kill_tree(pid):
    found=[]
    def collect(current):
        if current in found or len(found)>=4096:
            return
        found.append(current)
        try:
            children=[]
            for task in Path(f'/proc/{current}/task').iterdir():
                try:
                    children.extend((task/'children').read_text().split())
                except OSError:
                    continue
        except OSError:
            return
        for child in children:
            collect(int(child))
    collect(pid)
    # Freeze first so a browser driver cannot keep spawning children during cleanup.
    for current in found:
        try:
            os.kill(current,signal.SIGSTOP)
        except (OSError,ProcessLookupError):
            pass
    for current in reversed(found):
        try:
            os.kill(current,signal.SIGKILL)
        except (OSError,ProcessLookupError):
            pass


def render_bounded(url,configuration,profile,proxy,navigation_gate,*,total_timeout=None):
    context=multiprocessing.get_context('spawn')
    parent,child=context.Pipe()
    process=context.Process(target=_child,args=(child,url,configuration,profile,proxy),daemon=True)
    process.start()
    child.close()
    deadline=time.monotonic()+(total_timeout if total_timeout is not None else
                              max(5,min(120,int(configuration.get('render_timeout_seconds',30))))+35)
    try:
        while time.monotonic()<deadline:
            if parent.poll(min(1,max(0,deadline-time.monotonic()))):
                try:
                    message=parent.recv()
                except EOFError:
                    return {'error':'render_failed'}
                if message[0]=='ready':
                    navigation_gate(message[1],message[2])
                    parent.send('go')
                elif message[0]=='result':
                    return message[1]
            if not process.is_alive():
                return {'error':'render_failed'}
        return {'error':'timeout','render_complete':False}
    finally:
        if process.is_alive():
            kill_tree(process.pid)
        process.join(timeout=3)
        parent.close()
