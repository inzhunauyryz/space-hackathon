import csv
from pathlib import Path

import pandas as pd


BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"

# Пороги напряжения — предварительные.
# Если в техническом задании указаны другие, замени их.
BATTERY_MIN_V = 3.2
BATTERY_MAX_V = 4.3

LIGHT_MIN = 0
LIGHT_MAX = 65535


def parse_telemetry_line(line):
    """Разбирает один пакет телеметрии и проверяет его поля."""

    raw = str(line).strip()

    result = {
        "status": "CORRUPTED",
        "warnings": [],
        "error": None,
        "raw": raw,
    }

    if not raw:
        result["error"] = "Пустой пакет"
        return result

    parts = [part.strip() for part in raw.split(";")]

    # 17 полей без постамбулы или 18 с постамбулой.
    if len(parts) not in (17, 18):
        result["error"] = (
            f"Неверное количество полей: {len(parts)}. "
            "Ожидается 17 или 18."
        )
        return result

    preamble = parts[0]

    if not preamble:
        result["error"] = "Пустая преамбула"
        return result

    # Время должно быть целым неотрицательным числом.
    try:
        time_ms = int(parts[1])
        if time_ms < 0:
            raise ValueError("Время не может быть отрицательным")
    except ValueError:
        result["error"] = "Некорректное время пакета"
        return result

    # Если поле постамбулы присутствует, оно не должно быть пустым.
    if len(parts) == 18 and not parts[17]:
        result["error"] = "Пустая постамбула"
        return result

    try:
        values = [int(value) for value in parts[2:17]]
    except ValueError:
        result["error"] = (
            "Одно или несколько числовых полей "
            "содержат некорректные данные"
        )
        return result

    battery_raw = values[0]
    light_raw = values[1:5]
    accel_raw = values[5:8]
    gyro_raw = values[8:11]
    mag_raw = values[11:14]
    flywheel = values[14]

    # Проверка исходных диапазонов целочисленных значений.
    # Для акселерометра, гироскопа и магнитометра:
    # -32768 ... 32767.
    signed_values = accel_raw + gyro_raw + mag_raw + [flywheel]

    if any(value < -32768 or value > 32767
           for value in signed_values):
        result["error"] = (
            "Значение акселерометра, гироскопа, "
            "магнитометра или маховика вне диапазона int16"
        )
        return result

    if any(value < LIGHT_MIN or value > LIGHT_MAX
           for value in light_raw):
        result["error"] = (
            "Показания датчиков освещённости "
            "вне диапазона 0...65535"
        )
        return result

    # Перевод в физические единицы.
    battery_v = battery_raw / 1000.0
    light = light_raw

    accel = [value / 16384.0 for value in accel_raw]
    gyro = [value / 131.072 for value in gyro_raw]
    mag = [value / 1711.6 for value in mag_raw]

    warnings = []
    status = "NORMAL"

    # Напряжение аккумулятора.
    if battery_v < BATTERY_MIN_V or battery_v > BATTERY_MAX_V:
        status = "WARNING"
        warnings.append(
            f"Напряжение АКБ вне заданных порогов: "
            f"{battery_v:.3f} В"
        )

    # Физические пределы датчиков.
    if any(abs(value) > 2.0 for value in accel):
        status = "FAULT"
        warnings.append(
            "Акселерометр: превышен диапазон ±2 g"
        )

    if any(abs(value) > 250.0 for value in gyro):
        status = "FAULT"
        warnings.append(
            "Гироскоп: превышен диапазон ±250 °/с"
        )

    if any(abs(value) > 16.0 for value in mag):
        status = "FAULT"
        warnings.append(
            "Магнитометр: превышен диапазон ±16 Гс"
        )

    result.update({
        "status": status,
        "error": None,
        "preamble": preamble,
        "time_ms": time_ms,
        "battery_v": battery_v,
        "light": light,
        "accel": accel,
        "gyro": gyro,
        "mag": mag,
        "flywheel": flywheel,
        "warnings": warnings,
    })

    return result


def load_local_dataset(filename):
    """Загружает CSV с пакетами телеметрии из папки data."""

    path = DATA_DIR / filename

    if not path.is_file():
        return None

    try:
        lines = path.read_text(
            encoding="utf-8-sig"
        ).splitlines()
    except (OSError, UnicodeError):
        return None

    # Пропускаем пустые строки.
    lines = [line.strip() for line in lines if line.strip()]

    if not lines:
        return pd.DataFrame({"raw": []})

    # По правилам эмулятора первая непустая строка CSV —
    # заголовок, который не является пакетом телеметрии.
    if path.suffix.lower() == ".csv":
        lines = lines[1:]

    return pd.DataFrame({"raw": lines})
