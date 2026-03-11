from __future__ import annotations

import platform
import re
import shutil
from pathlib import Path

from .core import (
    Tunnel,
    TunnelSession,
    download_file,
    ensure_directory,
    ensure_executable,
    extract_tar_gz,
    get_public_ipv4,
    require_command,
    run_command,
)
from .exceptions import InstallError, TunnelStartError


def _require_linux_amd64(provider_name: str) -> None:
    """
    Ограничение текущей реализации.

    Автоматическая установка бинарников ориентирована на Kaggle
    и типичные Linux x86_64 окружения.
    """
    system = platform.system().lower()
    machine = platform.machine().lower()

    if system != "linux" or machine not in {"x86_64", "amd64"}:
        raise InstallError(
            f"{provider_name} в текущей версии автоматически устанавливается "
            f"только на Linux x86_64."
        )


class ZROK(Tunnel):
    """Провайдер zrok."""

    name = "zrok"
    requires_token = True
    token_env_var = "ZROK_TOKEN"

    version = "2.0.0-rc7"
    archive_url = (
        "https://github.com/openziti/zrok/releases/download/"
        f"v{version}/zrok_{version}_linux_amd64.tar.gz"
    )

    @property
    def binary_path(self) -> Path:
        """Путь к бинарнику zrok."""
        return self.bin_dir / "zrok"

    def install(self) -> None:
        _require_linux_amd64("zrok")

        if not self.binary_path.exists():
            archive_path = self.data_dir / f"zrok-{self.version}.tar.gz"
            extract_dir = self.data_dir / "tmp" / "zrok"

            if extract_dir.exists():
                shutil.rmtree(extract_dir)

            download_file(self.archive_url, archive_path)
            extract_tar_gz(archive_path, extract_dir)

            candidate: Path | None = None
            for file_name in ("zrok2", "zrok"):
                candidate = next(
                    (path for path in extract_dir.rglob(file_name) if path.is_file()),
                    None,
                )
                if candidate is not None:
                    break

            if candidate is None:
                raise InstallError("Не удалось найти бинарник zrok в архиве.")

            ensure_directory(self.binary_path.parent)
            shutil.copy2(candidate, self.binary_path)
            ensure_executable(self.binary_path)

            archive_path.unlink(missing_ok=True)
            shutil.rmtree(extract_dir, ignore_errors=True)
        else:
            ensure_executable(self.binary_path)

        if not self.token:
            raise InstallError("Для ZROK требуется token.")

        result = run_command([str(self.binary_path), "enable", self.token], timeout=30.0)
        output = "\n".join(part for part in (result.stdout, result.stderr) if part).lower()

        already_enabled = (
            "you already have an enabled environment" in output
            or "already enabled" in output
        )

        if result.returncode != 0 and not already_enabled:
            raise InstallError(f"Не удалось активировать zrok.\n{output}")

    def _start(self, port: int, timeout: float) -> TunnelSession:
        patterns = [
            re.compile(
                r"(https?://[a-z0-9-]+\.shares\.zrok\.io|[a-z0-9-]+\.shares\.zrok\.io)",
                re.IGNORECASE,
            )
        ]

        return self._spawn_and_wait_for_url(
            [str(self.binary_path), "share", "public", f"127.0.0.1:{port}", "--headless"],
            port=port,
            timeout=timeout,
            patterns=patterns,
        )


class NGROK(Tunnel):
    """Провайдер ngrok через pyngrok."""

    name = "ngrok"
    token_env_var = "NGROK_AUTHTOKEN"

    def install(self) -> None:
        try:
            from pyngrok import ngrok
        except ImportError as exc:
            raise InstallError(
                "Не установлен пакет pyngrok. "
                "Установите extra-зависимость: pip install 'webtunnel[ngrok]'"
            ) from exc

        if self.token:
            ngrok.set_auth_token(self.token)

    def _start(self, port: int, timeout: float) -> TunnelSession:
        del timeout

        try:
            from pyngrok import ngrok
        except ImportError as exc:
            raise TunnelStartError(
                "Не установлен пакет pyngrok. "
                "Установите extra-зависимость: pip install 'webtunnel[ngrok]'"
            ) from exc

        if self.token:
            ngrok.set_auth_token(self.token)

        listener = ngrok.connect(addr=port, proto="http")
        public_url = str(listener.public_url)

        session = TunnelSession(provider=self.name, port=port)
        session.set_url(public_url)
        session.mark_running()

        def _stop() -> None:
            try:
                ngrok.disconnect(public_url)
            finally:
                ngrok.kill()

        session.stop_callback = _stop
        return session


