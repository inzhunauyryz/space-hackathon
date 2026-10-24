"""Проверка файлов телеметрии из консоли (без интерфейса).

    python main.py                 # все .csv из папки data/
    python main.py data/01_normal_flight.csv

Для 01_normal_flight.csv аномалий почти не должно быть: если они есть,
подстройте пороги в начале telemetry.py (АКБ, ZERO_N, STUCK_N, JUMP_*).
"""
import glob
import re
import sys
from collections import Counter

from telemetry import Validator


def audit(path):
    val, status, texts = Validator(), Counter(), Counter()
    with open(path, encoding="utf-8", errors="replace") as fh:
        lines = [x.strip() for x in fh if x.strip()][1:]   # первая строка — заголовок
    for i, raw in enumerate(lines, 1):
        f = val.process(raw, i)
        status[f.status] += 1
        for _, _, tx in f.issues:
            texts[re.sub(r"-?\d+(\.\d+)?", "#", tx)] += 1
    print(f"\n=== {path}: {len(lines)} пакетов")
    print("  преамбула:", repr(val.pre), "· постамбула:", repr(val.post))
    print("  статусы:", dict(status))
    for tx, n in texts.most_common(8):
        print(f"  {n:7d} × {tx}")


if __name__ == "__main__":
    for p in (sys.argv[1:] or sorted(glob.glob("data/*.csv"))):
        audit(p)
