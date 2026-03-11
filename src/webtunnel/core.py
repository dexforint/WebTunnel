from __future__ import annotations

import logging
import os
import re
import shutil
import signal
import stat
import subprocess
import tarfile
import threading
import time
import urllib.request
from abc import ABC, abstractmethod
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Callable, Mapping, Pattern, Sequence

from .exceptions import InstallError, TunnelStartError

# Регулярное выражение для удаления ANSI-последовательностей из логов.
# Многие CLI-инструменты используют цветной вывод, который неудобно парсить.
_ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-9;]*m")

# Тип callback-функции, которая может разбирать одну строку лога
# и сохранять диагностические данные в TunnelSession.
LineHandler = Callable[[str, "TunnelSession"], None]


class TunnelState(str, Enum):
    """Состояние жизненного цикла туннеля."""

    STOPPED = "stopped"
    STARTING = "starting"
    RUNNING = "running"
    FAILED = "failed"


def is_kaggle() -> bool:
    """
    Определяет, выполняется ли код внутри Kaggle.

    Для Kaggle характерны:
    - переменная окружения KAGGLE_KERNEL_RUN_TYPE;
    - каталог /kaggle/working.
    """
    return "KAGGLE_KERNEL_RUN_TYPE" in os.environ or Path("/kaggle/working").exists()


def default_data_dir() -> Path:
    """
    Возвращает рабочий каталог библиотеки.

    В Kaggle используем /kaggle/working/.webtunnel,
    в обычной среде — ~/.cache/webtunnel.
    """
    if is_kaggle():
        return Path("/kaggle/working/.webtunnel")
    return Path.home() / ".cache" / "webtunnel"


def ensure_directory(path: Path) -> None:
    """Создает каталог, если он еще не существует."""
    path.mkdir(parents=True, exist_ok=True)


def strip_ansi(text: str) -> str:
    """Удаляет ANSI-коды из строки."""
    return _ANSI_ESCAPE_RE.sub("", text)


def normalize_url(value: str) -> str:
    """
    Приводит URL к стабильному виду.

    Если инструмент вернул только домен без схемы,
    добавляется https://.
    """
    cleaned = value.strip()
    if cleaned.startswith(("http://", "https://")):
        return cleaned
    return f"https://{cleaned}"


