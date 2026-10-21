import json
import time
from datetime import datetime, timezone
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

# ============================================================
# MISSION CONTROL CENTER — IntroSat / Хакатон-ЦУП 2026
# ============================================================

st.set_page_config(
    page_title="Mission Control Center | IntroSat",
    page_icon="🛰️",
    layout="wide",
    initial_sidebar_state="expanded",
)

DEFAULT_URL = "https://space-telemetry-emulator.onrender.com/telemetry"
MAX_HISTORY = 300

FIELD_NAMES = [
    "Преамбула", "Время, мс", "АКБ, мВ",
    "СД1", "СД2", "СД3", "СД4",
    "AX", "AY", "AZ", "GX", "GY", "GZ",
    "MX", "MY", "MZ", "Маховик", "Постамбула",
]

st.markdown("""
<style>
    .stApp { background: radial-gradient(ellipse at top, #10213b 0%, #07111f 48%, #050a13 100%); }
    [data-testid="stSidebar"] { background: #0a1525; }
    h1, h2, h3 { color: #e7f2ff; }
    .hero { padding: 18px 22px; border: 1px solid #29425f; border-radius: 16px;
            background: linear-gradient(120deg, #102642, #0b1728); margin-bottom: 14px; }
    .hero-title { font-size: 28px; font-weight: 800; letter-spacing: 1px; color: #eaf5ff; }
    .hero-sub { color: #91abc8; font-size: 13px; margin-top: 5px; }
    .small-label { color: #93acc7; text-transform: uppercase; font-size: 11px; letter-spacing: 1.4px; }
    .status-pill { display:inline-block; padding:5px 10px; border-radius:999px; font-weight:700; font-size:12px; }
    .ok { background:#123c34; color:#7fffd4; border:1px solid #24765f; }
    .warn { background:#473416; color:#ffd479; border:1px solid #91661e; }
    .bad { background:#4a202b; color:#ff9cab; border:1px solid #983d50; }
    div[data-testid="stMetric"] { background:#0d1c2e; border:1px solid #203a56; padding:12px 14px; border-radius:12px; }
    div[data-testid="stMetricLabel"] { color:#9bb4d0; }
    .note { color:#9bb4d0; font-size:12px; }
</style>
""", unsafe_allow_html=True)

# ----------------------- state -----------------------
defaults = {
    "history": [],
    "events": [],
    "last_error": "",
    "last_fetch": None,
    "source_url": DEFAULT_URL,
    "last_status": None,
}
for key, value in defaults.items():
    if key not in st.session_state:
        st.session_state[key] = value


def add_event(level, message):
    st.session_state["events"].append({
        "Время": datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S"),
        "Уровень": level,
        "Событие": message,
    })
    st.session_state["events"] = st.session_state["events"][-300:]


def parse_packet(raw):
    """Parse the documented IntroSat semicolon packet into physical units."""
    parts = [p.strip() for p in str(raw).strip().split(";")]
    if len(parts) < 17:
        return {
            "valid": False,
            "status": "CORRUPTED",
            "error": f"Неполный пакет: найдено {len(parts)} полей, ожидалось минимум 17",
            "raw": raw,
        }
    try:
        preamble = parts[0]
        elapsed_ms = int(parts[1])
        battery_v = int(parts[2]) / 1000.0
        light = [int(x) for x in parts[3:7]]
        accel = [int(x) / 16384.0 for x in parts[7:10]]
        gyro = [int(x) / 131.072 for x in parts[10:13]]
        mag = [int(x) / 1711.6 for x in parts[13:16]]
        wheel = int(parts[16])
        warnings = []
        status = "NORMAL"

        if not preamble:
            warnings.append("Пустая преамбула пакета")
        if elapsed_ms < 0:
            warnings.append("Отрицательное время работы")
                    if not 3.2 <= battery_v <= 4.3:
            warnings.append(f"Напряжение АКБ вне контрольного диапазона: {battery_v:.3f} В")
        if any(v < 0 or v > 65535 for v in light):
            warnings.append("Показания датчика света вне диапазона 0–65535")
        if any(abs(v) > 2.0 for v in accel):
            warnings.append("Акселерометр за пределами ±2 g")
        if any(abs(v) > 250 for v in gyro):
            warnings.append("Гироскоп за пределами ±250 °/с")
        if any(abs(v) > 16 for v in mag):
            warnings.append("Магнитометр за пределами ±16 Гс")

        if len(parts) >= 18 and not parts[17]:
            warnings.append("Пустая постамбула пакета")
        if warnings:
            status = "WARNING"
        return {
            "valid": True, "status": status, "error": "",
            "preamble": preamble, "elapsed_ms": elapsed_ms,
            "battery_v": battery_v, "light": light, "accel": accel,
            "gyro": gyro, "mag": mag, "wheel": wheel,
            "warnings": warnings, "raw": raw,
        }
    except (ValueError, IndexError) as exc:
        return {"valid": False, "status": "CORRUPTED",
                "error": f"Не удалось разобрать пакет: {exc}", "raw": raw}


