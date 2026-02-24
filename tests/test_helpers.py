"""Tests for device helper functions in main.py."""
import os
from unittest.mock import patch

from main import (
    get_device_name,
    get_device_mac,
    get_device_ip,
    get_device_serial,
    extract_asset_tag,
)


# ---------------------------------------------------------------------------
#  get_device_name
# ---------------------------------------------------------------------------

class TestGetDeviceName:
    def test_uses_name_field(self, sample_device):
        assert get_device_name(sample_device) == "IT-SW01"

    def test_falls_back_to_hostname(self):
        assert get_device_name({"hostname": "sw-core"}) == "sw-core"

    def test_falls_back_to_macAddress(self):
        assert get_device_name({"macAddress": "AA:BB:CC:DD:EE:FF"}) == "AA:BB:CC:DD:EE:FF"

    def test_falls_back_to_mac(self):
        assert get_device_name({"mac": "aa:bb:cc:dd:ee:ff"}) == "aa:bb:cc:dd:ee:ff"

    def test_falls_back_to_id(self):
        assert get_device_name({"id": "uuid-123"}) == "uuid-123"

    def test_returns_unknown_when_empty(self):
        assert get_device_name({}) == "unknown-device"


# ---------------------------------------------------------------------------
#  get_device_mac / get_device_ip
# ---------------------------------------------------------------------------

class TestGetDeviceMac:
    def test_uses_mac(self):
        assert get_device_mac({"mac": "aa:bb:cc:dd:ee:ff"}) == "aa:bb:cc:dd:ee:ff"

    def test_falls_back_to_macAddress(self):
        assert get_device_mac({"macAddress": "11:22:33:44:55:66"}) == "11:22:33:44:55:66"

    def test_returns_none_when_missing(self):
        assert get_device_mac({}) is None


class TestGetDeviceIp:
    def test_uses_ip(self):
        assert get_device_ip({"ip": "10.0.0.1"}) == "10.0.0.1"

    def test_falls_back_to_ipAddress(self):
        assert get_device_ip({"ipAddress": "192.168.1.1"}) == "192.168.1.1"

    def test_returns_none_when_missing(self):
        assert get_device_ip({}) is None


# ---------------------------------------------------------------------------
#  get_device_serial
# ---------------------------------------------------------------------------

class TestGetDeviceSerial:
    @patch.dict(os.environ, {"NETBOX_SERIAL_MODE": "mac"}, clear=False)
    def test_mac_mode_uses_serial_first(self, sample_device):
        assert get_device_serial(sample_device) == "ABC123456"

    @patch.dict(os.environ, {"NETBOX_SERIAL_MODE": "mac"}, clear=False)
    def test_mac_mode_falls_back_to_mac(self):
        result = get_device_serial({"mac": "aa:bb:cc:dd:ee:ff"})
        assert result == "AABBCCDDEEFF"

    @patch.dict(os.environ, {"NETBOX_SERIAL_MODE": "mac"}, clear=False)
    def test_mac_mode_normalizes_mac(self):
        result = get_device_serial({"mac": "AA:BB:CC:DD:EE:FF"})
        assert result == "AABBCCDDEEFF"

    @patch.dict(os.environ, {"NETBOX_SERIAL_MODE": "unifi"}, clear=False)
    def test_unifi_mode_returns_serial_only(self, sample_device):
        assert get_device_serial(sample_device) == "ABC123456"

    @patch.dict(os.environ, {"NETBOX_SERIAL_MODE": "unifi"}, clear=False)
    def test_unifi_mode_no_fallback(self):
        assert get_device_serial({"mac": "aa:bb:cc:dd:ee:ff"}) is None

    @patch.dict(os.environ, {"NETBOX_SERIAL_MODE": "none"}, clear=False)
    def test_none_mode_returns_none(self, sample_device):
        assert get_device_serial(sample_device) is None

    @patch.dict(os.environ, {"NETBOX_SERIAL_MODE": "id"}, clear=False)
    def test_id_mode_falls_back_to_id(self):
        result = get_device_serial({"id": "dev-uuid-001"})
        assert result == "dev-uuid-001"


# ---------------------------------------------------------------------------
#  extract_asset_tag
# ---------------------------------------------------------------------------

class TestExtractAssetTag:
    def test_extracts_id_tag(self):
        assert extract_asset_tag("IT-AULA-AP02-ID3006") == "ID3006"

    def test_extracts_aid_tag(self):
        assert extract_asset_tag("SWITCH-AID1234") == "AID1234"

    def test_returns_none_when_no_tag(self):
        assert extract_asset_tag("my-switch-01") is None

    def test_returns_none_for_empty(self):
        assert extract_asset_tag("") is None

    def test_returns_none_for_none(self):
        assert extract_asset_tag(None) is None