class LocalTunnel(Tunnel):
    """
    Провайдер localtunnel.

    Оставлен в статусе experimental.
    """

    name = "localtunnel"
    experimental = True

    @property
    def npm_prefix(self) -> Path:
        """Пользовательский npm-prefix без root-прав."""
        return self.data_dir / "npm"

    @property
    def binary_path(self) -> Path:
        """Путь к локально установленной команде lt."""
        return self.npm_prefix / "bin" / "lt"

    def install(self) -> None:
        if self.binary_path.exists():
            return

        npm = shutil.which("npm")
        if npm is None:
            raise InstallError(
                "Для LocalTunnel нужен npm. "
                "Провайдер experimental и может быть недоступен в текущей среде."
            )

        ensure_directory(self.npm_prefix)

        result = run_command(
            [
                npm,
                "install",
                "--global",
                "localtunnel",
                "--prefix",
                str(self.npm_prefix),
            ],
            timeout=300.0,
        )

        if result.returncode != 0:
            output = "\n".join(part for part in (result.stdout, result.stderr) if part)
            raise InstallError(f"Не удалось установить LocalTunnel.\n{output}")

        if not self.binary_path.exists():
            raise InstallError("npm завершился без ошибки, но бинарник localtunnel не найден.")

    def _start(self, port: int, timeout: float) -> TunnelSession:
        binary = self.binary_path if self.binary_path.exists() else Path(require_command("lt"))

        password = get_public_ipv4()
        details: dict[str, str] = {}
        if password is not None:
            details["password"] = password

        patterns = [
            re.compile(
                r"your url is:\s*(https://[a-z0-9-]+\.(?:loca\.lt|localtunnel\.me))",
                re.IGNORECASE,
            ),
            re.compile(
                r"(https://[a-z0-9-]+\.(?:loca\.lt|localtunnel\.me))",
                re.IGNORECASE,
            ),
        ]

        return self._spawn_and_wait_for_url(
            [str(binary), "--port", str(port)],
            port=port,
            timeout=timeout,
            patterns=patterns,
            initial_details=details,
        )


class Cloudflared(Tunnel):
    """Провайдер cloudflared."""

    name = "cloudflared"
    binary_url = (
        "https://github.com/cloudflare/cloudflared/releases/latest/"
        "download/cloudflared-linux-amd64"
    )

    @property
    def binary_path(self) -> Path:
        """Путь к бинарнику cloudflared."""
        return self.bin_dir / "cloudflared"

    def install(self) -> None:
        _require_linux_amd64("cloudflared")

        if self.binary_path.exists():
            ensure_executable(self.binary_path)
            return

        download_file(self.binary_url, self.binary_path)
        ensure_executable(self.binary_path)

    def _start(self, port: int, timeout: float) -> TunnelSession:
        patterns = [
            re.compile(r"(https://[a-z0-9-]+\.trycloudflare\.com)", re.IGNORECASE)
        ]

        def _handle_cloudflared_line(line: str, session: TunnelSession) -> None:
            """
            Разбирает полезные диагностические данные из логов cloudflared.
            """
            metrics_match = re.search(r"Starting metrics server on ([0-9.:/A-Za-z_-]+)", line)
            if metrics_match is not None:
                session.set_detail("metrics_url", f"http://{metrics_match.group(1)}")

            connector_match = re.search(
                r"Generated Connector ID:\s*([a-f0-9-]+)",
                line,
                re.IGNORECASE,
            )
            if connector_match is not None:
                session.set_detail("connector_id", connector_match.group(1))

            protocol_match = re.search(r"Initial protocol\s+([a-z0-9]+)", line, re.IGNORECASE)
            if protocol_match is not None:
                session.set_detail("protocol", protocol_match.group(1))

            location_match = re.search(r"location=([a-z0-9-]+)", line, re.IGNORECASE)
            if location_match is not None:
                session.set_detail("location", location_match.group(1))

        return self._spawn_and_wait_for_url(
            [
                str(self.binary_path),
                "tunnel",
                "--no-autoupdate",
                "--url",
                f"http://127.0.0.1:{port}",
            ],
            port=port,
            timeout=timeout,
            patterns=patterns,
            line_handler=_handle_cloudflared_line,
        )


class Pinggy(Tunnel):
    """Провайдер Pinggy через SSH reverse tunnel."""

    name = "pinggy"

    def install(self) -> None:
        require_command("ssh")

    def _start(self, port: int, timeout: float) -> TunnelSession:
        ssh = require_command("ssh")

        # Для Pinggy важно не схватить посторонний URL вроде dashboard.pinggy.io.
        # Поэтому URL туннеля ищется только в строке, которая целиком состоит из HTTPS-ссылки.
        patterns = [
            re.compile(
                r"^\s*(https://[a-zA-Z0-9.-]+\.pinggy\.link)\s*$",
                re.IGNORECASE,
            ),
            re.compile(
                r"^\s*(https://(?!dashboard\.pinggy\.io\b)[a-zA-Z0-9.-]+\.pinggy\.io)\s*$",
                re.IGNORECASE,
            ),
        ]

        def _handle_pinggy_line(line: str, session: TunnelSession) -> None:
            """
            Извлекает дополнительные диагностические поля из логов Pinggy.
            """
            remote_port_match = re.search(
                r"Allocated port\s+(\d+)\s+for remote forward",
                line,
                re.IGNORECASE,
            )
            if remote_port_match is not None:
                session.set_detail("remote_port", remote_port_match.group(1))

            if "You are not authenticated." in line:
                session.set_detail("authenticated", "false")

            if "You are authenticated." in line:
                session.set_detail("authenticated", "true")

            expires_match = re.search(
                r"expire in\s+(\d+)\s+minutes",
                line,
                re.IGNORECASE,
            )
            if expires_match is not None:
                session.set_detail("expires_in_minutes", expires_match.group(1))

        return self._spawn_and_wait_for_url(
            [
                ssh,
                "-p",
                "443",
                "-o",
                "StrictHostKeyChecking=no",
                "-o",
                "UserKnownHostsFile=/dev/null",
                "-o",
                "ServerAliveInterval=30",
                "-o",
                "ExitOnForwardFailure=yes",
                f"-R0:localhost:{port}",
                "a.pinggy.io",
            ],
            port=port,
            timeout=timeout,
            patterns=patterns,
            line_handler=_handle_pinggy_line,
        )