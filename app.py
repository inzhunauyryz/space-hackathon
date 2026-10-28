import html
import math
import re
import time
import urllib.error
import urllib.request
from collections import deque

import numpy as np
import pandas as pd
import streamlit as st

try:
    import plotly.graph_objects as go
except ImportError:   # 3D-вид необязателен: без plotly приложение работает как раньше
    go = None

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


def num(x, spec=".2f", suffix=""):
    """Число для отображения; если данных нет (датчик отключён) — «н/д»."""
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return "н/д"
    return format(x, spec) + suffix


def render_tiles(rec, link_level, link_text):
    """Плитки статусов модулей: зелёный — норма, жёлтый — внимание, красный — авария."""
    valid = rec is not None and rec.get("modules") is not None
    values = {}
    if valid:
        a = rec["accel"]
        mod = (a[0] ** 2 + a[1] ** 2 + a[2] ** 2) ** 0.5   # NaN, если нет данных
        values = {"mcu": "t = " + ("н/д" if rec["time_ms"] is None else str(rec["time_ms"])),
                  "power": num(rec["battery_v"], ".2f", " В"), "imu": "|a| = " + num(mod, ".2f", " g"),
                  "light": " · ".join(num(v, ".0f") for v in rec["light"]), "wheel": num(rec["flywheel"], "+.0f")}
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


ADVICE = {
    "power": "Проверьте заряд АКБ, при необходимости переведите КА в режим энергосбережения.",
    "light": "Проверьте подключение датчиков света; ориентация по Солнцу может быть неточной.",
    "imu": "Проверьте датчик положения; данные об ориентации могут быть недостоверны.",
    "wheel": "Проверьте плату маховика и его управление.",
    "mcu": "Проверьте микроконтроллер: возможен сбой времени или перезапуск.",
    "link": "Проверьте канал связи: возможны помехи, часть пакетов повреждена.",
}


def render_summary(rec, link_level):
    """Общее состояние КА одной строкой + рекомендации оператору."""
    if rec is None or rec.get("modules") is None:
        st.markdown(tile_html("Общее состояние КА", OK, "Ожидание данных", nodata=True), unsafe_allow_html=True)
        return
    levels = dict(rec["modules"], link=link_level)
    worst = max(levels.values())
    text = {OK: "Все системы в норме", WARN: "Есть отклонения — требуется внимание оператора",
            FAULT: "Авария — требуется вмешательство оператора"}[worst]
    st.markdown(tile_html("Общее состояние КА", worst, text), unsafe_allow_html=True)
    names = dict(MODULES, link="Канал связи")
    for key, lvl in levels.items():
        if lvl > OK:
            st.markdown(f"{'🔴' if lvl == FAULT else '🟡'} **{names[key]}:** {ADVICE[key]}")


def attitude(rec):
    """Крен, тангаж, курс (рад) по акселерометру и магнитометру."""
    ax, ay, az = rec["accel"]
    mx, my, mz = rec["mag"]
    roll, pitch = math.atan2(ay, az), math.atan2(-ax, math.hypot(ay, az))
    xh = mx * math.cos(pitch) + mz * math.sin(pitch)
    yh = mx * math.sin(roll) * math.sin(pitch) + my * math.cos(roll) - mz * math.sin(roll) * math.cos(pitch)
    return roll, pitch, math.atan2(-yh, xh)


def sat_figure(rec):
    """3D-модель спутника, повёрнутая по текущей ориентации."""
    roll, pitch, yaw = attitude(rec)
    cr, sr, cp, sp, cy, sy = (math.cos(roll), math.sin(roll), math.cos(pitch),
                              math.sin(pitch), math.cos(yaw), math.sin(yaw))
    rot = (np.array([[cy, -sy, 0], [sy, cy, 0], [0, 0, 1]]) @ np.array([[cp, 0, sp], [0, 1, 0], [-sp, 0, cp]])
           @ np.array([[1, 0, 0], [0, cr, -sr], [0, sr, cr]]))
    cube = np.array([[0, 0, 0], [0, 1, 0], [1, 1, 0], [1, 0, 0], [0, 0, 1], [0, 1, 1], [1, 1, 1], [1, 0, 1]], float)
    p = ((cube - 0.5) * 1.6) @ rot.T
    fig = go.Figure(go.Mesh3d(x=p[:, 0], y=p[:, 1], z=p[:, 2], i=[7, 0, 0, 0, 4, 4, 2, 6, 4, 0, 3, 7],
                              j=[3, 4, 1, 2, 5, 6, 5, 2, 0, 1, 6, 4], k=[0, 7, 2, 3, 6, 7, 1, 1, 5, 5, 7, 6],
                              color="#5b7fa6", opacity=0.9, flatshading=True))
    for axis, color, name in ((0, "#d62839", "X"), (1, "#1f9d55", "Y"), (2, "#2a6fdb", "Z")):
        e = rot[:, axis] * 1.6
        fig.add_trace(go.Scatter3d(x=[0, e[0]], y=[0, e[1]], z=[0, e[2]], mode="lines+text",
                                   line=dict(color=color, width=7), text=["", name], showlegend=False))
    axis_cfg = dict(range=[-2, 2], showticklabels=False, title="")
    fig.update_layout(height=320, margin=dict(l=0, r=0, t=0, b=0), uirevision="keep",
                      scene=dict(xaxis=axis_cfg, yaxis=axis_cfg, zaxis=axis_cfg, aspectmode="cube"))
    return fig, tuple(math.degrees(a) for a in (roll, pitch, yaw))


