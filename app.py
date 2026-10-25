import html
import re
import time
import urllib.error
import urllib.request
from collections import deque

import pandas as pd
import streamlit as st

from telemetry_core import (FAULT, MODULES, OK, WARN, SequenceChecker, analyze,
                       load_local_dataset, parse_telemetry_line)

st.set_page_config(page_title="ЦУП ИнтроСат", page_icon="🛰️", layout="wide")

EMULATOR_URL = "https://space-telemetry-emulator.onrender.com/telemetry"
STALL_SEC = 3.0   # столько секунд без новых пакетов = «передача прервана»
HEADER = "ПРЕАМБУЛА;ВРЕМЯ ОТ ЗАПУСКА МК;АКБ;СД1;СД2;СД3;СД4;AX;AY;AZ;GX;GY;GZ;MX;MY;MZ;МАХОВИК;ПОСТАМБУЛА"

GREEN, YELLOW, RED, GREY = "#1f9d55", "#e6a700", "#d62839", "#56606b"
LEVEL_UI = {OK: (GREEN, "НОРМА"), WARN: (YELLOW, "ВНИМАНИЕ"), FAULT: (RED, "АВАРИЯ")}

st.markdown("""<style>
.tile{border-radius:12px;padding:12px 14px;color:#fff;min-height:118px;margin-bottom:10px}
.tile .nm{font-size:1.1rem;font-weight:600}
.tile .lv{font-size:1.7rem;font-weight:800;line-height:1.25}
.tile .sub{font-size:.95rem;word-break:break-word}
.blink{animation:bl 1s steps(2,start) infinite}
@keyframes bl{50%{opacity:.5}}
@media (prefers-reduced-motion:reduce){.blink{animation:none}}
</style>""", unsafe_allow_html=True)

st.title("🛰️ Центр Управления Полётами КА «ИнтроСат»")
st.markdown("**Мониторинг и анализ космической телеметрии**")


# ---------------------------------------------------------------- отображение
def tile_html(name, level, sub, nodata=False):
    color, label = (GREY, "НЕТ ДАННЫХ") if nodata else LEVEL_UI[level]
    cls = "tile blink" if (level == FAULT and not nodata) else "tile"
    return (f'<div class="{cls}" style="background:{color}"><div class="nm">{html.escape(name)}</div>'
            f'<div class="lv">{label}</div><div class="sub">{html.escape(sub)}</div></div>')


def render_tiles(rec, link_level, link_text):
    """Плитки статусов модулей: зелёный — норма, жёлтый — внимание, красный — авария."""
    valid = rec is not None and rec.get("modules") is not None
    values = {}
    if valid:
        a = rec["accel"]
        values = {"mcu": f"t = {rec['time_ms']}", "power": f"{rec['battery_v']:.2f} В",
                  "imu": f"|a| = {(a[0] ** 2 + a[1] ** 2 + a[2] ** 2) ** 0.5:.2f} g",
                  "light": " · ".join(str(v) for v in rec["light"]), "wheel": f"{rec['flywheel']:+d}"}
    cards = []
    for key, name in MODULES.items():
        if not valid:
            cards.append(tile_html(name, OK, "", nodata=True))
            continue
        msgs = [t for m, _, t in rec["issues"] if m == key]
        text = (msgs[0] + (f" (+{len(msgs) - 1})" if len(msgs) > 1 else "")) if msgs else values.get(key, "")
        cards.append(tile_html(name, rec["modules"][key], text))
    cards.append(tile_html("Канал связи", link_level, link_text, nodata=rec is None))
    for row in range(0, len(cards), 3):
        for col, card in zip(st.columns(3), cards[row:row + 3]):
            col.markdown(card, unsafe_allow_html=True)


def render_frame(current):
    """Метрики одного пакета: гироскоп, магнитометр, свет."""
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Статус пакета", current.get("status", "UNKNOWN"))
    col2.metric("Напряжение АКБ", f"{current.get('battery_v', 0):.2f} В")
    col3.metric("Маховик", f"{current.get('flywheel', 0)}")
    col4.metric("Ускорение Z", f"{current.get('accel', [0, 0, 0])[2]:.2f} g")
    if current.get("error"):
        st.error(f"❌ Повреждённый пакет: {current['error']}")
    if current.get("warnings"):
        st.warning("⚠️ " + "; ".join(current["warnings"]))
    col_g, col_m = st.columns(2)
    with col_g:
        st.subheader("Гироскоп (°/с)")
        g = current.get("gyro", [0, 0, 0])
        st.write(f"GX: {g[0]:.2f} | GY: {g[1]:.2f} | GZ: {g[2]:.2f}")
    with col_m:
        st.subheader("Магнитометр (Гс)")
        m = current.get("mag", [0, 0, 0])
        st.write(f"MX: {m[0]:.2f} | MY: {m[1]:.2f} | MZ: {m[2]:.2f}")
    st.subheader("Освещенность (СД1 - СД4)")
    st.bar_chart(pd.DataFrame({"Уровень": current.get("light", [0, 0, 0, 0])},
                              index=["СД1", "СД2", "СД3", "СД4"]))


