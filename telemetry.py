from pathlib import Path

import pandas as pd


BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"

# Рабочие пороги напряжения аккумулятора.
# Перед защитой нужно сверить их с техническим заданием.
BATTERY_MIN_V = 3.2
BATTERY_MAX_V = 4.3

LIGHT_MIN = 0
LIGHT_MAX = 65535

INT16_MIN = -32768
INT16_MAX = 32767


def parse_telemetry_line(line):
    """Разбирает один пакет телеметрии и проверяет значения."""

    raw = str(line).strip()

    result = {
        "status": "CORRUPTED",
        "warnings": [],
        "error": None,
        "raw": raw,
    }

    if not raw:
        result["error"] = "Пустой пакет телеметрии"
        return result

    parts = [part.strip() for part in raw.split(";")]

    if len(parts) not in (17, 18):
        result["error"] = (
            f"Неверное количество полей: {len(parts)}. "
            "Ожидается 17 или 18."
        )
        return result

    if not parts[0]:
        result["error"] = "Отсутствует преамбула"
        return result

    try:
        time_ms = int(parts[1])

        if time_ms < 0:
            result["error"] = "Время не может быть отрицательным"
            return result

        values = [int(value) for value in parts[2:17]]

    except ValueError:
        result["error"] = (
            "Время или числовые поля содержат некорректные данные"
        )
        return result

    if len(parts) == 18 and not parts[17]:
        result["error"] = "Отсутствует постамбула"
        return result

    battery_raw = values[0]
    light_raw = values[1:5]
    accel_raw = values[5:8]
    gyro_raw = values[8:11]
    mag_raw = values[11:14]
    flywheel = values[14]

    # Проверка целочисленных диапазонов датчиков.
    signed_values = (
        accel_raw + gyro_raw + mag_raw + [flywheel]
    )

    if any(
        value < INT16_MIN or value > INT16_MAX
        for value in signed_values
    ):
        result["error"] = (
            "Показания датчиков или маховика выходят "
            "за пределы диапазона int16"
        )
        return result

    if any(
        value < LIGHT_MIN or value > LIGHT_MAX
        for value in light_raw
    ):
        result["error"] = (
            "Показания освещённости вне диапазона 0–65535"
        )
        return result

    # Перевод сырых показаний в физические единицы.
    battery_v = battery_raw / 1000.0
    accel = [value / 16384.0 for value in accel_raw]
    gyro = [value / 131.072 for value in gyro_raw]
    mag = [value / 1711.6 for value in mag_raw]

    warnings_list = []
    status = "NORMAL"

    # Проверка аккумулятора.
    if not BATTERY_MIN_V <= battery_v <= BATTERY_MAX_V:
        status = "WARNING"
        warnings_list.append(
            f"Напряжение аккумулятора: {battery_v:.3f} В"
        )

    # Проверка диапазонов датчиков.
    if any(abs(value) > 2.0 for value in accel):
        status = "FAULT"
        warnings_list.append(
            "Акселерометр: превышен диапазон ±2 g"
        )

    if any(abs(value) > 250.0 for value in gyro):
        status = "FAULT"
        warnings_list.append(
            "Гироскоп: превышен диапазон ±250 °/с"
        )

    if any(abs(value) > 16.0 for value in mag):
        status = "FAULT"
        warnings_list.append(
            "Магнитометр: превышен диапазон ±16 Гс"
        )

    result.update({
        "status": status,
        "error": None,
        "preamble": parts[0],
        "time_ms": time_ms,
        "battery_v": battery_v,
        "light": light_raw,
        "accel": accel,
        "gyro": gyro,
        "mag": mag,
        "flywheel": flywheel,
        "warnings": warnings_list,
    })

    return result


def load_local_dataset(filename):
    """Загружает текстовые пакеты телеметрии из папки data."""

    path = DATA_DIR / filename

    if not path.is_file():
        return None

    try:
        text = path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeError):
        return None

    lines = [
        line.strip()
        for line in text.splitlines()
        if line.strip()
    ]

    if not lines:
        return pd.DataFrame({"raw": []})

    # Пропускаем заголовок CSV, если он действительно есть.
    first = lines[0].lower()

    if any(
        marker in first
        for marker in (
            "время от запуска",
            "постамбула",
            "time_ms",
            "battery",
        )
    ):
        lines = lines[1:]

    return pd.DataFrame({"raw": lines})
