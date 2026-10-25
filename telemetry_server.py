"""Мини-сервер телеметрии для Render: отдаёт «последний кадр» из файлов data/*.csv.

Запуск (Render, Start Command):   python telemetry_server.py
Адреса:
  /telemetry                      — кадр из файла по умолчанию (01_normal_flight.csv)
  /telemetry?file=02_sensor_faults.csv   — другой файл (сценарий)
Формат ответа как у data-emulator: одна строка телеметрии, 204 — если данных нет.

Команды (POST /command, тело — код команды):
  GET_STATUS  — состояние сервера;  WHEEL_STOP / WHEEL_START — остановить / запустить маховик
  (после WHEEL_STOP в телеметрии поле МАХОВИК становится 0).
"""
import argparse
import os
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

DATA_DIR = Path(__file__).with_name("data")
ENCODINGS = ("utf-8-sig", "utf-8", "cp1251")
_cache = {}   # имя файла -> (кадры, момент старта)
STATE = {"wheel_stopped": False, "started": time.time()}


def apply_state(frame):
    """Применяет к кадру результат принятых команд (остановленный маховик)."""
    if STATE["wheel_stopped"]:
        parts = frame.split(";")
        if len(parts) == 18:
            parts[16] = "0"
            return ";".join(parts)
    return frame


def run_command(code, default_file):
    if code == "GET_STATUS":
        wheel = "остановлен" if STATE["wheel_stopped"] else "работает"
        return f"OK: файл {default_file}, маховик {wheel}, аптайм {int(time.time() - STATE['started'])} с"
    if code == "WHEEL_STOP":
        STATE["wheel_stopped"] = True
        return "ACK: маховик остановлен (в телеметрии МАХОВИК = 0)"
    if code == "WHEEL_START":
        STATE["wheel_stopped"] = False
        return "ACK: маховик запущен"
    return None


def read_frames(path):
    raw = path.read_bytes()
    for enc in ENCODINGS:
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    else:
        text = raw.decode("utf-8", errors="replace")
    frames = [x for x in text.splitlines() if x.strip()]
    if path.suffix.lower() == ".csv" and frames:
        frames = frames[1:]          # первая строка — заголовок
    return frames


def current_frame(name, interval):
    """Кадр определяется по времени с первого запроса: файл идёт по кругу."""
    path = (DATA_DIR / name).resolve()
    if DATA_DIR.resolve() not in path.parents or not path.is_file():
        return "NOFILE"
    if name not in _cache:
        _cache[name] = (read_frames(path), time.time())
    frames, t0 = _cache[name]
    if not frames:
        return None
    return frames[int((time.time() - t0) / interval) % len(frames)]


def make_handler(default_file, interval):
    class Handler(BaseHTTPRequestHandler):
        def _send(self, code, body=b"", ctype="text/plain; charset=utf-8"):
            self.send_response(code)
            if body:
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            if body and self.command != "HEAD":
                self.wfile.write(body)

        def do_GET(self):
            url = urlparse(self.path)
            if url.path in ("/", "/health"):
                files = sorted(p.name for p in DATA_DIR.glob("*") if p.is_file())
                text = "Telemetry server OK. GET /telemetry?file=<имя>. Файлы: " + ", ".join(files)
                return self._send(200, text.encode("utf-8"))
            if url.path != "/telemetry":
                return self._send(404, "Используйте GET /telemetry\n".encode("utf-8"))
            name = parse_qs(url.query).get("file", [default_file])[0]
            frame = current_frame(name, interval)
            if frame == "NOFILE":
                return self._send(404, f"Нет такого файла: {name}\n".encode("utf-8"))
            if frame is None:
                return self._send(204)
            self._send(200, apply_state(frame).encode("utf-8"))

        def do_POST(self):
            if urlparse(self.path).path != "/command":
                return self._send(404, "Используйте POST /command\n".encode("utf-8"))
            try:
                length = min(int(self.headers.get("Content-Length", 0)), 200)
            except ValueError:
                length = 0
            code = self.rfile.read(length).decode("utf-8", errors="replace").strip().upper()
            reply = run_command(code, default_file)
            if reply is None:
                return self._send(400, f"Неизвестная команда: {code[:40]}\n".encode("utf-8"))
            self._send(200, reply.encode("utf-8"))

        do_HEAD = do_GET

        def log_message(self, *args):
            return

    return Handler


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", default=os.environ.get("DEFAULT_FILE", "01_normal_flight.csv"))
    ap.add_argument("--interval", type=float, default=0.1, help="секунд между кадрами")
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=int(os.environ.get("PORT", 8765)))
    a = ap.parse_args()
    ThreadingHTTPServer.daemon_threads = True
    print(f"Telemetry server: http://{a.host}:{a.port}/telemetry (файл по умолчанию {a.file})", flush=True)
    ThreadingHTTPServer((a.host, a.port), make_handler(a.file, a.interval)).serve_forever()
