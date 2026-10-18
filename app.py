from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError
import json

import pandas as pd
import streamlit as st

from telemetry import parse_telemetry_line, load_local_dataset


BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"

st.set_page_config(
    page_title="ИнтроСат | Центр управления полётами",
    page_icon="🛰️",
    layout="wide",
)

# ---------- Оформление ----------

st.markdown(
    """
    <style>
    .stApp {
        background-color: #07111f;
        color: #e6edf7;
    }

    [data-testid="stMetric"] {
        background: #101f33;
        border: 1px solid #243a55;
        padding: 14px;
        border-radius: 12px;
    }

    div[data-testid="stMetricValue"] {
        color: #68d9ff;
    }

    h1, h2, h3 {
        color: #68d9ff;
    }

    </style>
    """,
    unsafe_allow_html=True,
)

st.title("🛰️ ИНТРОСАТ")
st.subheader("Центр управления полётами")
st.caption("Мониторинг телеметрии • Анализ состояния • Журнал событий")

# ---------- Боковая панель ----------

st.sidebar.title("🛰️ Панель оператора")

mode = st.sidebar.radio(
    "Источник телеметрии",
    [
        "Тестовые данные",
        "HTTP-источник",
    ],
)

st.sidebar.markdown("---")
st.sidebar.caption("Система анализа телеметрии учебного спутника")

# ---------- Получение данных ----------

if mode == "Тестовые данные":

    if not DATA_DIR.exists():
        st.error("Папка data не найдена в репозитории.")
        st.stop()

    files = sorted(
        file.name
        for file in DATA_DIR.iterdir()
        if file.is_file()
        and file.suffix.lower() in (".csv", ".txt", ".log")
    )

    if not files:
        st.error(
            "В папке data не найдены CSV, TXT или LOG-файлы."
        )
        st.info(
            "Проверь, что тестовые данные загружены в GitHub "
            "в папку data."
        )
        st.stop()

    selected_file = st.sidebar.selectbox(
        "Выбери набор телеметрии",
        files,
    )

    df = load_local_dataset(selected_file)

    if df is None or df.empty:
        st.error("Файл пустой или его не удалось прочитать.")
        st.stop()

    st.caption(f"Источник данных: {selected_file}")

    # Ограничиваем объём обработки для быстрой работы
    # приложения на мобильном устройстве.
    max_rows = min(len(df), 2000)

    if len(df) > max_rows:
        st.info(
            f"В файле {len(df):,} пакетов. "
            f"Для быстрого отображения анализируем первые "
            f"{max_rows:,}. "
            "Это ограничение интерфейса, а не всего файла."
        )

    selected_count = st.sidebar.slider(
        "Количество анализируемых пакетов",
        min_value=1,
        max_value=max_rows,
        value=min(300, max_rows),
        step=1,
    )

    raw_lines = df["raw"].head(selected_count).tolist()

elif mode == "HTTP-источник":

    st.markdown("### 📡 Подключение к телеметрии")

    st.warning(
        "Укажи доступный HTTP-адрес, возвращающий один пакет "
        "телеметрии. Адрес 127.0.0.1 в облаке указывает "
        "на сервер Streamlit, а не на твой телефон."
    )

    http_url = st.text_input(
        "URL источника",
        placeholder="https://example.com/telemetry",
    )

    if not http_url.strip():
        st.info(
            "Введи URL доступного источника и нажми "
            "«Получить пакет»."
        )
        st.stop()

    if not http_url.startswith(("http://", "https://")):
        st.error("Адрес должен начинаться с http:// или https://.")
        st.stop()

    if st.button("📡 Получить пакет", use_container_width=True):

        try:
            request = Request(
                http_url.strip(),
                headers={"User-Agent": "IntroSat-MCC/1.0"},
            )

            with urlopen(request, timeout=8) as response:
                body = response.read(100_000).decode("utf-8-sig")

            # Поддерживаем как обычную текстовую строку,
            # так и JSON с полем raw или telemetry.
            try:
                payload = json.loads(body)

                if isinstance(payload, dict):
                    packet = (
                        payload.get("raw")
                        or payload.get("telemetry")
                        or payload.get("data")
                    )

                    if isinstance(packet, list):
                        packet = ";".join(map(str, packet))

                    if not isinstance(packet, str):
                        packet = body
                else:
                    packet = body

            except json.JSONDecodeError:
                packet = body

            # Если сервер вернул несколько строк,
            # берём последнюю непустую строку.
            received_lines = [
                line.strip()
                for line in packet.splitlines()
                if line.strip()
            ]

            if not received_lines:
                st.error("Источник вернул пустой пакет.")
                st.stop()

            st.session_state["received_packet"] = received_lines[-1]

        except (HTTPError, URLError, TimeoutError, OSError) as exc:
            st.error(f"Не удалось получить телеметрию: {exc}")
            st.stop()

    if "received_packet" not in st.session_state:
        st.info("Нажми кнопку, чтобы получить пакет.")
        st.stop()

    raw_lines = [st.session_state["received_packet"]]

