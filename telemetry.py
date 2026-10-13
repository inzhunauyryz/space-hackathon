import pandas as pd

def parse_telemetry_line(line):
    """
    Парсит строку телеметрии по спецификации хакатона.
    """
    if not isinstance(line, str):
        line = str(line)
    
    parts = line.strip().split(';')
    
    if len(parts) < 17:
        return {
            "status": "CORRUPTED",
            "error": "Неполный пакет",
            "raw": line
        }
    
    try:
        preamble = parts[0]
        time_ms = int(parts[1])
        
        # Формулы пересчета по ТЗ
        battery_v = int(parts[2]) / 1000.0  
        light = [int(p) for p in parts[3:7]]  
        
        accel = [
            int(parts[7]) / 16384.0,  
            int(parts[8]) / 16384.0,  
            int(parts[9]) / 16384.0   
        ]
        
        gyro = [
            int(parts[10]) / 131.072, 
            int(parts[11]) / 131.072, 
            int(parts[12]) / 131.072  
        ]
        
        mag = [
            int(parts[13]) / 1711.6,  
            int(parts[14]) / 1711.6,  
            int(parts[15]) / 1711.6   
        ]
        
        flywheel = int(parts[16])       
        
        status = "NORMAL"
        warnings = []
        
        if battery_v < 3.3 or battery_v > 4.2:
            status = "WARNING"
            warnings.append(f"Критическое напряжение АКБ: {battery_v}В")
            
        if abs(accel[0]) > 2.0 or abs(accel[1]) > 2.0 or abs(accel[2]) > 2.0:
            status = "FAULT"
            warnings.append("Превышение пределов ускорения")

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
    path = f"data/{filename}"
    try:
        df = pd.read_csv(path)
        return df
    except Exception:
        return None
