from __future__ import annotations

from webtunnel import Cloudflared, LocalTunnel, ZROK, create_tunnel, get_provider_class


def test_get_provider_class_returns_expected_class() -> None:
    assert get_provider_class("cloudflared") is Cloudflared
    assert get_provider_class("zrok") is ZROK


def test_create_tunnel_builds_requested_provider() -> None:
    tunnel = create_tunnel("zrok", token="secret-token")

    assert isinstance(tunnel, ZROK)
    assert tunnel.token == "secret-token"


def test_localtunnel_is_marked_experimental() -> None:
    assert LocalTunnel.experimental is True