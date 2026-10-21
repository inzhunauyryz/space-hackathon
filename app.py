from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError
import json
import math

import pandas as pd
import streamlit as st
import plotly.graph_objects as go

from telemetry import parse_telemetry_line, load_local_dataset


BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"

st.set_page_config(
    page_title="ИнтроСат | Центр управления полётами",
    page_icon="🛰️",
    layout="wide",
)

# -------------------- Оформление --------------------

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
        padding: 12px;
        border-radius: 12px;
    }

    div[data-testid="stMetricValue"] {
        color: #68d9ff;
    }

    h1, h2, h3 {
        color: #68d9ff;
    }

    div.stButton > button {
        border-radius: 9px;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

st.title("🛰️ ИНТРОСАТ")
st.subheader("Центр управления полётами")
st.caption(
    "Мониторинг телеметрии • Анализ состояния • "
    "Сценарии миссии • Журнал событий"
)


# -------------------- Вспомогательные функции --------------------

STATUS_LABELS = {
    "NORMAL": "🟢 НОРМА",
    "WARNING": "🟡 ПРЕДУПРЕЖДЕНИЕ",
    "FAULT": "🔴 АВАРИЯ",
    "CORRUPTED": "⚫ ПОВРЕЖДЁННЫЙ ПАКЕТ",
}


def safe_number(value, default=0.0):
    try:
        number = float(value)
        return number if math.isfinite(number) else default
    except (TypeError, ValueError):
        return default


def status_rank(status):
    return {
        "NORMAL": 0,
        "WARNING": 1,
        "FAULT": 2,
        "CORRUPTED": 3,
    }.get(status, 0)


def overall_status(items):
    if not items:
        return "CORRUPTED"

    return max(
        (item.get("status", "CORRUPTED") for item in items),
        key=status_rank,
    )


def add_event(
    events,
    time_value,
    kind,
    description,
    recommendation,
    raw="",
):
    events.append({
        "Время": time_value,
        "Тип": kind,
        "Описание": description,
        "Рекомендация оператору": recommendation,
        "Исходный пакет": raw,
    })


def build_events(parsed_items):
    events = []

    for item in parsed_items:
        status = item.get("status", "CORRUPTED")
        time_ms = item.get("time_ms")
        time_label = (
            f"{time_ms} мс"
            if time_ms is not None
            else "Не определено"
        )
        raw = item.get("raw", "")

        if status == "CORRUPTED":
            add_event(
                events,
                time_label,
                "ПОВРЕЖДЕНИЕ",
                item.get("error")
                or "Не удалось разобрать пакет телеметрии.",
                "Проверьте формат пакета, разделители и источник данных.",
                raw,
            )

        elif status in ("WARNING", "FAULT"):
            warnings = item.get("warnings") or [
                "Обнаружено отклонение параметров."
            ]

            for message in warnings:
                text_message = str(message).lower()

                is_battery = (
                    "battery" in text_message
                    or "батар" in text_message
                    or "напряж" in text_message
                )

                recommendation = (
                    "Проверьте напряжение питания и нагрузку; "
                    "сравните значения с технической документацией."
                    if is_battery
                    else
                    "Сравните показания с соседними пакетами "
                    "и документацией датчика."
                )

                add_event(
                    events,
                    time_label,
                    status,
                    str(message),
                    recommendation,
                    raw,
                )

    return events


def rotate_point(x, y, z, ax, ay, az):
    x_angle, y_angle, z_angle = map(
        math.radians,
        (ax, ay, az),
    )

    # Поворот вокруг X
    y1 = (
        y * math.cos(x_angle)
        - z * math.sin(x_angle)
    )
    z1 = (
        y * math.sin(x_angle)
        + z * math.cos(x_angle)
    )

    # Поворот вокруг Y
    x2 = (
        x * math.cos(y_angle)
        + z1 * math.sin(y_angle)
    )
    z2 = (
        -x * math.sin(y_angle)
        + z1 * math.cos(y_angle)
    )

    # Поворот вокруг Z
    x3 = (
        x2 * math.cos(z_angle)
        - y1 * math.sin(z_angle)
    )
    y3 = (
        x2 * math.sin(z_angle)
        + y1 * math.cos(z_angle)
    )

    return x3, y3, z2


def make_satellite_figure(ax, ay, az):
    fig = go.Figure()

    # Корпус спутника
    body_x = [-1, 1, 1, -1, -1, 1, 1, -1]
    body_y = [
        -0.6, -0.6, 0.6, 0.6,
        -0.6, -0.6, 0.6, 0.6,
    ]
    body_z = [
        -0.6, -0.6, -0.6, -0.6,
        0.6, 0.6, 0.6, 0.6,
    ]

    triangles_i = [0, 0, 0, 1, 4, 4, 5, 2, 3, 6, 7, 7]
    triangles_j = [1, 2, 3, 2, 5, 6, 6, 6, 7, 7, 4, 3]
    triangles_k = [2, 3, 7, 5, 6, 7, 2, 3, 4, 2, 0, 0]

    # Левая солнечная панель
    panel_left = (
        [-3, -1.1, -1.1, -3],
        [-0.7, -0.7, 0.7, 0.7],
        [-0.08, -0.08, -0.08, -0.08],
    )

    # Правая солнечная панель
    panel_right = (
        [1.1, 3, 3, 1.1],
        [-0.7, -0.7, 0.7, 0.7],
        [-0.08, -0.08, -0.08, -0.08],
    )

    parts = [
        ("Корпус", (body_x, body_y, body_z), "#8fa7c4"),
        ("Левая солнечная панель", panel_left, "#1677d2"),
        ("Правая солнечная панель", panel_right, "#1677d2"),
    ]

    for name, coords, color in parts:
        rotated = [
            rotate_point(x, y, z, ax, ay, az)
            for x, y, z in zip(*coords)
        ]

        if name == "Корпус":
            tri_i = triangles_i
            tri_j = triangles_j
            tri_k = triangles_k
        else:
            tri_i = [0, 0]
            tri_j = [1, 2]
            tri_k = [2, 3]

        fig.add_trace(go.Mesh3d(
            x=[point[0] for point in rotated],
            y=[point[1] for point in rotated],
            z=[point[2] for point in rotated],
            i=tri_i,
            j=tri_j,
            k=tri_k,
            color=color,
            opacity=0.95,
            name=name,
        ))

    # Неподвижные оси координат
    for axis_name, endpoint, color in [
        ("X", (2.0, 0, 0), "red"),
        ("Y", (0, 2.0, 0), "lime"),
        ("Z", (0, 0, 2.0), "orange"),
    ]:
        fig.add_trace(go.Scatter3d(
            x=[0, endpoint[0]],
            y=[0, endpoint[1]],
            z=[0, endpoint[2]],
            mode="lines+text",
            text=["", axis_name],
            line=dict(color=color, width=7),
            name=f"Ось {axis_name}",
        ))

    fig.update_layout(
        height=470,
        margin=dict(l=0, r=0, t=10, b=0),
        paper_bgcolor="#07111f",
        font=dict(color="white"),
        scene=dict(
            bgcolor="#07111f",
            xaxis=dict(title="X", color="white"),
            yaxis=dict(title="Y", color="white"),
            zaxis=dict(title="Z", color="white"),
            aspectmode="data",
        ),
        legend=dict(font=dict(color="white")),
    )

    return fig
  # -------------------- Панель оператора --------------------

st.sidebar.title("🛰️ Панель оператора")

source_mode = st.sidebar.radio(
    "Источник телеметрии",
    ["Тестовые данные", "HTTP-источник"],
)

mission_mode = st.sidebar.selectbox(
    "🎮 Демонстрационный сценарий",
    [
        "Без симуляции",
        "Отказ датчика (демо)",
        "Помехи связи (демо)",
    ],
    help=(
        "Сценарии демонстрационные: они не запускают "
        "реальный эмулятор спутника."
    ),
)

st.sidebar.caption("ИнтроСат • Учебная система мониторинга")
st.sidebar.markdown("---")


# -------------------- Получение данных --------------------

raw_lines = []
source_label = ""

if source_mode == "Тестовые данные":
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
            "Загрузи тестовые данные в папку data в GitHub."
        )
        st.stop()

    selected_file = st.sidebar.selectbox(
        "Набор телеметрии",
        files,
    )

    df = load_local_dataset(selected_file)

    if (
        df is None
        or df.empty
        or "raw" not in df.columns
    ):
        st.error(
            "Файл пустой или не удалось прочитать столбец raw."
        )
        st.stop()

    max_rows = min(len(df), 2000)

    if len(df) > max_rows:
        st.info(
            f"В файле {len(df):,} пакетов. "
            f"Для интерфейса доступно до {max_rows:,} пакетов."
        )

    selected_count = st.sidebar.slider(
        "Количество пакетов",
        min_value=1,
        max_value=max_rows,
        value=min(300, max_rows),
        step=1,
    )

    raw_lines = (
        df["raw"]
        .head(selected_count)
        .astype(str)
        .tolist()
    )

    source_label = selected_file

