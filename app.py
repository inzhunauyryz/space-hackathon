import time
from urllib.request import urlopen
from urllib.error import URLError

import pandas as pd
import streamlit as st

from telemetry import (
    parse_telemetry_line,
    load_local_dataset,
)


# --------------------------------------------------
# НАСТРОЙКИ
# --------------------------------------------------

st.set_page_config(
    page_title="ЦУП | ИнтроСат",
    page_icon="🛰️",
    layout="wide",
)

HTTP_URL = "http://127.0.0.1:8765/telemetry"

DATASETS = {
    "Штатный полёт": "01_normal_flight.csv",
    "Сбои датчиков": "02_sensor_faults.csv",
    "Радиопомехи": "03_radio_noise.csv",
}


# --------------------------------------------------
# ОФОРМЛЕНИЕ
# --------------------------------------------------

st.markdown(
    """
    <style>
    .stApp {
        background-color: #0b1018;
        color: #edf3ff;
    }

    h1, h2, h3 {
        color: #edf3ff;
    }

    div[data-testid="stMetric"] {
        background-color: #151e2b;
        border: 1px solid #29384b;
        padding: 16px;
        border-radius: 12px;
    }

    div[data-testid="stMetricLabel"] {
        color: #a9bdd7;
    }

    div[data-testid="stMetricValue"] {
        color: #ffffff;
    }

    section[data-testid="stSidebar"] {
        background-color: #101722;
    }

    </style>
    """,
    unsafe_allow_html=True,
)


# --------------------------------------------------
# ЗАГОЛОВОК
# --------------------------------------------------

st.title("🛰️ ЦЕНТР УПРАВЛЕНИЯ ПОЛЁТАМИ")
st.subheader("Космический аппарат «ИнтроСат»")

st.caption(
    "Система мониторинга телеметрии, "
    "контроля датчиков и обнаружения аномалий."
)

st.divider()


# --------------------------------------------------
# БОКОВАЯ ПАНЕЛЬ
# --------------------------------------------------

st.sidebar.title("🛰️ Панель оператора")

mode = st.sidebar.radio(
    "Источник телеметрии",
    [
        "Тестовые данные",
        "Живая телеметрия",
    ],
)

st.sidebar.divider()

if mode == "Тестовые данные":
    selected_name = st.sidebar.selectbox(
        "Выберите сценарий",
        list(DATASETS.keys()),
    )

    filename = DATASETS[selected_name]

    df = load_local_dataset(filename)

    if df is None:
        st.error(
            f"Не удалось загрузить {filename}. "
            "Проверьте наличие файла в папке data."
        )
        st.stop()

    if df.empty:
        st.error("В выбранном файле нет телеметрии.")
        st.stop()

    # Для интерфейса ограничиваем количество записей,
    # чтобы не загружать сотни тысяч пакетов сразу.
    max_records = min(len(df), 2000)

    records = [
        parse_telemetry_line(str(line))
        for line in df["raw"].iloc[:max_records]
    ]

    st.sidebar.success(
        f"Загружено пакетов: {len(records)}"
    )

    st.sidebar.caption(
        "Режим анализа заранее подготовленного CSV."
    )

    st.sidebar.info(
        f"Сценарий: {selected_name}"
    )

