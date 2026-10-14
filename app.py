import streamlit as st
import pandas as pd
from telemetry import parse_telemetry_line, load_local_dataset

st.set_page_config(page_title="ЦУП ИнтроСат", page_icon="🛰️", layout="wide")

st.title("🛰️ Центр Управления Полётами КА «ИнтроСат»")
st.markdown("**Профессиональный комплекс мониторинга и анализа космической телеметрии**")
st.markdown("---")

dataset_options = {
    "01_normal_flight.csv (Штатный полет)": "01_normal_flight.csv",
    "02_sensor_faults.csv (Сбои датчиков)": "02_sensor_faults.csv",
    "03_radio_noise.csv (Радиошум)": "03_radio_noise.csv"
}

selected_key = st.sidebar.selectbox("📂 Выберите датасет", list(dataset_options.keys()))
filename = dataset_options[selected_key]

df = load_local_dataset(filename)

if df is not None and not df.empty:
    st.sidebar.success(f"Загружено записей: {len(df)}")
    
    parsed_records = []
    for i in range(min(len(df), 2000)):
        line = str(df.iloc[i].values[0])
        parsed_records.append(parse_telemetry_line(line))
        
    df_parsed = pd.DataFrame(parsed_records)
    
    tab_live, tab_dynamics, tab_raw = st.tabs(["🎛️ Пульты и кадры", "📈 Динамика полета (Тренд)", "💻 Сырые данные и аудит"])
    
    with tab_live:
        frame_idx = st.slider("Выбор кадра телеметрии", 0, len(parsed_records) - 1, 0)
        current = parsed_records[frame_idx]
        
        col1, col2, col3, col4 = st.columns(4)
        with col1:
            st.metric("Статус пакета", current.get("status", "UNKNOWN"))
        with col2:
            st.metric("Напряжение АКБ", f"{current.get('battery_v', 0):.2f} В")
        with col3:
            st.metric("Маховик", f"{current.get('flywheel', 0)} об/мин")
        with col4:
            accel = current.get("accel", [0, 0, 0])
            st.metric("Ускорение Z", f"{accel[2]:.2f} g")
            
        if current.get("warnings"):
            st.warning(f"⚠️ Предупреждения: {', '.join(current['warnings'])}")
            
        st.markdown("---")
        col_g, col_m = st.columns(2)
        with col_g:
            st.subheader("Гироскоп (°/с)")
            gyro = current.get("gyro", [0, 0, 0])
            st.write(f"GX: {gyro[0]:.2f} | GY: {gyro[1]:.2f} | GZ: {gyro[2]:.2f}")
        with col_m:
            st.subheader("Магнитометр (Гс)")
            mag = current.get("mag", [0, 0, 0])
            st.write(f"MX: {mag[0]:.2f} | MY: {mag[1]:.2f} | MZ: {mag[2]:.2f}")
            
        st.subheader("Освещенность (СД1 - СД4)")
        light_vals = current.get("light", [0, 0, 0, 0])
        st.bar_chart(pd.DataFrame({"Уровень": light_vals}, index=["СД1", "СД2", "СД3", "СД4"]))
        
    with tab_dynamics:
        st.subheader("📈 Графики изменения параметров за полет")
        if not df_parsed.empty:
            st.markdown("**Напряжение АКБ (В) во времени**")
            batt_df = pd.DataFrame({"АКБ (В)": [r["battery_v"] for r in parsed_records]})
            st.line_chart(batt_df)
            
            st.markdown("**Статистика состояния пакетов в датасете:**")
            status_counts = df_parsed["status"].value_counts()
            st.bar_chart(status_counts)
            
    with tab_raw:
        st.subheader("Анализ сырой строки")
        r_idx = st.number_input("Номер строки", 0, len(parsed_records)-1, 0)
        st.code(str(parsed_records[r_idx].get("raw", "")))
        st.json(parsed_records[r_idx])
else:
    st.error(f"Не удалось загрузить файл {filename} из папки data/. Проверьте структуру репозитория.")
