"""ЦУП «ИнтроСат» — веб-интерфейс оператора (Streamlit).

Запуск:  streamlit run app.py
Данные: эмулятор (http://127.0.0.1:8765/telemetry) или воспроизведение файла.
"""
import html
import math
import os
from datetime import datetime

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from sources import DEFAULT_URL, Collector, FileSource, HttpSource
from telemetry import BATT_FAULT, BATT_WARN, FAULT, MODULES, OK, WARN

st.set_page_config(page_title="ЦУП «ИнтроСат»", page_icon="🛰️", layout="wide")
ss = st.session_state

GREEN, YELLOW, RED, GREY = "#1f9d55", "#e6a700", "#d62839", "#56606b"
LEVEL_UI = {OK: (GREEN, "НОРМА"), WARN: (YELLOW, "ВНИМАНИЕ"), FAULT: (RED, "АВАРИЯ")}
ALL_MODULES = {**MODULES, "link": "Канал связи"}

st.markdown("""<style>
.tile{border-radius:12px;padding:12px 14px;color:#fff;min-height:128px}
.tile .nm{font-size:1.1rem;font-weight:600}
.tile .lv{font-size:1.7rem;font-weight:800;line-height:1.25}
.tile .sub{font-size:.95rem;opacity:.95;word-break:break-word}
.banner{border-radius:12px;padding:12px 18px;color:#fff;font-size:1.3rem;font-weight:700}
.banner small{display:block;font-size:.95rem;font-weight:400;opacity:.95}
.blink{animation:bl 1s steps(2,start) infinite}
@keyframes bl{50%{opacity:.5}}
@media (prefers-reduced-motion:reduce){.blink{animation:none}}
.big{font-size:2.3rem;font-weight:800;line-height:1.1}
.cap{font-size:1rem;opacity:.75}
</style>""", unsafe_allow_html=True)


# ------------------------------------------------------------ источник
def launch():
    if ss.get("col"):
        ss.col.stop()
    if ss.mode == "Эмулятор (HTTP)":
        src = HttpSource(ss.url)
    else:
        text = None
        if ss.get("upl") is not None:
            text = ss.upl.getvalue().decode("utf-8", errors="replace")
        elif ss.get("sample", "—") != "—":
            with open(os.path.join("data", ss.sample), encoding="utf-8", errors="replace") as fh:
                text = fh.read()
        if text is None:
            st.sidebar.error("Загрузите файл или выберите его из data/")
            return
        src = FileSource(text, rate=ss.rate, loop=ss.loop)
    ss.col = Collector(src)
    ss.col.start()


with st.sidebar:
    st.header("Источник данных")
    st.radio("Режим", ["Эмулятор (HTTP)", "Воспроизведение файла"], key="mode")
    if ss.mode == "Эмулятор (HTTP)":
        st.text_input("Адрес эмулятора", DEFAULT_URL, key="url")
    else:
        st.file_uploader("CSV или сохранённый лог", type=["csv", "txt", "log"], key="upl")
        files = sorted(x for x in os.listdir("data") if x.lower().endswith((".csv", ".txt"))) \
            if os.path.isdir("data") else []
        st.selectbox("…или файл из папки data/", ["—"] + files, key="sample")
        st.slider("Скорость, пакетов/с", 1, 500, 20, key="rate")
        st.checkbox("Зациклить", key="loop")
    c1, c2 = st.columns(2)
    if c1.button("▶ Старт"):
        launch()
    if c2.button("■ Стоп") and ss.get("col"):
        ss.col.stop()
    st.slider("Окно графиков, пакетов", 100, 5000, 600, 50, key="window")
    st.text_input("COM-порт для команд (необязательно)", key="com", placeholder="COM3 или /dev/ttyUSB0")

if "col" not in ss:
    launch()   # по умолчанию сразу подключаемся к эмулятору


# ------------------------------------------------------------ вспомогательное
def tile(name, level, sub, nodata=False):
    color, label = (GREY, "НЕТ ДАННЫХ") if nodata else LEVEL_UI[level]
    cls = "tile blink" if (level == FAULT and not nodata) else "tile"
    return (f'<div class="{cls}" style="background:{color}"><div class="nm">{name}</div>'
            f'<div class="lv">{label}</div><div class="sub">{html.escape(sub)}</div></div>')