# ---------- Анализ пакетов ----------

parsed = [
    parse_telemetry_line(line)
    for line in raw_lines
]

valid_packets = [
    item
    for item in parsed
    if item["status"] != "CORRUPTED"
]

normal_count = sum(
    item["status"] == "NORMAL"
    for item in parsed
)

warning_count = sum(
    item["status"] == "WARNING"
    for item in parsed
)

fault_count = sum(
    item["status"] == "FAULT"
    for item in parsed
)

corrupted_count = sum(
    item["status"] == "CORRUPTED"
    for item in parsed
)

# ---------- Общая сводка ----------

st.markdown("## 📊 Состояние системы")

c1, c2, c3, c4 = st.columns(4)

c1.metric("Всего пакетов", f"{len(parsed):,}")
c2.metric("Норма", f"{normal_count:,}")
c3.metric("Предупреждения / аварии", f"{warning_count + fault_count:,}")
c4.metric("Повреждены", f"{corrupted_count:,}")

if corrupted_count:
    st.error(
        f"Обнаружено повреждённых пакетов: {corrupted_count}."
    )
elif fault_count:
    st.error(
        f"Обнаружено аварийных состояний: {fault_count}."
    )
elif warning_count:
    st.warning(
        f"Обнаружены предупреждения: {warning_count}."
    )
else:
    st.success("Ошибок и предупреждений в выбранных пакетах нет.")

if not valid_packets:
    st.error(
        "Нет корректных пакетов для построения графиков. "
        "Проверь формат данных."
    )

    with st.expander("Показать исходные данные"):
        st.code("\n".join(raw_lines[:30]))

    st.stop()

# ---------- Таблица корректных измерений ----------

records = []

for item in valid_packets:
    records.append({
        "Время, мс": item["time_ms"],
        "АКБ, В": item["battery_v"],
        "СД1": item["light"][0],
        "СД2": item["light"][1],
        "СД3": item["light"][2],
        "СД4": item["light"][3],
        "AX, g": item["accel"][0],
        "AY, g": item["accel"][1],
        "AZ, g": item["accel"][2],
        "GX, °/с": item["gyro"][0],
        "GY, °/с": item["gyro"][1],
        "GZ, °/с": item["gyro"][2],
        "MX, Гс": item["mag"][0],
        "MY, Гс": item["mag"][1],
        "MZ, Гс": item["mag"][2],
        "Маховик": item["flywheel"],
        "Статус": item["status"],
    })

measurements = pd.DataFrame(records)

# ---------- Последний пакет ----------

latest = valid_packets[-1]

st.markdown("## 🛰️ Текущие показания")

a, b, c = st.columns(3)

a.metric(
    "🔋 Напряжение АКБ",
    f"{latest['battery_v']:.3f} В",
)

b.metric(
    "⏱️ Время работы",
    f"{latest['time_ms'] / 1000:.1f} с",
)