def render_charts(recs):
    """Графики всех параметров по списку неповреждённых пакетов."""
    if not recs:
        st.info("Пока нет данных для графиков.")
        return
    st.markdown("**Напряжение АКБ (В)**")
    st.line_chart(pd.DataFrame({"АКБ (В)": [r["battery_v"] for r in recs]}))
    st.markdown("**Освещённость**")
    st.line_chart(pd.DataFrame({f"СД{i + 1}": [r["light"][i] for r in recs] for i in range(4)}))
    for title, key, names in (("Акселерометр (g)", "accel", "AX AY AZ"), ("Гироскоп (°/с)", "gyro", "GX GY GZ"),
                              ("Магнитометр (Гс)", "mag", "MX MY MZ")):
        st.markdown(f"**{title}**")
        st.line_chart(pd.DataFrame({nm: [r[key][i] for r in recs] for i, nm in enumerate(names.split())}))
    st.markdown("**Маховик**")
    st.line_chart(pd.DataFrame({"Маховик": [r["flywheel"] for r in recs]}))


def link_state(statuses):
    """Качество канала по доле повреждённых пакетов среди последних."""
    if not statuses:
        return OK, ""
    bad = sum(1 for s in statuses if s == "CORRUPTED")
    pct = 100 * bad / len(statuses)
    level = FAULT if pct > 30 else (WARN if bad else OK)
    return level, f"повреждено {bad} из последних {len(statuses)}"


# ---------------------------------------------------------------- режим «Эмулятор»
def fetch_frame(url):
    """Один запрос к эмулятору. Возвращает (строка | None, текст ошибки | None)."""
    try:
        with urllib.request.urlopen(url, timeout=4) as response:
            if response.status == 204:
                return None, None
            return response.read().decode("utf-8", errors="replace"), None
    except Exception as e:  # нет связи, таймаут, неверный адрес — приложение не падает
        return None, str(getattr(e, "reason", None) or e)


def init_live_state():
    s = st.session_state
    defaults = {"lv_raw": None, "lv_rx": None, "lv_rec": None, "lv_valid": None,
                "lv_hist": deque(maxlen=600), "lv_status": deque(maxlen=30), "lv_counts": {},
                "lv_events": [], "lv_sig": None, "lv_checker": SequenceChecker(), "lv_seq": 0,
                "lv_log": deque(maxlen=50000), "cmds": [], "export": None}
    for key, default in defaults.items():
        s.setdefault(key, default)


@st.fragment(run_every=1.0)
def live_view(url):
    init_live_state()
    s = st.session_state
    error = None
    for _ in range(8):                      # несколько запросов за секунду, чтобы не терять пакеты
        raw, error = fetch_frame(url)
        if error:
            break
        if raw and raw.strip() and raw != s.lv_raw:
            s.lv_raw, s.lv_rx, s.lv_seq = raw, time.time(), s.lv_seq + 1
            s.lv_log.append(raw.strip())   # журнал принятых пакетов (как есть)
            rec = parse_telemetry_line(raw)
            if rec["status"] != "CORRUPTED":
                s.lv_checker.apply(rec)
                rec.pop("n", None)
                s.lv_valid = rec
                s.lv_hist.append(rec)
            s.lv_rec = rec
            s.lv_status.append(rec["status"])
            s.lv_counts[rec["status"]] = s.lv_counts.get(rec["status"], 0) + 1
            if rec["status"] != "NORMAL":   # событие — когда меняется набор проблем
                texts = [t for _, _, t in rec["issues"]]
                sig = tuple(re.sub(r"-?\d+(\.\d+)?", "#", t) for t in texts)
                if sig != s.lv_sig:
                    s.lv_events.insert(0, {"время": time.strftime("%H:%M:%S"), "пакет": s.lv_seq,
                                           "статус": rec["status"], "событие": "; ".join(texts)})
                    del s.lv_events[200:]
                s.lv_sig = sig
            else:
                s.lv_sig = None
        time.sleep(0.1)

    age = None if s.lv_rx is None else time.time() - s.lv_rx
    if error:
        st.error(f"🔴 НЕТ СВЯЗИ С ЭМУЛЯТОРОМ: {error}. Приложение повторяет попытку каждую секунду.")
    elif age is None:
        st.warning("🟡 Эмулятор доступен, но пакетов пока нет")
    elif age > STALL_SEC:
        st.error(f"🔴 ПЕРЕДАЧА ПРЕРВАНА: новых пакетов нет {age:.0f} с. Данные ниже устарели.")
    else:
        st.success("🟢 Связь есть")
    c = s.lv_counts
    st.caption("Принято пакетов: " + str(sum(c.values())) + " · " +
               " · ".join(f"{k}: {v}" for k, v in sorted(c.items())))

    link_level, link_text = link_state(list(s.lv_status))
    tab_panel, tab_charts, tab_events = st.tabs(["🎛️ Пульт", "📈 Графики", "📋 События"])
    with tab_panel:
        render_tiles(s.lv_valid, link_level, link_text)
        if s.lv_rec is not None and s.lv_rec["status"] == "CORRUPTED":
            st.error(f"❌ Последний пакет повреждён: {s.lv_rec['error']}")
        if s.lv_valid is not None:
            render_frame(s.lv_valid)
        elif not error:
            st.info("Данных пока нет.")
    with tab_charts:
        render_charts(list(s.lv_hist))
    with tab_events:
        if s.lv_events:
            st.dataframe(pd.DataFrame(s.lv_events), hide_index=True)
        else:
            st.success("Событий нет — все системы в норме.")


