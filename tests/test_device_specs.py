"""Tests for community device specs integration."""
from unittest.mock import patch
import pytest

from main import (
    _load_community_specs,
    _lookup_community_specs,
    _resolve_device_specs,
)


# ---------------------------------------------------------------------------
#  Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def reset_community_specs():
    """Reset the cached community specs between tests."""
    import main
    main._community_specs = None
    yield
    main._community_specs = None


@pytest.fixture
def sample_community_json():
    """Minimal community specs JSON structure."""
    return {
        "by_part": {
            "USW-PRO-48-POE": {
                "model_name": "UniFi Switch 48 Pro PoE",
                "slug": "ubiquiti-usw-pro-48-poe",
                "part_number": "USW-PRO-48-POE",
                "u_height": 1,
                "is_full_depth": False,
                "airflow": "front-to-rear",
                "weight": 4.5,
                "weight_unit": "kg",
                "interfaces": [
                    {"name": "Port 1", "type": "1000base-t", "poe_mode": "pse", "poe_type": "type2-ieee802.3at"},
                ],
                "console_ports": [
                    {"name": "Console", "type": "rj-45"},
                ],
                "power_ports": [
                    {"name": "PS1", "type": "iec-60320-c14", "maximum_draw": 600},
                ],
            },
            "UAP-AC-LITE": {
                "model_name": "UniFi AP AC Lite",
                "slug": "ubiquiti-uap-ac-lite",
                "part_number": "UAP-AC-LITE",
                "u_height": 0,
                "interfaces": [
                    {"name": "eth0", "type": "1000base-t"},
                ],
            },
        },
        "by_model": {
            "UniFi Switch 48 Pro PoE": {
                "model_name": "UniFi Switch 48 Pro PoE",
                "slug": "ubiquiti-usw-pro-48-poe",
                "part_number": "USW-PRO-48-POE",
                "u_height": 1,
            },
        },
    }


# ---------------------------------------------------------------------------
#  _load_community_specs
# ---------------------------------------------------------------------------

class TestLoadCommunitySpecs:
    def test_loads_from_json_file(self):
        """Should load the actual bundled JSON file."""
        specs = _load_community_specs()
        assert "by_part" in specs
        assert "by_model" in specs
        assert len(specs["by_part"]) > 0

    def test_caches_result(self):
        """Second call returns cached result (no file I/O)."""
        first = _load_community_specs()
        second = _load_community_specs()
        assert first is second

    def test_missing_file_returns_empty(self):
        """Should return empty dicts if file doesn't exist."""
        import main
        with patch("os.path.exists", return_value=False):
            main._community_specs = None
            specs = _load_community_specs()
            assert specs == {"by_part": {}, "by_model": {}}


# ---------------------------------------------------------------------------
#  _lookup_community_specs
# ---------------------------------------------------------------------------

class TestLookupCommunitySpecs:
    def test_exact_part_number(self, sample_community_json):
        import main
        main._community_specs = sample_community_json
        result = _lookup_community_specs(part_number="USW-PRO-48-POE")
        assert result is not None
        assert result["slug"] == "ubiquiti-usw-pro-48-poe"

    def test_case_insensitive_part_number(self, sample_community_json):
        import main
        main._community_specs = sample_community_json
        result = _lookup_community_specs(part_number="usw-pro-48-poe")
        assert result is not None
        assert result["slug"] == "ubiquiti-usw-pro-48-poe"

    def test_mixed_case_part_number(self, sample_community_json):
        """The actual case: hardcoded has 'USW-Pro-48-PoE', community has 'USW-PRO-48-POE'."""
        import main
        main._community_specs = sample_community_json
        result = _lookup_community_specs(part_number="USW-Pro-48-PoE")
        assert result is not None

    def test_model_name_lookup(self, sample_community_json):
        import main
        main._community_specs = sample_community_json
        result = _lookup_community_specs(model="UniFi Switch 48 Pro PoE")
        assert result is not None

    def test_returns_none_for_unknown(self, sample_community_json):
        import main
        main._community_specs = sample_community_json
        result = _lookup_community_specs(part_number="NONEXISTENT-MODEL")
        assert result is None

    def test_returns_none_when_no_args(self, sample_community_json):
        import main
        main._community_specs = sample_community_json
        result = _lookup_community_specs()
        assert result is None


# ---------------------------------------------------------------------------
#  _resolve_device_specs
# ---------------------------------------------------------------------------

class TestResolveDeviceSpecs:
    def test_known_hardcoded_model(self, sample_community_json):
        """US48PRO has hardcoded specs with part_number USW-Pro-48-PoE."""
        import main
        main._community_specs = sample_community_json
        result = _resolve_device_specs("US48PRO")
        assert result is not None
        # Hardcoded values should be present
        assert "ports" in result
        assert result["poe_budget"] == 600
        # Community values should be merged in
        assert result.get("interfaces") is not None or result.get("console_ports") is not None

    def test_unknown_model_returns_none(self, sample_community_json):
        import main
        main._community_specs = sample_community_json
        result = _resolve_device_specs("TOTALLY-UNKNOWN-XYZ")
        assert result is None

    def test_hardcoded_overrides_community(self, sample_community_json):
        """Hardcoded values should win over community values."""
        import main
        main._community_specs = sample_community_json
        result = _resolve_device_specs("US48PRO")
        if result:
            # Hardcoded has part_number "USW-Pro-48-PoE" (not "USW-PRO-48-POE")
            assert result["part_number"] == "USW-Pro-48-PoE"

    def test_community_only_model(self, sample_community_json):
        """A model only in community (not in UNIFI_MODEL_SPECS) can still be found via part_number=model fallback."""
        import main
        main._community_specs = sample_community_json
        # UAP-AC-LITE is both a hardcoded part_number AND a community key
        result = _resolve_device_specs("UAP-AC-Lite")
        assert result is not None