c.metric(
    "⚙️ Маховик",
    str(latest["flywheel"]),
)

status_labels = {
    "NORMAL": "🟢 НОРМА",
    "WARNING": "🟡 ПРЕДУПРЕЖДЕНИЕ",
    "FAULT": "🔴 АВАРИЯ",
    "CORRUPTED": "⚫ ПОВРЕЖДЁННЫЙ ПАКЕТ",
}

st.markdown(
    f"### Состояние последнего корректного пакета: "
    f"{status_labels[latest['status']]}"
)

# ---------- Вкладки ----------

tab1, tab2, tab3, tab4, tab5 = st.tabs([
    "📈 Графики",
    "🔬 Датчики",
    "🚨 Журнал событий",
    "📋 Исходные данные",
    "🛰️ 3D Спутник",
])

with tab1:
    st.markdown("### Напряжение аккумулятора")

    st.line_chart(
        measurements.set_index("Время, мс")[["АКБ, В"]]
    )

    st.markdown("### Показания акселерометра")

    st.line_chart(
        measurements.set_index("Время, мс")[
            ["AX, g", "AY, g", "AZ, g"]
        ]
    )

    st.markdown("### Показания гироскопа")

    st.line_chart(
        measurements.set_index("Время, мс")[
            ["GX, °/с", "GY, °/с", "GZ, °/с"]
        ]
    )

    st.markdown("### Датчики освещённости")

    st.line_chart(
        measurements.set_index("Время, мс")[
            ["СД1", "СД2", "СД3", "СД4"]
        ]
    )

with tab2:
    st.markdown("### Показания последнего корректного пакета")

    left, right = st.columns(2)

    with left:
        st.markdown("**Датчики освещённости**")

        for i, value in enumerate(latest["light"], start=1):
            st.metric(f"СД{i}", value)

        st.markdown("**Акселерометр, g**")

        for axis, value in zip("XYZ", latest["accel"]):
            st.metric(f"A{axis}", f"{value:.4f}")

    with right:
        st.markdown("**Гироскоп, °/с**")

        for axis, value in zip("XYZ", latest["gyro"]):
            st.metric(f"G{axis}", f"{value:.3f}")

        st.markdown("**Магнитометр, Гс**")

        for axis, value in zip("XYZ", latest["mag"]):
            st.metric(f"M{axis}", f"{value:.4f}")

    st.caption(
        "Показания представлены в пересчитанных единицах. "
        "Пороговые значения необходимо сверить с документацией."
    )

with tab3:
    st.markdown("### 🚨 Журнал событий")

    events = []

    for item in parsed:
        if item["status"] == "NORMAL":
            continue

        if item["status"] == "CORRUPTED":
            events.append({
                "Время": "Не определено",
                "Тип": "ПОВРЕЖДЕНИЕ",
                "Описание": item["error"] or "Ошибка пакета",
                "Исходный пакет": item["raw"],
            })

        else:
            for message in item["warnings"]:
                events.append({
                    "Время": f"{item['time_ms']} мс",
                    "Тип": item["status"],
                    "Описание": message,
                    "Исходный пакет": item["raw"],
                })

    if events:
        st.dataframe(
            pd.DataFrame(events),
            use_container_width=True,
            hide_index=True,
        )

        event_csv = pd.DataFrame(events).to_csv(
            index=False
        ).encode("utf-8-sig")

        st.download_button(
            "⬇️ Скачать журнал событий CSV",
            data=event_csv,
            file_name="introsat_events.csv",
            mime="text/csv",
            use_container_width=True,
        )
    else:
        st.success("Событий не обнаружено.")

with tab4:
    st.markdown("### Исходные пакеты телеметрии")

    st.dataframe(
        measurements,
        use_container_width=True,
        hide_index=True,
    )

    st.download_button(
        "⬇️ Скачать измерения CSV",
        data=measurements.to_csv(
            index=False
        ).encode("utf-8-sig"),
        file_name="introsat_telemetry.csv",
        mime="text/csv",
        use_container_width=True,
    )

    with st.expander("Показать сырые пакеты"):
        st.code("\n".join(raw_lines[:100]))