else:
    st.markdown("### 📡 Подключение к HTTP-телеметрии")

    st.warning(
        "Нужен доступный HTTP-адрес, возвращающий пакет "
        "телеметрии. В Streamlit Cloud адрес 127.0.0.1 "
        "относится к облачному серверу, а не к телефону."
    )

    http_url = st.text_input(
        "URL источника",
        placeholder="https://example.com/telemetry",
    )

    if not http_url.strip():
        st.info("Введи URL и нажми «Получить пакет».")
        st.stop()

    if not http_url.startswith(("http://", "https://")):
        st.error(
            "Адрес должен начинаться с http:// или https://."
        )
        st.stop()

    if st.button(
        "📡 Получить пакет",
        use_container_width=True,
    ):
                try:
            request = Request(
                http_url.strip(),
                headers={"User-Agent": "IntroSat-MCC/1.0"},
            )

            with urlopen(request, timeout=8) as response:
                body = response.read(100_000).decode("utf-8-sig")

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

            received_lines = [
                line.strip()
                for line in packet.splitlines()
                if line.strip()
            ]

            if not received_lines:
                st.error("Источник вернул пустую телеметрию.")
            else:
                st.success(
                    f"Получено строк телеметрии: {len(received_lines)}"
                )

                st.error("Источник вернул пустой пакет.")
                st.stop()

            st.session_state["received_packet"] = (
                received_lines[-1]
            )

            if "received_packets" not in st.session_state:
                st.session_state["received_packets"] = []

            st.session_state["received_packets"].append(
                received_lines[-1]
            )

            st.session_state["received_packets"] = (
                st.session_state["received_packets"][-100:]
            )

            if "received_packets" not in st.session_state:
                st.session_state["received_packets"] = []

            st.session_state["received_packets"].append(
                received_lines[-1]
            )

            st.session_state["received_packets"] = (
                st.session_state["received_packets"][-100:]
            )

