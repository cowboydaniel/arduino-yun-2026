#!/usr/bin/env python3
# Preview the Yun Panel with a fake board: python3 serve.py [port]
# Then open http://localhost:8080/ (the demo password is "arduino").

import functools
import http.server
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
WWW = os.path.join(HERE, '..', 'files', 'www')


class Handler(http.server.SimpleHTTPRequestHandler):
    def do_GET(self):
        if self.path.split('?')[0] in ('/', '/index.html'):
            with open(os.path.join(WWW, 'index.html'), 'rb') as f:
                page = f.read().replace(b'<script src="yun/app.js">',
                                        b'<script src="/demo/mock.js"></script>\n<script src="yun/app.js">')
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.send_header('Content-Length', str(len(page)))
            self.end_headers()
            self.wfile.write(page)
            return
        if self.path == '/demo/mock.js':
            return self._serve(HERE, 'mock.js')
        return super().do_GET()

    def _serve(self, directory, name):
        with open(os.path.join(directory, name), 'rb') as f:
            body = f.read()
        self.send_response(200)
        self.send_header('Content-Type', 'text/javascript')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)


if __name__ == '__main__':
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8080
    handler = functools.partial(Handler, directory=WWW)
    print(f'Yun Panel demo on http://localhost:{port}/ (password: arduino)')
    http.server.ThreadingHTTPServer(('127.0.0.1', port), handler).serve_forever()
