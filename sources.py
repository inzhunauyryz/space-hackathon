"""Источники данных (эмулятор по HTTP, воспроизведение файла) и фоновый сборщик.

Collector в отдельном потоке опрашивает источник, проверяет пакеты Validator-ом,
пишет сырой лог на диск и хранит историю для интерфейса. Любые сбои источника
(нет связи, пустой ответ, мусор) обрабатываются без падения приложения.
"""
import http.client
import os
import re
import threading
import time
import urllib.error
import urllib.request
from collections import deque
from datetime import datetime

from telemetry import FAULT, HEADER, MODULES, OK, Validator

DEFAULT_URL = "http://127.0.0.1:8765/telemetry"
STALL_SEC = 3.0      # нет новых пакетов дольше — «передача прервана»
IDLE_STOP = 300      # никто не смотрит интерфейс — поток останавливается


class SourceError(Exception):
    pass


class HttpSource:
    """Последний пакет от data-emulator (204 — данных ещё нет)."""
    kind = "http"
    interval = 0.1

    def __init__(self, url=DEFAULT_URL, timeout=1.5):
        self.url, self.timeout = url, timeout

    def poll(self):
        try:
            with urllib.request.urlopen(self.url, timeout=self.timeout) as r:
                if r.status == 204:
                    return None
                return r.read().decode("utf-8", errors="replace")
        except (urllib.error.URLError, OSError, http.client.HTTPException, ValueError) as e:
            raise SourceError(str(getattr(e, "reason", None) or e)) from e


class FileSource:
    """Воспроизведение CSV/лога с заданной скоростью (демо без эмулятора)."""
    kind = "file"

    def __init__(self, text, rate=20.0, loop=False):
        lines = [x.strip() for x in text.splitlines() if x.strip()]
        # первая непустая строка — заголовок (как в эмуляторе), если это не данные
        if lines and not re.match(r"^-?\d+$", (lines[0].split(";") + ["", ""])[1].strip()):
            lines = lines[1:]
        self.lines, self.i, self.loop, self.finished = lines, 0, loop, False
        self.interval = 1.0 / max(rate, 0.1)

    @property
    def progress(self):
        return self.i / max(len(self.lines), 1)

    def poll(self):
        if self.i >= len(self.lines):
            if not self.loop:
                self.finished = True
                return None
            self.i = 0
        self.i += 1
        return self.lines[self.i - 1]


class Collector:
    def __init__(self, source, window=5000, log_dir="logs"):
        self.src, self.val, self.lock = source, Validator(), threading.Lock()
        self.frames, self.events = deque(maxlen=window), deque(maxlen=300)
        self.counts = dict(total=0, NORMAL=0, WARNING=0, FAULT=0, CORRUPTED=0, dups=0)
        self.conn, self.error, self.last_rx, self.last_raw = "CONNECTING", "", None, None
        self.mod_level = {m: OK for m in MODULES}
        self.mod_msgs = {m: [] for m in MODULES}
        self.active, self.seq, self.last, self.last_read = set(), 0, None, time.time()
        self.log_path, self._log, self.log_dir = None, None, log_dir
        self._stop = threading.Event()
        self._th = threading.Thread(target=self._run, daemon=True)

    # ---------- жизненный цикл ----------
    def start(self):
        try:
            os.makedirs(self.log_dir, exist_ok=True)
            self.log_path = os.path.join(
                self.log_dir, datetime.now().strftime("telemetry_%Y%m%d_%H%M%S.csv"))
            self._log = open(self.log_path, "w", encoding="utf-8", buffering=1)
            self._log.write(HEADER + "\n")
        except OSError as e:
            self.log_path, self._log = None, None
            self._event("link", FAULT, f"лог недоступен: {e}")
        self._th.start()

    def stop(self):
        self._stop.set()
        with self.lock:
            self.conn = "STOPPED"
            if self._log:
                self._log.close()
                self._log = None

    # ---------- фоновый поток ----------
    def _run(self):
        while not self._stop.is_set():
            if time.time() - self.last_read > IDLE_STOP:
                self.stop()
                break
            try:
                raw = self.src.poll()
            except SourceError as e:
                with self.lock:
                    if self.conn != "NO_CONNECTION":
                        self._event("link", FAULT, f"нет связи с источником: {e}")
                    self.conn, self.error = "NO_CONNECTION", str(e)
                self._stop.wait(1.0)
                continue
            except Exception as e:  # любой непредвиденный сбой источника
                with self.lock:
                    self.conn, self.error = "NO_CONNECTION", repr(e)
                self._stop.wait(1.0)
                continue
            with self.lock:
                if raw is None or not raw.strip():
                    if getattr(self.src, "finished", False):
                        self.conn = "FINISHED"
                    elif self.last_rx is None:
                        self.conn = "WAITING"
                elif self.src.kind == "http" and raw == self.last_raw:
                    self.counts["dups"] += 1      # эмулятор отдал тот же пакет
                else:
                    self._ingest(raw)
            self._stop.wait(self.src.interval)

    def _event(self, module, level, text, seq=0):
        self.events.appendleft({"время": datetime.now().strftime("%H:%M:%S"),
                                "пакет": seq, "уровень": ("норма", "внимание", "авария")[level],
                                "модуль": MODULES.get(module, "Канал связи"), "событие": text})

    def _ingest(self, raw):
        self.seq += 1
        self.last_raw, self.last_rx, self.conn, self.error = raw, time.time(), "ONLINE", ""
        f = self.val.process(raw, self.seq)
        self.frames.append(f)
        self.counts["total"] += 1
        self.counts[f.status] += 1
        if self._log:
            try:
                self._log.write(raw.strip() + "\n")
            except OSError:
                self._log = None
        if f.status == "CORRUPTED":
            self._event("link", FAULT, f.issues[0][2], f.seq)
            return
        self.last = f
        levels, msgs = {m: OK for m in MODULES}, {m: [] for m in MODULES}
        for m, lv, tx in f.issues:
            levels[m] = max(levels[m], lv)
            msgs[m].append(tx)
        self.mod_level, self.mod_msgs = levels, msgs
        keys = {(m, re.sub(r"-?\d+(\.\d+)?", "#", tx)): (lv, tx) for m, lv, tx in f.issues}
        for k in keys.keys() - self.active:       # событие — только при появлении
            self._event(k[0], *keys[k], f.seq)
        for k in self.active - keys.keys():
            self._event(k[0], OK, "восстановлено: " + k[1], f.seq)
        self.active = set(keys)

    # ---------- чтение из интерфейса ----------
    def snapshot(self, frames=False):
        self.last_read = time.time()
        with self.lock:
            now, conn = time.time(), self.conn
            age = now - self.last_rx if self.last_rx else None
            if conn == "ONLINE" and age is not None and age > STALL_SEC:
                conn = "STALLED"
            recent = list(self.frames)[-30:]
            bad = sum(1 for x in recent if x.status == "CORRUPTED")
            link = FAULT if bad > 0.3 * max(len(recent), 1) else (1 if bad else OK)
            return {
                "conn": conn, "error": self.error, "age": age,
                "counts": dict(self.counts), "last": self.last,
                "mod_level": dict(self.mod_level, link=link),
                "mod_msgs": {k: list(x) for k, x in self.mod_msgs.items()},
                "bad_pct": 100 * bad / max(len(recent), 1), "n_recent": len(recent),
                "events": list(self.events), "log_path": self.log_path,
                "frames": list(self.frames) if frames else None,
                "progress": getattr(self.src, "progress", None),
            }