def render_attitude(rec, key):
    st.subheader("Ориентация КА")
    if any(math.isnan(v) for v in rec["accel"] + rec["mag"]):
        st.info("Ориентация недоступна: нет данных датчика положения.")
        return
    roll, pitch, yaw = (math.degrees(a) for a in attitude(rec))
    st.write(f"Крен: **{roll:.0f}°** · Тангаж: **{pitch:.0f}°** · Курс: **{yaw:.0f}°** "
             "(ориентировочно, по акселерометру и магнитометру)")
    if go is None:
        st.info("3D-вид недоступен: добавьте plotly в requirements.txt")
        return
    fig, _ = sat_figure(rec)
    st.plotly_chart(fig, key="att3d_" + key)


def render_stats(recs, title):
    """Таблица мин / макс / среднее по параметрам."""
    if not recs:
        st.info("Пока нет данных для статистики.")
        return
    st.caption(title)
    series = {"АКБ, В": [r["battery_v"] for r in recs]}
    series.update({f"СД{i + 1}": [r["light"][i] for r in recs] for i in range(4)})
    for key, names in (("accel", "AX AY AZ"), ("gyro", "GX GY GZ"), ("mag", "MX MY MZ")):
        series.update({nm: [r[key][i] for r in recs] for i, nm in enumerate(names.split())})
    series["МАХОВИК"] = [r["flywheel"] for r in recs]
    rows = []
    for name, vals in series.items():
        v = pd.Series(vals)
        rows.append({"параметр": name, "сейчас": round(v.iloc[-1], 3), "мин": round(v.min(), 3),
                     "макс": round(v.max(), 3), "среднее": round(v.mean(), 3)})
    st.dataframe(pd.DataFrame(rows), hide_index=True)


def render_frame(current):
    """Метрики одного пакета: гироскоп, магнитометр, свет."""
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Статус пакета", current.get("status", "UNKNOWN"))
    col2.metric("Напряжение АКБ", num(current.get("battery_v"), ".2f", " В"))
    col3.metric("Маховик", num(current.get("flywheel"), ".0f"))
    col4.metric("Ускорение Z", num(current.get("accel", [None] * 3)[2], ".2f", " g"))
    if current.get("error"):
        st.error(f"❌ Повреждённый пакет: {current['error']}")
    if current.get("warnings"):
        st.warning("⚠️ " + "; ".join(current["warnings"]))
    col_g, col_m = st.columns(2)
    with col_g:
        st.subheader("Гироскоп (°/с)")
        g = current.get("gyro", [None] * 3)
        st.write(f"GX: {num(g[0])} | GY: {num(g[1])} | GZ: {num(g[2])}")
    with col_m:
        st.subheader("Магнитометр (Гс)")
        m = current.get("mag", [None] * 3)
        st.write(f"MX: {num(m[0])} | MY: {num(m[1])} | MZ: {num(m[2])}")
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
                "lv_log": deque(maxlen=50000), "cmds": [], "export": None, "lv_modcnt": {}}
    for key, default in defaults.items():
        s.setdefault(key, default)


