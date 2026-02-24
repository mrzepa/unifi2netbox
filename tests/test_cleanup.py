"""Tests for cleanup utility functions."""
import os
from unittest.mock import patch

from main import (
    _is_cleanup_enabled,
    _cleanup_stale_days,
    _sync_interval_seconds,
    _unifi_verify_ssl,
    _netbox_verify_ssl,
)


# ---------------------------------------------------------------------------
#  _is_cleanup_enabled
# ---------------------------------------------------------------------------

class TestIsCleanupEnabled:
    @patch.dict(os.environ, {"NETBOX_CLEANUP": "true"}, clear=False)
    def test_true(self):
        assert _is_cleanup_enabled() is True

    @patch.dict(os.environ, {"NETBOX_CLEANUP": "1"}, clear=False)
    def test_one(self):
        assert _is_cleanup_enabled() is True

    @patch.dict(os.environ, {"NETBOX_CLEANUP": "yes"}, clear=False)
    def test_yes(self):
        assert _is_cleanup_enabled() is True

    @patch.dict(os.environ, {"NETBOX_CLEANUP": "TRUE"}, clear=False)
    def test_case_insensitive(self):
        assert _is_cleanup_enabled() is True

    @patch.dict(os.environ, {"NETBOX_CLEANUP": "on"}, clear=False)
    def test_on(self):
        assert _is_cleanup_enabled() is True

    @patch.dict(os.environ, {"NETBOX_CLEANUP": "false"}, clear=False)
    def test_false(self):
        assert _is_cleanup_enabled() is False

    @patch.dict(os.environ, {"NETBOX_CLEANUP": "no"}, clear=False)
    def test_no(self):
        assert _is_cleanup_enabled() is False

    @patch.dict(os.environ, {}, clear=False)
    def test_default_is_false(self):
        os.environ.pop("NETBOX_CLEANUP", None)
        assert _is_cleanup_enabled() is False


# ---------------------------------------------------------------------------
#  _cleanup_stale_days
# ---------------------------------------------------------------------------

class TestCleanupStaleDays:
    @patch.dict(os.environ, {"CLEANUP_STALE_DAYS": "7"}, clear=False)
    def test_custom_value(self):
        assert _cleanup_stale_days() == 7

    @patch.dict(os.environ, {"CLEANUP_STALE_DAYS": "0"}, clear=False)
    def test_zero(self):
        assert _cleanup_stale_days() == 0

    @patch.dict(os.environ, {}, clear=False)
    def test_default_is_30(self):
        os.environ.pop("CLEANUP_STALE_DAYS", None)
        assert _cleanup_stale_days() == 30

    @patch.dict(os.environ, {"CLEANUP_STALE_DAYS": "not-a-number"}, clear=False)
    def test_invalid_returns_default(self):
        assert _cleanup_stale_days() == 30

    @patch.dict(os.environ, {"CLEANUP_STALE_DAYS": "-5"}, clear=False)
    def test_negative_returns_default(self):
        assert _cleanup_stale_days() == 30


class TestSyncIntervalSeconds:
    @patch.dict(os.environ, {"SYNC_INTERVAL": "600"}, clear=False)
    def test_valid_value(self):
        assert _sync_interval_seconds() == 600

    @patch.dict(os.environ, {"SYNC_INTERVAL": "invalid"}, clear=False)
    def test_invalid_returns_default(self):
        assert _sync_interval_seconds() == 0

    @patch.dict(os.environ, {"SYNC_INTERVAL": "-1"}, clear=False)
    def test_negative_returns_default(self):
        assert _sync_interval_seconds() == 0


class TestTlsVerifySettings:
    @patch.dict(os.environ, {}, clear=False)
    def test_defaults_true(self):
        os.environ.pop("UNIFI_VERIFY_SSL", None)
        os.environ.pop("NETBOX_VERIFY_SSL", None)
        assert _unifi_verify_ssl() is True
        assert _netbox_verify_ssl() is True

    @patch.dict(os.environ, {"UNIFI_VERIFY_SSL": "false", "NETBOX_VERIFY_SSL": "0"}, clear=False)
    def test_can_disable(self):
        assert _unifi_verify_ssl() is False
        assert _netbox_verify_ssl() is False


class TestRunNetboxCleanup:
    @patch.dict(os.environ, {"NETBOX_CLEANUP": "false"}, clear=False)
    def test_skips_when_disabled(self):
        import main

        with patch("main.cleanup_stale_devices") as stale, patch(
            "main.cleanup_orphan_interfaces"
        ) as ifaces, patch("main.cleanup_orphan_cables") as cables, patch(
            "main.cleanup_orphan_ips"
        ) as ips, patch(
            "main.cleanup_device_types"
        ) as dev_types:
            main.run_netbox_cleanup(
                nb=object(),
                nb_ubiquity=object(),
                tenant=object(),
                netbox_sites_dict={"site-a": type("Site", (), {"id": 1, "name": "A"})()},
                all_unifi_serials_by_site={1: {"ABC123"}},
            )

        stale.assert_not_called()
        ifaces.assert_not_called()
        cables.assert_not_called()
        ips.assert_not_called()
        dev_types.assert_not_called()

    @patch.dict(os.environ, {"NETBOX_CLEANUP": "true"}, clear=False)
    def test_runs_all_cleanup_steps_when_enabled(self):
        import main

        site = type("Site", (), {"id": 7, "name": "Site 7"})()
        with patch("main.cleanup_stale_devices") as stale, patch(
            "main.cleanup_orphan_interfaces"
        ) as ifaces, patch("main.cleanup_orphan_cables") as cables, patch(
            "main.cleanup_orphan_ips"
        ) as ips, patch(
            "main.cleanup_device_types"
        ) as dev_types:
            main.run_netbox_cleanup(
                nb="nb",
                nb_ubiquity="ubiq",
                tenant="tenant",
                netbox_sites_dict={"site-7": site},
                all_unifi_serials_by_site={7: {"SER1"}},
            )

        stale.assert_called_once_with("nb", site, "tenant", {"SER1"})
        ifaces.assert_called_once_with("nb", site, "tenant")
        cables.assert_called_once_with("nb", site)
        ips.assert_called_once_with("nb", "tenant")
        dev_types.assert_called_once_with("nb", "ubiq")