if "received_packets" not in st.session_state:
    st.session_state["received_packets"] = []

st.session_state["received_packets"].append(
    received_lines[-1]
)

st.session_state["received_packets"] = (
    st.session_state["received_packets"][-100:]
)
            st.session_state["received_source"] = (
                http_url.strip()
            )

        except (
            HTTPError,
            URLError,
            TimeoutError,
            OSError,
        ) as exc:
            st.error(
                f"Не удалось получить телеметрию: {exc}"
            )
            st.stop()

    if "received_packet" not in st.session_state:
        st.info("Нажми кнопку, чтобы получить пакет.")
        st.stop()

    raw_lines = st.session_state.get(
    "received_packets",
    [st.session_state["received_packet"]],
)

    source_label = st.session_state.get(
        "received_source",
        "HTTP",
    )


# -------------------- Разбор пакетов --------------------

parsed = [
    parse_telemetry_line(line)
    for line in raw_lines
]


# -------------------- Демонстрационные сценарии --------------------

if mission_mode == "Отказ датчика (демо)" and parsed:
    target = next(
        (
            item
            for item in reversed(parsed)
            if item.get("status") != "CORRUPTED"
        ),
        None,
    )

    if target is not None:
        target["status"] = "FAULT"
        target.setdefault("warnings", []).append(
            "ДЕМО: смоделирован отказ одного из датчиков."
        )

