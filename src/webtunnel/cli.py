from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import Any

from .exceptions import WebTunnelError
from .registry import PROVIDERS, create_tunnel, get_provider_class


def build_parser() -> argparse.ArgumentParser:
    """
    Создает CLI-парсер.

    На первом этапе поддерживаются две команды:
    - providers: показать доступные провайдеры;
    - share: поднять туннель к уже работающему локальному сервису.
    """
    parser = argparse.ArgumentParser(prog="webtunnel", description="CLI для WebTunnel")
    subparsers = parser.add_subparsers(dest="command", required=True)

    providers_parser = subparsers.add_parser("providers", help="Показать список провайдеров")
    providers_parser.add_argument(
        "--json",
        action="store_true",
        dest="as_json",
        help="Вывести данные в JSON",
    )

    share_parser = subparsers.add_parser("share", help="Запустить туннель")
    share_parser.add_argument(
        "--provider",
        choices=sorted(PROVIDERS),
        default="cloudflared",
        help="Имя провайдера туннеля",
    )
    share_parser.add_argument(
        "--port",
        type=int,
        default=8000,
        help="Локальный порт приложения",
    )
    share_parser.add_argument(
        "--token",
        default=None,
        help="Токен провайдера",
    )
    share_parser.add_argument(
        "--token-env",
        default=None,
        help="Имя переменной окружения, из которой нужно прочитать токен",
    )
    share_parser.add_argument(
        "--data-dir",
        type=Path,
        default=None,
        help="Каталог для хранения бинарников и служебных файлов",
    )
    share_parser.add_argument(
        "--timeout",
        type=float,
        default=60.0,
        help="Таймаут ожидания публичного URL",
    )
    share_parser.add_argument(
        "--skip-install",
        action="store_true",
        help="Не выполнять install() перед запуском",
    )
    share_parser.add_argument(
        "--json",
        action="store_true",
        dest="as_json",
        help="Вывести диагностику в JSON",
    )
    share_parser.add_argument(
        "--verbose",
        action="store_true",
        help="Показывать логи провайдера в реальном времени",
    )

    return parser


def configure_logging(verbose: bool) -> None:
    """
    Настраивает логирование для CLI.

    По умолчанию библиотека не зашумляет stdout.
    Подробные логи включаются только флагом --verbose.
    """
    level = logging.INFO if verbose else logging.WARNING
    logging.basicConfig(level=level, format="%(message)s")


def resolve_token(provider_name: str, token: str | None, token_env: str | None) -> str | None:
    """
    Выбирает токен из аргументов и переменных окружения.

    Приоритет:
    1. --token
    2. --token-env
    3. стандартная переменная окружения провайдера
    """
    if token is not None:
        return token

    if token_env is not None:
        return os.getenv(token_env)

    provider_class = get_provider_class(provider_name)
    if provider_class.token_env_var is not None:
        return os.getenv(provider_class.token_env_var)

    return None


def print_providers(as_json: bool) -> int:
    """Печатает список провайдеров."""
    payload: list[dict[str, Any]] = []

    for name, provider_class in sorted(PROVIDERS.items()):
        payload.append(
            {
                "name": name,
                "experimental": provider_class.experimental,
                "requires_token": provider_class.requires_token,
                "token_env_var": provider_class.token_env_var,
            }
        )

    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    for item in payload:
        suffix = " [experimental]" if item["experimental"] else ""
        token_note = f", token: {item['token_env_var']}" if item["requires_token"] else ""
        print(f"{item['name']}{suffix}{token_note}")

    return 0


def print_human_summary(data: dict[str, Any]) -> None:
    """
    Печатает диагностическую информацию в человекочитаемом виде.
    """
    print(f"Provider: {data['provider']}")
    print(f"State: {data['state']}")

    public_url = data.get("public_url")
    if isinstance(public_url, str):
        print(f"URL: {public_url}")

    details = data.get("details")
    if isinstance(details, dict):
        for key in sorted(details):
            value = details[key]
            print(f"{key}: {value}")

    pid = data.get("pid")
    if isinstance(pid, int):
        print(f"PID: {pid}")


def run_share(args: argparse.Namespace) -> int:
    """
    Реализует команду share.

    Команда:
    - создает провайдер;
    - при необходимости выполняет install();
    - запускает туннель;
    - печатает URL и diagnostics;
    - держит процесс живым, пока пользователь не остановит его.
    """
    token = resolve_token(args.provider, args.token, args.token_env)
    tunnel = create_tunnel(
        args.provider,
        token=token,
        data_dir=args.data_dir,
    )

    try:
        if not args.skip_install:
            tunnel.install()

        session = tunnel.start(port=args.port, timeout=args.timeout)
        payload = session.as_dict()

        if args.as_json:
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            print_human_summary(payload)
            print("Press Ctrl+C to stop.")

        while tunnel.is_running:
            time.sleep(0.5)

        final_payload = tunnel.diagnostics
        if args.as_json:
            print(json.dumps(final_payload, ensure_ascii=False, indent=2), file=sys.stderr)
        else:
            print("Tunnel stopped.")

        return 0

    except KeyboardInterrupt:
        tunnel.stop()
        print("Tunnel stopped.")
        return 0
    finally:
        tunnel.stop()


def main() -> int:
    """
    Точка входа CLI.
    """
    parser = build_parser()
    args = parser.parse_args()

    verbose = getattr(args, "verbose", False)
    configure_logging(verbose)

    try:
        if args.command == "providers":
            return print_providers(as_json=args.as_json)

        if args.command == "share":
            return run_share(args)

        parser.error("Неизвестная команда.")
        return 2

    except WebTunnelError as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())