def fetch_packet(url):
    request = Request(url.strip(), headers={"User-Agent": "IntroSat-MCC/2.0"})
    with urlopen(request, timeout=65) as response:
        if response.status == 204:
            return ""
        return response.read(100_000).decode("utf-8-sig").strip()


def store_packet(raw):
    parsed = parse_packet(raw)
    number = (st.session_state["history"][-1]["number"] + 1
              if st.session_state["history"] else 1)
    item = {
        "number": number,
        "received_at": datetime.now().astimezone().strftime("%H:%M:%S"),
        "raw": raw,
        "parsed": parsed,
    }
    st.session_state["history"].append(item)
    st.session_state["history"] = st.session_state["history"][-MAX_HISTORY:]
    st.session_state["last_fetch"] = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S")

    if not parsed["valid"]:
        add_event("КРИТИЧНО", f"Пакет №{number} повреждён: {parsed['error']}")
    elif parsed["warnings"]:
        add_event("ПРЕДУПРЕЖДЕНИЕ", f"Пакет №{number}: " + "; ".join(parsed["warnings"]))
    else:
        if st.session_state["last_status"] != "NORMAL":
            add_event("ИНФО", f"Получен корректный пакет телеметрии №{number}")
    st.session_state["last_status"] = parsed["status"]
    # ----------------------- sidebar -----------------------
with st.sidebar:
    st.markdown("## ⚙️ Панель оператора")
    st.session_state["source_url"] = st.text_input(
        "HTTP-источник телеметрии",
        value=st.session_state["source_url"],
        help="Адрес эмулятора или другого совместимого HTTP-источника.",
    )

    if st.button(
        "📡 Получить пакет",
        type="primary",
        use_container_width=True,
    ):
        try:
            body = fetch_packet(st.session_state["source_url"])

            if not body:
                raise ValueError("Источник вернул пустой ответ.")

            lines = [
                line.strip()
                for line in body.splitlines()
                if line.strip()
            ]

            for line in lines:
                store_packet(line)

            st.session_state["last_error"] = ""
            add_event(
                "СВЯЗЬ",
                f"Успешно получено пакетов: {len(lines)}",
            )

        except (
            HTTPError,
            URLError,
            TimeoutError,
            OSError,
            ValueError,
        ) as exc:
            st.session_state["last_error"] = str(exc)
            add_event("ОШИБКА СВЯЗИ", str(exc))

        except Exception as exc:
            st.session_state["last_error"] = (
                f"{type(exc).__name__}: {exc}"
            )
            add_event("ОШИБКА", st.session_state["last_error"])

    if st.button("🗑️ Очистить историю", use_container_width=True):
        st.session_state["history"] = []
        st.session_state["events"] = []
        st.session_state["last_status"] = None
        st.session_state["last_error"] = ""
        add_event("ИНФО", "История очищена оператором")
        st.rerun()

    st.divider()
    st.markdown("**Подсказка**")
    st.caption(
        "Нажимай «Получить пакет» несколько раз, "
        "чтобы наполнить историю и построить графики."
    )
    st.caption(
        "Бесплатный Render может просыпаться после простоя. "
        "Первый запрос иногда занимает до минуты."
    )


# ----------------------- header -----------------------
st.markdown("""
<div class="hero">
  <div class="small-label">INTRO-SAT / FLIGHT OPERATIONS</div>
  <div class="hero-title">🛰️ MISSION CONTROL CENTER</div>
  <div class="hero-sub">
    Центр управления полётом · Хакатон-ЦУП 2026 ·
    Панель телеметрии и диагностики
  </div>
</div>
""", unsafe_allow_html=True)

history = st.session_state["history"]
latest = history[-1] if history else None
parsed = latest["parsed"] if latest else None

status = parsed["status"] if parsed else "WAITING"

status_text = {
    "NORMAL": "НОРМА",
    "WARNING": "ВНИМАНИЕ",
    "CORRUPTED": "ПОВРЕЖДЁННЫЙ ПАКЕТ",
    "WAITING": "ОЖИДАНИЕ",
}.get(status, status)