if mission_mode == "Помехи связи (демо)" and parsed:
    for index, item in enumerate(parsed):
        if index % 3 == 0:
            item["status"] = "CORRUPTED"
            item["error"] = (
                "ДЕМО: имитация потери или повреждения "
                "пакета при радиопомехах."
            )


# -------------------- Сводка телеметрии --------------------

valid_packets = [
    item
    for item in parsed
    if item.get("status") != "CORRUPTED"
]

normal_count = sum(
    item.get("status") == "NORMAL"
    for item in parsed
)

warning_count = sum(
    item.get("status") == "WARNING"
    for item in parsed
)

fault_count = sum(
    item.get("status") == "FAULT"
    for item in parsed
)

corrupted_count = sum(
    item.get("status") == "CORRUPTED"
    for item in parsed
)

st.caption(
    f"Источник данных: {source_label} • "
    f"Сценарий: {mission_mode}"
)

st.markdown("## 📊 Состояние системы")

m1, m2, m3, m4 = st.columns(4)

m1.metric("Всего пакетов", f"{len(parsed):,}")
m2.metric("Норма", f"{normal_count:,}")
m3.metric(
    "Предупреждения / аварии",
    f"{warning_count + fault_count:,}",
)
m4.metric("Повреждены", f"{corrupted_count:,}")

overall = overall_status(parsed)

if overall == "CORRUPTED":
    st.error(
        "⚫ СВЯЗЬ / ДАННЫЕ: есть повреждённые пакеты "
        "или нет корректных данных."
    )
elif overall == "FAULT":
    st.error(
        "🔴 АВАРИЯ: требуется внимание оператора."
    )
elif overall == "WARNING":
    st.warning(
        "🟡 ПРЕДУПРЕЖДЕНИЕ: проверь параметры телеметрии."
    )
else:
    st.success(
        "🟢 НОРМА: критических отклонений "
        "в выбранных пакетах не обнаружено."
    )

if not valid_packets:
    st.error(
        "Нет корректных пакетов для построения графиков."
    )

    with st.expander("Показать исходные пакеты"):
        st.code("\n".join(raw_lines[:50]))

    st.stop()


# -------------------- Таблица измерений --------------------

records = []

for item in valid_packets:
    light = list(item.get("light", [0, 0, 0, 0]))
    accel = list(item.get("accel", [0, 0, 0]))
    gyro = list(item.get("gyro", [0, 0, 0]))
    mag = list(item.get("mag", [0, 0, 0]))

    records.append({
        "Время, мс": item.get("time_ms"),
        "АКБ, В": item.get("battery_v"),
        "СД1": light[0] if len(light) > 0 else None,
        "СД2": light[1] if len(light) > 1 else None,
        "СД3": light[2] if len(light) > 2 else None,
        "СД4": light[3] if len(light) > 3 else None,
        "AX, g": accel[0] if len(accel) > 0 else None,
        "AY, g": accel[1] if len(accel) > 1 else None,
        "AZ, g": accel[2] if len(accel) > 2 else None,
        "GX, °/с": gyro[0] if len(gyro) > 0 else None,
        "GY, °/с": gyro[1] if len(gyro) > 1 else None,
        "GZ, °/с": gyro[2] if len(gyro) > 2 else None,
        "MX, Гс": mag[0] if len(mag) > 0 else None,
        "MY, Гс": mag[1] if len(mag) > 1 else None,
        "MZ, Гс": mag[2] if len(mag) > 2 else None,
        "Маховик": item.get("flywheel"),
        "Статус": item.get("status"),
    })

measurements = pd.DataFrame(records)

latest = valid_packets[-1]
latest_status = latest.get("status", "CORRUPTED")


# -------------------- Панель состояния аппарата --------------------

st.markdown("## 📡 Панель состояния аппарата")

s1, s2, s3, s4 = st.columns(4)

s1.metric(
    "🔋 Напряжение АКБ",
    f"{safe_number(latest.get('battery_v')):.3f} В",
)

s2.metric(
    "⏱️ Время работы",
    f"{safe_number(latest.get('time_ms')) / 1000:.1f} с",
)

s3.metric(
    "⚙️ Маховик",
    str(latest.get("flywheel", "—")),
)

