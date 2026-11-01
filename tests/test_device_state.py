"""Tests for device state normalization and AP inference (model-based fallback)."""
from main import is_access_point_device, normalize_device_state


# ---------------------------------------------------------------------------
#  normalize_device_state
# ---------------------------------------------------------------------------

class TestNormalizeDeviceStateInt:
    """UniFi legacy API sends state as int: 0=offline, 1=online."""

    def test_int_zero_is_disconnected(self):
        assert normalize_device_state({"state": 0}) == "DISCONNECTED"

    def test_int_one_is_connected(self):
        assert normalize_device_state({"state": 1}) == "CONNECTED"

    def test_int_other_value_passes_through_as_string(self):
        # Unknown int codes get stringified, not silently dropped.
        assert normalize_device_state({"state": 2}) == "2"

    def test_int_zero_does_not_trigger_or_empty_bug(self):
        """Regression: previously `(0 or "")` evaluated to empty string and the
        `.upper()` call masked the offline state, so offline devices were not
        detected as offline."""
        result = normalize_device_state({"state": 0})
        assert result == "DISCONNECTED"
        assert result != ""


class TestNormalizeDeviceStateString:
    def test_string_connected(self):
        assert normalize_device_state({"state": "connected"}) == "CONNECTED"

    def test_string_disconnected(self):
        assert normalize_device_state({"state": "disconnected"}) == "DISCONNECTED"

    def test_uppercases_arbitrary_string(self):
        assert normalize_device_state({"state": "pending"}) == "PENDING"

    def test_string_online_via_status_fallback(self):
        # If `state` is missing, fall back to `status` field.
        assert normalize_device_state({"status": "online"}) == "ONLINE"


class TestNormalizeDeviceStateEdgeCases:
    def test_missing_state_and_status_returns_empty(self):
        assert normalize_device_state({}) == ""

    def test_none_state_falls_back_to_status(self):
        assert normalize_device_state({"state": None, "status": "connected"}) == "CONNECTED"

    def test_none_state_and_empty_status_returns_empty(self):
        # `None or "" or ""` previously produced "" — must stay "".
        assert normalize_device_state({"state": None, "status": ""}) == ""

    def test_bool_true_is_connected(self):
        # bool is a subclass of int; ensure we handle it explicitly.
        assert normalize_device_state({"state": True}) == "CONNECTED"

    def test_bool_false_is_disconnected(self):
        assert normalize_device_state({"state": False}) == "DISCONNECTED"

    def test_int_one_does_not_raise_attributeerror(self):
        """Regression: `(1 or "").upper()` used to raise AttributeError because
        `1` is truthy and has no `.upper()` method. This crashed processing of
        every connected device in the legacy UniFi API path."""
        # Must not raise.
        result = normalize_device_state({"state": 1})
        assert result == "CONNECTED"


# ---------------------------------------------------------------------------
#  is_access_point_device — model-based fallback for offline devices
# ---------------------------------------------------------------------------

class TestIsAccessPointModelFallback:
    """When `is_access_point` flag is absent (typical for offline devices in
    legacy UniFi API), APs should still be recognized by model prefix."""

    def test_uap_prefix_treated_as_ap(self):
        assert is_access_point_device({"model": "UAP6MP"}) is True

    def test_u7lr_treated_as_ap(self):
        # U7LR is UniFi AC-LR-PRO — an AP.
        assert is_access_point_device({"model": "U7LR"}) is True

    def test_u7pg2_treated_as_ap(self):
        # U7PG2 is UniFi AC Pro — an AP.
        assert is_access_point_device({"model": "U7PG2"}) is True

    def test_u6_prefix_treated_as_ap(self):
        assert is_access_point_device({"model": "U6MP"}) is True

    def test_bz2_prefix_treated_as_ap(self):
        # BZ2 is the original UAP-AC form factor.
        assert is_access_point_device({"model": "BZ2"}) is True


class TestIsAccessPointModelFallbackExclusions:
    """Switches and routers must NOT match the AP fallback even if they share
    a similar prefix."""

    def test_u7s_switch_not_ap(self):
        assert is_access_point_device({"model": "U7S150"}) is False

    def test_u6s_switch_not_ap(self):
        assert is_access_point_device({"model": "U6S200"}) is False

    def test_usw_switch_not_ap(self):
        assert is_access_point_device({"model": "USW48PRO"}) is False

    def test_us_prefix_switch_not_ap(self):
        assert is_access_point_device({"model": "US24"}) is False

    def test_usg_gateway_not_ap(self):
        assert is_access_point_device({"model": "USG4"}) is False

    def test_udm_gateway_not_ap(self):
        assert is_access_point_device({"model": "UDM"}) is False

    def test_uxg_gateway_not_ap(self):
        assert is_access_point_device({"model": "UXG"}) is False

    def test_udr_router_not_ap(self):
        assert is_access_point_device({"model": "UDR"}) is False

    def test_uck_cloud_key_not_ap(self):
        assert is_access_point_device({"model": "UCK"}) is False

    def test_empty_model_returns_false(self):
        assert is_access_point_device({"model": ""}) is False

    def test_missing_model_returns_false(self):
        assert is_access_point_device({}) is False

    def test_unrelated_model_returns_false(self):
        assert is_access_point_device({"model": "DS-1016+"}) is False


class TestIsAccessPointExplicitSignalsStillWork:
    """The new model fallback must not break existing detection paths."""

    def test_is_access_point_true_wins_over_unknown_model(self):
        assert is_access_point_device({"is_access_point": True, "model": "UNKNOWN"}) is True

    def test_is_access_point_false_wins_over_ap_model_prefix(self):
        # Explicit flag takes precedence over the fallback heuristic.
        assert is_access_point_device({"is_access_point": False, "model": "UAP6MP"}) is False

    def test_features_access_point(self):
        assert is_access_point_device({"features": ["accessPoint"]}) is True

    def test_features_access_point_dict(self):
        assert is_access_point_device({"features": {"accessPoint": {}}}) is True

    def test_interfaces_with_radios(self):
        assert is_access_point_device({"interfaces": {"radios": [1]}}) is True

    def test_interfaces_list_with_radio_entry(self):
        assert is_access_point_device(
            {"interfaces": [{"name": "radio0", "band": "5g"}]}
        ) is True
