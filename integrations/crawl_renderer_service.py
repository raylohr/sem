"""Internal rendering RPC. The container has no production credentials."""
import hmac
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import threading
import time

from .crawl_process import render_bounded

SLOTS = threading.BoundedSemaphore(20)


class NavigationLimiter:
    def __init__(self):
        self.lock=threading.Lock()
        self.next_start={}

    def acquire(self,domain,rate):
        rate=max(.05,min(20,rate))
        deadline=time.monotonic()+10
        while True:
            if time.monotonic()>deadline:
                raise TimeoutError("Navigation budget wait exceeded")
            with self.lock:
                now=time.monotonic()
                wait=self.next_start.get(domain,0)-now
                if wait<=0:
                    self.next_start[domain]=now+1/rate
                    return
            time.sleep(min(.2,wait))


NAVIGATIONS=NavigationLimiter()


class Handler(BaseHTTPRequestHandler):
    def log_message(self,*args):
        pass  # Never log URLs, response bodies or authorization headers.

    def reply(self,status,payload):
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header('Content-Type','application/json')
        self.send_header('Content-Length',str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        self.reply(200 if self.path=='/health/' else 404,{'status':'ready' if self.path=='/health/' else 'not_found'})

    def do_POST(self):
        expected = os.environ.get('CRAWLER_RENDER_TOKEN','')
        if not expected or not hmac.compare_digest(self.headers.get('Authorization',''),'Bearer '+expected):
            self.reply(403,{'error':'forbidden'})
            return
        if self.path != '/render/':
            self.reply(404,{'error':'not_found'})
            return
        try:
            size = int(self.headers.get('Content-Length','0'))
            if not 0<size<=2*1024*1024:
                raise ValueError
            payload = json.loads(self.rfile.read(size))
            proxy = os.environ.get('CRAWLER_EGRESS_PROXY','')
            if not proxy:
                raise ValueError
            if not SLOTS.acquire(blocking=False):
                self.reply(429,{'error':'busy'})
                return
            try:
                result = render_bounded(payload['url'],payload['configuration'],payload['profile'],proxy,NAVIGATIONS.acquire)
                self.reply(200,result)
            finally:
                SLOTS.release()
        except Exception:
            self.reply(502,{'error':'render_failed'})


if __name__ == '__main__':
    server = ThreadingHTTPServer(('0.0.0.0',8081),Handler)
    server.daemon_threads = True
    server.serve_forever()