s4.metric(
    "Статус последнего пакета",
    STATUS_LABELS.get(latest_status, latest_status),
)

if latest_status == "FAULT":
    sensor_status = "Аварийное отклонение в последнем пакете"
elif latest_status == "WARNING":
    sensor_status = "Есть предупреждения"
elif latest_status == "NORMAL":
    sensor_status = "Критических отклонений не отмечено"
else:
    sensor_status = "Состояние не определено"

st.info(
    f"**Состояние датчиков:** {sensor_status}. "
    "Это программная оценка по телеметрии, "
    "а не самодиагностика аппаратуры."
) # -------------------- Вкладки приложения --------------------

tab1, tab2, tab3, tab4, tab5 = st.tabs([
    "📈 Графики",
    "🔬 Датчики",
    "🚨 Журнал событий",
    "📋 Исходные данные",
    "🛰️ 3D Спутник",
])


# -------------------- Вкладка 1: Графики --------------------

with tab1:
    st.markdown("### 🔋 Напряжение аккумулятора")

    st.line_chart(
        measurements.set_index("Время, мс")[["АКБ, В"]]
    )

    st.markdown("### 📐 Акселерометр")

    st.line_chart(
        measurements.set_index("Время, мс")[
            ["AX, g", "AY, g", "AZ, g"]
        ]
    )

    st.markdown("### 🌀 Гироскоп")

    st.line_chart(
        measurements.set_index("Время, мс")[
            ["GX, °/с", "GY, °/с", "GZ, °/с"]
        ]
    )

    st.markdown("### ☀️ Датчики освещённости")

    st.line_chart(
        measurements.set_index("Время, мс")[
            ["СД1", "СД2", "СД3", "СД4"]
        ]
    )


# -------------------- Вкладка 2: Датчики --------------------

with tab2:
    st.markdown("### Последние корректные показания")

    left, right = st.columns(2)

    with left:
        st.markdown("#### ☀️ Датчики освещённости")

        for i, value in enumerate(
            latest.get("light", []),
            start=1,
        ):
            st.metric(f"СД{i}", value)

        st.markdown("#### 📐 Акселерометр, g")

        for axis, value in zip(
            "XYZ",
            latest.get("accel", []),
        ):
            st.metric(
                f"A{axis}",
                f"{safe_number(value):.4f}",
            )

    with right:
        st.markdown("#### 🌀 Гироскоп, °/с")

        for axis, value in zip(
            "XYZ",
            latest.get("gyro", []),
        ):
            st.metric(
                f"G{axis}",
                f"{safe_number(value):.3f}",
            )

        st.markdown("#### 🧭 Магнитометр")

        for axis, value in zip(
            "XYZ",
            latest.get("mag", []),
        ):
            st.metric(
                f"M{axis}",
                f"{safe_number(value):.4f}",
            )

    st.caption(
        "Единицы измерения и пороги необходимо сверить "
        "с технической документацией эмулятора."
    )


# -------------------- Вкладка 3: Журнал событий --------------------

with tab3:
    st.markdown("### 🚨 Журнал событий")

    events = build_events(parsed)

    if events:
        event_df = pd.DataFrame(events)

        st.dataframe(
            event_df,
            use_container_width=True,
            hide_index=True,
        )

        event_csv = event_df.to_csv(
            index=False,
        ).encode("utf-8-sig")

        st.download_button(
            "⬇️ Скачать журнал событий CSV",
            data=event_csv,
            file_name="introsat_events.csv",
            mime="text/csv",
            use_container_width=True,
        )

        st.markdown("#### Сводка по типам событий")

        event_counts = (
            event_df["Тип"]
            .value_counts()
            .rename_axis("Тип события")
            .reset_index(name="Количество")
        )

        st.dataframe(
            event_counts,
            use_container_width=True,
            hide_index=True,
        )

    else:
        st.success("Событий не обнаружено.")
        st.caption(
            "Журнал формируется по статусам, "
            "которые возвращает парсер телеметрии."
        )


# -------------------- Вкладка 4: Исходные данные --------------------

