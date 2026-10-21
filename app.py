import json
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

import pandas as pd
import streamlit as st


# ==========================================
# MISSION CONTROL CENTER 2026
# ==========================================

st.set_page_config(
    page_title="Mission Control Center",
    page_icon="🛰️",
    layout="wide",
)

DEFAULT_URL = (
    "https://space-telemetry-emulator.onrender.com/telemetry"
)

MAX_HISTORY = 100


# ==========================================
# SESSION STATE
# ==========================================

if "history" not in st.session_state:
    st.session_state["history"] = []

if "last_error" not in st.session_state:
    st.session_state["last_error"] = ""

if "last_packet" not in st.session_state:
    st.session_state["last_packet"] = ""


# ==========================================
# HEADER
# ==========================================

st.title("🛰️ Mission Control Center")
st.caption(
    "Центр управления полётом · Хакатон 2026"
)

st.divider()


# ==========================================
# SIDEBAR
# ==========================================

with st.sidebar:
    st.header("⚙️ Настройки")

    source_url = st.text_input(
        "Источник телеметрии",
        value=DEFAULT_URL,
    )

    st.divider()

    st.metric(
        "Пакетов в истории",
        len(st.session_state["history"]),
    )

    if st.button(
        "🗑️ Очистить историю",
        use_container_width=True,
    ):
        st.session_state["history"] = []
        st.session_state["last_packet"] = ""
        st.session_state["last_error"] = ""
        st.rerun()


# ==========================================
# GET TELEMETRY
# ==========================================

st.subheader("📡 Связь со спутником")

if st.button(
    "Получить пакет телеметрии",
    type="primary",
):
    try:
        request = Request(
            source_url.strip(),
            headers={
                "User-Agent": "IntroSat-MCC/1.0",
            },
        )

        with urlopen(request, timeout=60) as response:
            body = response.read(100_000).decode(
                "utf-8-sig"
            )

        body = body.strip()

        if not body:
            raise ValueError(
                "Эмулятор вернул пустой ответ."
            )

        packets = []

        try:
            payload = json.loads(body)

            if isinstance(payload, dict):
                value = (
                    payload.get("raw")
                    or payload.get("telemetry")
                    or payload.get("data")
                )

                if value is None:
                    packets = [body]
                elif isinstance(value, list):
                    packets = [
                        ";".join(map(str, value))
                    ]
                elif isinstance(value, dict):
                    packets = [
                        json.dumps(value)
                    ]
                else:
                    packets = [str(value)]

            elif isinstance(payload, list):
                if all(
                    not isinstance(item, (list, dict))
                    for item in payload
                ):
                    packets = [
                        ";".join(map(str, payload))
                    ]
                else:
                    packets = [
                        json.dumps(item)
                        if isinstance(item, (dict, list))
                        else str(item)
                        for item in payload
                    ]

            else:
                packets = [str(payload)]

        except json.JSONDecodeError:
            packets = [
                line.strip()
                for line in body.splitlines()
                if line.strip()
            ]

        if not packets:
            raise ValueError(
                "В ответе не найдено ни одного пакета."
            )

        for raw in packets:
            st.session_state["history"].append(
                {
                    "number": (
                        len(st.session_state["history"]) + 1
                    ),
                    "raw": raw,
                }
            )

        st.session_state["history"] = (
            st.session_state["history"][-MAX_HISTORY:]
        )

        st.session_state["last_packet"] = packets[-1]
        st.session_state["last_error"] = ""

    except (
        HTTPError,
        URLError,
        TimeoutError,
        OSError,
        ValueError,
    ) as error:
        st.session_state["last_error"] = str(error)

    except Exception as error:
        st.session_state["last_error"] = (
            f"{type(error).__name__}: {error}"
        )


# ==========================================
# CONNECTION STATUS
# ==========================================

if st.session_state["last_error"]:
    st.error(
        "❌ Ошибка получения телеметрии: "
        + st.session_state["last_error"]
    )

elif st.session_state["history"]:
    st.success("🟢 Телеметрия получена")

else:
    st.info(
        "Ожидание данных. Нажми кнопку "
        "«Получить пакет телеметрии»."
    )


st.divider()


# ==========================================
# PARSE TELEMETRY
# ==========================================

def parse_values(raw):
    """
    Разбирает JSON или пакет с разделителем ;
    Возвращает распознанные числовые параметры.
    """

    result = {}

    try:
        obj = json.loads(raw)

        if isinstance(obj, dict):
            for key, value in obj.items():
                if isinstance(value, (int, float)):
                    result[str(key)] = float(value)

            if result:
                return result

        if isinstance(obj, list):
            raw_values = obj
        else:
            raw_values = []

    except (json.JSONDecodeError, TypeError):
        raw_values = raw.split(";")

    if not raw_values:
        raw_values = raw.split(";")

    for index, value in enumerate(raw_values):
        try:
            number = float(str(value).strip())

            result[f"Параметр {index + 1}"] = number

        except (ValueError, TypeError):
            continue

    return result


# ==========================================
# SATELLITE STATUS
# ==========================================

st.header("🛰️ Состояние спутника")

history = st.session_state["history"]

if not history:
    st.warning(
        "Данных пока нет. Получи первый пакет телеметрии."
    )

else:
    latest = history[-1]
    latest_values = parse_values(latest["raw"])

    st.caption(
        f"Последний пакет №{latest['number']}"
    )

    if latest_values:
        columns = st.columns(
            min(len(latest_values), 4)
        )

        for index, (name, value) in enumerate(
            latest_values.items()
        ):
            with columns[index % len(columns)]:
                st.metric(
                    label=name,
                    value=f"{value:.3f}",
                )

    else:
        st.info(
            "Пакет получен. Числовые параметры "
            "не удалось определить автоматически."
        )

    st.divider()


# ==========================================
# TELEMETRY HISTORY
# ==========================================

st.header("📋 История телеметрии")

if history:
    table = pd.DataFrame(
        [
            {
                "Номер пакета": item["number"],
                "Телеметрия": item["raw"],
            }
            for item in reversed(history[-10:])
        ]
    )

    st.dataframe(
        table,
        use_container_width=True,
        hide_index=True,
    )

else:
    st.write("История появится после получения данных.")


st.divider()


# ==========================================
# TELEMETRY GRAPHS
# ==========================================

st.header("📈 Графики телеметрии")

if history:
    rows = []

    for item in history:
        values = parse_values(item["raw"])

        row = {
            "Номер пакета": item["number"],
        }

        row.update(values)
        rows.append(row)

    dataframe = pd.DataFrame(rows)

    numeric_columns = [
        column
        for column in dataframe.columns
        if column != "Номер пакета"
    ]

    if numeric_columns:
        selected_parameter = st.selectbox(
            "Выбери параметр",
            numeric_columns,
        )

        chart_data = dataframe[
            ["Номер пакета", selected_parameter]
        ].dropna()

        if not chart_data.empty:
            st.line_chart(
                chart_data.set_index("Номер пакета")
            )

        else:
            st.info(
                "Нет числовых значений для графика."
            )

    else:
        st.info(
            "Для построения графика нужны числовые данные."
        )

else:
    st.info(
        "Получи несколько пакетов, чтобы построить график."
    )


st.divider()


# ==========================================
# RAW TELEMETRY
# ==========================================

st.header("🔍 Исходные данные")

if history:
    with st.expander("Посмотреть последний пакет"):
        st.code(
            history[-1]["raw"],
            language="text",
        )


# ==========================================
# FOOTER
# ==========================================

st.divider()

st.caption(
    "Mission Control Center · Space Hackathon 2026"
)