def frames_df(frames):
    rows = [{"пакет": f.seq, "t": f.t, "статус": f.status, **f.vals} for f in frames if f.valid]
    return pd.DataFrame(rows)


def line_fig(df, cols, title, unit, hlines=()):
    fig = go.Figure()
    for c in cols:
        fig.add_trace(go.Scatter(x=df["пакет"], y=df[c], mode="lines", name=c.upper()))
    for y, color in hlines:
        fig.add_hline(y=y, line_dash="dash", line_color=color)
    fig.update_layout(title=title, height=270, margin=dict(l=10, r=10, t=45, b=10),
                      yaxis_title=unit, xaxis_title="пакет №", uirevision="keep",
                      legend=dict(orientation="h", y=-0.25), font=dict(size=14))
    return fig


def sat_fig(v):
    """3D-вид: ориентация по акселерометру (крен/тангаж) и магнитометру (курс)."""
    ax, ay, az, mx, my, mz = (v[k] for k in ("ax", "ay", "az", "mx", "my", "mz"))
    if any(math.isnan(x) for x in (ax, ay, az, mx, my, mz)):
        return None, None
    roll, pitch = math.atan2(ay, az), math.atan2(-ax, math.hypot(ay, az))
    xh = mx * math.cos(pitch) + mz * math.sin(pitch)
    yh = mx * math.sin(roll) * math.sin(pitch) + my * math.cos(roll) - mz * math.sin(roll) * math.cos(pitch)
    yaw = math.atan2(-yh, xh)
    cr, sr, cp, sp, cy, sy = (math.cos(roll), math.sin(roll), math.cos(pitch),
                              math.sin(pitch), math.cos(yaw), math.sin(yaw))
    R = np.array([[cy, -sy, 0], [sy, cy, 0], [0, 0, 1]]) @ np.array([[cp, 0, sp], [0, 1, 0], [-sp, 0, cp]]) \
        @ np.array([[1, 0, 0], [0, cr, -sr], [0, sr, cr]])
    cube = np.array([[0, 0, 0], [0, 1, 0], [1, 1, 0], [1, 0, 0], [0, 0, 1], [0, 1, 1], [1, 1, 1], [1, 0, 1]], float)
    p = (cube - 0.5) * np.array([1.6, 1.6, 1.6]) @ R.T
    fig = go.Figure(go.Mesh3d(x=p[:, 0], y=p[:, 1], z=p[:, 2], i=[7, 0, 0, 0, 4, 4, 2, 6, 4, 0, 3, 7],
                              j=[3, 4, 1, 2, 5, 6, 5, 2, 0, 1, 6, 4], k=[0, 7, 2, 3, 6, 7, 1, 1, 5, 5, 7, 6],
                              color="#5b7fa6", opacity=0.9, flatshading=True))
    for axis, color, name in ((0, "#d62839", "X"), (1, "#1f9d55", "Y"), (2, "#2a6fdb", "Z")):
        e = (R[:, axis] * 1.6)
        fig.add_trace(go.Scatter3d(x=[0, e[0]], y=[0, e[1]], z=[0, e[2]], mode="lines+text",
                                   line=dict(color=color, width=7), text=["", name], showlegend=False))
    rng = dict(range=[-2, 2], showticklabels=False, title="")
    fig.update_layout(height=330, margin=dict(l=0, r=0, t=0, b=0), uirevision="keep",
                      scene=dict(xaxis=rng, yaxis=rng, zaxis=rng, aspectmode="cube"))
    return fig, tuple(math.degrees(a) for a in (roll, pitch, yaw))


BANNERS = {"ONLINE": (GREEN, "● СВЯЗЬ ЕСТЬ"), "WAITING": (YELLOW, "● ОЖИДАНИЕ ДАННЫХ"),
           "STALLED": (RED, "▲ ПЕРЕДАЧА ПРЕРВАНА"), "NO_CONNECTION": (RED, "▲ НЕТ СВЯЗИ С ИСТОЧНИКОМ"),
           "FINISHED": (GREY, "■ ВОСПРОИЗВЕДЕНИЕ ЗАВЕРШЕНО"), "CONNECTING": (GREY, "… ПОДКЛЮЧЕНИЕ"),
           "STOPPED": (GREY, "■ ОСТАНОВЛЕНО")}