with tab4:
    st.markdown("### 📋 Измерения телеметрии")

    st.dataframe(
        measurements,
        use_container_width=True,
        hide_index=True,
    )

    st.download_button(
        "⬇️ Скачать измерения CSV",
        data=measurements.to_csv(
            index=False,
        ).encode("utf-8-sig"),
        file_name="introsat_telemetry.csv",
        mime="text/csv",
        use_container_width=True,
    )

    with st.expander("Показать исходные пакеты"):
        st.code("\n".join(raw_lines[:100]))

    st.caption(
        f"Всего пакетов в выбранной выборке: {len(raw_lines)}. "
        f"Корректных пакетов для таблицы: {len(valid_packets)}."
    )


# -------------------- Вкладка 5: 3D-спутник --------------------

with tab5:
    st.markdown("## 🛰️ Трёхмерная модель спутника")

    st.caption(
        "Демонстрационная модель. Углы не являются "
        "рассчитанной ориентацией реального аппарата."
    )

    # Инициализация углов
    if "sat_x" not in st.session_state:
        st.session_state["sat_x"] = 0

    if "sat_y" not in st.session_state:
        st.session_state["sat_y"] = 0

    if "sat_z" not in st.session_state:
        st.session_state["sat_z"] = 0

    st.markdown("### 🎮 Управление ориентацией")

    st.caption(
        "Каждое нажатие поворачивает корпус и панели на 15°."
    )

    cx, cy, cz, cr = st.columns(4)

    with cx:
        st.markdown("**Ось X**")

        if st.button(
            "↶ X −15°",
            key="x_minus",
            use_container_width=True,
        ):
            st.session_state["sat_x"] -= 15

        if st.button(
            "↷ X +15°",
            key="x_plus",
            use_container_width=True,
        ):
            st.session_state["sat_x"] += 15

    with cy:
        st.markdown("**Ось Y**")

        if st.button(
            "↶ Y −15°",
            key="y_minus",
            use_container_width=True,
        ):
            st.session_state["sat_y"] -= 15

        if st.button(
            "↷ Y +15°",
            key="y_plus",
            use_container_width=True,
        ):
            st.session_state["sat_y"] += 15

    with cz:
        st.markdown("**Ось Z**")

        if st.button(
            "↶ Z −15°",
            key="z_minus",
            use_container_width=True,
        ):
            st.session_state["sat_z"] -= 15

        if st.button(
            "↷ Z +15°",
            key="z_plus",
            use_container_width=True,
        ):
            st.session_state["sat_z"] += 15

    with cr:
        st.markdown("**Сброс**")

        if st.button(
            "🔄 Сбросить",
            key="sat_reset",
            use_container_width=True,
        ):
            st.session_state["sat_x"] = 0
            st.session_state["sat_y"] = 0
            st.session_state["sat_z"] = 0

    st.write(
        f"**Углы модели:** "
        f"X = {st.session_state['sat_x']}° · "
        f"Y = {st.session_state['sat_y']}° · "
        f"Z = {st.session_state['sat_z']}°"
    )

    # Строим модель с учётом текущих углов
    fig = make_satellite_figure(
        st.session_state["sat_x"],
        st.session_state["sat_y"],
        st.session_state["sat_z"],
    )

    st.plotly_chart(
        fig,
        use_container_width=True,
        config={
            "scrollZoom": False,
            "displaylogo": False,
        },
    )

    st.markdown("### 📡 Телеметрия спутника")

    v1, v2, v3 = st.columns(3)

    v1.metric(
        "Напряжение АКБ",
        f"{safe_number(latest.get('battery_v')):.3f} В",
    )

    v2.metric(
        "Время телеметрии",
        f"{safe_number(latest.get('time_ms')) / 1000:.1f} с",
    )

    v3.metric(
        "Статус",
        STATUS_LABELS.get(latest_status, latest_status),
    )

    st.info(
        "Оси X, Y и Z служат неподвижными ориентирами. "
        "Для отображения реальной ориентации нужны "
        "алгоритм оценки ориентации и соответствующие "
        "данные датчиков."
    )


# -------------------- Подвал приложения --------------------

st.markdown("---")

st.caption(
    "ИнтроСат | Учебный центр управления полётами. "
    "Сценарии с пометкой «демо» изменяют только отображаемую "
    "оценку и не воздействуют на источник данных."
)