if st.session_state["last_error"]:
    st.error(
        "Ошибка связи: " + st.session_state["last_error"]
    )
elif status == "NORMAL":
    st.success(
        "Связь установлена · Последний пакет разобран "
        "без обнаруженных отклонений"
    )
elif status == "WARNING":
    st.warning(
        "Телеметрия получена, но обнаружены значения, "
        "требующие внимания"
    )
elif status == "CORRUPTED":
    st.error(
        "Получен пакет с ошибками структуры "
        "или преобразования"
    )
else:
    st.info(
        "Ожидание первого пакета. Нажми "
        "«Получить пакет» в панели слева."
    )


# ----------------------- KPIs -----------------------
k1, k2, k3, k4, k5 = st.columns(5)

k1.metric("Пакетов в истории", len(history))
k2.metric("Статус спутника", status_text)

k3.metric(
    "АКБ",
    f"{parsed['battery_v']:.3f} В"
    if parsed and parsed.get("valid")
    else "—",
)

k4.metric(
    "Время работы",
    f"{parsed['elapsed_ms'] / 1000:.1f} с"
    if parsed and parsed.get("valid")
    else "—",
)

k5.metric(
    "Последний приём",
    latest["received_at"] if latest else "—",
)

st.divider()


# ----------------------- main tabs -----------------------
tab_overview, tab_telemetry, tab_3d, tab_events, tab_raw = st.tabs([
    "🛰️ Обзор миссии",
    "📈 Телеметрия",
    "🌍 3D-модель",
    "📋 Журнал событий",
    "🔍 Пакеты",
])
# ----------------------- mission overview -----------------------
with tab_overview:
    left, right = st.columns([1.2, 1])

    with left:
        st.subheader("Состояние бортовых подсистем")

        if parsed and parsed.get("valid"):
            subsystem_rows = [
                {
                    "Подсистема": "Электропитание",
                    "Показатель": f"{parsed['battery_v']:.3f} В",
                    "Состояние": (
                        "ВНИМАНИЕ"
                        if not 3.2 <= parsed["battery_v"] <= 4.3
                        else "НОРМА"
                    ),
                },
                {
                    "Подсистема": "Датчики освещённости",
                    "Показатель": " · ".join(
                        map(str, parsed["light"])
                    ),
                    "Состояние": "НОРМА",
                },
                {
                    "Подсистема": "Акселерометр",
                    "Показатель": ", ".join(
                        f"{v:.3f} g" for v in parsed["accel"]
                    ),
                    "Состояние": (
                        "НОРМА"
                        if all(abs(v) <= 2 for v in parsed["accel"])
                        else "ВНИМАНИЕ"
                    ),
                },
                {
                    "Подсистема": "Гироскоп",
                    "Показатель": ", ".join(
                        f"{v:.2f} °/с" for v in parsed["gyro"]
                    ),
                    "Состояние": (
                        "НОРМА"
                        if all(abs(v) <= 250 for v in parsed["gyro"])
                        else "ВНИМАНИЕ"
                    ),
                },
                {
                    "Подсистема": "Магнитометр",
                    "Показатель": ", ".join(
                        f"{v:.3f} Гс" for v in parsed["mag"]
                    ),
                    "Состояние": (
                        "НОРМА"
                        if all(abs(v) <= 16 for v in parsed["mag"])
                        else "ВНИМАНИЕ"
                    ),
                },
                {
                    "Подсистема": "Маховик",
                    "Показатель": (
                        f"{parsed['wheel']} (исходные единицы)"
                    ),
                    "Состояние": "ДАННЫЕ ПОЛУЧЕНЫ",
                },
            ]

            st.dataframe(
                pd.DataFrame(subsystem_rows),
                use_container_width=True,
                hide_index=True,
            )

        else:
            st.info(
                "Таблица подсистем появится после получения "
                "корректного пакета."
            )

    with right:
        st.subheader("Диагностика")

        if parsed:
            if parsed.get("valid") and parsed["warnings"]:
                for warning in parsed["warnings"]:
                    st.warning("⚠️ " + warning)

            elif not parsed.get("valid"):
                st.error(
                    parsed.get("error", "Неизвестная ошибка пакета")
                )

            else:
                st.success("Все реализованные проверки пройдены.")

        else:
            st.info("Пока нет данных для диагностики.")

        st.markdown("**Проверки пакета**")
        st.write("• Структура: минимум 17 полей")
        st.write("• АКБ: контрольный диапазон 3,2–4,3 В")
        st.write("• Акселерометр: ±2 g")
        st.write("• Гироскоп: ±250 °/с")
        st.write("• Магнитометр: ±16 Гс")

        st.caption(
            "Контрольные диапазоны являются настройками прототипа. "
            "Их нужно сверить с техническим заданием хакатона."
        )


