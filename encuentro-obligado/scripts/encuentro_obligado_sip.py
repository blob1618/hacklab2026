#!/usr/bin/env python3
"""Cliente SIP para la línea de reclamos de Encuentro Obligado.

Uso: python3 scripts/encuentro_obligado_sip.py \
    --sip-uri sip:destino@dominio --username USUARIO --password CLAVE_SIP \
    --identificador ID --pin CLAVE_TELEFONICA --reclamo NUMERO --accion 2

Los datos se pasan por parámetros y el IVR recibe los tonos automáticamente.
Dos o tres --reclamo junto con --parallel inician llamadas simultáneas.
Por defecto no se guardan credenciales ni audio.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import ipaddress
import os
import re
import shutil
import socket
import struct
import subprocess
import sys
import threading
import time
import uuid
import wave
from pathlib import Path

DOMAIN = ""
USER = ""
TARGET_USER = ""
SIP_PORT = 5060


def destination_uri() -> str:
    port = f":{SIP_PORT}" if SIP_PORT != 5060 else ""
    return f"sip:{TARGET_USER}@{DOMAIN}{port}"

def local_ip_for(host: str, port: int) -> str:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
        probe.connect((host, port))
        return probe.getsockname()[0]


LOCAL_IP = ""

ULAW_DECODE = None


def ulaw_table():
    global ULAW_DECODE
    if ULAW_DECODE is None:
        tbl = []
        for i in range(256):
            u = ~i & 0xFF
            t = ((u & 0x0F) << 3) + 0x84
            t <<= (u & 0x70) >> 4
            tbl.append((t - 0x84) if u & 0x80 else (0x84 - t))
        ULAW_DECODE = tbl
    return ULAW_DECODE


def ulaw_to_pcm(data: bytes) -> bytes:
    tbl = ulaw_table()
    return struct.pack("<%dh" % len(data), *[tbl[b] for b in data])


def digest(user, pw, realm, nonce, uri, method, qop=None, cnonce=None, nc=None):
    def h(x):
        return hashlib.md5(x.encode()).hexdigest()

    if qop:
        return h(f"{h(f'{user}:{realm}:{pw}')}:{nonce}:{nc}:{cnonce}:{qop}:{h(f'{method}:{uri}')}")
    return h(f"{h(f'{user}:{realm}:{pw}')}:{nonce}:{h(f'{method}:{uri}')}")


def parse_headers(msg: str) -> dict:
    head = msg.split("\r\n\r\n", 1)[0]
    lines = head.split("\r\n")
    status = lines[0]
    hdrs = {}
    for line in lines[1:]:
        if ":" in line:
            k, v = line.split(":", 1)
            hdrs.setdefault(k.strip().lower(), []).append(v.strip())
    return {"status": status, "headers": hdrs}


def parse_sip_uri(value: str) -> tuple[str, str, int]:
    """Acepta únicamente la dirección SIP publicada por el laboratorio."""
    match = re.fullmatch(r"sip:([^@\s]+)@([A-Za-z0-9.-]+)(?::(\d{1,5}))?", value.strip(), re.I)
    if not match:
        raise ValueError("la dirección debe tener el formato sip:destino@dominio[:puerto]")
    target, host, port_text = match.groups()
    port = int(port_text) if port_text else 5060
    if not 1 <= port <= 65535:
        raise ValueError("el puerto SIP debe estar entre 1 y 65535")
    return target, host, port


def parallel_child_command(args, claim: str) -> list[str]:
    """Construye una llamada independiente para cada reclamo."""
    command = [
        sys.executable, str(Path(__file__).resolve()),
        "--sip-uri", args.sip_uri,
        "--username", args.username,
        "--password", args.password,
        "--identificador", args.identificador,
        "--pin", args.pin,
        "--reclamo", claim,
        "--accion", args.accion,
        "--ivr-times", ",".join(f"{value:g}" for value in args.ivr_times),
        "--seconds", str(args.seconds),
    ]
    if args.record:
        suffix = args.record.suffix or ".wav"
        output = args.record.with_name(f"{args.record.stem}-{claim}{suffix}")
        command.extend(("--record", str(output)))
    return command


def run_parallel_calls(args, claims: list[str]) -> int:
    """Inicia todos los clientes antes de esperar a cualquiera de ellos."""
    processes = []
    try:
        for claim in claims:
            processes.append((claim, subprocess.Popen(
                parallel_child_command(args, claim),
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
            )))
        print(f"Iniciadas {len(processes)} llamadas en paralelo.", flush=True)
        with concurrent.futures.ThreadPoolExecutor(max_workers=len(processes)) as pool:
            outputs = list(pool.map(lambda pair: pair[1].communicate()[0], processes))
    except (KeyboardInterrupt, OSError) as exc:
        for _, process in processes:
            if process.poll() is None:
                process.terminate()
        for _, process in processes:
            process.wait()
        if isinstance(exc, KeyboardInterrupt):
            return 130
        print(f"No se pudieron iniciar todas las llamadas: {exc}", file=sys.stderr)
        return 1
    for (claim, process), output in zip(processes, outputs):
        print(f"\n=== Reclamo {claim}: salida {process.returncode} ===\n{output}", flush=True)
    print("Verificá cada reclamo y el mapa; una llamada terminada no prueba el bloqueo.", flush=True)
    return 0 if all(process.returncode == 0 for _, process in processes) else 1


def audio_player_command() -> list[str]:
    """El audio se reproduce en vivo; fallar antes de llamar si falta el reproductor."""
    if shutil.which("ffplay"):
        return ["ffplay", "-nodisp", "-autoexit", "-loglevel", "error",
                "-f", "s16le", "-ar", "8000", "-ch_layout", "mono", "-i", "pipe:0"]
    if shutil.which("aplay"):
        return ["aplay", "-q", "-t", "raw", "-f", "S16_LE", "-r", "8000", "-c", "1"]
    raise RuntimeError("falta ffplay o aplay para escuchar el menú telefónico")


def stun_mapped(sock: socket.socket, server=("stun.l.google.com", 19302)):
    """Devuelve (ip, puerto) publicos vistos por el servidor STUN, o None."""
    tid = os.urandom(12)
    req = struct.pack("!HHI", 0x0001, 0, 0x2112A442) + tid
    try:
        sock.sendto(req, server)
        sock.settimeout(4)
        data, _ = sock.recvfrom(2048)
    except Exception:
        return None
    finally:
        sock.settimeout(None)
    if len(data) < 20 or data[4:8] != b"\x21\x12\xa4\x42" or data[8:20] != tid:
        return None
    length = struct.unpack("!H", data[2:4])[0]
    attrs = data[20:20 + length]
    while len(attrs) >= 4:
        typ, ln = struct.unpack("!HH", attrs[:4])
        val = attrs[4:4 + ln]
        if len(val) < ln:
            return None
        if typ == 0x0020 and len(val) >= 8:  # XOR-MAPPED-ADDRESS
            if val[1] != 0x01:
                return None
            port = struct.unpack("!H", val[2:4])[0] ^ 0x2112
            ip = bytes(b ^ c for b, c in zip(val[4:8], b"\x21\x12\xa4\x42"))
            return (".".join(str(b) for b in ip), port)
        if typ == 0x0001 and len(val) >= 8:  # MAPPED-ADDRESS
            if val[1] != 0x01:
                return None
            port = struct.unpack("!H", val[2:4])[0]
            return (".".join(str(b) for b in val[4:8]), port)
        pad = (4 - ln % 4) % 4
        attrs = attrs[4 + ln + pad:]
    return None


class Call:
    def __init__(self, args):
        self.args = args
        self.public_ip = args.public_ip
        self.rtp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.rtp.bind((args.bind, 0))
        self.rtp_port = self.rtp.getsockname()[1]
        if args.stun:
            for srv in (("stun.l.google.com", 19302), ("stun.cloudflare.com", 3478)):
                got = stun_mapped(self.rtp, srv)
                print(f"[stun] {srv[0]} -> {got}", flush=True)
                if got:
                    self.public_ip, self.public_rtp_port = got
                    break
            else:
                self.public_rtp_port = self.rtp_port
        else:
            self.public_rtp_port = self.rtp_port
        self.rtp_mapping = None
        self.sip = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sip.bind((args.bind, 0))
        self.sip_port = self.sip.getsockname()[1]
        self.sip.settimeout(2)
        self.call_id = uuid.uuid4().hex + "@" + LOCAL_IP
        self.tag = uuid.uuid4().hex[:10]
        self.cseq = 1
        self.remote = None
        self.remote_rtp = None
        self.pcm = bytearray()
        self.running = False
        self.remote_bye = False
        self.rtp_lock = threading.Lock()
        self.dtmf_gap_slots = 0
        self.player = None
        self.rtp_seq = 0
        self.rtp_ts = 0
        self.rtp_ssrc = int.from_bytes(os.urandom(4), "big")

    # ---------- SIP ----------
    def discover_mapping(self, dst):
        """Aprende la IP y el puerto públicos del socket RTP vía received/rport."""
        branch = "z9hG4bK" + uuid.uuid4().hex[:16]
        msg = (f"OPTIONS sip:{DOMAIN} SIP/2.0\r\n"
               f"Via: SIP/2.0/UDP {LOCAL_IP}:{self.rtp_port};branch={branch};rport\r\n"
               "Max-Forwards: 70\r\n"
               f"From: <sip:probe@{LOCAL_IP}>;tag={uuid.uuid4().hex[:8]}\r\n"
               f"To: <sip:{DOMAIN}>\r\n"
               f"Call-ID: {uuid.uuid4().hex}@{LOCAL_IP}\r\n"
               "CSeq: 1 OPTIONS\r\n"
               "Content-Length: 0\r\n\r\n")
        self.rtp.sendto(msg.encode(), dst)
        self.rtp.settimeout(5)
        try:
            data, _ = self.rtp.recvfrom(4096)
        except socket.timeout:
            return None
        finally:
            self.rtp.settimeout(None)
        txt = data.decode(errors="replace")
        m = re.search(r"received=([0-9.]+)", txt)
        p = re.search(r"rport=(\d+)", txt)
        if m and p:
            return (m.group(1), int(p.group(1)))
        return None

    def send(self, msg: str, dst):
        self.sip.sendto(msg.encode(), dst)

    def recv(self, timeout=2.0):
        self.sip.settimeout(timeout)
        try:
            data, addr = self.sip.recvfrom(8192)
            return data.decode(errors="replace"), addr
        except socket.timeout:
            return None, None

    def build_invite(self, auth=None, cseq=1, branch=None):
        to_uri = destination_uri()
        branch = branch or "z9hG4bK" + uuid.uuid4().hex[:16]
        sdp = ("v=0\r\n"
               f"o=- {int(time.time())} {int(time.time())} IN IP4 {self.public_ip}\r\n"
               "s=encuentro-obligado\r\n"
               f"c=IN IP4 {self.public_ip}\r\n"
               "t=0 0\r\n"
               f"m=audio {self.public_rtp_port} RTP/AVP 0 101\r\n"
               "a=rtpmap:0 PCMU/8000\r\n"
               "a=rtpmap:101 telephone-event/8000\r\n"
               "a=fmtp:101 0-16\r\n"
               "a=sendrecv\r\n")
        h = (f"INVITE {to_uri} SIP/2.0\r\n"
             f"Via: SIP/2.0/UDP {LOCAL_IP}:{self.sip_port};branch={branch};rport\r\n"
             "Max-Forwards: 70\r\n"
             f"From: <sip:{USER}@{DOMAIN}>;tag={self.tag}\r\n"
             f"To: <{to_uri}>\r\n"
             f"Call-ID: {self.call_id}\r\n"
             f"CSeq: {cseq} INVITE\r\n"
             f"Contact: <sip:{USER}@{self.public_ip}:{self.sip_port}>\r\n"
             "Allow: INVITE, ACK, BYE, CANCEL, OPTIONS\r\n"
             "Supported: replaces\r\n"
             "User-Agent: encuentro-obligado-sip\r\n")
        if auth:
            h += f"{auth[0]}: {auth[1]}\r\n"
        h += f"Content-Type: application/sdp\r\nContent-Length: {len(sdp)}\r\n\r\n{sdp}"
        return h

    def invite(self, dst):
        to_uri = destination_uri()
        cseq = 1
        auth_attempts = 0
        deadline = time.monotonic() + 30
        branch = "z9hG4bK" + uuid.uuid4().hex[:16]
        self.send(self.build_invite(cseq=cseq, branch=branch), dst)
        while time.monotonic() < deadline:
            resp, addr = self.recv(6)
            if not resp:
                print("[sip] sin respuesta al INVITE", flush=True)
                return False
            hdr = parse_headers(resp)
            status = hdr["status"]
            response_cseq = (hdr["headers"].get("cseq") or [""])[0]
            if response_cseq != f"{cseq} INVITE":
                continue  # Retransmisión de una transacción anterior.
            print(f"[sip] INVITE CSeq {cseq} -> {status}", flush=True)
            if status.startswith("SIP/2.0 1"):
                continue
            if status.startswith("SIP/2.0 2"):
                self.cseq = cseq + 1
                self.remote = addr
                self.to_header = hdr["headers"].get("to", [""])[0]
                self.contact = (hdr["headers"].get("contact") or [None])[0]
                body = resp.split("\r\n\r\n", 1)[1] if "\r\n\r\n" in resp else ""
                self.parse_sdp(body)
                self.ack(cseq, to_uri, dst)
                if not self.remote_rtp:
                    print("[sip] la llamada no anunció un canal de audio compatible", flush=True)
                    return False
                return True
            m = re.search(r"(WWW|Proxy)-Authenticate:\s*Digest\s*(.*)", resp, re.I)
            self.ack_rejection(cseq, to_uri, branch, hdr, dst)
            if not m:
                print("[sip] llamada rechazada:", status, flush=True)
                return False
            auth_attempts += 1
            if auth_attempts > 1:
                print("[sip] autenticación rechazada; comprobá los datos SIP de esta instancia", flush=True)
                return False
            params = {}
            for k, v, w in re.findall(r'(\w+)=(?:"([^"]*)"|([^,\s]+))', m.group(2)):
                params[k] = v or w
            if not params.get("realm") or not params.get("nonce"):
                print("[sip] respuesta de autenticación incompleta", flush=True)
                return False
            if params.get("algorithm", "MD5").upper() != "MD5":
                print("[sip] algoritmo de autenticación no compatible", flush=True)
                return False
            qop_values = [v.strip() for v in params.get("qop", "").split(",") if v.strip()]
            if qop_values and "auth" not in qop_values:
                print("[sip] modalidad de autenticación no compatible", flush=True)
                return False
            qop = "auth" if qop_values else None
            cnonce, nc = uuid.uuid4().hex[:16], "00000001"
            r = digest(USER, self.args.password, params["realm"], params["nonce"], to_uri, "INVITE", qop, cnonce, nc)
            auth = (f'Digest username="{USER}", realm="{params["realm"]}", nonce="{params["nonce"]}", '
                    f'uri="{to_uri}", response="{r}"')
            if qop:
                auth += f', qop={qop}, nc={nc}, cnonce="{cnonce}"'
            if params.get("opaque"):
                auth += f', opaque="{params["opaque"]}"'
            if params.get("algorithm"):
                auth += f', algorithm={params["algorithm"]}'
            header = "Proxy-Authorization" if m.group(1).lower() == "proxy" else "Authorization"
            cseq += 1
            branch = "z9hG4bK" + uuid.uuid4().hex[:16]
            self.send(self.build_invite((header, auth), cseq=cseq, branch=branch), dst)
        print("[sip] tiempo de establecimiento agotado", flush=True)
        return False

    def ack_rejection(self, cseq, to_uri, branch, headers, dst):
        """El ACK del 407 conserva la rama y el CSeq del INVITE rechazado."""
        to_value = (headers["headers"].get("to") or [f"<{to_uri}>"])[0]
        msg = (f"ACK {to_uri} SIP/2.0\r\n"
               f"Via: SIP/2.0/UDP {LOCAL_IP}:{self.sip_port};branch={branch};rport\r\n"
               "Max-Forwards: 70\r\n"
               f"From: <sip:{USER}@{DOMAIN}>;tag={self.tag}\r\n"
               f"To: {to_value}\r\n"
               f"Call-ID: {self.call_id}\r\n"
               f"CSeq: {cseq} ACK\r\n"
               "Content-Length: 0\r\n\r\n")
        self.send(msg, dst)

    def parse_sdp(self, body: str):
        ip = re.search(r"c=IN IP4 ([^\r\n]+)", body)
        port = re.search(r"m=audio (\d+)", body)
        if ip and port:
            self.remote_rtp = (ip.group(1).strip(), int(port.group(1)))
            print("[sdp] media remota:", self.remote_rtp, flush=True)

    def ack(self, cseq, to_uri, dst):
        ruri = to_uri
        if getattr(self, "contact", None):
            import re as _re
            m = _re.search(r"<([^>]+)>", self.contact)
            ruri = m.group(1) if m else self.contact
        to_value = getattr(self, "to_header", None) or f"<{to_uri}>"
        if not to_value.startswith("<"):
            to_value = f"<{to_value}>"
        msg = (f"ACK {ruri} SIP/2.0\r\n"
               f"Via: SIP/2.0/UDP {LOCAL_IP}:{self.sip_port};branch=z9hG4bK{uuid.uuid4().hex[:16]};rport\r\n"
               "Max-Forwards: 70\r\n"
               f"From: <sip:{USER}@{DOMAIN}>;tag={self.tag}\r\n"
               f"To: {to_value}\r\n"
               f"Call-ID: {self.call_id}\r\n"
               f"CSeq: {cseq} ACK\r\n"
               f"Contact: <sip:{USER}@{self.public_ip}:{self.sip_port}>\r\n"
               "Content-Length: 0\r\n\r\n")
        self.send(msg, dst)
        print(f"[sip] ACK -> {ruri[:80]} | To {to_value[:80]}", flush=True)

    def bye(self, dst):
        to_uri = destination_uri()
        contact = getattr(self, "contact", None) or ""
        found = re.search(r"<([^>]+)>", contact)
        request_uri = found.group(1) if found else (contact or to_uri)
        to_value = getattr(self, "to_header", None) or f"<{to_uri}>"
        msg = (f"BYE {request_uri} SIP/2.0\r\n"
               f"Via: SIP/2.0/UDP {LOCAL_IP}:{self.sip_port};branch=z9hG4bK{uuid.uuid4().hex[:16]};rport\r\n"
               "Max-Forwards: 70\r\n"
               f"From: <sip:{USER}@{DOMAIN}>;tag={self.tag}\r\n"
               f"To: {to_value}\r\n"
               f"Call-ID: {self.call_id}\r\n"
               f"CSeq: {self.cseq} BYE\r\n"
               "Content-Length: 0\r\n\r\n")
        self.send(msg, dst)
        print("[sip] BYE enviado", flush=True)

    # ---------- RTP ----------
    def sip_recv_loop(self):
        self.sip.settimeout(1.0)
        while self.running:
            try:
                data, addr = self.sip.recvfrom(8192)
            except socket.timeout:
                continue
            except OSError:
                break
            message = data.decode(errors="replace")
            first = message.split("\r\n", 1)[0]
            if first.startswith("BYE "):
                headers = parse_headers(message)["headers"]
                if (headers.get("call-id") or [None])[0] != self.call_id:
                    continue
                reply = "SIP/2.0 200 OK\r\n"
                for value in headers.get("via", []):
                    reply += f"Via: {value}\r\n"
                for name, canonical in (("from", "From"), ("to", "To"),
                                        ("call-id", "Call-ID"), ("cseq", "CSeq")):
                    if name in headers:
                        reply += f"{canonical}: {headers[name][0]}\r\n"
                self.sip.sendto((reply + "Content-Length: 0\r\n\r\n").encode(), addr)
                self.remote_bye = True
                self.running = False
                print("[sip] la línea terminó la llamada", flush=True)
                break
            print(f"[sip-in] {first}", flush=True)

    def rtp_send_loop(self):
        silence = b"\xff" * 160
        while self.running:
            if self.remote_rtp:
                with self.rtp_lock:
                    hdr = struct.pack("!BBHII", 0x80, 0, self.rtp_seq & 0xFFFF, self.rtp_ts & 0xFFFFFFFF, self.rtp_ssrc)
                    self.rtp_seq += 1
                    self.rtp_ts += 160
                try:
                    self.rtp.sendto(hdr + silence, self.remote_rtp)
                except OSError:
                    pass
            time.sleep(0.02)

    def rtp_recv_loop(self):
        self.rtp.settimeout(1.0)
        while self.running:
            try:
                data, _ = self.rtp.recvfrom(4096)
            except socket.timeout:
                continue
            except OSError:
                break
            if len(data) < 12:
                continue
            b0, b1, seq, ts, ssrc = struct.unpack("!BBHII", data[:12])
            cc = b0 & 0x0F
            off = 12 + 4 * cc
            pt = b1 & 0x7F
            payload = data[off:]
            if pt == 0 and payload:
                if self.args.record:
                    self.pcm.extend(payload)
                if self.player and self.player.stdin:
                    try:
                        pcm = struct.pack("<%dh" % len(payload),
                                          *[ulaw_table()[b] for b in payload])
                        self.player.stdin.write(pcm)
                    except (BrokenPipeError, OSError):
                        self.player = None
            elif pt == 101 and len(payload) >= 4:
                ev = payload[0]
                print(f"[rtp] DTMF entrante event={ev}", flush=True)

    def send_dtmf(self, digits: str):
        if not self.remote_rtp:
            print("[dtmf] sin destino RTP", flush=True)
            return
        with self.rtp_lock:
            for ch in digits:
                code = "0123456789*#ABCD".find(ch.upper())
                if code < 0:
                    continue
                ts = self.rtp_ts
                packets = []
                dur = 0
                for i in range(8):  # 160 ms de tono
                    dur += 160
                    payload = struct.pack("!BBH", code, 0x0A, dur)
                    b1 = (0x80 | 101) if i == 0 else 101
                    packets.append(struct.pack("!BBHII", 0x80, b1, self.rtp_seq & 0xFFFF, ts & 0xFFFFFFFF, self.rtp_ssrc) + payload)
                    self.rtp_seq += 1
                for i in range(3):  # cierre del evento
                    dur += 160
                    payload = struct.pack("!BBH", code, 0x80 | 0x0A, dur)
                    packets.append(struct.pack("!BBHII", 0x80, 101, self.rtp_seq & 0xFFFF, ts & 0xFFFFFFFF, self.rtp_ssrc) + payload)
                    self.rtp_seq += 1
                self.rtp_ts = ts + 160 * (11 + max(0, int(self.dtmf_gap_slots)))
                for pkt in packets:
                    try:
                        self.rtp.sendto(pkt, self.remote_rtp)
                    except OSError:
                        pass
                    time.sleep(0.02)
                print("[dtmf] tono enviado", flush=True)
        time.sleep(self.args.gap)

    def guided_loop(self):
        steps = [
            ("el identificador", self.args.identificador),
            ("la clave telefónica", self.args.pin),
            ("el número de reclamo", self.args.reclamo),
            ("la opción del menú", self.args.accion),
        ]
        for label, digits in steps:
            while self.running:
                try:
                    response = input(
                        f"Cuando la locución pida {label}, pulsá Enter (o escribí 'colgar'): "
                    ).strip().lower()
                except (EOFError, KeyboardInterrupt):
                    self.running = False
                    return
                if response == "colgar":
                    self.running = False
                    return
                if response:
                    print("Ese dato ya está cargado. Pulsá solo Enter cuando corresponda.", flush=True)
                    continue
                self.send_dtmf(digits)
                print(f"[ivr] enviado {label}", flush=True)
                break
        while self.running:
            try:
                response = input("Después de la confirmación, escribí 'colgar' y Enter: ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                break
            if response == "colgar":
                break
        self.running = False

    def automatic_loop(self, started: float):
        """Envía las cuatro etapas cuando termina cada locución del IVR."""
        steps = [
            ("identificador", self.args.identificador),
            ("clave telefónica", self.args.pin),
            ("número de reclamo", self.args.reclamo),
            ("acción del menú", self.args.accion),
        ]
        for (label, digits), second in zip(steps, self.args.ivr_times):
            while self.running and time.monotonic() < started + second:
                time.sleep(0.05)
            if not self.running:
                return
            self.send_dtmf(digits)
            print(f"[ivr] enviado {label}", flush=True)
        # Dejar que termine el mensaje de confirmación antes de colgar.
        until = max(started + self.args.ivr_times[-1] + 8,
                    time.monotonic() + 5)
        while self.running and time.monotonic() < until:
            time.sleep(0.05)
        self.running = False

    def save_wav(self, path):
        tbl = ulaw_table()
        frames = struct.pack("<%dh" % len(self.pcm), *[tbl[b] for b in self.pcm]) if self.pcm else b""
        with wave.open(path, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(8000)
            w.writeframes(frames)
        print(f"[rec] {len(self.pcm)} bytes PCMU -> {path}", flush=True)


def main(argv: list[str] | None = None) -> int:
    global DOMAIN, USER, TARGET_USER, SIP_PORT, LOCAL_IP
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--sip-uri", required=True,
                    help="dirección SIP publicada en «Reclamos»")
    ap.add_argument("--username", required=True,
                    help="usuario SIP publicado en «Reclamos»")
    ap.add_argument("--password", required=True,
                    help="contraseña SIP publicada en «Reclamos»")
    ap.add_argument("--identificador", required=True,
                    help="número «Identificador» al inicio de la página tras el check-in; no es la URL")
    ap.add_argument("--pin", required=True,
                    help="clave telefónica de cuatro dígitos")
    ap.add_argument("--reclamo", action="append", required=True,
                    help="número de reclamo; repetir dos o tres veces con --parallel")
    ap.add_argument("--accion", required=True, choices=("1", "2", "3"),
                    help="opción del menú: 1 cancelar, 2 evaluar, 3 hablar")
    ap.add_argument("--parallel", action="store_true",
                    help="iniciar simultáneamente las llamadas de dos o tres reclamos pendientes")
    ap.add_argument("--manual", action="store_true",
                    help="escuchar el menú y pulsar Enter para enviar cada etapa")
    ap.add_argument("--ivr-times", default="3,7,12,18",
                    help="segundos desde la conexión para identificador,PIN,reclamo,acción")
    ap.add_argument("--seconds", type=int, default=180,
                    help="duración máxima de la llamada (predeterminado: 180)")
    ap.add_argument("--record", type=Path,
                    help="guardar el audio recibido en WAV (opcional)")
    args = ap.parse_args(argv)
    claims = args.reclamo
    if len(set(claims)) != len(claims):
        ap.error("cada --reclamo debe tener un número distinto")
    if args.parallel and len(claims) not in (2, 3):
        ap.error("--parallel requiere dos o tres --reclamo")
    if not args.parallel and len(claims) != 1:
        ap.error("varios --reclamo requieren --parallel")
    if args.parallel and (args.manual or args.accion != "2"):
        ap.error("--parallel requiere --accion 2 y no admite --manual")
    if args.seconds < 30:
        ap.error("--seconds debe ser al menos 30")
    player_command = None
    if args.manual:
        try:
            player_command = audio_player_command()
        except RuntimeError as exc:
            ap.error(str(exc))

    try:
        TARGET_USER, DOMAIN, SIP_PORT = parse_sip_uri(args.sip_uri)
    except ValueError as exc:
        ap.error(str(exc))
    USER = args.username.strip()
    if not USER or re.search(r"\s|[:@]", USER):
        ap.error("falta un usuario SIP válido")
    if not args.password:
        ap.error("falta la contraseña SIP")
    if not re.fullmatch(r"[0-9]+#?", args.identificador):
        ap.error("--identificador debe contener dígitos, con # final si el menú lo pide")
    if not re.fullmatch(r"[0-9]{4}#?", args.pin):
        ap.error("--pin debe contener cuatro dígitos, con # final si el menú lo pide")
    if any(not re.fullmatch(r"[0-9]+#?", claim) for claim in claims):
        ap.error("cada --reclamo debe contener dígitos, con # final si el menú lo pide")
    try:
        args.ivr_times = tuple(float(part) for part in args.ivr_times.split(","))
    except ValueError:
        ap.error("--ivr-times debe tener cuatro segundos separados por comas")
    if (len(args.ivr_times) != 4 or
            any(not 0 <= value <= args.seconds for value in args.ivr_times) or
            list(args.ivr_times) != sorted(args.ivr_times) or
            len(set(args.ivr_times)) != 4):
        ap.error("--ivr-times requiere cuatro tiempos crecientes dentro de --seconds")
    # El IVR de este laboratorio cierra las dos primeras etapas con #.
    args.identificador = args.identificador.rstrip("#") + "#"
    args.pin = args.pin.rstrip("#") + "#"
    if args.parallel:
        return run_parallel_calls(args, claims)
    args.reclamo = claims[0]
    try:
        LOCAL_IP = local_ip_for(DOMAIN, SIP_PORT)
        dst = socket.getaddrinfo(DOMAIN, SIP_PORT, type=socket.SOCK_DGRAM)[0][4]
    except OSError as exc:
        ap.error(f"no se pudo resolver la dirección SIP: {exc}")

    # La URI web de la instancia no interviene en SIP. El IVR asocia la llamada
    # con esa sesión cuando el usuario ingresa su identificador y clave.
    args.bind = "0.0.0.0"
    args.public_ip = LOCAL_IP
    args.stun = True
    args.gap = 0.35
    c = Call(args)
    workers = []
    try:
        if player_command:
            c.player = subprocess.Popen(
                player_command, stdin=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=0
            )
        mapping = c.discover_mapping(dst)
        if mapping:
            c.public_ip, c.public_rtp_port = mapping
        if not ipaddress.ip_address(c.public_ip).is_global:
            print("No se pudo obtener una dirección pública para el audio; no se llama.", file=sys.stderr)
            return 1
        if c.player and c.player.poll() is not None:
            print("El reproductor de audio terminó antes de la llamada; no se llama.", file=sys.stderr)
            return 1
        if not c.invite(dst):
            print("No se estableció la llamada.", file=sys.stderr)
            return 1
        c.running = True
        workers = [
            threading.Thread(target=c.rtp_send_loop, daemon=True),
            threading.Thread(target=c.rtp_recv_loop, daemon=True),
            threading.Thread(target=c.sip_recv_loop, daemon=True),
        ]
        for worker in workers:
            worker.start()
        started = time.monotonic()
        if args.manual:
            threading.Thread(target=c.guided_loop, daemon=True).start()
            print("Escuchá el menú y pulsá Enter al pedir cada dato.", flush=True)
            print("Para terminar, escribí 'colgar' y Enter.", flush=True)
        else:
            threading.Thread(target=c.automatic_loop, args=(started,), daemon=True).start()
            print("El programa enviará los datos del reclamo por tonos, sin pulsar Enter.", flush=True)
        deadline = time.monotonic() + args.seconds
        try:
            while c.running and time.monotonic() < deadline:
                time.sleep(0.25)
        except KeyboardInterrupt:
            pass
    finally:
        c.running = False
        for worker in workers:
            worker.join(timeout=1.5)
        if c.player:
            try:
                if c.player.stdin:
                    c.player.stdin.close()
                c.player.wait(timeout=2)
            except (OSError, subprocess.TimeoutExpired):
                c.player.kill()
        if getattr(c, "to_header", None) and not c.remote_bye:
            c.bye(dst)
        if args.record and getattr(c, "to_header", None):
            args.record.parent.mkdir(parents=True, exist_ok=True)
            c.save_wav(args.record)
        c.sip.close()
        c.rtp.close()
    print("La llamada terminó. Verificá el reclamo y el mapa en la web; "
          "los tonos enviados no confirman un asiento bloqueado. "
          "No repitas la llamada para un reclamo que ya cambió de estado.", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
