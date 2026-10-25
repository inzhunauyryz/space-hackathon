import time
import urllib.request

import pandas as pd
import streamlit as st

from telemetry import parse_telemetry_line, load_local_dataset

st.set_page_config(page_title="ЦУП ИнтроСат", page_icon="🛰️", layout="wide")

st.title("🛰️ Центр Управления Полётами КА «ИнтроСат»")
st.markdown("**Профессиональный комплекс мониторинга и анализа космической телеметрии**")
st.markdown("---")

EMULATOR_URL = "https://space-telemetry-emulator.onrender.com/telemetry"
STALL_SEC = 3.0   # столько секунд без новых пакетов = «передача прервана»


# ---------------------------------------------------------------- общее
def render_frame(current):
    """Показывает один пакет: метрики, предупреждения, гироскоп, магнитометр, свет."""
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.metric("Статус пакета", current.get("status", "UNKNOWN"))
    with col2:
        st.metric("Напряжение АКБ", f"{current.get('battery_v', 0):.2f} В")
    with col3:
        st.metric("Маховик", f"{current.get('flywheel', 0)}")
    with col4:
        accel = current.get("accel", [0, 0, 0])
        st.metric("Ускорение Z", f"{accel[2]:.2f} g")

    if current.get("error"):
        st.error(f"❌ Повреждённый пакет: {current['error']}")
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


# ---------------------------------------------------------------- режим «Эмулятор»
def fetch_frame(url):
    """Один запрос к эмулятору. Возвращает (строка | None, текст ошибки | None)."""
    try:
        with urllib.request.urlopen(url, timeout=1.5) as response:
            if response.status == 204:
                return None, None
            return response.read().decode("utf-8", errors="replace"), None
    except Exception as e:  # нет связи, таймаут, неверный адрес — приложение не падает
        return None, str(getattr(e, "reason", None) or e)


@st.fragment(run_every=1.0)
def live_view(url):
    s = st.session_state
    for key, default in (("lv_raw", None), ("lv_rx", None), ("lv_rec", None),
                         ("lv_valid", None), ("lv_batt", []), ("lv_counts", {})):
        s.setdefault(key, default)

    error = None
    for _ in range(8):                      # несколько запросов за секунду, чтобы не терять пакеты
        raw, error = fetch_frame(url)
        if error:
            break
        if raw and raw.strip() and raw != s.lv_raw:
            s.lv_raw, s.lv_rx = raw, time.time()
            rec = parse_telemetry_line(raw)
            s.lv_rec = rec
            s.lv_counts[rec["status"]] = s.lv_counts.get(rec["status"], 0) + 1
            if rec["status"] != "CORRUPTED":
                s.lv_valid = rec
                s.lv_batt = (s.lv_batt + [rec["battery_v"]])[-300:]
        time.sleep(0.1)

    age = None if s.lv_rx is None else time.time() - s.lv_rx
    if error:
        st.error(f"🔴 НЕТ СВЯЗИ С ЭМУЛЯТОРОМ: {error}. "
                 "Запустите «python emulator.py» — приложение повторяет попытку каждую секунду.")
    elif age is None:
        st.warning("🟡 Эмулятор доступен, но пакетов пока нет")
    elif age > STALL_SEC:
        st.error(f"🔴 ПЕРЕДАЧА ПРЕРВАНА: новых пакетов нет {age:.0f} с. Данные ниже устарели.")
    else:
        st.success("🟢 Связь есть")

    c = s.lv_counts
    st.caption("Принято пакетов: " + str(sum(c.values())) + " · " +
               " · ".join(f"{k}: {v}" for k, v in sorted(c.items())))

    if s.lv_rec is not None and s.lv_rec["status"] == "CORRUPTED":
        st.error(f"❌ Последний пакет повреждён: {s.lv_rec['error']}")
    if s.lv_valid is not None:
        render_frame(s.lv_valid)
        st.markdown("**Напряжение АКБ (В), последние пакеты**")
        st.line_chart(pd.DataFrame({"АКБ (В)": s.lv_batt}))
    elif not error:
        st.info("Данных пока нет.")


# ---------------------------------------------------------------- режим «Файл»
@st.cache_data(show_spinner="Разбор файла…")
def parse_file(filename):
    df = load_local_dataset(filename)
    if df is None or df.empty:
        return None
    return [parse_telemetry_line(str(x)) for x in df.iloc[:, 0]]


def file_view():
    dataset_options = {
        "01_normal_flight.csv (Штатный полет)": "01_normal_flight.csv",
        "02_sensor_faults.csv (Сбои датчиков)": "02_sensor_faults.csv",
        "03_radio_noise.csv (Радиошум)": "03_radio_noise.csv",
    }
    selected_key = st.sidebar.selectbox("📂 Выберите датасет", list(dataset_options.keys()))
    filename = dataset_options[selected_key]
    parsed_records = parse_file(filename)

    if not parsed_records:
        st.error(f"Не удалось загрузить файл {filename} из папки data/. Проверьте структуру репозитория.")
        return

    st.sidebar.success(f"Загружено записей: {len(parsed_records)}")
    tab_live, tab_dynamics, tab_raw = st.tabs(
        ["🎛️ Пульты и кадры", "📈 Динамика полета (Тренд)", "💻 Сырые данные и аудит"])

    with tab_live:
        frame_idx = st.slider("Выбор кадра телеметрии", 0, len(parsed_records) - 1, 0)
        render_frame(parsed_records[frame_idx])

    with tab_dynamics:
        st.subheader("📈 Графики изменения параметров за полет")
        batt = [r["battery_v"] for r in parsed_records if "battery_v" in r]
        step = max(1, len(batt) // 5000)
        st.markdown("**Напряжение АКБ (В) во времени**")
        st.line_chart(pd.DataFrame({"АКБ (В)": batt[::step]}))
        st.markdown("**Статистика состояния пакетов в датасете:**")
        st.bar_chart(pd.Series([r["status"] for r in parsed_records]).value_counts())

    with tab_raw:
        st.subheader("Анализ сырой строки")
        r_idx = st.number_input("Номер строки", 0, len(parsed_records) - 1, 0)
        st.code(str(parsed_records[r_idx].get("raw", "")))
        st.json(parsed_records[r_idx])


# ---------------------------------------------------------------- выбор режима
mode = st.sidebar.radio("Источник данных", ["Файл (датасет)", "Эмулятор (live)"])
if mode == "Эмулятор (live)":
    url = st.sidebar.text_input("Адрес эмулятора", EMULATOR_URL)
    st.sidebar.caption("Эмулятор должен работать на том же компьютере, где запущено приложение "
                       "(на streamlit.app он недоступен).")
    live_view(url)
else:
    file_view()
