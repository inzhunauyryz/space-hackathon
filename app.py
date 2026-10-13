import streamlit as st
import pandas as pd
from telemetry import parse_telemetry_line, load_local_dataset

st.set_page_config(page_title="ЦУП ИнтроСат", page_icon="🛰️", layout="wide")

st.title("🛰️ Центр Управления Полётами КА «ИнтроСат»")
st.markdown("**Профессиональный комплекс мониторинга телеметрии**")
st.markdown("---")

dataset_options = {
    "01_normal_flight.csv (Штатный полет)": "01_normal_flight.csv",
    "02_sensor_faults.csv (Сбои датчиков)": "02_sensor_faults.csv",
    "03_radio_noise.csv (Радиошум)": "03_radio_noise.csv"
}

selected_key = st.sidebar.selectbox("📂 Выберите режим полета", list(dataset_options.keys()))
filename = dataset_options[selected_key]

df = load_local_dataset(filename)

if df is not None and not df.empty:
    st.sidebar.success(f"Загружено пакетов: {len(df)}")
    
    frame_idx = st.slider("Выбор кадра телеметрии", 0, min(len(df)-1, 10000), 0)
    
    raw_row = str(df.iloc[frame_idx].values[0])
    parsed = parse_telemetry_line(raw_row)
    
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.metric("Статус пакета", parsed.get("status", "UNKNOWN"))
    with col2:
        st.metric("Напряжение АКБ", f"{parsed.get('battery_v', 0):.2f} В")
    with col3:
        st.metric("Скорость маховика", f"{parsed.get('flywheel', 0)} об/мин")
    with col4:
        accel = parsed.get("accel", [0, 0, 0])
        st.metric("Ускорение Z", f"{accel[2]:.2f} g")
        
    if parsed.get("warnings"):
        st.warning(f"⚠️ Предупреждения: {', '.join(parsed['warnings'])}")
        
    st.markdown("---")
    
    tab1, tab2, tab3 = st.tabs(["📊 Физические датчики", "💡 Освещенность и СД", "💻 Сырые данные"])
    
    with tab1:
        col_g, col_m = st.columns(2)
        with col_g:
            st.subheader("Гироскоп (°/с)")
            gyro = parsed.get("gyro", [0,0,0])
            st.write(f"GX: {gyro[0]:.2f} | GY: {gyro[1]:.2f} | GZ: {gyro[2]:.2f}")
        with col_m:
            st.subheader("Магнитометр (Гс)")
            mag = parsed.get("mag", [0,0,0])
            st.write(f"MX: {mag[0]:.2f} | MY: {mag[1]:.2f} | MZ: {mag[2]:.2f}")
            
    with tab2:
        st.subheader("Датчики света (СД1 - СД4)")
        light_vals = parsed.get("light", [0, 0, 0, 0])
        chart_df = pd.DataFrame({"Уровень": light_vals}, index=["СД1", "СД2", "СД3", "СД4"])
        st.bar_chart(chart_df)
        
    with tab3:
        st.code(str(parsed.get("raw", "")))
        st.json(parsed)
else:
    st.error("Не удалось загрузить датасет из папки data/.")
