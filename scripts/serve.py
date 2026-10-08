"""Serves this folder on http://127.0.0.1:8891 with HTTP range requests, which PMTiles needs
to read a few tiles out of a large file. Python's own http.server sends whole files.

Usage: python3 scripts/serve.py
"""

import http.server
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8891


class Ranges(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT), **kwargs)

    def end_headers(self):
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Expose-Headers", "Content-Range, Content-Length")
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def do_GET(self):
        match = re.fullmatch(r"bytes=(\d+)-(\d*)", self.headers.get("Range", ""))
        path = Path(self.translate_path(self.path))
        if not match or not path.is_file():
            return super().do_GET()
        size = path.stat().st_size
        start = int(match[1])
        end = min(int(match[2]) if match[2] else size - 1, size - 1)
        if start >= size:
            self.send_response(416)
            self.send_header("Content-Range", f"bytes */{size}")
            self.end_headers()
            return
        self.send_response(206)
        self.send_header("Content-Type", self.guess_type(str(path)))
        self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.send_header("Content-Length", str(end - start + 1))
        self.end_headers()
        with path.open("rb") as file:
            file.seek(start)
            self.wfile.write(file.read(end - start + 1))


print(f"Open http://127.0.0.1:{PORT}/visor/")
http.server.ThreadingHTTPServer(("127.0.0.1", PORT), Ranges).serve_forever()
