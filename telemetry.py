import pandas as pd

def parse_telemetry_line(line):
    """
    Парсит одну строку телеметрии по спецификации хакатона:
    Преамбула; Время; АКБ; СД1-4; AX,AY,AZ; GX,GY,GZ; MX,MY,MZ; Маховик
    """
    if not isinstance(line, str):
        line = str(line)
    
    parts = line.strip().split(';')
    
    # Проверка целостности пакета (минимум 17 полей по ТЗ)
    if len(parts) < 17:
        return {
            "status": "CORRUPTED",
            "error": "Неполный пакет (повреждение преамбулы/постамбулы)",
            "raw": line
        }
    
    try:
        preamble = parts[0]
        time_ms = int(parts[1])
        
        # Формулы пересчета по ТЗ
        battery_v = int(parts[2]) / 1000.0  мВ в В
        light = [int(p) for p in parts[3:7]]  # СД1-СД4
        
        accel = [
            int(parts[7]) / 16384.0,  # AX (±2g)
            int(parts[8]) / 16384.0,  # AY
            int(parts[9]) / 16384.0   # AZ
        ]
        
        gyro = [
            int(parts[10]) / 131.072, # GX (±250°/с)
            int(parts[11]) / 131.072, # GY
            int(parts[12]) / 131.072  # GZ
        ]
        
        mag = [
            int(parts[13]) / 1711.6,  # MX (±16 Гс)
            int(parts[14]) / 1711.6,  # MY
            int(parts[15]) / 1711.6   # MZ
        ]
        
        flywheel = int(parts[16])       # Маховик
        
        # Детекция аномалий и сбоев датчиков
        status = "NORMAL"
        warnings = []
        
        if battery_v < 3.3 or battery_v > 4.2:
            status = "WARNING"
            warnings.warn(f"Критическое напряжение АКБ: {battery_v}В")
            
        if abs(accel[0]) > 2.0 or abs(accel[1]) > 2.0 or abs(accel[2]) > 2.0:
        # Превышение диапазона ускорения — признак сбоя датчика
            status = "FAULT"
            warnings.append("Превышение пределов ускорения (сбой датчика)")

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
            "warnings": warnings,
            "raw": line
        }
    except (ValueError, IndexError):
        return {
            "status": "CORRUPTED",
            "error": "Ошибка парсинга типов данных",
            "raw": line
        }

def load_local_dataset(filename):
    """Загружает CSV-датасет из локальной папки data/ в репозитории."""
    path = f"data/{filename}"
    try:
        df = pd.read_csv(path)
        return df
    except Exception as e:
        return None
