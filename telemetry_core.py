import math
import os
import re
from collections import Counter, deque
from statistics import median

import pandas as pd

# Формат пакета (18 полей через «;»):
# ПРЕАМБУЛА;ВРЕМЯ;АКБ;СД1-4;AX;AY;AZ;GX;GY;GZ;MX;MY;MZ;МАХОВИК;ПОСТАМБУЛА
N_FIELDS = 18
_INT = re.compile(r"^-?\d+$")

# ---- ПОРОГИ (при необходимости подстройте под штатный полёт 01_normal_flight.csv) ----
BATT_MIN, BATT_MAX = 3.2, 4.3   # В, вне диапазона — «внимание»
ZERO_N = 15                     # столько пакетов подряд нули = «внимание»
ZERO_FAULT_N = 150              # столько пакетов подряд нули = «авария» (отказ датчика)
STUCK_N = 60                    # столько пакетов подряд без изменений = «внимание»
JUMP_IMU = 16384                # скачок между соседними пакетами (сырые единицы)
JUMP_LIGHT = 32768
TIME_JUMP_X = 10                # скачок времени больше 10 обычных шагов

OK, WARN, FAULT = 0, 1, 2
MODULES = {"mcu": "Микроконтроллер", "power": "Питание", "imu": "Датчик положения",
           "light": "Датчики света", "wheel": "Плата маховика"}

# Маркеры преамбулы/постамбулы определяются по первым пакетам (самое частое значение)
_seen = {"pre": Counter(), "post": Counter(), "n": 0}
_expected = {"pre": None, "post": None}


def _learn_markers(pre, post):
    if _expected["pre"] is not None and _expected["post"] is not None:
        return
    _seen["n"] += 1
    _seen["pre"][pre] += 1
    _seen["post"][post] += 1
    if _seen["n"] >= 20:
        for key in ("pre", "post"):
            if _expected[key] is None:
                value, count = _seen[key].most_common(1)[0]
                if count / _seen["n"] >= 0.6:
                    _expected[key] = value


def finalize(rec):
    """Считает статусы модулей, общий статус и список предупреждений по rec['issues']."""
    modules = {m: OK for m in MODULES}
    for m, lvl, _ in rec["issues"]:
        if m in modules:
            modules[m] = max(modules[m], lvl)
    worst = max(modules.values())
    rec["modules"] = modules
    rec["status"] = ("NORMAL", "WARNING", "FAULT")[worst]
    rec["warnings"] = [t for _, _, t in sorted(rec["issues"], key=lambda x: -x[1])]
    return rec


def _corrupted(line, error):
    return {"status": "CORRUPTED", "error": error, "raw": line, "modules": None,
            "issues": [("link", FAULT, error)], "warnings": []}


def parse_telemetry_line(line):
    """Разбор и проверка одной строки. Статусы: NORMAL, WARNING, FAULT, CORRUPTED."""
    if not isinstance(line, str):
        line = str(line)
    parts = [p.strip() for p in line.strip().split(";")]
    if len(parts) == N_FIELDS + 1 and parts[-1] == "":
        parts.pop()
    if len(parts) != N_FIELDS:
        return _corrupted(line, f"Неверное число полей: {len(parts)} вместо {N_FIELDS}")
    bad = [i for i in range(1, 17) if not _INT.match(parts[i])]
    if bad:
        return _corrupted(line, f"Нечисловые значения в полях: {bad[:5]}")

    preamble, postamble = parts[0], parts[17]
    _learn_markers(preamble, postamble)
    if not preamble or (_expected["pre"] is not None and preamble != _expected["pre"]):
        return _corrupted(line, "Повреждена преамбула")
    if not postamble or (_expected["post"] is not None and postamble != _expected["post"]):
        return _corrupted(line, "Повреждена постамбула")

    n = [int(p) for p in parts[1:17]]   # t, akb, sd1-4, ax ay az, gx gy gz, mx my mz, wheel
    time_ms, akb, light = n[0], n[1], n[2:6]
    acc_raw, gyr_raw, mag_raw, flywheel = n[6:9], n[9:12], n[12:15], n[15]
    battery_v = akb / 1000.0
    accel = [a / 16384.0 for a in acc_raw]
    gyro = [g / 131.072 for g in gyr_raw]
    mag = [m / 1711.6 for m in mag_raw]

    issues = []
    add = lambda m, lvl, txt: issues.append((m, lvl, txt))

    if time_ms < 0:
        add("mcu", FAULT, "Отрицательное время пакета")
    if akb <= 0:
        add("power", FAULT, "Нет напряжения АКБ")
    elif battery_v < BATT_MIN or battery_v > BATT_MAX:
        add("power", WARN, f"Напряжение АКБ вне нормы: {battery_v:.2f} В")
    if any(v < 0 or v > 65535 for v in light):
        add("light", FAULT, "Датчик света: значение вне диапазона 0…65535")
    elif any(v == 65535 for v in light):
        add("light", WARN, "Датчик света: насыщение")
    for name, raw in (("Акселерометр", acc_raw), ("Гироскоп", gyr_raw), ("Магнитометр", mag_raw)):
        if any(v < -32768 or v > 32767 for v in raw):
            add("imu", FAULT, f"{name}: значение вне диапазона int16")
        elif any(v in (-32768, 32767) for v in raw):
            add("imu", WARN, f"{name}: насыщение (граничное значение)")
    if any(abs(m) > 16.0 for m in mag):
        add("imu", FAULT, "Превышение магнитного поля (>16 Гс)")
    if flywheel < -32768 or flywheel > 32767:
        add("wheel", FAULT, "Маховик: значение вне диапазона int16")

    rec = {"preamble": preamble, "time_ms": time_ms, "battery_v": battery_v, "light": light,
           "accel": accel, "gyro": gyro, "mag": mag, "flywheel": flywheel,
           "raw": line, "n": n, "issues": issues}
    return finalize(rec)