# ----------------------- telemetry graphs -----------------------
with tab_telemetry:
    st.subheader("История измерений")

    if history:
        rows = []

        for item in history:
            p = item["parsed"]

            if not p.get("valid"):
                continue

            rows.append({
                "Пакет": item["number"],
                "Время работы, с": p["elapsed_ms"] / 1000,
                "АКБ, В": p["battery_v"],
                "СД1": p["light"][0],
                "СД2": p["light"][1],
                "СД3": p["light"][2],
                "СД4": p["light"][3],
                "AX, g": p["accel"][0],
                "AY, g": p["accel"][1],
                "AZ, g": p["accel"][2],
                "GX, °/с": p["gyro"][0],
                "GY, °/с": p["gyro"][1],
                "GZ, °/с": p["gyro"][2],
                "MX, Гс": p["mag"][0],
                "MY, Гс": p["mag"][1],
                "MZ, Гс": p["mag"][2],
                "Маховик": p["wheel"],
            })

        if rows:
            df = pd.DataFrame(rows)

            choices = [
                column
                for column in df.columns
                if column != "Пакет"
            ]

            default_index = (
                choices.index("АКБ, В")
                if "АКБ, В" in choices
                else 0
            )

            selected = st.selectbox(
                "Параметр для графика",
                choices,
                index=default_index,
            )

            st.line_chart(
                df.set_index("Пакет")[[selected]],
                height=320,
            )

            with st.expander(
                "Показать таблицу всех числовых параметров"
            ):
                st.dataframe(
                    df.iloc[::-1],
                    use_container_width=True,
                    hide_index=True,
                )

        else:
            st.warning(
                "Пакеты получены, но ни один пока не прошёл разбор."
            )

    else:
        st.info(
            "Получи несколько пакетов, чтобы увидеть "
            "динамику показателей."
        )
        # ----------------------- 3D satellite model -----------------------
with tab_3d:
    st.subheader("🌍 Визуализация спутника")

    st.caption(
        "Схематическая анимированная 3D-модель спутника. "
        "Вращение модели декоративное и не отражает "
        "фактическую ориентацию аппарата."
    )

    components.html(
        """
        <!DOCTYPE html>
        <html>
        <head>
        <style>
            * {
                box-sizing: border-box;
            }

            body {
                margin: 0;
                background:
                    radial-gradient(
                        circle at 50% 45%,
                        #17365a,
                        #07111f 75%
                    );
                color: #dcecff;
                font-family: Arial, sans-serif;
                overflow: hidden;
            }

            .scene {
                height: 390px;
                display: flex;
                align-items: center;
                justify-content: center;
                perspective: 850px;
                position: relative;
            }

            .stars {
                position: absolute;
                inset: 0;
                background-image:
                    radial-gradient(
                        #9ecbff 1px,
                        transparent 1.5px
                    ),
                    radial-gradient(
                        #fff 1px,
                        transparent 1.5px
                    );
                background-size: 53px 61px, 89px 97px;
                background-position: 0 0, 22px 31px;
                opacity: 0.45;
            }

            .sat {
                position: relative;
                width: 210px;
                height: 150px;
                transform-style: preserve-3d;
                animation: orbit 9s linear infinite;
            }

            .body {
                position: absolute;
                width: 76px;
                height: 82px;
                left: 67px;
                top: 33px;
                border: 2px solid #b7cce0;
                background: linear-gradient(
                    135deg,
                    #dce8f5,
                    #7189a4 45%,
                    #263e5b
                );
                transform: rotateY(25deg) rotateX(10deg);
                box-shadow: 0 0 25px #4b9fff55;
            }

            .face {
                position: absolute;
                inset: 9px;
                border: 1px solid #38536f;
                background: repeating-linear-gradient(
                    0deg,
                    #1e4675 0 8px,
                    #4d82b6 9px 10px
                );
            }

            .panel {
                position: absolute;
                width: 61px;
                height: 112px;
                top: 19px;
                background: repeating-linear-gradient(
                    90deg,
                    #163a69 0 13px,
                    #6aa4dd 14px 15px,
                    #163a69 16px 28px
                );
                border: 3px solid #93b9e0;
                box-shadow: 0 0 15px #258aff44;
            }

            .left {
                left: -1px;
                transform: rotateY(28deg) rotateZ(-4deg);
            }

            .right {
                right: -1px;
                transform: rotateY(-28deg) rotateZ(4deg);
            }

            .antenna {
                position: absolute;
                width: 5px;
                height: 45px;
                background: #d5e1ee;
                left: 102px;
                top: -7px;
                transform: rotate(25deg);
            }

            .dish {
                position: absolute;
                width: 32px;
                height: 32px;
                border: 3px solid #e2edf7;
                border-radius: 50%;
                left: 88px;
                top: -18px;
                background: #a5bed7;
                box-shadow: inset 0 0 0 7px #6f8ba7;
            }

            .label {
                position: absolute;
                bottom: 17px;
                left: 0;
                right: 0;
                text-align: center;
                color: #9eb9d6;
                font-size: 12px;
                letter-spacing: 2px;
            }

            .beam {
                position: absolute;
                width: 160px;
                height: 160px;
                border: 1px solid #4a9cff55;
                border-radius: 50%;
                transform: rotateX(70deg);
                box-shadow: 0 0 30px #238bff20;
            }

            @keyframes orbit {
                0% {
                    transform:
                        rotateY(0deg)
                        rotateX(8deg)
                        rotateZ(-3deg);
                }

                50% {
                    transform:
                        rotateY(180deg)
                        rotateX(-8deg)
                        rotateZ(3deg);
                }

                100% {
                    transform:
                        rotateY(360deg)
                        rotateX(8deg)
                        rotateZ(-3deg);
                }
            }
        </style>
        </head>

        <body>
            <div class="scene">
                <div class="stars"></div>
                <div class="beam"></div>

                <div class="sat">
                    <div class="panel left"></div>
                    <div class="panel right"></div>

                    <div class="body">
                        <div class="face"></div>
                    </div>

                    <div class="antenna"></div>
                    <div class="dish"></div>
                </div>

                <div class="label">
                    INTROSAT · SCHEMATIC MODEL
                </div>
            </div>
        </body>
        </html>
        """,
        height=410,
        scrolling=False,
    )

    st.info(
        "Модель демонстрационная. Для отображения реальной "
        "ориентации спутника потребуется отдельно рассчитать "
        "её по данным датчиков."
    )
    # ----------------------- EVENT LOG -----------------------
