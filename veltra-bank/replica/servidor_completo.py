"""Servidor local para la réplica completa de Veltra Bank.

Sirve login.html y verificacion-local.html en 127.0.0.1:8001.
Acepta el login de prueba y registra el código enviado en claro a
/api/verificar. No guarda cuerpos en disco.
"""

from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit
import json
import re


ROOT = Path(__file__).resolve().parent


def send_json(handler, status, payload):
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT), **kwargs)

    def translate_path(self, path):
        route = urlsplit(path).path
        if route in ("/", "/login"):
            return str(ROOT / "login.html")
        if route == "/verificar":
            return str(ROOT / "verificacion-local.html")
        return super().translate_path(path)

    def do_POST(self):
        route = urlsplit(self.path).path
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length)

        try:
            data = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            send_json(self, 400, {"ok": False, "error": "JSON invalido."})
            return

        if route == "/api/login":
            print("LOCAL POST /api/login (JSON recibido; valores omitidos)", flush=True)
            send_json(self, 200, {"ok": True, "next": "/verificar"})
            return

        if route == "/api/verificar":
            codigo = str(data.get("codigo", ""))
            if re.fullmatch(r"[0-9]{6}", codigo):
                print(f"LOCAL POST /api/verificar codigo={codigo}", flush=True)
            else:
                print("LOCAL POST /api/verificar (codigo ausente o no valido)", flush=True)
            send_json(self, 200, {
                "ok": False,
                "error": "Codigo de verificacion incorrecto.",
            })
            return

        self.send_error(404)


if __name__ == "__main__":
    print("Servidor completo en http://127.0.0.1:8001/", flush=True)
    ThreadingHTTPServer(("127.0.0.1", 8001), Handler).serve_forever()