@st.fragment(run_every=1.0)
def live_view(url):
    init_live_state()
    s = st.session_state
    error = None
    new_fault = None
    for _ in range(8):                      # несколько запросов за секунду, чтобы не терять пакеты
        raw, error = fetch_frame(url)
        if error:
            break
        if raw and raw.strip() and raw != s.lv_raw:
            s.lv_raw, s.lv_rx, s.lv_seq = raw, time.time(), s.lv_seq + 1
            s.lv_log.append(raw.strip())   # журнал принятых пакетов (как есть)
            rec = parse_telemetry_line(raw)
            if rec["status"] != "CORRUPTED":
                s.lv_checker.apply(rec)      # может переклассифицировать пакет (шум или отказ датчика)
                rec.pop("n", None)
            if rec["status"] != "CORRUPTED":
                s.lv_valid = rec
                s.lv_hist.append(rec)
                for m, lvl in rec["modules"].items():
                    if lvl > OK:
                        s.lv_modcnt[MODULES[m]] = s.lv_modcnt.get(MODULES[m], 0) + 1
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
                    if rec["status"] == "FAULT":
                        new_fault = texts[0]
                s.lv_sig = sig
            else:
                s.lv_sig = None
        time.sleep(0.1)

    if new_fault:
        st.toast(f"🔴 АВАРИЯ: {new_fault}")
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
    tab_panel, tab_charts, tab_stats, tab_events = st.tabs(["🎛️ Пульт", "📈 Графики", "📊 Статистика", "📋 События"])
    with tab_panel:
        render_summary(s.lv_valid, link_level)
        render_tiles(s.lv_valid, link_level, link_text)
        if s.lv_valid is not None:
            render_attitude(s.lv_valid, "live")
        if s.lv_rec is not None and s.lv_rec["status"] == "CORRUPTED":
            st.error(f"❌ Последний пакет повреждён: {s.lv_rec['error']}")
        if s.lv_valid is not None:
            render_frame(s.lv_valid)
        elif not error:
            st.info("Данных пока нет.")
    with tab_charts:
        render_charts(list(s.lv_hist))
    with tab_stats:
        render_stats(list(s.lv_hist), "Статистика по последним 600 пакетам.")
        if s.lv_modcnt:
            st.markdown("**Пакеты с отклонениями по модулям (с начала сеанса)**")
            st.bar_chart(pd.Series(s.lv_modcnt))
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