# ------------------------------------------------------------ живые блоки
@st.fragment(run_every=1.0)
def banner():
    col = ss.get("col")
    if col is None:
        return
    s = col.snapshot()
    color, title = BANNERS[s["conn"]]
    n = s["counts"]
    detail = {
        "ONLINE": f'принято {n["total"]} · норма {n["NORMAL"]} · внимание {n["WARNING"]} · '
                  f'аварий {n["FAULT"]} · битых {n["CORRUPTED"]}',
        "WAITING": "источник доступен, но пакетов пока нет — запущен ли выбор файла в эмуляторе?",
        "STALLED": f'последний пакет получен {s["age"] or 0:.0f} с назад; данные на экране устарели',
        "NO_CONNECTION": f'{s["error"]}. Запустите «python emulator.py» — повтор каждую секунду',
        "FINISHED": f'принято {n["total"]} пакетов',
        "STOPPED": f'принято {n["total"]} пакетов, лог сохранён',
    }.get(s["conn"], "")
    cls = "banner blink" if s["conn"] in ("STALLED", "NO_CONNECTION") else "banner"
    st.markdown(f'<div class="{cls}" style="background:{color}">{title}'
                f'<small>{html.escape(detail)}</small></div>', unsafe_allow_html=True)


@st.fragment(run_every=1.0)
def overview():
    col = ss.get("col")
    if col is None:
        return
    s = col.snapshot(frames=True)
    last = s["last"]
    v = last.vals if last else None
    sub = {}
    if v:
        sub = {"mcu": f"t = {last.t}", "power": f'{v["akb"]:.2f} В',
               "imu": f'|a| = {math.sqrt(v["ax"]**2 + v["ay"]**2 + v["az"]**2):.2f} g',
               "light": " · ".join(f'{v[f"sd{i}"]:.0f}' for i in range(1, 5)),
               "wheel": f'{v["wheel"]:+.0f}',
               "link": f'битых {s["bad_pct"]:.0f}% из последних {s["n_recent"]}'}
    cols = st.columns(len(ALL_MODULES))
    for c, (key, name) in zip(cols, ALL_MODULES.items()):
        msgs = s["mod_msgs"].get(key, [])
        text = (msgs[0] + (f" (+{len(msgs) - 1})" if len(msgs) > 1 else "")) if msgs else sub.get(key, "")
        c.markdown(tile(name, s["mod_level"].get(key, OK), text, nodata=last is None),
                   unsafe_allow_html=True)
    if v is None:
        st.info("Данных пока нет. Запустите эмулятор или выберите воспроизведение файла.")
        return
    left, mid, right = st.columns([1, 1.2, 1.3])
    with left:
        st.markdown(f'<div class="cap">Напряжение АКБ</div><div class="big">{v["akb"]:.2f} В</div>',
                    unsafe_allow_html=True)
        w = v["wheel"]
        arrow = "● стоит" if w == 0 else ("↻ знак +" if w > 0 else "↺ знак −")
        st.markdown(f'<div class="cap">Маховик</div><div class="big">{w:+.0f}</div>'
                    f'<div class="cap">{arrow}</div>', unsafe_allow_html=True)
        st.markdown(f'<div class="cap">Последний пакет</div><div class="big">№ {last.seq}</div>'
                    f'<div class="cap">статус: {last.status}</div>', unsafe_allow_html=True)
        if s["progress"] is not None:
            st.progress(min(s["progress"], 1.0), text="воспроизведение файла")
    with mid:
        fig, ang = sat_fig(v)
        if fig:
            st.plotly_chart(fig, key="sat3d")
            st.caption(f"Крен {ang[0]:.0f}° · тангаж {ang[1]:.0f}° · курс {ang[2]:.0f}° "
                       "(ориентировочно, по акселерометру и магнитометру)")
        else:
            st.warning("3D-вид недоступен: некорректные данные датчика положения")
    with right:
        df = frames_df(s["frames"]).tail(ss.window)
        if not df.empty:
            hl = [(BATT_WARN[0], YELLOW), (BATT_WARN[1], YELLOW), (BATT_FAULT[0], RED), (BATT_FAULT[1], RED)]
            fig = line_fig(df, ["akb"], "Напряжение АКБ", "В", hl)
            fig.update_layout(showlegend=False, height=330)
            st.plotly_chart(fig, key="akb_main")


