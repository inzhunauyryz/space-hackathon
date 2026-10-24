import os
import re
from collections import Counter

import pandas as pd

# Формат пакета (18 полей через «;»):
# ПРЕАМБУЛА;ВРЕМЯ;АКБ;СД1-4;AX;AY;AZ;GX;GY;GZ;MX;MY;MZ;МАХОВИК;ПОСТАМБУЛА
N_FIELDS = 18
BATT_MIN, BATT_MAX = 3.2, 4.3      # В, вне диапазона — предупреждение
_INT = re.compile(r"^-?\d+$")

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


def _corrupted(line, error):
    return {"status": "CORRUPTED", "error": error, "raw": line}


def parse_telemetry_line(line):
    """Разбор и проверка одной строки телеметрии.

    Статусы: NORMAL, WARNING, FAULT (авария), CORRUPTED (пакет не разобран).
    """
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

    n = [int(p) for p in parts[1:17]]
    time_ms, akb, light = n[0], n[1], n[2:6]
    acc_raw, gyr_raw, mag_raw, flywheel = n[6:9], n[9:12], n[12:15], n[15]

    battery_v = akb / 1000.0
    accel = [a / 16384.0 for a in acc_raw]
    gyro = [g / 131.072 for g in gyr_raw]
    mag = [m / 1711.6 for m in mag_raw]

    fault, warn = [], []

    if time_ms < 0:
        fault.append("Отрицательное время пакета")

    if akb <= 0:
        fault.append("Нет напряжения АКБ")
    elif battery_v < BATT_MIN or battery_v > BATT_MAX:
        warn.append(f"Напряжение АКБ вне нормы: {battery_v:.2f} В")

    if any(l < 0 or l > 65535 for l in light):
        fault.append("Датчик света: значение вне диапазона 0…65535")
    elif any(l == 65535 for l in light):
        warn.append("Датчик света: насыщение")

    for name, raw in (("Акселерометр", acc_raw), ("Гироскоп", gyr_raw),
                      ("Магнитометр", mag_raw), ("Маховик", [flywheel])):
        if any(v < -32768 or v > 32767 for v in raw):
            fault.append(f"{name}: значение вне диапазона int16")
        elif any(v in (-32768, 32767) for v in raw):
            warn.append(f"{name}: насыщение (граничное значение)")

    if any(abs(m) > 16.0 for m in mag):
        fault.append("Превышение магнитного поля (>16 Гс)")

    status = "FAULT" if fault else ("WARNING" if warn else "NORMAL")
    return {
        "status": status,
        "preamble": preamble,
        "time_ms": time_ms,
        "battery_v": battery_v,
        "light": light,
        "accel": accel,
        "gyro": gyro,
        "mag": mag,
        "flywheel": flywheel,
        "warnings": fault + warn,
        "raw": line,
    }


def load_local_dataset(filename):
    """Читает файл построчно как есть (битые строки из радиошума не ломают загрузку).

    Возвращает DataFrame с одной колонкой «raw»; первая непустая строка — заголовок.
    """
    path = os.path.join("data", filename)
    try:
        if not os.path.exists(path):
            return None
        with open(path, encoding="utf-8-sig", errors="replace") as fh:
            lines = [x.strip() for x in fh if x.strip()]
        return pd.DataFrame({"raw": lines[1:]})
    except Exception:
        return None
