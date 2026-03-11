from __future__ import annotations

import re
from pathlib import Path

from webtunnel.core import Tunnel, TunnelSession, default_data_dir, normalize_url, strip_ansi


class DummyTunnel(Tunnel):
    """Тестовый провайдер для проверки общей логики базового класса."""

    name = "dummy"

    def install(self) -> None:
        """Для тестового провайдера установка не требуется."""

    def _start(self, port: int, timeout: float) -> TunnelSession:
        raise NotImplementedError


def test_normalize_url_adds_https() -> None:
    assert normalize_url("example.com") == "https://example.com"


def test_normalize_url_keeps_existing_scheme() -> None:
    assert normalize_url("http://example.com") == "http://example.com"
    assert normalize_url("https://example.com") == "https://example.com"


def test_strip_ansi() -> None:
    raw = "\x1b[32mHello\x1b[0m"
    assert strip_ansi(raw) == "Hello"


def test_default_data_dir_for_kaggle(monkeypatch) -> None:
    monkeypatch.setenv("KAGGLE_KERNEL_RUN_TYPE", "Interactive")
    assert default_data_dir() == Path("/kaggle/working/.webtunnel")


def test_extract_url_from_line() -> None:
    tunnel = DummyTunnel()
    patterns = [re.compile(r"(https://example\.com)")]

    extracted = tunnel._extract_url_from_line(
        "Public URL: https://example.com",
        patterns,
    )

    assert extracted == "https://example.com"