@st.fragment(run_every=1.0)
def charts():
    col = ss.get("col")
    if col is None:
        return
    df = frames_df(col.snapshot(frames=True)["frames"]).tail(ss.window)
    if df.empty:
        st.info("Пока нет данных для графиков.")
        return
    specs = [("Освещённость", ["sd1", "sd2", "sd3", "sd4"], "усл. ед."),
             ("Акселерометр", ["ax", "ay", "az"], "g"), ("Гироскоп", ["gx", "gy", "gz"], "°/с"),
             ("Магнитометр", ["mx", "my", "mz"], "Гс"), ("Маховик", ["wheel"], "ед."),
             ("Напряжение АКБ", ["akb"], "В")]
    a, b = st.columns(2)
    for i, (title, cs, unit) in enumerate(specs):
        (a if i % 2 == 0 else b).plotly_chart(line_fig(df, cs, title, unit), key=f"ch{i}")


@st.fragment(run_every=1.0)
def journal():
    col = ss.get("col")
    if col is None:
        return
    s = col.snapshot()
    n = s["counts"]
    st.markdown(f'Пакетов: **{n["total"]}** · норма **{n["NORMAL"]}** · внимание **{n["WARNING"]}** · '
                f'аварий **{n["FAULT"]}** · битых **{n["CORRUPTED"]}** · повторов **{n["dups"]}**')
    st.caption(f'Файл лога: {s["log_path"] or "недоступен"}')
    if s["events"]:
        st.dataframe(pd.DataFrame(s["events"]).head(100), hide_index=True)
    else:
        st.success("Событий нет — все системы в норме.")


# ------------------------------------------------------------ команды
COMMANDS = {"Запрос статуса": "GET_STATUS", "Калибровка датчика положения": "CALIB_IMU",
            "Остановить маховик": "WHEEL_STOP", "Режим энергосбережения": "POWER_SAVE",
            "Перезапуск МК": "MCU_RESET"}


def send_command(code):
    port = (ss.get("com") or "").strip()
    if not port:
        return "ИМИТАЦИЯ: эмулятор команды не принимает"
    try:
        import serial
        with serial.Serial(port, 115200, timeout=1) as sp:
            sp.write((code + "\n").encode())
        return f"отправлено в {port}"
    except Exception as e:
        return f"ошибка: {e}"


def commands_tab():
    st.caption("Эмулятор работает только на передачу. Без указанного COM-порта команда "
               "записывается в журнал как имитация; с портом — отправляется на устройство "
               "(коды команд подстройте под прошивку).")
    ss.setdefault("cmds", [])
    cols = st.columns(len(COMMANDS))
    for c, (title, code) in zip(cols, COMMANDS.items()):
        if c.button(title, key="cmd_" + code):
            ss.cmds.insert(0, {"время": datetime.now().strftime("%H:%M:%S"), "команда": title,
                               "код": code, "результат": send_command(code)})
    if ss.cmds:
        st.dataframe(pd.DataFrame(ss.cmds), hide_index=True)
        st.download_button("Скачать журнал команд", pd.DataFrame(ss.cmds).to_csv(index=False).encode("utf-8-sig"),
                           "commands.csv", "text/csv")


def export_tab():
    col = ss.get("col")
    st.write("Лог хранит принятые пакеты как есть — его можно загрузить обратно через "
             "режим «Воспроизведение файла» и просмотреть позже.")
    if col and st.button("Подготовить файлы для скачивания"):
        ss.export = {"log": open(col.log_path, "rb").read() if col.log_path and os.path.exists(col.log_path) else b"",
                     "csv": frames_df(col.snapshot(frames=True)["frames"]).to_csv(index=False).encode("utf-8-sig")}
    if ss.get("export"):
        a, b = st.columns(2)
        a.download_button("Скачать сырой лог", ss.export["log"], "telemetry_log.csv", "text/csv")
        b.download_button("Скачать разобранные данные", ss.export["csv"], "telemetry_parsed.csv", "text/csv")


# ------------------------------------------------------------ страница
st.title("🛰️ Центр управления полётами · «ИнтроСат»")
banner()
t1, t2, t3, t4, t5 = st.tabs(["Обзор", "Графики", "Команды", "События", "Лог и экспорт"])
with t1:
    overview()
with t2:
    charts()
with t3:
    commands_tab()
with t4:
    journal()
with t5:
    export_tab()