with tab_events:
    st.subheader("📋 Журнал событий оператора")

    if st.session_state["events"]:
        event_df = pd.DataFrame(
            st.session_state["events"][::-1]
        )

        st.dataframe(
            event_df,
            use_container_width=True,
            hide_index=True,
        )

        csv = event_df.to_csv(
            index=False
        ).encode("utf-8-sig")

        st.download_button(
            "⬇️ Скачать журнал CSV",
            data=csv,
            file_name="mission_events.csv",
            mime="text/csv",
        )

    else:
        st.info(
            "Журнал пуст. События появятся при получении "
            "телеметрии или ошибках связи."
        )


# ----------------------- RAW TELEMETRY -----------------------
with tab_raw:
    st.subheader("🔍 Сырые пакеты телеметрии")

    if history:
        packet_options = [
            f"№{item['number']} · {item['received_at']}"
            for item in reversed(history)
        ]

        selected_packet = st.selectbox(
            "Выбери пакет",
            packet_options,
        )

        idx = packet_options.index(selected_packet)

        selected_item = list(reversed(history))[idx]

        st.markdown("**Исходный пакет**")

        st.code(
            selected_item["raw"],
            language="text",
        )

        packet_data = selected_item["parsed"]

        if packet_data.get("valid"):
            st.markdown("**Разобранные данные**")

            st.json({
                "status": packet_data["status"],
                "time_ms": packet_data["elapsed_ms"],
                "battery_v": packet_data["battery_v"],
                "light": packet_data["light"],
                "accel_g": packet_data["accel"],
                "gyro_deg_s": packet_data["gyro"],
                "mag_gauss": packet_data["mag"],
                "flywheel_raw": packet_data["wheel"],
                "warnings": packet_data["warnings"],
            })

        else:
            st.error(
                packet_data.get(
                    "error",
                    "Не удалось разобрать пакет",
                )
            )

    else:
        st.info(
            "Здесь появятся исходные пакеты после получения "
            "телеметрии."
        )


# ----------------------- FOOTER -----------------------
st.divider()

st.caption(
    "Mission Control Center · IntroSat · "
    "Space Hackathon 2026"
)
st.divider()

st.caption(
    "Mission Control Center · IntroSat · "
    "Space Hackathon 2026"
)
