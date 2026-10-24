"""Разбор и проверка пакетов телеметрии «ИнтроСат» (без зависимостей от Streamlit).

Формат пакета: 18 полей через «;»
ПРЕАМБУЛА;ВРЕМЯ;АКБ;СД1..СД4;AX;AY;AZ;GX;GY;GZ;MX;MY;MZ;МАХОВИК;ПОСТАМБУЛА

Validator проверяет каждый пакет и всю последовательность (время, залипание,
скачки) и возвращает Frame со статусом NORMAL / WARNING / FAULT / CORRUPTED.
"""
import math
import re
from collections import Counter, deque
from dataclasses import dataclass, field
from statistics import median

FIELD_NAMES = ["ПРЕАМБУЛА", "ВРЕМЯ", "АКБ", "СД1", "СД2", "СД3", "СД4",
               "AX", "AY", "AZ", "GX", "GY", "GZ", "MX", "MY", "MZ",
               "МАХОВИК", "ПОСТАМБУЛА"]
HEADER = ";".join(FIELD_NAMES)
N_FIELDS = len(FIELD_NAMES)
CH = ["t", "akb", "sd1", "sd2", "sd3", "sd4", "ax", "ay", "az",
      "gx", "gy", "gz", "mx", "my", "mz", "wheel"]  # числовые поля 1..16

# Коэффициенты перевода в физические величины (из ТЗ)
ACC_K, GYR_K, MAG_K = 16384.0, 131.072, 1711.6
MAG_LIMIT = 16.0  # диапазон магнитометра, Гс

# ПОРОГИ — при необходимости поправьте после прогона 01_normal_flight.csv
BATT_WARN = (3.3, 4.25)    # В: вне диапазона — «внимание»
BATT_FAULT = (3.0, 4.5)    # В: вне диапазона — «авария»
ZERO_N = 15                # столько пакетов подряд «нули» = датчик отключён
STUCK_N = 60               # столько пакетов подряд без изменений = залип
JUMP_IMU = 16384           # скачок между соседними пакетами (сырые единицы)
JUMP_LIGHT = 32768
TIME_JUMP_X = 10           # скачок времени > 10 медианных шагов

OK, WARN, FAULT = 0, 1, 2
LEVEL_TEXT = {OK: "норма", WARN: "внимание", FAULT: "авария"}
MODULES = {"mcu": "Микроконтроллер", "power": "Питание",
           "imu": "Датчик положения", "light": "Датчики света",
           "wheel": "Плата маховика"}
IMU = (("Акселерометр", ("ax", "ay", "az"), ACC_K),
       ("Гироскоп", ("gx", "gy", "gz"), GYR_K),
       ("Магнитометр", ("mx", "my", "mz"), MAG_K))

NAN = float("nan")
_INT = re.compile(r"^-?\d+$")


@dataclass
class Frame:
    seq: int
    raw: str
    status: str = "NORMAL"                 # NORMAL / WARNING / FAULT / CORRUPTED
    issues: list = field(default_factory=list)   # (модуль, уровень, текст)
    t: int | None = None
    vals: dict | None = None               # физические значения; NaN = невалидно

    @property
    def valid(self):
        return self.vals is not None