def ensure_executable(path: Path) -> None:
    """Выставляет файлу права на исполнение."""
    current_mode = path.stat().st_mode
    path.chmod(current_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def download_file(url: str, destination: Path, timeout: float = 120.0) -> Path:
    """
    Скачивает файл во временное имя и затем атомарно переименовывает.

    Такой подход защищает от частично скачанных файлов.
    """
    ensure_directory(destination.parent)
    temp_path = destination.with_suffix(f"{destination.suffix}.part")

    try:
        with urllib.request.urlopen(url, timeout=timeout) as response, temp_path.open("wb") as target:
            shutil.copyfileobj(response, target)

        temp_path.replace(destination)
        return destination
    finally:
        temp_path.unlink(missing_ok=True)


def extract_tar_gz(archive_path: Path, destination: Path) -> None:
    """
    Безопасно распаковывает tar.gz.

    Перед распаковкой проверяется, что все пути остаются внутри
    целевого каталога.
    """
    ensure_directory(destination)
    destination_resolved = destination.resolve()

    with tarfile.open(archive_path, "r:gz") as archive:
        for member in archive.getmembers():
            member_path = (destination / member.name).resolve()
            if not str(member_path).startswith(str(destination_resolved)):
                raise InstallError(f"Обнаружен небезопасный путь в архиве: {member.name}")

        archive.extractall(destination)


def run_command(
    args: Sequence[str],
    *,
    cwd: Path | None = None,
    env: Mapping[str, str] | None = None,
    timeout: float | None = None,
) -> subprocess.CompletedProcess[str]:
    """
    Выполняет внешнюю команду без shell=True.

    Это безопаснее и удобнее для типизированного кода.
    """
    full_env = os.environ.copy()
    if env is not None:
        full_env.update(env)

    return subprocess.run(
        list(args),
        cwd=cwd,
        env=full_env,
        capture_output=True,
        text=True,
        check=False,
        timeout=timeout,
    )


def require_command(name: str) -> str:
    """
    Проверяет наличие команды в PATH и возвращает путь к ней.
    """
    resolved = shutil.which(name)
    if resolved is None:
        raise InstallError(
            f"Не найдена внешняя команда '{name}'. "
            f"Установите ее или выберите другой провайдер."
        )
    return resolved


def get_public_ipv4(timeout: float = 5.0) -> str | None:
    """
    Пытается определить внешний IPv4-адрес.

    Для LocalTunnel это полезное дополнительное диагностическое поле.
    """
    try:
        with urllib.request.urlopen("https://ipv4.icanhazip.com", timeout=timeout) as response:
            return response.read().decode("utf-8").strip()
    except Exception:
        return None


@dataclass(slots=True)
class TunnelSession:
    """
    Описывает текущую сессию туннеля.

    Объект является живым:
    - URL появляется после запуска;
    - details могут дополняться по мере чтения логов;
    - logs_tail обновляется в фоне, пока процесс работает.
    """

    provider: str
    port: int
    process: subprocess.Popen[str] | None = field(default=None, repr=False)
    reader_thread: threading.Thread | None = field(default=None, repr=False)
    stop_callback: Callable[[], None] | None = field(default=None, repr=False)
    terminate_process_group: bool = field(default=False, repr=False)
    started_at: float = field(default_factory=time.time)

    _state: TunnelState = field(default=TunnelState.STARTING, init=False, repr=False)
    _url: str | None = field(default=None, init=False, repr=False)
    _details: dict[str, str] = field(default_factory=dict, init=False, repr=False)
    _logs_tail: deque[str] = field(default_factory=lambda: deque(maxlen=200), init=False, repr=False)
    _last_error: str | None = field(default=None, init=False, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)

    @property
    def url(self) -> str | None:
        """Публичный URL туннеля."""
        with self._lock:
            return self._url

    @property
    def public_url(self) -> str | None:
        """Алиас для url."""
        return self.url

    @property
    def state(self) -> TunnelState:
        """Текущее состояние сессии."""
        with self._lock:
            return self._state

    @property
    def last_error(self) -> str | None:
        """Последняя диагностическая ошибка, если она была сохранена."""
        with self._lock:
            return self._last_error

    @property
    def details(self) -> dict[str, str]:
        """
        Возвращает копию дополнительных данных сессии.

        Примеры:
        - LocalTunnel: password
        - Cloudflared: connector_id, metrics_url, protocol, location
        - Pinggy: remote_port, authenticated, expires_in_minutes
        """
        with self._lock:
            return dict(self._details)

    @property
    def password(self) -> str | None:
        """Удобный доступ к password для LocalTunnel."""
        return self.get_detail("password")

    @property
    def logs_tail(self) -> tuple[str, ...]:
        """Последние строки логов текущей сессии."""
        with self._lock:
            return tuple(self._logs_tail)

    @property
    def is_running(self) -> bool:
        """
        Показывает, работает ли туннель сейчас.

        Для внешних процессов используется poll(),
        для встроенных провайдеров — внутреннее состояние.
        """
        state = self.state
        if self.process is None:
            return state == TunnelState.RUNNING

        if self.process.poll() is not None:
            return False

        return state in {TunnelState.STARTING, TunnelState.RUNNING}

    def get_detail(self, key: str) -> str | None:
        """Возвращает одно диагностическое поле по ключу."""
        with self._lock:
            return self._details.get(key)

    def set_url(self, value: str) -> None:
        """Сохраняет публичный URL сессии."""
        with self._lock:
            self._url = normalize_url(value)

    def set_detail(self, key: str, value: str) -> None:
        """Сохраняет или обновляет диагностическое поле."""
        with self._lock:
            self._details[key] = value

    def append_log(self, line: str) -> None:
        """Добавляет строку в хвост логов."""
        with self._lock:
            self._logs_tail.append(line)

    def mark_running(self) -> None:
        """Переводит сессию в состояние running."""
        with self._lock:
            self._state = TunnelState.RUNNING
            self._last_error = None

    def mark_failed(self, message: str | None = None) -> None:
        """Переводит сессию в состояние failed."""
        with self._lock:
            self._state = TunnelState.FAILED
            self._last_error = message

    def mark_stopped(self) -> None:
        """Переводит сессию в состояние stopped."""
        with self._lock:
            self._state = TunnelState.STOPPED

    def as_dict(self) -> dict[str, object]:
        """
        Возвращает JSON-совместимое представление сессии.

        Это удобно для CLI, логирования и внешней интеграции.
        """
        with self._lock:
            return {
                "provider": self.provider,
                "port": self.port,
                "state": self._state.value,
                "url": self._url,
                "public_url": self._url,
                "details": dict(self._details),
                "started_at": self.started_at,
                "pid": self.process.pid if self.process is not None else None,
                "is_running": self.is_running,
                "last_error": self._last_error,
                "logs_tail": list(self._logs_tail),
                "returncode": self.process.poll() if self.process is not None else None,
            }

    def stop(self, timeout: float = 10.0) -> None:
        """
        Останавливает сессию.

        Метод старается завершить процесс мягко, а затем, при необходимости,
        принудительно завершает его.
        """
        if self.stop_callback is not None:
            try:
                self.stop_callback()
            except Exception:
                pass

        if self.process is not None and self.process.poll() is None:
            try:
                if self.terminate_process_group and os.name == "posix":
                    os.killpg(self.process.pid, signal.SIGTERM)
                else:
                    self.process.terminate()
            except ProcessLookupError:
                pass

            try:
                self.process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                try:
                    if self.terminate_process_group and os.name == "posix":
                        os.killpg(self.process.pid, signal.SIGKILL)
                    else:
                        self.process.kill()
                except ProcessLookupError:
                    pass

                self.process.wait(timeout=timeout)

        if self.reader_thread is not None and self.reader_thread.is_alive():
            self.reader_thread.join(timeout=timeout)

        self.mark_stopped()


class Tunnel(ABC):
    """
    Базовый класс всех провайдеров туннелей.

    Здесь сосредоточена общая логика:
    - lifecycle start/stop;
    - хранение текущей сессии;
    - свойства url/details/password/logs_tail;
    - запуск процессов и ожидание URL из логов.
    """

    name = "tunnel"
    experimental = False
    requires_token = False
    token_env_var: str | None = None

    def __init__(
        self,
        token: str | None = None,
        *,
        data_dir: Path | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        resolved_token = token
        if resolved_token is None and self.token_env_var is not None:
            resolved_token = os.getenv(self.token_env_var)

        self.token = resolved_token
        self.data_dir = data_dir if data_dir is not None else default_data_dir()
        self.bin_dir = self.data_dir / "bin"
        ensure_directory(self.bin_dir)

        self.logger = logger if logger is not None else logging.getLogger(f"webtunnel.{self.name}")
        self._session: TunnelSession | None = None

    @property
    def session(self) -> TunnelSession | None:
        """Текущая активная сессия."""
        return self._session

    @property
    def state(self) -> TunnelState:
        """Состояние туннеля."""
        if self._session is None:
            return TunnelState.STOPPED
        return self._session.state

    @property
    def is_running(self) -> bool:
        """Показывает, работает ли туннель."""
        if self._session is None:
            return False
        return self._session.is_running

    @property
    def url(self) -> str | None:
        """Публичный URL активной сессии."""
        if self._session is None:
            return None
        return self._session.url

    @property
    def public_url(self) -> str | None:
        """Алиас для url."""
        return self.url

    @property
    def details(self) -> dict[str, str]:
        """Дополнительные диагностические данные."""
        if self._session is None:
            return {}
        return self._session.details

    @property
    def password(self) -> str | None:
        """Удобный алиас к detail['password'] для LocalTunnel."""
        if self._session is None:
            return None
        return self._session.password

    @property
    def logs_tail(self) -> tuple[str, ...]:
        """Последние строки логов туннеля."""
        if self._session is None:
            return ()
        return self._session.logs_tail

    @property
    def diagnostics(self) -> dict[str, object]:
        """Снимок диагностической информации."""
        if self._session is None:
            return {
                "provider": self.name,
                "port": None,
                "state": TunnelState.STOPPED.value,
                "url": None,
                "public_url": None,
                "details": {},
                "started_at": None,
                "pid": None,
                "is_running": False,
                "last_error": None,
                "logs_tail": [],
                "returncode": None,
            }
        return self._session.as_dict()

    @abstractmethod
    def install(self) -> None:
        """Подготавливает зависимости провайдера."""

    @abstractmethod
    def _start(self, port: int, timeout: float) -> TunnelSession:
        """Внутренняя реализация запуска провайдера."""

    def start(self, port: int = 8000, timeout: float = 60.0) -> TunnelSession:
        """
        Запускает туннель и возвращает готовую сессию.

        Метод синхронный: после возврата URL уже известен.
        """
        if self._session is not None and self._session.is_running:
            raise TunnelStartError(
                f"Туннель '{self.name}' уже запущен. Сначала вызовите stop()."
            )

        session = self._start(port=port, timeout=timeout)
        self._session = session
        return session

    def stop(self) -> None:
        """Останавливает текущую сессию."""
        if self._session is None:
            return

        self._session.stop()
        self._session = None

    close = stop

    def _extract_url_from_line(
        self,
        line: str,
        patterns: Sequence[Pattern[str]],
    ) -> str | None:
        """
        Ищет URL в строке по набору регулярных выражений.
        """
        for pattern in patterns:
            match = pattern.search(line)
            if match is None:
                continue

            value = match.group(1) if match.lastindex else match.group(0)
            return normalize_url(value)

        return None

    def _spawn_and_wait_for_url(
        self,
        args: Sequence[str],
        *,
        port: int,
        timeout: float,
        patterns: Sequence[Pattern[str]],
        cwd: Path | None = None,
        env: Mapping[str, str] | None = None,
        line_handler: LineHandler | None = None,
        initial_details: Mapping[str, str] | None = None,
    ) -> TunnelSession:
        """
        Запускает внешний процесс и ждет появления публичного URL.

        Дополнительно:
        - сохраняет хвост логов;
        - позволяет разбирать provider-specific diagnostics;
        - возвращает TunnelSession, которая продолжает обновляться в фоне.
        """
        full_env = os.environ.copy()
        if env is not None:
            full_env.update(env)

        use_process_group = os.name == "posix"

        process = subprocess.Popen(
            list(args),
            cwd=cwd or self.data_dir,
            env=full_env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            start_new_session=use_process_group,
        )

        session = TunnelSession(
            provider=self.name,
            port=port,
            process=process,
            terminate_process_group=use_process_group,
        )

        if initial_details is not None:
            for key, value in initial_details.items():
                session.set_detail(key, value)

        url_event = threading.Event()

        def _reader() -> None:
            """
            Фоновый поток чтения stdout.

            Он не только ищет URL, но и собирает диагностические данные.
            """
            stdout = process.stdout
            if stdout is None:
                return

            try:
                for raw_line in stdout:
                    line = strip_ansi(raw_line).rstrip()
                    if not line:
                        continue

                    session.append_log(line)
                    self.logger.info("[%s] %s", self.name, line)

                    if line_handler is not None:
                        try:
                            line_handler(line, session)
                        except Exception:
                            # Диагностика не должна ломать основной сценарий запуска.
                            pass

                    extracted = self._extract_url_from_line(line, patterns)
                    if extracted is not None and session.url is None:
                        session.set_url(extracted)
                        session.mark_running()
                        url_event.set()
            finally:
                stdout.close()

        reader_thread = threading.Thread(
            target=_reader,
            name=f"{self.name}-stdout-reader",
            daemon=True,
        )
        session.reader_thread = reader_thread
        reader_thread.start()

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if url_event.wait(timeout=0.2):
                break

            if process.poll() is not None:
                break

        if session.url is not None:
            return session

        recent_output = "\n".join(session.logs_tail)
        session.mark_failed("Не удалось получить публичный URL.")
        session.stop()

        if process.poll() is not None:
            raise TunnelStartError(
                f"Процесс '{self.name}' завершился до получения публичного URL.\n"
                f"Последний вывод:\n{recent_output}"
            )

        raise TunnelStartError(
            f"Не удалось получить публичный URL от '{self.name}' за {timeout:.1f} сек.\n"
            f"Последний вывод:\n{recent_output}"
        )