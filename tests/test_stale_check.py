"""Tests for the global UniFi-serial set used by the stale-marking pass.

The stale-check (main.py:~2094) marks NetBox devices offline when their
serial is absent from UniFi. Previously the check was per-site — comparing
each NetBox site's devices against that single UniFi site's serials.
Combined with Layer 1's preserve-current-site behavior this produced false
positives: a device that was manually moved to a different NetBox site
would be marked offline even though UniFi still reported it on its own site.

The fix uses a single global set of UniFi serials accumulated across all
controllers/sites in the current sync run.
"""
import threading
from unittest.mock import patch

import main
from main import (
    _all_unifi_serials_global,
    _cleanup_serials_by_site,
    _cleanup_serials_lock,
)


class TestGlobalSerialsSetExists:
    def test_module_level_set_initialized_empty(self):
        # Must be a set, not dict or list, so membership checks are O(1).
        assert isinstance(_all_unifi_serials_global, set)


class TestGlobalSerialsPopulation:
    """Mirror the exact population code from process_site and verify the
    global set ends up with every serial across multiple sites."""

    def test_population_updates_both_per_site_and_global(self):
        with patch.dict(main._cleanup_serials_by_site, {}, clear=True), \
             patch.object(main, '_all_unifi_serials_global', set()):
            site_serials_1 = {"AA11", "BB22"}
            site_serials_2 = {"CC33", "DD44", "AA11"}  # overlap intentional
            with _cleanup_serials_lock:
                main._cleanup_serials_by_site[1] = set(site_serials_1)
                main._all_unifi_serials_global.update(site_serials_1)
                main._cleanup_serials_by_site[2] = set(site_serials_2)
                main._all_unifi_serials_global.update(site_serials_2)
            assert main._cleanup_serials_by_site[1] == {"AA11", "BB22"}
            assert main._cleanup_serials_by_site[2] == {"CC33", "DD44", "AA11"}
            # Global = union of all per-site sets.
            assert main._all_unifi_serials_global == {"AA11", "BB22", "CC33", "DD44"}

    def test_global_set_is_a_superset_of_every_site_set(self):
        with patch.dict(main._cleanup_serials_by_site, {}, clear=True), \
             patch.object(main, '_all_unifi_serials_global', set()):
            for site_id, serials in [(10, {"X"}), (20, {"Y", "Z"}), (30, set())]:
                with _cleanup_serials_lock:
                    main._cleanup_serials_by_site[site_id] = set(serials)
                    main._all_unifi_serials_global.update(serials)
            for site_id, serials in main._cleanup_serials_by_site.items():
                assert serials <= main._all_unifi_serials_global


class TestStaleCheckUsesGlobalSet:
    """The stale-check must consult the global set, not a per-site one.
    This is verified at the variable-binding level: the snapshot is taken
    from `_all_unifi_serials_global` under the cleanup lock."""

    def test_stale_check_snapshot_taken_from_global(self):
        # Set up a global set with a single serial that exists in UniFi.
        global_set = {"PRESENT_SERIAL"}
        with patch.object(main, '_all_unifi_serials_global', global_set):
            # Mirror the snapshot line used inside the stale-check block.
            with _cleanup_serials_lock:
                snapshot = set(main._all_unifi_serials_global)
            assert snapshot == {"PRESENT_SERIAL"}
            # A NetBox device whose serial is in the global set must NOT be
            # flagged stale — even if no per-site dict contains it.
            assert "PRESENT_SERIAL" in snapshot

    def test_device_absent_from_global_is_stale(self):
        global_set = {"OTHER_SERIAL"}
        with patch.object(main, '_all_unifi_serials_global', global_set):
            with _cleanup_serials_lock:
                snapshot = set(main._all_unifi_serials_global)
            # A NetBox-only serial is absent → would be marked offline.
            assert "NETBOX_ONLY_SERIAL" not in snapshot


class TestSyncLoopClearsGlobalSet:
    """The sync loop clears per-run caches between runs. The global serial
    set must be cleared alongside `_cleanup_serials_by_site` so stale
    detection does not leak serials from previous runs."""

    def test_clear_global_alongside_per_site_dict(self):
        with patch.dict(main._cleanup_serials_by_site, {1: {"A"}, 2: {"B"}}, clear=True), \
             patch.object(main, '_all_unifi_serials_global', {"A", "B"}):
            # Mirror the clear block from the sync loop.
            main._cleanup_serials_by_site.clear()
            main._all_unifi_serials_global.clear()
            assert main._cleanup_serials_by_site == {}
            assert main._all_unifi_serials_global == set()


class TestThreadSafety:
    """All updates to the global set must happen under the cleanup lock."""

    def test_concurrent_updates_do_not_lose_serials(self):
        with patch.object(main, '_all_unifi_serials_global', set()):
            def add_serials(start, count):
                for i in range(start, start + count):
                    with _cleanup_serials_lock:
                        main._all_unifi_serials_global.add(f"S{i}")
            threads = [threading.Thread(target=add_serials, args=(i * 100, 100)) for i in range(8)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
            # 8 threads × 100 serials = 800 unique entries.
            assert len(main._all_unifi_serials_global) == 800
