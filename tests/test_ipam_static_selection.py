from types import SimpleNamespace

from sync import ipam


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


def _reset_ipam_state():
    ipam._assigned_static_ips.clear()
    ipam._exhausted_static_prefixes.clear()
    ipam._unifi_dhcp_ranges.clear()
    with ipam._static_prefix_locks_lock:
        ipam._static_prefix_locks.clear()


def test_exhausted_prefix_is_cached_within_run(monkeypatch):
    _reset_ipam_state()

    prefix = SimpleNamespace(id=10, prefix="10.0.0.0/24")
    tenant = SimpleNamespace(id=1)

    ipam._unifi_dhcp_ranges["site-a"] = [ipam.ipaddress.ip_network("10.0.0.0/24")]

    calls = {"count": 0}

    def fake_get(*args, **kwargs):
        calls["count"] += 1
        return _FakeResponse(
            [{"address": f"10.0.0.{i}/24"} for i in range(2, 52)]
        )

    monkeypatch.setenv("NETBOX_URL", "https://netbox.example.com")
    monkeypatch.setenv("NETBOX_TOKEN", "token")
    monkeypatch.setattr(ipam.requests, "get", fake_get)
    monkeypatch.setattr(ipam, "ping_ip", lambda _: False)

    first = ipam.find_available_static_ip(None, prefix, None, tenant, unifi_device_ips=set())
    second = ipam.find_available_static_ip(None, prefix, None, tenant, unifi_device_ips=set())

    assert first is None
    assert second is None
    assert calls["count"] == 1


def test_finds_available_candidate_when_not_filtered(monkeypatch):
    _reset_ipam_state()

    prefix = SimpleNamespace(id=20, prefix="192.168.10.0/24")
    tenant = SimpleNamespace(id=1)

    def fake_get(*args, **kwargs):
        return _FakeResponse(
            [
                {"address": "192.168.10.10/24"},
                {"address": "192.168.10.11/24"},
            ]
        )

    monkeypatch.setenv("NETBOX_URL", "https://netbox.example.com")
    monkeypatch.setenv("NETBOX_TOKEN", "token")
    monkeypatch.setattr(ipam.requests, "get", fake_get)
    monkeypatch.setattr(ipam, "ping_ip", lambda _: False)

    result = ipam.find_available_static_ip(
        None,
        prefix,
        None,
        tenant,
        unifi_device_ips={"192.168.10.10"},
    )

    assert result == "192.168.10.11/24"
