import pandas as pd
import os

def parse_telemetry_line(line):
    """
    Парсит и валидирует строку телеметрии по полному стандарту ТЗ:
    Преамбула; Время; АКБ; СД1-4; AX,AY,AZ; GX,GY,GZ; MX,MY,MZ; Маховик; [Постамбула]
    """
    if not isinstance(line, str):
        line = str(line)
    
    parts = [p.strip() for p in line.strip().split(';')]
    
    # Проверка минимальной структуры пакета (17-18 полей)
    if len(parts) < 17:
        return {
            "status": "CORRUPTED",
            "error": "Неполный пакет (менее 17 полей)",
            "raw": line
        }
    
    try:
        preamble = parts[0]
        # Проверка преамбулы (ожидается маркер пакета)
        if not preamble:
            return {"status": "CORRUPTED", "error": "Пустая преамбула", "raw": line}
            
        time_ms = int(parts[1])
        
        # Пересчет физических величин по ТЗ
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
        
        # Комплексная детекция аномалий и сбоев датчиков
        status = "NORMAL"
        warnings = []
        
        # Валидация напряжения АКБ (штатно 3.3V - 4.2V)
        if battery_v < 3.2 or battery_v > 4.3:
            status = "WARNING"
            warnings.warn(f"Выход напряжения АКБ за нормы: {battery_v:.2f}В")
            
        # Валидация акселерометра (±2g)
        if any(abs(a) > 2.0 for a in accel):
            status = "FAULT"
            warnings.append("Превышение перегрузки акселерометра (>2g)")
            
        # Валидация гироскопа (±250 °/с)
        if any(abs(g) > 250.0 for g in gyro):
            status = "FAULT"
            warnings.append("Превышение угловой скорости гироскопа (>250°/с)")
            
        # Валидация магнитометра (±16 Гс)
        if any(abs(m) > 16.0 for m in mag):
            status = "FAULT"
            warnings.append("Превышение магнитного поля (>16 Гс)")
            
        # Валидация датчиков света (0 - 1023 ADC)
        if any(l < 0 or l > 1023 for l in light):
            status = "WARNING"
            warnings.append("Некорректные показания датчиков света")

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
    except (ValueError, IndexError) as e:
        return {
            "status": "CORRUPTED",
            "error": f"Ошибка преобразования типов: {str(e)}",
            "raw": line
        }

def load_local_dataset(filename):
    path = os.path.join("data", filename)
    try:
        if not os.path.exists(path):
            return None
        df = pd.read_csv(path)
        return df
    except Exception:
        return None
