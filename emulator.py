from __future__ import annotations

import argparse
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from typing import Iterable

try:
    import serial
    from serial.tools import list_ports
except ImportError:
    serial = None
    list_ports = None


def configure_console_output() -> None:
    for stream in (sys.stdout, sys.stderr):
        encoding = getattr(stream, "encoding", None)
        if not encoding or not hasattr(stream, "reconfigure"):
            continue
        try:
            "Проверка".encode(encoding)
        except UnicodeEncodeError:
            stream.reconfigure(errors="backslashreplace")


configure_console_output()


DEFAULT_DATA_DIR = Path(__file__).with_name("data")
DEFAULT_BAUDRATE = 115_200
DEFAULT_HTTP_PORT = 8765
ENCODINGS_TO_TRY = ("utf-8-sig", "utf-8", "cp1251")


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Передаёт телеметрию из файла или COM-порта в локальный HTTP-сервер."
        )
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=DEFAULT_DATA_DIR,
        help="каталог с файлами телеметрии (по умолчанию: ./data рядом со скриптом)",
    )
    parser.add_argument(
        "--file",
        type=Path,
        help="файл телеметрии; если не указан, будет показано меню выбора",
    )
    parser.add_argument(
        "--csv-header",
        choices=("auto", "yes", "no"),
        default="auto",
        help=(
            "обрабатывать ли первую непустую строку как заголовок: auto "
            "пропускает её только в .csv (по умолчанию)"
        ),
    )
    parser.add_argument(
        "--port",
        help="COM-порт: источник при --source serial или приёмник при --transport serial",
    )
    parser.add_argument(
        "--baudrate",
        type=int,
        default=DEFAULT_BAUDRATE,
        help=f"скорость порта (по умолчанию: {DEFAULT_BAUDRATE})",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=0.1,
        help="пауза между кадрами в секундах (по умолчанию: 0.1)",
    )
    parser.add_argument(
        "--transport",
        choices=("http", "serial"),
        default="http",
        help="способ передачи: http (по умолчанию) или serial (COM-порт)",
    )
    parser.add_argument(
        "--source",
        choices=("file", "serial"),
        default="file",
        help=(
            "источник кадров для HTTP: file (по умолчанию) или serial. "
            "serial поддерживается только вместе с --transport http"
        ),
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="адрес HTTP-сервера (по умолчанию: 127.0.0.1)",
    )
    parser.add_argument(
        "--http-port",
        type=int,
        default=DEFAULT_HTTP_PORT,
        help=f"порт HTTP-сервера (по умолчанию: {DEFAULT_HTTP_PORT})",
    )
    parser.add_argument(
        "--loop",
        action="store_true",
        help="после последнего кадра начать файл заново (только для --transport serial)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="выводить кадры на экран вместо отправки в COM-порт",
    )
    parser.add_argument(
        "--list-files",
        action="store_true",
        help="вывести доступные файлы и завершить работу",
    )
    return parser.parse_args()


def telemetry_files(directory: Path) -> list[Path]:
    """Возвращает все обычные, не скрытые файлы внутри каталога рекурсивно."""
    if not directory.exists():
        raise FileNotFoundError(f"Каталог с данными не найден: {directory}")
    if not directory.is_dir():
        raise NotADirectoryError(f"Это не каталог: {directory}")

    return sorted(
        (
            file
            for file in directory.rglob("*")
            if file.is_file() and not any(part.startswith(".") for part in file.parts)
        ),
        key=lambda file: str(file.relative_to(directory)).lower(),
    )


def choose_from_menu(items: list[Path], base_dir: Path, title: str) -> Path:
    print(title)
    for number, item in enumerate(items, start=1):
        print(f"  {number}. {item.relative_to(base_dir)}")

    while True:
        answer = input("Введите номер: ").strip()
        try:
            choice = int(answer)
            if 1 <= choice <= len(items):
                return items[choice - 1]
        except (ValueError, IndexError):
            pass
        print(f"Укажите число от 1 до {len(items)}.")


