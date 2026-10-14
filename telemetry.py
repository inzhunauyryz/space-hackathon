import pandas as pd
import os

def parse_telemetry_line(line):
    """
    Парсит строку телеметрии строго по спецификации хакатона.
    Формат: Преамбула; Время; АКБ; СД1-4; AX,AY,AZ; GX,GY,GZ; MX,MY,MZ; Маховик
    """
    if not isinstance(line, str):
        line = str(line)
    
    parts = line.strip().split(';')
    
    # Проверка целостности пакета по длине
    if len(parts) < 17:
        return {
            "status": "CORRUPTED",
            "error": "Неполный пакет (повреждение структуры)",
            "raw": line
        }
    
    try:
        preamble = parts[0].strip()
        time_ms = int(parts[1])
        
        # Коэффициенты пересчета строго по ТЗ
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
        
        # Детекция аномалий
        status = "NORMAL"
        warnings = []
        
        if battery_v < 3.0 or battery_v > 4.5:
            status = "WARNING"
            warnings.append(f"Аномальное напряжение АКБ: {battery_v:.2f}В")
            
        if any(abs(a) > 2.0 for a in accel):
            status = "FAULT"
            warnings.append("Превышение перегрузки акселерометра")

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
    """Загружает официальный CSV-датасет из папки data/ без заглушек."""
    path = os.path.join("data", filename)
    try:
        if not os.path.exists(path):
            return None
        df = pd.read_csv(path)
        return df
    except Exception:
        return None