# ---------------------------------------------------------------- режим «Файл»
@st.cache_resource(show_spinner="Разбор лога…")
def parse_text(text):
    lines = [x.strip() for x in text.splitlines() if x.strip()][1:]   # первая строка — заголовок
    if not lines:
        return None
    return analyze([parse_telemetry_line(x) for x in lines])


@st.cache_resource(show_spinner="Разбор файла…")
def parse_file(filename):
    df = load_local_dataset(filename)
    if df is None or df.empty:
        return None
    return analyze([parse_telemetry_line(str(x)) for x in df.iloc[:, 0]])


def file_view():
    dataset_options = {
        "01_normal_flight.csv (Штатный полет)": "01_normal_flight.csv",
        "02_sensor_faults.csv (Сбои датчиков)": "02_sensor_faults.csv",
        "03_radio_noise.csv (Радиошум)": "03_radio_noise.csv",
    }
    uploaded = st.sidebar.file_uploader("📥 Загрузить сохранённый лог (CSV)", type=["csv", "txt"])
    selected_key = st.sidebar.selectbox("📂 …или выберите датасет", list(dataset_options.keys()))
    if uploaded is not None:
        filename = uploaded.name
        records = parse_text(uploaded.getvalue().decode("utf-8-sig", errors="replace"))
    else:
        filename = dataset_options[selected_key]
        records = parse_file(filename)
    if not records:
        st.error(f"Не удалось загрузить {filename}. Проверьте файл (для датасетов — папку data/).")
        return
    st.sidebar.success(f"Загружено записей: {len(records)}")

    tab_panel, tab_charts, tab_events, tab_raw = st.tabs(
        ["🎛️ Пульт", "📈 Графики", "📋 События", "💻 Сырые данные"])
    with tab_panel:
        idx = st.slider("Выбор кадра телеметрии", 0, len(records) - 1, 0)
        rec = records[idx]
        window = records[max(0, idx - 15): idx + 16]
        link_level, link_text = link_state([r["status"] for r in window])
        render_tiles(rec if rec["status"] != "CORRUPTED" else None, link_level, link_text)
        render_frame(rec)
    with tab_charts:
        st.caption("Графики — последние 600 пакетов до выбранного кадра.")
        render_charts([r for r in records[max(0, idx - 600): idx + 1] if r["status"] != "CORRUPTED"])
    with tab_events:
        counts = pd.Series([r["status"] for r in records]).value_counts()
        st.markdown("**Состояние пакетов во всём файле:**")
        st.bar_chart(counts)
        bad = [{"пакет": i, "статус": r["status"],
                "событие": r.get("error") or "; ".join(r["warnings"])}
               for i, r in enumerate(records) if r["status"] != "NORMAL"]
        st.markdown(f"**Аномалии: {len(bad)} из {len(records)} пакетов** (показаны первые 500)")
        if bad:
            st.dataframe(pd.DataFrame(bad[:500]), hide_index=True)
        else:
            st.success("Аномалий нет.")
    with tab_raw:
        r_idx = st.number_input("Номер строки", 0, len(records) - 1, 0)
        st.code(str(records[r_idx].get("raw", "")))
        st.json({k: v for k, v in records[r_idx].items() if k != "raw"})


