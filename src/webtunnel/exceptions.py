from __future__ import annotations


class WebTunnelError(Exception):
    """Базовая ошибка библиотеки."""


class InstallError(WebTunnelError):
    """Ошибка установки или подготовки провайдера туннеля."""


class TunnelStartError(WebTunnelError):
    """Ошибка запуска туннеля или получения публичного URL."""