def choose_port() -> str:
    if list_ports is None:
        missing_pyserial()

    ports = sorted(list_ports.comports(), key=lambda port: port.device)
    if not ports:
        return input("COM-порт не найден автоматически. Введите имя порта (например, COM3): ").strip()

    print("Доступные последовательные порты:")
    for number, port in enumerate(ports, start=1):
        description = f" — {port.description}" if port.description else ""
        print(f"  {number}. {port.device}{description}")
    print("  0. Ввести имя порта вручную")

    while True:
        answer = input("Введите номер порта: ").strip()
        try:
            choice = int(answer)
            if choice == 0:
                port_name = input("Имя порта: ").strip()
                if port_name:
                    return port_name
            elif 1 <= choice <= len(ports):
                return ports[choice - 1].device
        except (ValueError, IndexError):
            pass
        print(f"Укажите число от 0 до {len(ports)}.")


def read_frames(path: Path, csv_header: str = "auto") -> list[str]:
    """Читает непустые строки без разбора формата кадра.

    Скрипт не предполагает ни определённое количество полей, ни разделитель.
    В CSV первая непустая строка обычно является заголовком, поэтому в режиме
    ``auto`` она не передаётся получателю.
    """
    raw_data = path.read_bytes()
    for encoding in ENCODINGS_TO_TRY:
        try:
            content = raw_data.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    else:
        raise UnicodeError(
            f"Не удалось прочитать {path.name}. Поддерживаются кодировки: "
            + ", ".join(ENCODINGS_TO_TRY)
        )

    frames = [line for line in content.splitlines() if line.strip()]
    skip_header = csv_header == "yes" or (
        csv_header == "auto" and path.suffix.lower() == ".csv"
    )
    if skip_header and frames:
        frames = frames[1:]
    if not frames:
        raise ValueError(f"В файле {path.name} нет строк с телеметрией.")
    return frames


def send_frames(
    frames: Iterable[str],
    *,
    port: str | None,
    baudrate: int,
    interval: float,
    dry_run: bool,
) -> int:
    """Отправляет кадры в порт или печатает их при --dry-run."""
    sent = 0

    if dry_run:
        for sent, frame in enumerate(frames, start=1):
            print(frame)
            if interval:
                time.sleep(interval)
        return sent

    if serial is None:
        missing_pyserial()
    assert port is not None

    with serial.Serial(port=port, baudrate=baudrate, timeout=1, write_timeout=3) as connection:
        print(f"Подключено к {connection.port} на скорости {baudrate} бод.")
        for sent, frame in enumerate(frames, start=1):
            connection.write((frame + "\r\n").encode("utf-8"))
            connection.flush()
            print(f"Отправлен кадр {sent}")
            if interval:
                time.sleep(interval)
    return sent


class LatestFrame:
    """Хранит последний полностью полученный кадр."""

    def __init__(self) -> None:
        self._frame: str | None = None
        self._lock = threading.Lock()

    def set(self, frame: str) -> None:
        with self._lock:
            self._frame = frame

    def get(self) -> str | None:
        with self._lock:
            return self._frame


def run_http_server(
frames: list[str] | None,
*,
host: str,
http_port: int,
interval: float,
source_port: str | None = None,
baudrate: int = DEFAULT_BAUDRATE,
scenario_frames: dict[str, list[str]] | None = None,
) -> None:

    """Запускает HTTP-сервер, который по запросу отдаёт последний кадр."""

    latest_frame = LatestFrame()
    stop_event = threading.Event()
    serial_connection = None

    if source_port is not None:
        if serial is None:
            missing_pyserial()
        serial_connection = serial.Serial(
            port=source_port,
            baudrate=baudrate,
            timeout=1,
        )
        print(f"Источник телеметрии: {serial_connection.port} ({baudrate} бод).")

        def produce_from_serial() -> None:
            assert serial_connection is not None
            while not stop_event.is_set():
                try:
                    raw_frame = serial_connection.readline()
                except Exception as error:
                    if not stop_event.is_set():
                        print(f"Ошибка чтения COM-порта: {error}", file=sys.stderr)
                    return
                if not raw_frame:
                    continue
                frame = raw_frame.decode("utf-8", errors="replace").rstrip("\r\n")
                if not frame.strip():
                    continue
                latest_frame.set(frame)

        producer = threading.Thread(target=produce_from_serial, daemon=True)
    else:
        assert frames is not None

        def produce_from_file() -> None:
            while not stop_event.is_set():
                for frame in frames:
                    latest_frame.set(frame)
                    if stop_event.wait(interval):
                        return

        producer = threading.Thread(target=produce_from_file, daemon=True)

    class TelemetryHandler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

    def do_GET(self) -> None:
        parsed_url = urlparse(self.path)

        if parsed_url.path != "/telemetry":
            self.send_response(404)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write("Используйте GET /telemetry\n".encode("utf-8"))
            return

        params = parse_qs(parsed_url.query)
        scenario = params.get("scenario", [""])[0]

        frame = latest_frame.get()

        if frame is None:
            self.send_response(204)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            return

        body = frame.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

        def log_message(self, format: str, *args: object) -> None:
            return

    class TelemetryServer(ThreadingHTTPServer):
        daemon_threads = True
        allow_reuse_address = True

    server = TelemetryServer((host, http_port), TelemetryHandler)
    print("HTTP-сервер запущен.")
    print(f"Адрес: http://{host}:{http_port}/telemetry")
    print("GET /telemetry вернёт последнюю полученную строку или статус 204.")
    print("Для остановки нажмите Ctrl+C.")
    producer.start()
    try:
        server.serve_forever()
    finally:
        stop_event.set()
        if serial_connection is not None:
            serial_connection.close()
        server.server_close()


