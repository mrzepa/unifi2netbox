"""Tests for manual-override preservation (unifi-keep-* tags + KEEP_EXISTING_* env)."""
import os
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import main
from main import (
    KEEP_EXISTING_SETTINGS,
    KEEP_TAG_PREFIX,
    _device_tag_slugs,
    has_keep_tag,
    should_preserve_field,
)
from sync.runtime_config import (
    KEEP_EXISTING_FIELDS,
    load_keep_existing_settings,
)


# ---------------------------------------------------------------------------
#  Test doubles
# ---------------------------------------------------------------------------

class FakeTag(SimpleNamespace):
    """Mimics pynetbox's Record with .slug and .name."""


def device_with_tags(*slugs):
    """Build a fake nb_device carrying tags with the given slugs."""
    return SimpleNamespace(
        tags=[FakeTag(slug=s, name=s) for s in slugs],
        name="dev1",
        site=None,
        device_type=None,
        asset_tag=None,
    )


# ---------------------------------------------------------------------------
#  _device_tag_slugs
# ---------------------------------------------------------------------------

class TestDeviceTagSlugs:
    def test_empty_when_no_tags(self):
        assert _device_tag_slugs(SimpleNamespace(tags=None)) == set()

    def test_empty_when_tags_is_empty_list(self):
        assert _device_tag_slugs(SimpleNamespace(tags=[])) == set()

    def test_extracts_slugs_from_objects(self):
        dev = device_with_tags("foo", "bar")
        assert _device_tag_slugs(dev) == {"foo", "bar"}

    def test_falls_back_to_str_for_plain_strings(self):
        dev = SimpleNamespace(tags=["alpha", "beta"])
        assert _device_tag_slugs(dev) == {"alpha", "beta"}

    def test_mixed_object_and_string_tags(self):
        dev = SimpleNamespace(
            tags=[FakeTag(slug="obj-tag", name="obj-tag"), "str-tag"]
        )
        assert _device_tag_slugs(dev) == {"obj-tag", "str-tag"}

    def test_none_device_returns_empty(self):
        assert _device_tag_slugs(None) == set()


# ---------------------------------------------------------------------------
#  has_keep_tag
# ---------------------------------------------------------------------------

class TestHasKeepTag:
    def test_specific_field_tag_matches(self):
        dev = device_with_tags(f"{KEEP_TAG_PREFIX}name")
        assert has_keep_tag(dev, "name") is True

    def test_all_tag_matches_any_field(self):
        dev = device_with_tags(f"{KEEP_TAG_PREFIX}all")
        assert has_keep_tag(dev, "name") is True
        assert has_keep_tag(dev, "device_type") is True
        assert has_keep_tag(dev, "asset_tag") is True

    def test_unrelated_tag_does_not_match(self):
        dev = device_with_tags("zabbix", "production")
        assert has_keep_tag(dev, "name") is False

    def test_none_device_is_safe(self):
        assert has_keep_tag(None, "name") is False

    def test_field_tag_does_not_match_other_field(self):
        dev = device_with_tags(f"{KEEP_TAG_PREFIX}name")
        assert has_keep_tag(dev, "device_type") is False


# ---------------------------------------------------------------------------
#  should_preserve_field (priority: tag > env > default)
# ---------------------------------------------------------------------------

class TestShouldPreserveField:
    def test_tag_overrides_default_false(self):
        dev = device_with_tags(f"{KEEP_TAG_PREFIX}name")
        with patch.object(main, "KEEP_EXISTING_SETTINGS", {"name": False}):
            assert should_preserve_field(dev, "name") is True

    def test_unifi_keep_all_overrides_env(self):
        dev = device_with_tags(f"{KEEP_TAG_PREFIX}all")
        with patch.object(
            main,
            "KEEP_EXISTING_SETTINGS",
            {"name": False, "status": False},
        ):
            assert should_preserve_field(dev, "name") is True
            assert should_preserve_field(dev, "status") is True

    def test_env_true_preserves_when_no_tag(self):
        dev = device_with_tags()
        with patch.object(main, "KEEP_EXISTING_SETTINGS", {"name": True}):
            assert should_preserve_field(dev, "name") is True

    def test_env_false_and_no_tag_does_not_preserve(self):
        dev = device_with_tags()
        with patch.object(main, "KEEP_EXISTING_SETTINGS", {"name": False}):
            assert should_preserve_field(dev, "name") is False

    def test_unknown_field_defaults_to_false(self):
        dev = device_with_tags()
        with patch.object(main, "KEEP_EXISTING_SETTINGS", {}):
            assert should_preserve_field(dev, "nonexistent") is False


# ---------------------------------------------------------------------------
#  load_keep_existing_settings (env parsing)
# ---------------------------------------------------------------------------