@st.cache_resource(show_spinner=False)
def file_summary(key, _records):
    """Аномалии по модулям и таймлайн аномалий по всему файлу (считается один раз)."""
    per_module = {name: 0 for name in MODULES.values()}
    flags = []
    for r in _records:
        flags.append(r["status"] != "NORMAL")
        if r.get("modules"):
            for m, lvl in r["modules"].items():
                if lvl > OK:
                    per_module[MODULES[m]] += 1
    size = max(1, len(flags) // 60)
    timeline = pd.Series(flags).groupby(pd.Series(range(len(flags))) // size).sum()
    timeline.index = [i * size for i in timeline.index]
    return pd.Series(per_module), timeline


@st.cache_resource(show_spinner=False)
def event_types(key, _records):
    """Сводка типов аномалий: сколько пакетов, первый пакет, пример сырой строки."""
    groups = {}
    for i, r in enumerate(_records):
        if r["status"] == "NORMAL":
            continue
        for text in ([r["error"]] if r.get("error") else r["warnings"]):
            kind = re.sub(r"-?\d+(\.\d+)?", "#", text)
            if kind in groups:
                groups[kind]["пакетов"] += 1
            else:
                groups[kind] = {"тип аномалии": kind, "пакетов": 1, "первый пакет": i,
                                "статус": r["status"], "пример строки": r["raw"][:120]}
    return sorted(groups.values(), key=lambda g: -g["пакетов"])


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

    tab_panel, tab_charts, tab_stats, tab_events, tab_raw = st.tabs(
        ["🎛️ Пульт", "📈 Графики", "📊 Статистика", "📋 События", "💻 Сырые данные"])
    with tab_panel:
        if st.session_state.get("frame_file") != (filename, len(records)):   # сменился файл — начинаем с начала
            st.session_state["frame_idx"], st.session_state["frame_file"] = 0, (filename, len(records))
        st.session_state["frame_idx"] = min(st.session_state.get("frame_idx", 0), len(records) - 1)
        cur = st.session_state["frame_idx"]
        # начала участков аномалий: пакет не в норме, а предыдущий в норме
        starts = [i for i, r in enumerate(records)
                  if r["status"] != "NORMAL" and (i == 0 or records[i - 1]["status"] == "NORMAL")]
        prev_a = next((i for i in reversed(starts) if i < cur), None)
        next_a = next((i for i in starts if i > cur), None)
        last = len(records) - 1

        def goto(i):
            st.session_state["frame_idx"] = i

        b1, b2, b3 = st.columns(3)
        b1.button("⏮ В начало", on_click=goto, args=(0,))
        b2.button("◀ Пред. аномалия", on_click=goto, args=(prev_a,), disabled=prev_a is None)
        b3.button("След. аномалия ▶", on_click=goto, args=(next_a,), disabled=next_a is None)
        c1, c2, c3, c4 = st.columns(4)
        c1.button("−5000", on_click=goto, args=(max(0, cur - 5000),))
        c2.button("−500", on_click=goto, args=(max(0, cur - 500),))
        c3.button("+500", on_click=goto, args=(min(last, cur + 500),))
        c4.button("+5000", on_click=goto, args=(min(last, cur + 5000),))
        idx = st.slider("Выбор кадра телеметрии", 0, len(records) - 1, key="frame_idx")
        rec = records[idx]
        window = records[max(0, idx - 15): idx + 16]
        link_level, link_text = link_state([r["status"] for r in window])
        shown = rec if rec["status"] != "CORRUPTED" else None
        render_summary(shown, link_level)
        render_tiles(shown, link_level, link_text)
        if shown is not None:
            render_attitude(shown, "file")
        render_frame(rec)
    with tab_charts:
        st.caption("Графики — 600 пакетов вокруг выбранного кадра.")
        render_charts([r for r in records[max(0, idx - 300): idx + 301] if r["status"] != "CORRUPTED"])
    with tab_stats:
        render_stats([r for r in records[max(0, idx - 300): idx + 301] if r["status"] != "CORRUPTED"],
                     "Статистика по 600 пакетам вокруг выбранного кадра.")
        per_module, timeline = file_summary(f"{filename}:{len(records)}:{records[0].get('raw', '')[:30]}", records)
        st.markdown("**Пакеты с отклонениями по модулям (весь файл)**")
        st.bar_chart(per_module)
        st.markdown("**Где в файле происходят аномалии** (число аномальных пакетов по участкам файла)")
        st.bar_chart(timeline)
    with tab_events:
        counts = pd.Series([r["status"] for r in records]).value_counts()
        st.markdown("**Состояние пакетов во всём файле:**")
        st.bar_chart(counts)
        bad = [{"пакет": i, "статус": r["status"],
                "событие": r.get("error") or "; ".join(r["warnings"])}
               for i, r in enumerate(records) if r["status"] != "NORMAL"]
        types = event_types(f"{filename}:{len(records)}:{records[0].get('raw', '')[:30]}", records)
        if types:
            st.markdown("**Типы аномалий** (сколько пакетов, первый пакет, пример строки)")
            st.dataframe(pd.DataFrame(types), hide_index=True)
        st.markdown(f"**Аномалии: {len(bad)} из {len(records)} пакетов** (показаны первые 500)")
        if bad:
            st.dataframe(pd.DataFrame(bad[:500]), hide_index=True)
        else:
            st.success("Аномалий нет.")
    with tab_raw:
        r_idx = st.number_input("Номер строки", 0, len(records) - 1, 0)
        st.code(str(records[r_idx].get("raw", "")))
        st.json({k: v for k, v in records[r_idx].items() if k != "raw"})


SCENARIOS = {"01 — штатный полёт": "01_normal_flight.csv",
             "02 — сбои датчиков": "02_sensor_faults.csv",
             "03 — радиошум": "03_radio_noise.csv"}


def reset_live_state():
    """Сбрасывает накопленные данные live-режима (журнал команд сохраняется)."""
    for key in [k for k in st.session_state.keys() if k.startswith("lv_")]:
        del st.session_state[key]
    st.session_state["export"] = None


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
    scenario = st.sidebar.selectbox("Сценарий данных", list(SCENARIOS.keys()))
    if "?" not in url:                      # сценарий выбирается параметром ?file=
        url = f"{url}?file={SCENARIOS[scenario]}"
    if st.session_state.get("lv_url") != url:   # сменился адрес или сценарий — начинаем с чистого листа
        reset_live_state()
        st.session_state["lv_url"] = url
    if st.sidebar.button("🔄 Сбросить данные"):
        reset_live_state()
        st.session_state["lv_url"] = url
    t_tel, t_cmd, t_log = st.tabs(["📡 Телеметрия", "🕹️ Команды", "💾 Лог"])
    with t_tel:
        live_view(url)
    with t_cmd:
        commands_tab(url)
    with t_log:
        log_tab()
else:
    file_view()