def missing_pyserial() -> None:
    print("Не установлена библиотека pyserial. Выполните: python -m pip install -r requirements.txt", file=sys.stderr)
    raise SystemExit(2)


def main() -> int:
    args = parse_arguments()
    if args.interval < 0:
        print("Параметр --interval не может быть отрицательным.", file=sys.stderr)
        return 2
    if args.baudrate <= 0:
        print("Параметр --baudrate должен быть положительным.", file=sys.stderr)
        return 2
    if not 1 <= args.http_port <= 65_535:
        print("Параметр --http-port должен быть в диапазоне от 1 до 65535.", file=sys.stderr)
        return 2
    if args.transport == "serial" and args.source == "serial":
        print("Источник serial можно использовать только с --transport http.", file=sys.stderr)
        return 2
    if args.dry_run and args.source == "serial":
        print("--dry-run доступен только для источника file.", file=sys.stderr)
        return 2

    try:
        if args.list_files:
            files = telemetry_files(args.data_dir)
            if not files:
                print(f"В каталоге {args.data_dir} нет файлов телеметрии.", file=sys.stderr)
                return 1
            for file in files:
                print(file.relative_to(args.data_dir))
            return 0

        frames: list[str] | None = None
        if args.source == "file":
            files = telemetry_files(args.data_dir)
            if not files:
                print(f"В каталоге {args.data_dir} нет файлов телеметрии.", file=sys.stderr)
                return 1
            selected_file = args.file
            if selected_file is None:
                selected_file = choose_from_menu(files, args.data_dir, "Доступные файлы телеметрии:")
            elif not selected_file.is_absolute():
                selected_file = args.data_dir / selected_file
            selected_file = selected_file.resolve()
            if not selected_file.is_file():
                print(f"Файл не найден: {selected_file}", file=sys.stderr)
                return 1
            frames = read_frames(selected_file, args.csv_header)
            print(f"Выбран файл: {selected_file.name}; кадров: {len(frames)}")

        if args.dry_run:
            assert frames is not None
            sent = send_frames(
                frames,
                port=None,
                baudrate=args.baudrate,
                interval=args.interval,
                dry_run=True,
            )
            print(f"Отправлено кадров: {sent}")
            return 0

        if args.transport == "http":
            source_port = None
            if args.source == "serial":
                source_port = args.port or choose_port()
                if not source_port:
                    print("Имя COM-порта не задано.", file=sys.stderr)
                    return 2
            run_http_server(
                frames,
                host=args.host,
                http_port=args.http_port,
                interval=args.interval,
                source_port=source_port,
                baudrate=args.baudrate,
            )
            return 0

        assert frames is not None
        port = args.port or choose_port()
        if not port:
            print("Имя COM-порта не задано.", file=sys.stderr)
            return 2

        cycle = 0
        while True:
            cycle += 1
            sent = send_frames(
                frames,
                port=port,
                baudrate=args.baudrate,
                interval=args.interval,
                dry_run=False,
            )
            print(f"Цикл {cycle}: отправлено кадров: {sent}")
            if not args.loop:
                break
        return 0
    except (FileNotFoundError, NotADirectoryError, UnicodeError, ValueError) as error:
        print(f"Ошибка: {error}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nЭмуляция остановлена пользователем.")
        return 130
    except Exception as error:
        print(f"Ошибка при работе с эмулятором: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