class Validator:
    def __init__(self, preamble=None, postamble=None):
        # Если маркеры не заданы, они определяются по первым пакетам
        # (самое частое значение) — их точный вид в ТЗ не указан.
        self.pre, self.post = preamble, postamble
        self._pre_c, self._post_c, self._learned = Counter(), Counter(), 0
        self.last_t, self._cand, self._dts = None, None, deque(maxlen=50)
        self.prev, self._same = None, {}

    # ---------- служебное ----------
    def _learn(self, pre, post):
        if self._learned >= 200 or (self.pre and self.post):
            return
        self._learned += 1
        self._pre_c[pre] += 1
        self._post_c[post] += 1
        if self._learned >= 20:
            for attr, c in (("pre", self._pre_c), ("post", self._post_c)):
                if getattr(self, attr) is None:
                    val, n = c.most_common(1)[0]
                    if n / self._learned >= 0.6:
                        setattr(self, attr, val)

    def _run(self, key, val):
        last, n = self._same.get(key, (None, 0))
        n = n + 1 if val == last else 1
        self._same[key] = (val, n)
        return n

    @staticmethod
    def _corrupt(f, text):
        f.status = "CORRUPTED"
        f.issues = [("link", FAULT, text)]
        return f

    def _check_time(self, t, add):
        if t < 0:
            return add("mcu", FAULT, f"отрицательное время ({t})")
        if self.last_t is None:
            self.last_t = t
            return
        big = TIME_JUMP_X * median(self._dts) if len(self._dts) >= 5 else None
        dt = t - self.last_t
        if dt > 0 and (not big or dt <= big):
            self._dts.append(dt)
            self.last_t, self._cand = t, None
            return
        if dt == 0:
            return add("mcu", WARN, "повтор метки времени")
        step = t - self._cand if self._cand is not None else 0
        if step > 0 and (not big or step <= big):
            self.last_t, self._cand = t, None   # новая «база» подтвердилась
            return add("mcu", WARN, "разрыв времени (пропуск пакетов или перезапуск МК)")
        self._cand = t
        add("mcu", WARN, "время идёт назад" if dt < 0 else f"скачок времени на {dt}")

    # ---------- основной разбор ----------
    def process(self, raw, seq):
        f = Frame(seq=seq, raw=raw)
        parts = [p.strip() for p in raw.strip().split(";")]
        if len(parts) == N_FIELDS + 1 and parts[-1] == "":
            parts.pop()
        if len(parts) != N_FIELDS:
            return self._corrupt(f, f"неверное число полей: {len(parts)} из {N_FIELDS}")
        bad = [FIELD_NAMES[i] for i in range(1, 17) if not _INT.match(parts[i])]
        if bad:
            return self._corrupt(f, "нечисловые значения: " + ", ".join(bad[:4]))
        self._learn(parts[0], parts[17])
        if self.pre is not None and parts[0] != self.pre:
            return self._corrupt(f, "повреждена преамбула")
        if self.post is not None and parts[17] != self.post:
            return self._corrupt(f, "повреждена постамбула")

        r = dict(zip(CH, map(int, parts[1:17])))
        issues, v = f.issues, {}

        def add(m, lvl, txt):
            issues.append((m, lvl, txt))

        f.t = r["t"]
        self._check_time(r["t"], add)

        # питание (АКБ в мВ)
        if r["akb"] < 0:
            v["akb"] = NAN
            add("power", FAULT, "АКБ: отрицательное значение")
        else:
            u = v["akb"] = r["akb"] / 1000
            if not BATT_FAULT[0] <= u <= BATT_FAULT[1]:
                add("power", FAULT, f"АКБ {u:.2f} В — критично")
            elif not BATT_WARN[0] <= u <= BATT_WARN[1]:
                add("power", WARN, f"АКБ {u:.2f} В — вне нормы")

        # датчики света
        for i in range(1, 5):
            k, x = f"sd{i}", r[f"sd{i}"]
            if 0 <= x <= 65535:
                v[k] = float(x)
                if x == 65535:
                    add("light", WARN, f"СД{i}: насыщение")
                n = self._run(k, x)
                if x == 0 and n >= ZERO_N:
                    add("light", FAULT, f"СД{i}: нет сигнала (возможно, отключён)")
                elif n >= STUCK_N:
                    add("light", WARN, f"СД{i}: показания не меняются")
            else:
                v[k] = NAN
                self._same.pop(k, None)
                add("light", FAULT, f"СД{i}: значение вне диапазона ({x})")

        # датчик положения
        for title, keys, k in IMU:
            ok = True
            for key in keys:
                x = r[key]
                if -32768 <= x <= 32767:
                    v[key] = x / k
                    if x in (-32768, 32767):
                        add("imu", WARN, f"{title}: насыщение {key.upper()}")
                else:
                    ok, v[key] = False, NAN
                    add("imu", FAULT, f"{title}: {key.upper()} вне диапазона ({x})")
            if ok:
                tup = tuple(r[key] for key in keys)
                n = self._run(title, tup)
                if not any(tup) and n >= ZERO_N:
                    add("imu", FAULT, f"{title}: нет сигнала (отключён?)")
                elif n >= STUCK_N:
                    add("imu", WARN, f"{title}: показания не меняются")
            else:
                self._same.pop(title, None)
            if title == "Магнитометр" and any(
                    abs(v[key]) > MAG_LIMIT for key in keys if not math.isnan(v[key])):
                add("imu", WARN, "магнитометр: выход за ±16 Гс")

        # маховик
        if -32768 <= r["wheel"] <= 32767:
            v["wheel"] = float(r["wheel"])
        else:
            v["wheel"] = NAN
            add("wheel", FAULT, f"МАХОВИК вне диапазона ({r['wheel']})")

        # резкие скачки относительно предыдущего пакета («хаотичные данные»)
        if self.prev:
            jl = [k for k in ("sd1", "sd2", "sd3", "sd4")
                  if 0 <= r[k] <= 65535 and 0 <= self.prev[k] <= 65535
                  and abs(r[k] - self.prev[k]) > JUMP_LIGHT]
            ji = [k for _, keys, _ in IMU for k in keys
                  if abs(r[k]) <= 32768 and abs(self.prev[k]) <= 32768
                  and abs(r[k] - self.prev[k]) > JUMP_IMU]
            if jl:
                add("light", WARN, "резкий скачок: " + ", ".join(k.upper() for k in jl))
            if ji:
                add("imu", WARN, "резкий скачок: " + ", ".join(k.upper() for k in ji))
        self.prev = r

        f.vals = v
        worst = max((lv for _, lv, _ in issues), default=OK)
        f.status = ("NORMAL", "WARNING", "FAULT")[worst]
        return f
