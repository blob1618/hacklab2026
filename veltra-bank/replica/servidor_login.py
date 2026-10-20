"""Servidor local para la primera etapa de la réplica de Veltra Bank.

Sirve login.html y sus recursos en 127.0.0.1:8001, atiende POST /api/login
y muestra en la terminal el JSON recibido. No guarda el cuerpo en disco.
"""

from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit
import json


ROOT = Path(__file__).resolve().parent


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT), **kwargs)

    def translate_path(self, path):
        route = urlsplit(path).path
        if route in ("/", "/login"):
            return str(ROOT / "login.html")
        return super().translate_path(path)

    def do_POST(self):
        if urlsplit(self.path).path != "/api/login":
            self.send_error(404)
            return

        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length)

        try:
            data = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            self.send_error(400, "Invalid JSON")
            return

        print(
            f"TEST POST /api/login: {json.dumps(data, ensure_ascii=False)}",
            flush=True,
        )

        body = json.dumps(
            {
                "ok": False,
                "error": "Usuario o contrasena incorrectos.",
            },
            ensure_ascii=False,
        ).encode("utf-8")

        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


if __name__ == "__main__":
    print("Servidor de login en http://127.0.0.1:8001/", flush=True)
    ThreadingHTTPServer(("127.0.0.1", 8001), Handler).serve_forever()
