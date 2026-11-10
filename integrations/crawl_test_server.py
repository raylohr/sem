"""Isolated integration-test fixture. Never used by production Compose."""
import asyncio
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import threading
from unittest.mock import patch
from .crawl_proxy import serve


class Fixture(BaseHTTPRequestHandler):
    def log_message(self,*args):
        pass
    def do_HEAD(self):
        self.send_response(200)
        self.send_header('Content-Length','9999999')
        self.end_headers()
    def do_GET(self):
        if self.path=='/robots.txt':
            body=b'User-agent: *\nDisallow: /\nSitemap: http://fixture.invalid/sitemap.xml\n'
            content='text/plain'
        elif self.path=='/sitemap.xml':
            body=b'<sitemapindex><sitemap><loc>http://fixture.invalid/pages.xml</loc></sitemap></sitemapindex>'
            content='application/xml'
        elif self.path=='/pages.xml':
            body=b'<urlset><url><loc>http://fixture.invalid/sitemap-child</loc></url><url><loc>http://sub.fixture.invalid/subpage</loc></url></urlset>'
            content='application/xml'
        elif self.path=='/':
            body=b'<html><body><div id="links"></div><script>document.getElementById("links").innerHTML=\'<a href="/from-js">JS</a>\';</script><a href="http://external.invalid/outbound">Extern</a></body></html>'
            content='text/html'
        else:
            body=b'<html><body>Gerenderde vervolgpagina<a href="http://fixture.invalid/">Home</a></body></html>'
            content='text/html'
        self.send_response(200)
        self.send_header('Content-Type',content)
        self.send_header('Content-Length',str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main():
    fixture=ThreadingHTTPServer(('127.0.0.1',0),Fixture)
    threading.Thread(target=fixture.serve_forever,daemon=True).start()
    async def fixture_dns(host,port):
        if host not in ('fixture.invalid','external.invalid') and not host.endswith('.fixture.invalid'):
            raise PermissionError
        return [('127.0.0.1',fixture.server_port)]
    with patch('integrations.crawl_proxy.resolve_target',side_effect=fixture_dns):
        asyncio.run(serve())


if __name__=='__main__':
    main()
