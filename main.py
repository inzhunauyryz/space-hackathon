import os
from telemetry import load_local_dataset, parse_telemetry_line

def run_cli_audit():
    print("=== АУДИТ ТЕЛЕМЕТРИИ КА ИНТРОСАТ ===")
    datasets = ["01_normal_flight.csv", "02_sensor_faults.csv", "03_radio_noise.csv"]
    
    for ds in datasets:
        print(f"\nАнализ файла: {ds}")
        df = load_local_dataset(ds)
        if df is None:
            print(f"  [!] Файл {ds} не найден в папке data/")
            continue
            
        total = len(df)
        normal = 0
        faults = 0
        corrupted = 0
        
        for idx in range(min(total, 1000)):  # Проверяем до 1000 строк
            row_str = str(df.iloc[idx].values[0])
            res = parse_telemetry_line(row_str)
            if res["status"] == "NORMAL":
                normal += 1
            elif res["status"] in ["FAULT", "WARNING"]:
                faults += 1
            else:
                corrupted += 1
                
        print(f"  Всего проверено пакетов: {min(total, 1000)}")
        print(f"  [OK] Штатных: {normal}")
        print(f"  [!] С предупреждениями/сбоями: {faults}")
        print(f"  [X] Поврежденных/шумных: {corrupted}")

if __name__ == "__main__":
    run_cli_audit()