else:
    st.sidebar.info(
        "Для живого режима сначала запустите emulator.py "
        "на том же компьютере, где работает приложение."
    )

    st.sidebar.caption(
        f"Источник: {HTTP_URL}"
    )

    @st.fragment(run_every="1s")
    def live_telemetry():
        try:
            with urlopen(HTTP_URL, timeout=2) as response:
                raw = response.read().decode("utf-8").strip()

        except URLError:
            st.error(
                "Нет соединения с эмулятором. "
                "Запустите python emulator.py "
                "и проверьте HTTP-адрес."
            )
            return

        except TimeoutError:
            st.warning(
                "Эмулятор не ответил вовремя."
            )
            return

        except OSError as error:
            st.error(
                f"Ошибка получения телеметрии: {error}"
            )
            return

        if not raw:
            st.info(
                "Ожидание первого пакета телеметрии..."
            )
            return

        current = parse_telemetry_line(raw)

        st.subheader("📡 Мониторинг в реальном времени")

        col1, col2, col3, col4 = st.columns(4)

        col1.metric(
            "Статус пакета",
            current["status"],
        )

        col2.metric(
            "Напряжение АКБ",
            (
                f'{current["battery_v"]:.3f} В'
                if "battery_v" in current
                else "Нет данных"
            ),
        )

        col3.metric(
            "Время от запуска",
            (
                f'{current["time_ms"]} мс'
                if "time_ms" in current
                else "Нет данных"
            ),
        )

        col4.metric(
            "Маховик",
            (
                str(current["flywheel"])
                if "flywheel" in current
                else "Нет данных"
            ),
        )

        if current.get("error"):
            st.error(current["error"])

        for warning in current.get("warnings", []):
            st.warning(warning)

        if current["status"] == "NORMAL":
            st.success("Пакет прошёл проверки.")

        if current.get("accel"):
            st.subheader("Акселерометр")
            st.bar_chart(
                pd.DataFrame(
                    {
                        "Ускорение, g": current["accel"]
                    },
                    index=["AX", "AY", "AZ"],
                )
            )

        if current.get("gyro"):
            st.subheader("Гироскоп")
            st.bar_chart(
                pd.DataFrame(
                    {
                        "Угловая скорость, °/с":
                            current["gyro"]
                    },
                    index=["GX", "GY", "GZ"],
                )
            )

        if current.get("light"):
            st.subheader("Датчики освещённости")
            st.bar_chart(
                pd.DataFrame(
                    {
                        "Показания": current["light"]
                    },
                    index=["СД1", "СД2", "СД3", "СД4"],
                )
            )

        with st.expander("Исходный пакет телеметрии"):
            st.code(current["raw"])

    live_telemetry()
    st.stop()


# --------------------------------------------------
# ОБРАБОТКА ТЕСТОВЫХ ДАННЫХ
# --------------------------------------------------

st.subheader("📊 Обзор состояния спутника")

statuses = [
    record.get("status", "UNKNOWN")
    for record in records
]

normal_count = statuses.count("NORMAL")
warning_count = statuses.count("WARNING")
fault_count = statuses.count("FAULT")
corrupted_count = statuses.count("CORRUPTED")

col1, col2, col3, col4 = st.columns(4)

col1.metric(
    "Всего пакетов",
    len(records),
)

col2.metric(
    "Норма",
    normal_count,
)

col3.metric(
    "Предупреждения",
    warning_count,
)

col4.metric(
    "Ошибки",
    fault_count + corrupted_count,
)

st.caption(
    "Статистика рассчитана по загруженным пакетам."
)

st.divider()


# --------------------------------------------------
# ВЫБОР ТЕЛЕМЕТРИИ
# --------------------------------------------------

tab_live, tab_graphs, tab_events, tab_raw = st.tabs(
    [
        "🎛️ Приборы",
        "📈 Графики",
        "⚠️ События",
        "💻 Исходные данные",
    ]
)


# --------------------------------------------------
# ВКЛАДКА 1: ПРИБОРЫ
# --------------------------------------------------

with tab_live:
    st.subheader("Показания телеметрии")

    frame_idx = st.slider(
        "Выберите пакет",
        min_value=0,
        max_value=len(records) - 1,
        value=0,
    )

    current = records[frame_idx]

    st.caption(
        f"Пакет {frame_idx + 1} из {len(records)}"
    )

    if current["status"] == "CORRUPTED":
        st.error(
            "Пакет повреждён: "
            + str(current.get("error", "Неизвестная ошибка"))
        )

        st.code(current.get("raw", ""))

    else:
        status = current["status"]

        if status == "NORMAL":
            st.success("Состояние пакета: НОРМА")
        elif status == "WARNING":
            st.warning("Состояние пакета: ПРЕДУПРЕЖДЕНИЕ")
        else:
            st.error("Состояние пакета: НЕИСПРАВНОСТЬ")

        col1, col2, col3 = st.columns(3)

        col1.metric(
            "Напряжение АКБ",
            f'{current["battery_v"]:.3f} В',
        )

        col2.metric(
            "Время пакета",
            f'{current["time_ms"]} мс',
        )

        col3.metric(
            "Маховик",
            current["flywheel"],
        )

        for warning in current.get("warnings", []):
            st.warning(warning)

        st.divider()

        left, right = st.columns(2)

        with left:
            st.markdown("### Акселерометр")

            st.bar_chart(
                pd.DataFrame(
                    {"Ускорение, g": current["accel"]},
                    index=["AX", "AY", "AZ"],
                )
            )

            st.markdown("### Гироскоп")

            st.bar_chart(
                pd.DataFrame(
                    {
                        "Угловая скорость, °/с":
                            current["gyro"]
                    },
                    index=["GX", "GY", "GZ"],
                )
            )

        with right:
            st.markdown("### Магнитометр")

            st.bar_chart(
                pd.DataFrame(
                    {"Магнитное поле, Гс": current["mag"]},
                    index=["MX", "MY", "MZ"],
                )
            )

            st.markdown("### Освещённость")

            st.bar_chart(
                pd.DataFrame(
                    {"Показания": current["light"]},
                    index=["СД1", "СД2", "СД3", "СД4"],
                )
            )