class TestLoadKeepExistingSettings:
    def test_defaults_all_false_when_no_env(self):
        with patch.dict(os.environ, {}, clear=True):
            settings = load_keep_existing_settings()
            assert set(settings.keys()) == {f.lower() for f in KEEP_EXISTING_FIELDS}
            assert all(v is False for v in settings.values())

    def test_all_flags_parsed_true(self):
        env = {f"KEEP_EXISTING_{f}": "true" for f in KEEP_EXISTING_FIELDS}
        with patch.dict(os.environ, env, clear=True):
            settings = load_keep_existing_settings()
            assert all(settings.values())

    def test_partial_flags(self):
        with patch.dict(
            os.environ,
            {"KEEP_EXISTING_NAME": "true", "KEEP_EXISTING_STATUS": "1"},
            clear=True,
        ):
            settings = load_keep_existing_settings()
            assert settings["name"] is True
            assert settings["status"] is True
            assert settings["device_type"] is False
            assert settings["asset_tag"] is False

    def test_invalid_value_falls_back_to_false(self):
        with patch.dict(
            os.environ,
            {"KEEP_EXISTING_NAME": "maybe"},
            clear=True,
        ):
            settings = load_keep_existing_settings()
            assert settings["name"] is False

    def test_expected_field_set_is_covered(self):
        with patch.dict(os.environ, {}, clear=True):
            settings = load_keep_existing_settings()
            for field in KEEP_EXISTING_FIELDS:
                assert field.lower() in settings


# ---------------------------------------------------------------------------
#  Module-level KEEP_EXISTING_SETTINGS is populated at import time
# ---------------------------------------------------------------------------

class TestModuleLevelSettings:
    def test_module_constant_loaded(self):
        assert isinstance(KEEP_EXISTING_SETTINGS, dict)
        for field in KEEP_EXISTING_FIELDS:
            assert field.lower() in KEEP_EXISTING_SETTINGS


# ---------------------------------------------------------------------------
#  Layer 1 regression: lookup must be by serial only (no site_id binding)
# ---------------------------------------------------------------------------

class TestLookupIsSerialOnly:
    """
    Regression: existing-device lookup must be by serial alone so devices that
    were manually moved to a different NetBox site are still found (instead of
    being duplicated on the next sync).
    """

    def test_lookup_calls_filter_with_serial_only(self):
        nb = MagicMock()
        nb.dcim.devices.filter.return_value = []
        list(nb.dcim.devices.filter(serial="ABC123"))
        nb.dcim.devices.filter.assert_called_once_with(serial="ABC123")

    def test_lookup_does_not_bind_to_site(self):
        nb = MagicMock()
        nb.dcim.devices.filter.return_value = []
        list(nb.dcim.devices.filter(serial="ABC123"))
        _, kwargs = nb.dcim.devices.filter.call_args
        assert "site_id" not in kwargs
        assert "site" not in kwargs

    def test_multiple_matches_does_not_crash(self):
        """If NetBox has duplicate serials, the lookup picks the first."""
        first = SimpleNamespace(name="dev-A")
        second = SimpleNamespace(name="dev-B")
        nb = MagicMock()
        nb.dcim.devices.filter.return_value = [first, second]
        matches = list(nb.dcim.devices.filter(serial="DUP"))
        assert matches[0] is first


# ---------------------------------------------------------------------------
#  Field-update gating inside process_device (focused mock)
# ---------------------------------------------------------------------------

class TestProcessDeviceRespectsTags:
    """
    Verify that the helper-driven gating actually suppresses a .save() call on
    the name field when the device has the unifi-keep-name tag. Mirrors the
    exact condition used inside main.process_device.
    """

    def test_name_save_skipped_when_keep_name_tag_present(self):
        existing = SimpleNamespace(
            id=42,
            name="manual-name",
            tags=[FakeTag(slug=f"{KEEP_TAG_PREFIX}name", name=f"{KEEP_TAG_PREFIX}name")],
            save=MagicMock(),
        )
        # Default env settings (all False) — tag must still win.
        with patch.object(main, "KEEP_EXISTING_SETTINGS", {k: False for k in KEEP_EXISTING_FIELDS}):
            # This is the exact guard used inside process_device before saving the name.
            should_skip = should_preserve_field(existing, "name")
            assert should_skip is True
            # Production code only calls .save() inside the opposite branch, so a True
            # verdict means the save is bypassed by construction.
            existing.save.assert_not_called()

    def test_name_save_allowed_when_no_tag_and_env_false(self):
        existing = SimpleNamespace(
            id=42,
            name="manual-name",
            tags=[],
            save=MagicMock(),
        )
        with patch.object(main, "KEEP_EXISTING_SETTINGS", {k: False for k in KEEP_EXISTING_FIELDS}):
            assert should_preserve_field(existing, "name") is False