# ---------------------------------------------------------------- команды и лог
COMMANDS = {"Запрос статуса": "GET_STATUS", "Остановить маховик": "WHEEL_STOP", "Запустить маховик": "WHEEL_START"}


def send_command(base_url, code):
    """POST /command на источник. Возвращает (доставлено, ответ)."""
    try:
        req = urllib.request.Request(base_url + "/command", data=code.encode("utf-8"), method="POST",
                                     headers={"Content-Type": "text/plain; charset=utf-8"})
        with urllib.request.urlopen(req, timeout=8) as response:
            return True, response.read().decode("utf-8", errors="replace").strip()[:200]
    except urllib.error.HTTPError as e:
        if e.code in (404, 501):
            return False, "Источник не принимает команды (обновите telemetry_server.py на Render)"
        return False, f"Ошибка {e.code}"
    except Exception as e:
        return False, f"Нет связи: {getattr(e, 'reason', None) or e}"


def commands_tab(url):
    init_live_state()
    s = st.session_state
    base = url.split("/telemetry")[0].rstrip("/")
    st.caption("Команды отправляются на сервер телеметрии. Результат виден в телеметрии: "
               "после «Остановить маховик» значение маховика становится 0.")
    for label, code in COMMANDS.items():
        if st.button(label, key="cmd_" + code):
            ok, reply = send_command(base, code)
            s.cmds.insert(0, {"время": time.strftime("%H:%M:%S"), "команда": label, "код": code,
                              "результат": "✅ доставлена" if ok else "❌ не доставлена", "ответ": reply})
            (st.success if ok else st.error)(f"{label}: {reply}")
    if s.cmds:
        st.markdown("**Журнал команд**")
        st.dataframe(pd.DataFrame(s.cmds), hide_index=True)


def build_report(lines):
    """Разобранный отчёт (CSV) по списку сырых строк."""
    rows = []
    for i, r in enumerate(analyze([parse_telemetry_line(x) for x in lines]), 1):
        if r["status"] == "CORRUPTED":
            rows.append({"пакет": i, "статус": "CORRUPTED", "события": r["error"], "сырая строка": r["raw"]})
            continue
        row = {"пакет": i, "время": r["time_ms"], "статус": r["status"], "АКБ, В": r["battery_v"]}
        row.update({f"СД{k + 1}": v for k, v in enumerate(r["light"])})
        for key, names in (("accel", ("AX", "AY", "AZ")), ("gyro", ("GX", "GY", "GZ")), ("mag", ("MX", "MY", "MZ"))):
            row.update({nm: round(r[key][k], 4) for k, nm in enumerate(names)})
        row["МАХОВИК"] = r["flywheel"]
        row["события"] = "; ".join(r["warnings"])
        rows.append(row)
    return pd.DataFrame(rows).to_csv(index=False).encode("utf-8-sig")


def log_tab():
    init_live_state()
    s = st.session_state
    st.write(f"В журнале: **{len(s.lv_log)}** принятых пакетов и **{len(s.cmds)}** команд.")
    st.caption("Сохранённый лог можно открыть позже: режим «Файл» → «Загрузить сохранённый лог».")
    if st.button("Подготовить файлы для скачивания"):
        lines = list(s.lv_log)
        s.export = {"raw": (HEADER + "\n" + "\n".join(lines) + "\n").encode("utf-8"),
                    "report": build_report(lines),
                    "cmds": pd.DataFrame(s.cmds).to_csv(index=False).encode("utf-8-sig") if s.cmds else b""}
        st.success("Файлы готовы — скачайте ниже.")
    if s.export:
        st.download_button("⬇️ Сырой лог телеметрии (CSV)", s.export["raw"], "telemetry_log.csv", "text/csv")
        st.download_button("⬇️ Разобранный отчёт (CSV)", s.export["report"], "telemetry_report.csv", "text/csv")
        if s.export["cmds"]:
            st.download_button("⬇️ Журнал команд (CSV)", s.export["cmds"], "commands_log.csv", "text/csv")


# ---------------------------------------------------------------- выбор режима
mode = st.sidebar.radio("Источник данных", ["Файл (датасет)", "Эмулятор (live)"])
if mode == "Эмулятор (live)":
    url = st.sidebar.text_input("Адрес эмулятора", EMULATOR_URL)
    st.sidebar.caption("Другой сценарий: добавьте в адрес ?file=02_sensor_faults.csv "
                       "или ?file=03_radio_noise.csv")
    t_tel, t_cmd, t_log = st.tabs(["📡 Телеметрия", "🕹️ Команды", "💾 Лог"])
    with t_tel:
        live_view(url)
    with t_cmd:
        commands_tab(url)
    with t_log:
        log_tab()
else:
    file_view()