# --------------------------------------------------
# ВКЛАДКА 2: ГРАФИКИ
# --------------------------------------------------

with tab_graphs:
    st.subheader("Динамика телеметрии")

    valid_records = [
        record
        for record in records
        if record.get("status") != "CORRUPTED"
        and "battery_v" in record
    ]

    if not valid_records:
        st.warning("Нет корректных пакетов для построения графиков.")

    else:
        graph_df = pd.DataFrame(
            [
                {
                    "Время, мс": record["time_ms"],
                    "АКБ, В": record["battery_v"],
                    "AX, g": record["accel"][0],
                    "AY, g": record["accel"][1],
                    "AZ, g": record["accel"][2],
                    "GX, °/с": record["gyro"][0],
                    "GY, °/с": record["gyro"][1],
                    "GZ, °/с": record["gyro"][2],
                }
                for record in valid_records
            ]
        )

        st.markdown("### Напряжение аккумулятора")

        st.line_chart(
            graph_df.set_index("Время, мс")[["АКБ, В"]]
        )

        st.markdown("### Акселерометр")

        st.line_chart(
            graph_df.set_index("Время, мс")[
                ["AX, g", "AY, g", "AZ, g"]
            ]
        )

        st.markdown("### Гироскоп")

        st.line_chart(
            graph_df.set_index("Время, мс")[
                ["GX, °/с", "GY, °/с", "GZ, °/с"]
            ]
        )

        st.markdown("### Состояния пакетов")

        status_df = pd.Series(
            statuses,
            name="Количество",
        ).value_counts()

        st.bar_chart(status_df)


# --------------------------------------------------
# ВКЛАДКА 3: СОБЫТИЯ
# --------------------------------------------------

with tab_events:
    st.subheader("Журнал диагностики")

    events = []

    for index, record in enumerate(records):
        status = record.get("status", "UNKNOWN")

        if status == "NORMAL":
            continue

        event_time = record.get("time_ms", "неизвестно")

        if status == "CORRUPTED":
            description = record.get(
                "error",
                "Повреждённый пакет",
            )
        else:
            description = "; ".join(
                record.get("warnings", [])
            )

        events.append(
            {
                "Номер пакета": index + 1,
                "Время, мс": event_time,
                "Статус": status,
                "Описание": description,
            }
        )

    if events:
        st.dataframe(
            pd.DataFrame(events),
            use_container_width=True,
            hide_index=True,
        )

        st.caption(
            f"Обнаружено событий: {len(events)}"
        )

    else:
        st.success(
            "На выбранном участке неисправностей не обнаружено."
        )


# --------------------------------------------------
# ВКЛАДКА 4: СЫРЫЕ ДАННЫЕ
# --------------------------------------------------

with tab_raw:
    st.subheader("Исходная телеметрия")

    raw_idx = st.number_input(
        "Номер пакета",
        min_value=1,
        max_value=len(records),
        value=1,
        step=1,
    )

    raw_record = records[int(raw_idx) - 1]

    st.markdown("### Исходная строка")

    st.code(
        raw_record.get("raw", ""),
        language="text",
    )

    st.markdown("### Результат парсинга")

    st.json(raw_record)

    if raw_record.get("error"):
        st.error(raw_record["error"])


# --------------------------------------------------
# ПОДВАЛ
# --------------------------------------------------

st.divider()

st.caption(
    "ЦУП «ИнтроСат» | Система анализа телеметрии. "
    "Предупреждения основаны на программных проверках "
    "и не заменяют сертифицированную систему управления КА."
)
