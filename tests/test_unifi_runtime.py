"""Runtime behavior tests for UniFi client security/retry settings."""
import json
import os
from unittest.mock import patch

from unifi.unifi import Unifi


class _FakeResponse:
    def __init__(self, status_code=200, payload=None, text="{}", reason="OK", headers=None):
        self.status_code = status_code
        self._payload = payload if payload is not None else {}
        self.text = text
        self.reason = reason
        self.headers = headers or {}
        self.request = type("Req", (), {"path_url": "/sites"})()

    def json(self):
        return self._payload


def _build_unifi(monkeypatch, **env):
    for key in ("UNIFI_VERIFY_SSL", "UNIFI_PERSIST_SESSION"):
        monkeypatch.delenv(key, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)

    with patch.object(Unifi, "load_session_from_file", return_value=None), patch.object(
        Unifi, "configure_integration_api", return_value=True
    ), patch.object(Unifi, "get_sites", return_value={}):
        return Unifi("https://controller.example.com", api_key="api-key")


def test_unifi_ssl_verify_defaults_to_true(monkeypatch):
    unifi = _build_unifi(monkeypatch)
    assert unifi.verify_ssl is True
    assert unifi.persist_session is True


def test_unifi_ssl_verify_can_be_disabled(monkeypatch):
    unifi = _build_unifi(
        monkeypatch,
        UNIFI_VERIFY_SSL="false",
        UNIFI_PERSIST_SESSION="false",
    )
    assert unifi.verify_ssl is False
    assert unifi.persist_session is False


def test_integration_request_uses_unifi_verify_ssl(monkeypatch):
    unifi = _build_unifi(monkeypatch, UNIFI_VERIFY_SSL="false")
    unifi.integration_api_base = "https://controller.example.com/proxy/network/integration/v1"
    unifi.integration_auth_headers = {"X-API-KEY": "api-key"}

    fake_response = _FakeResponse(payload={"data": []})
    with patch.object(unifi.session, "request", return_value=fake_response) as req_mock:
        response = unifi._make_request_integration("/sites", "GET")

    assert response == {"data": []}
    assert req_mock.call_count == 1
    assert req_mock.call_args.kwargs["verify"] is False


def test_session_cache_does_not_persist_auth_headers(monkeypatch, tmp_path):
    unifi = _build_unifi(monkeypatch)
    session_file = tmp_path / "unifi_session.json"
    monkeypatch.setattr(Unifi, "SESSION_FILE", str(session_file))
    unifi.integration_auth_headers = {"X-API-KEY": "super-secret"}
    unifi.save_session_to_file()

    with open(session_file, "r", encoding="utf-8") as handle:
        data = json.load(handle)
    stored = data[unifi.base_url]
    assert "integration_auth_headers" not in stored

    mode = os.stat(session_file).st_mode & 0o777
    assert mode & 0o077 == 0


def test_load_session_tightens_file_permissions(monkeypatch, tmp_path):
    unifi = _build_unifi(monkeypatch)
    session_file = tmp_path / "unifi_session.json"
    monkeypatch.setattr(Unifi, "SESSION_FILE", str(session_file))

    payload = {
        unifi.base_url: {
            "cookies": {"TOKEN": "cookie"},
            "csrf_token": "csrf",
            "auth_mode": "api_key",
            "api_prefix": "/proxy/network/api/s/default",
            "api_style": "integration",
            "integration_api_base": "https://controller.example.com/proxy/network/integration/v1",
        }
    }
    with open(session_file, "w", encoding="utf-8") as handle:
        json.dump(payload, handle)
    os.chmod(session_file, 0o644)

    unifi.load_session_from_file()

    mode = os.stat(session_file).st_mode & 0o777
    assert mode & 0o077 == 0


def test_integration_request_retries_on_transient_status(monkeypatch):
    unifi = _build_unifi(monkeypatch)
    unifi.integration_api_base = "https://controller.example.com/proxy/network/integration/v1"
    unifi.integration_auth_headers = {"X-API-KEY": "api-key"}

    first = _FakeResponse(status_code=429, payload={"message": "rate limited"}, text="rate limited")
    second = _FakeResponse(status_code=200, payload={"data": [{"id": "site-1"}]})
    with patch.object(unifi.session, "request", side_effect=[first, second]) as req_mock, patch(
        "unifi.unifi.time.sleep", return_value=None
    ):
        response = unifi._make_request_integration("/sites", "GET", max_retries=1)

    assert req_mock.call_count == 2
    assert response == {"data": [{"id": "site-1"}]}


def test_falls_back_to_legacy_when_integration_probe_fails(monkeypatch):
    for key in ("UNIFI_VERIFY_SSL", "UNIFI_PERSIST_SESSION"):
        monkeypatch.delenv(key, raising=False)

    with patch.object(Unifi, "load_session_from_file", return_value=None), patch.object(
        Unifi, "configure_integration_api", return_value=False
    ), patch.object(Unifi, "authenticate", return_value=None) as auth_mock, patch.object(
        Unifi, "get_sites", return_value={}
    ):
        unifi = Unifi(
            "https://controller.example.com",
            username="admin",
            password="secret",
            api_key="not-valid-for-legacy",
        )

    assert unifi.api_style == "legacy"
    assert auth_mock.call_count == 1
