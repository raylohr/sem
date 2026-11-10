"""Real-browser tests on loopback fixtures, inside a network-disabled container.

The DNS resolver is mocked only inside this test process. Production proxy code
has no private-network bypass switch.
"""
import asyncio
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import threading
from unittest.mock import patch

from .crawl_proxy import handle_client
from .crawl_renderer import render_page


class Fixture(BaseHTTPRequestHandler):
    def log_message(self,*args):
        pass
    def do_GET(self):
        body=b'<html><head><title>Browser test</title></head><body><main id="result">voor render</main><script>document.getElementById("result").textContent="na render";</script><a href="/next">Volgende</a><a href="https://example.org/">Extern</a></body></html>'
        self.send_response(200)
        self.send_header('Content-Type','text/html; charset=utf-8')
        self.send_header('Content-Length',str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main():
    fixture=ThreadingHTTPServer(('127.0.0.1',0),Fixture)
    threading.Thread(target=fixture.serve_forever,daemon=True).start()
    loop=asyncio.new_event_loop()
    ready=threading.Event()
    state={}
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
    async def fixture_dns(host,port):
        if host!='fixture.invalid':
            raise PermissionError
        return [('127.0.0.1',fixture.server_port)]
    configuration={'domain':'fixture.invalid','website':'http://fixture.invalid/', 'include_subdomains':False,
        'respect_robots':False,'render_timeout_seconds':30,'settle_seconds':1,
        'excluded_hosts':[],'excluded_paths':[]}
    try:
        with patch('integrations.crawl_proxy.resolve_target',side_effect=fixture_dns):
            for profile in ('chrome_windows','firefox_linux','safari_macos'):
                capture=render_page('http://fixture.invalid/',configuration,profile,f'http://127.0.0.1:{state["port"]}')
                assert capture.http_status==200,(profile,capture.metadata())
                assert capture.render_complete,(profile,capture.metadata())
                assert b'>voor render<' in capture.server_html,profile
                assert '>na render<' in capture.rendered_html,profile
                assert 'na render' in capture.text,profile
                assert 'http://fixture.invalid/next' in capture.links,profile
                assert 'Headless' not in capture.user_agent,profile
                print(f'{profile}: HTTP, JavaScript rendering, HTML and links OK',flush=True)
    finally:
        fixture.shutdown()
        loop.call_soon_threadsafe(loop.stop)


if __name__=='__main__':
    main()