class SequenceChecker:
    """Проверки по последовательности пакетов: время, «нули», залипание, скачки.

    Вызывайте apply(rec) для каждого неповреждённого пакета по порядку."""

    def __init__(self):
        self.last_t, self.cand, self.dts = None, None, deque(maxlen=50)
        self.prev, self.same = None, {}

    def _run(self, key, val):
        last, cnt = self.same.get(key, (None, 0))
        cnt = cnt + 1 if val == last else 1
        self.same[key] = (val, cnt)
        return cnt

    def apply(self, rec):
        if rec.get("status") == "CORRUPTED" or "n" not in rec:
            return rec
        n, issues = rec["n"], rec["issues"]
        add = lambda m, lvl, txt: issues.append((m, lvl, txt))
        t = n[0]

        # время
        if t >= 0:
            if self.last_t is None:
                self.last_t = t
            else:
                big = TIME_JUMP_X * median(self.dts) if len(self.dts) >= 5 else None
                dt = t - self.last_t
                if dt > 0 and (not big or dt <= big):
                    self.dts.append(dt)
                    self.last_t, self.cand = t, None
                elif dt == 0:
                    add("mcu", WARN, "Повтор метки времени")
                else:
                    step = t - self.cand if self.cand is not None else 0
                    if step > 0 and (not big or step <= big):
                        self.last_t, self.cand = t, None
                        add("mcu", WARN, "Разрыв времени (пропуск пакетов или перезапуск МК)")
                    else:
                        self.cand = t
                        add("mcu", WARN, "Время идёт назад" if dt < 0 else f"Скачок времени на {dt}")

        # датчики света: нули и залипание
        for i in range(4):
            x = n[2 + i]
            if not 0 <= x <= 65535:
                self.same.pop(f"sd{i}", None)
                continue
            cnt = self._run(f"sd{i}", x)
            if x == 0 and cnt >= ZERO_N:
                add("light", FAULT if cnt >= ZERO_FAULT_N else WARN, f"СД{i + 1}: нет сигнала (тень или отключён)")
            elif cnt >= STUCK_N:
                add("light", WARN, f"СД{i + 1}: показания не меняются")

        # датчик положения: нули и залипание по группам
        for title, sl in (("Акселерометр", slice(6, 9)), ("Гироскоп", slice(9, 12)), ("Магнитометр", slice(12, 15))):
            tup = tuple(n[sl])
            if all(-32768 <= v <= 32767 for v in tup):
                cnt = self._run(title, tup)
                if not any(tup) and cnt >= ZERO_N:
                    add("imu", FAULT if cnt >= ZERO_FAULT_N else WARN, f"{title}: нет сигнала (отключён?)")
                elif cnt >= STUCK_N:
                    add("imu", WARN, f"{title}: показания не меняются")
            else:
                self.same.pop(title, None)

        # резкие скачки относительно предыдущего пакета («хаотичные данные»)
        if self.prev:
            jl = [i + 1 for i in range(4) if 0 <= n[2 + i] <= 65535 and 0 <= self.prev[2 + i] <= 65535
                  and abs(n[2 + i] - self.prev[2 + i]) > JUMP_LIGHT]
            ji = [j for j in range(6, 15) if abs(n[j]) <= 32768 and abs(self.prev[j]) <= 32768
                  and abs(n[j] - self.prev[j]) > JUMP_IMU]
            if jl:
                add("light", WARN, "Резкий скачок: " + ", ".join(f"СД{k}" for k in jl))
            if ji:
                add("imu", WARN, "Резкий скачок показаний датчика положения")
        self.prev = n
        return finalize(rec)


def analyze(records):
    """Прогоняет SequenceChecker по списку записей (для файла целиком)."""
    checker = SequenceChecker()
    for rec in records:
        checker.apply(rec)
        rec.pop("n", None)
    return records


def load_local_dataset(filename):
    """Читает файл построчно как есть (битые строки радиошума не ломают загрузку).

    Возвращает DataFrame с колонкой «raw»; первая непустая строка — заголовок."""
    path = os.path.join("data", filename)
    try:
        if not os.path.exists(path):
            return None
        with open(path, encoding="utf-8-sig", errors="replace") as fh:
            lines = [x.strip() for x in fh if x.strip()]
        return pd.DataFrame({"raw": lines[1:]})
    except Exception:
        return None
