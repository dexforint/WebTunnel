from __future__ import annotations

import logging
from pathlib import Path

from .core import Tunnel
from .exceptions import WebTunnelError
from .providers import Cloudflared, LocalTunnel, NGROK, Pinggy, ZROK

PROVIDERS: dict[str, type[Tunnel]] = {
    "zrok": ZROK,
    "ngrok": NGROK,
    "localtunnel": LocalTunnel,
    "cloudflared": Cloudflared,
    "pinggy": Pinggy,
}


def get_provider_class(name: str) -> type[Tunnel]:
    """
    Возвращает класс провайдера по его имени.

    Имя нормализуется к нижнему регистру.
    Если провайдер неизвестен, выбрасывается WebTunnelError.
    """
    normalized = name.strip().lower()
    provider_class = PROVIDERS.get(normalized)

    if provider_class is None:
        available = ", ".join(sorted(PROVIDERS))
        raise WebTunnelError(
            f"Неизвестный провайдер '{name}'. Доступные провайдеры: {available}"
        )

    return provider_class


def create_tunnel(
    name: str,
    token: str | None = None,
    *,
    data_dir: Path | None = None,
    logger: logging.Logger | None = None,
) -> Tunnel:
    """
    Создает экземпляр провайдера по строковому имени.

    Это удобно, когда выбор провайдера приходит из конфигурации,
    переменной окружения или параметра ноутбука.
    """
    provider_class = get_provider_class(name)
    return provider_class(token=token, data_dir=data_dir, logger=logger)