with tab5:
    import plotly.graph_objects as go

    st.markdown("## 🛰️ Трёхмерная модель спутника")
    st.caption(
        "Демонстрационная модель. "
        "Ориентация не является реальной ориентацией аппарата."
    )

    # Корпус спутника
    fig = go.Figure()

    fig.add_trace(go.Mesh3d(
        x=[-1, 1, 1, -1, -1, 1, 1, -1],
        y=[-0.6, -0.6, 0.6, 0.6, -0.6, -0.6, 0.6, 0.6],
        z=[-0.6, -0.6, -0.6, -0.6, 0.6, 0.6, 0.6, 0.6],
        i=[0, 0, 0, 1, 4, 4, 5, 2, 3, 6, 7, 7],
        j=[1, 2, 3, 2, 5, 6, 6, 6, 7, 7, 4, 3],
        k=[2, 3, 7, 5, 6, 7, 2, 3, 4, 2, 0, 0],
        color="#8fa7c4",
        opacity=0.95,
        name="Корпус"
    ))

    # Левая солнечная панель
    fig.add_trace(go.Mesh3d(
        x=[-3, -1.1, -1.1, -3],
        y=[-0.7, -0.7, 0.7, 0.7],
        z=[-0.08, -0.08, -0.08, -0.08],
        i=[0, 0],
        j=[1, 2],
        k=[2, 3],
        color="#1677d2",
        opacity=0.95,
        name="Солнечная панель"
    ))

    # Правая солнечная панель
    fig.add_trace(go.Mesh3d(
        x=[1.1, 3, 3, 1.1],
        y=[-0.7, -0.7, 0.7, 0.7],
        z=[-0.08, -0.08, -0.08, -0.08],
        i=[0, 0],
        j=[1, 2],
        k=[2, 3],
        color="#1677d2",
        opacity=0.95,
        name="Солнечная панель"
    ))

    # Оси координат
    fig.add_trace(go.Scatter3d(
        x=[0, 2.0],
        y=[0, 0],
        z=[0, 0],
        mode="lines+text",
        text=["", "X"],
        line=dict(color="red", width=7),
        name="Ось X"
    ))

    fig.add_trace(go.Scatter3d(
        x=[0, 0],
        y=[0, 2.0],
        z=[0, 0],
        mode="lines+text",
        text=["", "Y"],
        line=dict(color="green", width=7),
        name="Ось Y"
    ))

    fig.add_trace(go.Scatter3d(
        x=[0, 0],
        y=[0, 0],
        z=[0, 2.0],
        mode="lines+text",
        text=["", "Z"],
        line=dict(color="orange", width=7),
        name="Ось Z"
    ))

    fig.update_layout(
        height=480,
        margin=dict(l=0, r=0, t=20, b=0),
        paper_bgcolor="#07111f",
        font=dict(color="white"),
        scene=dict(
            bgcolor="#07111f",
            xaxis=dict(title="X", color="white"),
            yaxis=dict(title="Y", color="white"),
            zaxis=dict(title="Z", color="white"),
            aspectmode="data",
        ),
        legend=dict(
            font=dict(color="white")
        ),
    )

    st.plotly_chart(
        fig,
        use_container_width=True,
        config={
            "scrollZoom": False,
            "displaylogo": False,
        },
    )

    st.info(
        "Модель показывает форму и оси спутника. "
        "Показания датчиков отображаются отдельно; "
        "для реального поворота нужны данные ориентации."
    )

    left, right = st.columns(2)

    with left:
        st.metric(
            "Напряжение АКБ",
            f"{latest['battery_v']:.3f} В",
        )

    with right:
        st.metric(
            "Время телеметрии",
            f"{latest['time_ms'] / 1000:.1f} с",
        )

st.markdown("---")

st.caption(
    "ИнтроСат | Учебный центр управления полётами. "
    "Статусы определяются программными проверками телеметрии."
)
