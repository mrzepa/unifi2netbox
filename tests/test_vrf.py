"""Tests for VRF selection and creation behavior."""
import os
from unittest.mock import patch

import pytest

import main


class FakeVRF:
    def __init__(self, vrf_id, name):
        self.id = vrf_id
        self.name = name


class FakeVRFEndpoint:
    def __init__(self, existing=None):
        self._items = list(existing or [])
        self.create_calls = 0

    def filter(self, **kwargs):
        name = kwargs.get("name")
        if name is None:
            return list(self._items)
        return [item for item in self._items if item.name == name]

    def all(self):
        return list(self._items)

    def create(self, payload):
        self.create_calls += 1
        vrf = FakeVRF(len(self._items) + 1, payload["name"])
        self._items.append(vrf)
        return vrf


class FakeIPAM:
    def __init__(self, vrf_endpoint):
        self.vrfs = vrf_endpoint


class FakeNB:
    def __init__(self, existing=None):
        self.ipam = FakeIPAM(FakeVRFEndpoint(existing=existing))


@pytest.fixture(autouse=True)
def reset_vrf_state():
    main.vrf_cache.clear()
    main.vrf_locks.clear()
    yield
    main.vrf_cache.clear()
    main.vrf_locks.clear()


class TestVRFHelpers:
    @patch.dict(os.environ, {"NETBOX_VRF_MODE": "create"}, clear=False)
    def test_create_mode_uses_site_name_without_prefix(self):
        nb = FakeNB()
        vrf, mode = main.get_vrf_for_site(nb, "  Vig   Festival  ")
        assert mode == "create"
        assert vrf is not None
        assert vrf.name == "Vig Festival"
        assert not vrf.name.lower().startswith("vrf_")

    def test_get_or_create_uses_cache_and_creates_once(self):
        nb = FakeNB()
        first = main.get_or_create_vrf(nb, "Vig Festival")
        second = main.get_or_create_vrf(nb, "vig festival")
        assert first is not None
        assert second is not None
        assert first.id == second.id
        assert nb.ipam.vrfs.create_calls == 1

    def test_existing_mode_finds_case_variant_without_create(self):
        existing = [FakeVRF(10, "Vig Festival")]
        nb = FakeNB(existing=existing)
        with patch.dict(os.environ, {"NETBOX_VRF_MODE": "existing"}, clear=False):
            vrf, mode = main.get_vrf_for_site(nb, "vig festival")
        assert mode == "existing"
        assert vrf is not None
        assert vrf.id == 10
        assert nb.ipam.vrfs.create_calls == 0

    @patch.dict(os.environ, {"NETBOX_VRF_MODE": "none"}, clear=False)
    def test_none_mode_returns_no_vrf(self):
        nb = FakeNB()
        vrf, mode = main.get_vrf_for_site(nb, "Vig Festival")
        assert mode == "none"
        assert vrf is None

    @patch.dict(
        os.environ,
        {"NETBOX_VRF_MODE": "create", "NETBOX_DEFAULT_VRF": "  Shared   VRF  "},
        clear=False,
    )
    def test_default_vrf_overrides_site_name_in_create_mode(self):
        nb = FakeNB()
        vrf, mode = main.get_vrf_for_site(nb, "Vig Festival")
        assert mode == "create"
        assert vrf is not None
        assert vrf.name == "Shared VRF"
        assert nb.ipam.vrfs.create_calls == 1

    @patch.dict(
        os.environ,
        {"NETBOX_VRF_MODE": "existing", "NETBOX_DEFAULT_VRF": "Shared VRF"},
        clear=False,
    )
    def test_default_vrf_uses_existing_vrf(self):
        nb = FakeNB(existing=[FakeVRF(42, "Shared VRF")])
        vrf, mode = main.get_vrf_for_site(nb, "Some Site")
        assert mode == "existing"
        assert vrf is not None
        assert vrf.id == 42
        assert nb.ipam.vrfs.create_calls == 0

    @patch.dict(
        os.environ,
        {"NETBOX_VRF_MODE": "existing", "NETBOX_DEFAULT_VRF": '"Shared VRF"'},
        clear=False,
    )
    def test_default_vrf_strips_wrapping_quotes(self):
        nb = FakeNB(existing=[FakeVRF(42, "Shared VRF")])
        vrf, mode = main.get_vrf_for_site(nb, "Some Site")
        assert mode == "existing"
        assert vrf is not None
        assert vrf.id == 42
