"""Real RPC and hard-timeout test against local fixtures; no external networking."""
import asyncio
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import os
import threading
from unittest.mock import patch

import requests
from .crawl_proxy import handle_client
from .crawl_renderer_service import Handler


class Fixture(BaseHTTPRequestHandler):
    def log_message(self,*args):
        pass
    def do_GET(self):
        body=(b'<html><body><script>while(true){}</script></body></html>' if self.path=='/hang' else
              b'<html><body><main id="result"></main><script>document.getElementById("result").textContent="RPC works"</script></body></html>')
        self.send_response(200)
        self.send_header('Content-Type','text/html')
        self.send_header('Content-Length',str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main():
    fixture=ThreadingHTTPServer(('127.0.0.1',0),Fixture)
    threading.Thread(target=fixture.serve_forever,daemon=True).start()
    loop=asyncio.new_event_loop()
    ready=threading.Event()
    state={}
    # Child renderer processes use a real local proxy. Only this proxy's resolver
    # is mocked; the browser's target URL still undergoes all scope checks.
    async def fixture_dns(host,port):
        if host!='fixture.invalid':
            raise PermissionError
        return [('127.0.0.1',fixture.server_port)]
    async def start():
        state['server']=await asyncio.start_server(handle_client,'127.0.0.1',0)
        state['port']=state['server'].sockets[0].getsockname()[1]
        ready.set()
    def worker():
        asyncio.set_event_loop(loop)
        loop.run_until_complete(start())
        loop.run_forever()
    threading.Thread(target=worker,daemon=True).start()
    ready.wait(5)
    os.environ['CRAWLER_EGRESS_PROXY']=f'http://127.0.0.1:{state["port"]}'
    os.environ['CRAWLER_RENDER_TOKEN']='isolated-browser-smoke'
    rpc=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    threading.Thread(target=rpc.serve_forever,daemon=True).start()
    configuration={'domain':'fixture.invalid','website':'http://fixture.invalid/','include_subdomains':False,
        'respect_robots':False,'render_timeout_seconds':5,'settle_seconds':1,'pages_per_second':20,
        'excluded_hosts':[],'excluded_paths':[]}
    address=f'http://127.0.0.1:{rpc.server_port}/render/'
    try:
        with patch('integrations.crawl_proxy.resolve_target',side_effect=fixture_dns),requests.Session() as session:
            session.trust_env=False
            response=session.post(address,json={},timeout=10)
            assert response.status_code==403
            payload={'url':'http://fixture.invalid/','profile':'chrome_windows','configuration':configuration}
            response=session.post(address,json=payload,headers={'Authorization':'Bearer isolated-browser-smoke'},timeout=25)
            result=response.json()
            assert result.get('render_complete'),result
            assert result.get('text_length',0)>0,result
            assert 'rendered_html' not in result
            print('Authenticated render RPC and JavaScript metadata: OK',flush=True)
            from .crawl_process import render_bounded
            timed=render_bounded('http://fixture.invalid/hang',configuration,'chrome_windows',
                os.environ['CRAWLER_EGRESS_PROXY'],lambda domain,rate:None,total_timeout=8)
            assert timed.get('error')=='timeout',timed
            print('Infinite JavaScript stopped by process deadline: OK',flush=True)
    finally:
        rpc.shutdown()
        fixture.shutdown()
        loop.call_soon_threadsafe(loop.stop)


if __name__=='__main__':
    main()
