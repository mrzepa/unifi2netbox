"""Tests for gateway/DNS lookup and DHCP range extraction with network info."""
import ipaddress
import os
from unittest.mock import patch

import main


# ---------------------------------------------------------------------------
#  _get_network_info_for_ip
# ---------------------------------------------------------------------------

class TestGetNetworkInfoForIp:
    """Tests for the _get_network_info_for_ip helper."""

    def setup_method(self):
        main._unifi_network_info.clear()

    def teardown_method(self):
        main._unifi_network_info.clear()

    def test_returns_gateway_and_dns_for_matching_network(self):
        main._unifi_network_info["site1"] = [
            {
                "network": ipaddress.ip_network("10.0.0.0/24"),
                "gateway": "10.0.0.1",
                "dns": ["1.1.1.1", "8.8.8.8"],
                "name": "LAN",
            }
        ]
        gw, dns = main._get_network_info_for_ip("10.0.0.50")
        assert gw == "10.0.0.1"
        assert dns == ["1.1.1.1", "8.8.8.8"]

    def test_returns_correct_network_from_multiple(self):
        main._unifi_network_info["site1"] = [
            {
                "network": ipaddress.ip_network("10.0.0.0/24"),
                "gateway": "10.0.0.1",
                "dns": ["1.1.1.1"],
                "name": "LAN",
            },
            {
                "network": ipaddress.ip_network("192.168.1.0/24"),
                "gateway": "192.168.1.1",
                "dns": ["8.8.4.4"],
                "name": "VLAN10",
            },
        ]
        gw, dns = main._get_network_info_for_ip("192.168.1.100")
        assert gw == "192.168.1.1"
        assert dns == ["8.8.4.4"]

    def test_returns_none_for_unmatched_ip(self):
        main._unifi_network_info["site1"] = [
            {
                "network": ipaddress.ip_network("10.0.0.0/24"),
                "gateway": "10.0.0.1",
                "dns": ["1.1.1.1"],
                "name": "LAN",
            }
        ]
        gw, dns = main._get_network_info_for_ip("172.16.0.5")
        assert gw is None
        assert dns == []

    @patch.dict(os.environ, {"DEFAULT_GATEWAY": "172.16.0.1", "DEFAULT_DNS": "1.1.1.1,8.8.8.8"})
    def test_falls_back_to_env_vars(self):
        gw, dns = main._get_network_info_for_ip("172.16.0.5")
        assert gw == "172.16.0.1"
        assert dns == ["1.1.1.1", "8.8.8.8"]

    @patch.dict(os.environ, {"DEFAULT_GATEWAY": "10.0.0.1"}, clear=False)
    def test_env_gateway_only(self):
        os.environ.pop("DEFAULT_DNS", None)
        gw, dns = main._get_network_info_for_ip("172.16.0.5")
        assert gw == "10.0.0.1"
        assert dns == []

    @patch.dict(os.environ, {"DEFAULT_DNS": "8.8.8.8"}, clear=False)
    def test_env_dns_only(self):
        os.environ.pop("DEFAULT_GATEWAY", None)
        gw, dns = main._get_network_info_for_ip("172.16.0.5")
        assert gw is None
        assert dns == ["8.8.8.8"]

    def test_invalid_ip_returns_none(self):
        gw, dns = main._get_network_info_for_ip("not-an-ip")
        assert gw is None
        assert dns == []

    def test_empty_cache_no_env_returns_none(self):
        os.environ.pop("DEFAULT_GATEWAY", None)
        os.environ.pop("DEFAULT_DNS", None)
        gw, dns = main._get_network_info_for_ip("10.0.0.1")
        assert gw is None
        assert dns == []

    def test_searches_across_multiple_sites(self):
        main._unifi_network_info["site1"] = [
            {
                "network": ipaddress.ip_network("10.0.0.0/24"),
                "gateway": "10.0.0.1",
                "dns": ["1.1.1.1"],
                "name": "LAN",
            }
        ]
        main._unifi_network_info["site2"] = [
            {
                "network": ipaddress.ip_network("192.168.5.0/24"),
                "gateway": "192.168.5.1",
                "dns": ["9.9.9.9"],
                "name": "Remote LAN",
            }
        ]
        gw, dns = main._get_network_info_for_ip("192.168.5.50")
        assert gw == "192.168.5.1"
        assert dns == ["9.9.9.9"]

    def test_handles_network_without_gateway(self):
        main._unifi_network_info["site1"] = [
            {
                "network": ipaddress.ip_network("10.0.0.0/24"),
                "gateway": None,
                "dns": ["1.1.1.1"],
                "name": "LAN",
            }
        ]
        gw, dns = main._get_network_info_for_ip("10.0.0.50")
        assert gw is None
        assert dns == ["1.1.1.1"]


# ---------------------------------------------------------------------------
#  extract_dhcp_ranges_from_unifi – network info extraction
# ---------------------------------------------------------------------------

class FakeSiteObj:
    """Minimal site_obj mock for extract_dhcp_ranges_from_unifi."""

    def __init__(self, net_configs, site_id="site-abc"):
        self.id = site_id
        self._id = site_id
        self.network_conf = FakeNetworkConfEndpoint(net_configs)


class FakeNetworkConfEndpoint:
    def __init__(self, configs):
        self._configs = configs

    def all(self):
        return list(self._configs)


class TestExtractDhcpRangesNetworkInfo:
    """Tests that extract_dhcp_ranges_from_unifi populates _unifi_network_info."""

    def setup_method(self):
        main._unifi_network_info.clear()
        main._unifi_dhcp_ranges.clear()

    def teardown_method(self):
        main._unifi_network_info.clear()
        main._unifi_dhcp_ranges.clear()

    def test_stores_gateway_and_dns(self):
        configs = [
            {
                "name": "LAN",
                "dhcpd_enabled": True,
                "ip_subnet": "10.0.0.0/24",
                "gateway_ip": "10.0.0.1",
                "dhcpd_dns_1": "1.1.1.1",
                "dhcpd_dns_2": "8.8.8.8",
            }
        ]
        site = FakeSiteObj(configs, site_id="site-001")
        ranges = main.extract_dhcp_ranges_from_unifi(site)

        assert len(ranges) == 1
        assert str(ranges[0]) == "10.0.0.0/24"

        # Check network info was stored
        info = main._unifi_network_info.get("site-001")
        assert info is not None
        assert len(info) == 1
        assert info[0]["gateway"] == "10.0.0.1"
        assert info[0]["dns"] == ["1.1.1.1", "8.8.8.8"]

    def test_handles_missing_gateway_and_dns(self):
        configs = [
            {
                "name": "VLAN20",
                "dhcpd_enabled": True,
                "ip_subnet": "192.168.20.0/24",
            }
        ]
        site = FakeSiteObj(configs, site_id="site-002")
        ranges = main.extract_dhcp_ranges_from_unifi(site)

        assert len(ranges) == 1
        info = main._unifi_network_info.get("site-002")
        assert info is not None
        assert info[0]["gateway"] is None
        assert info[0]["dns"] == []

    def test_deduplicates_dns(self):
        configs = [
            {
                "name": "LAN",
                "dhcpd_enabled": True,
                "ip_subnet": "10.0.0.0/24",
                "gateway_ip": "10.0.0.1",
                "dhcpd_dns_1": "1.1.1.1",
                "dhcpd_dns_2": "1.1.1.1",
                "dhcpd_dns_3": "8.8.8.8",
            }
        ]
        site = FakeSiteObj(configs, site_id="site-003")
        main.extract_dhcp_ranges_from_unifi(site)

        info = main._unifi_network_info["site-003"]
        assert info[0]["dns"] == ["1.1.1.1", "8.8.8.8"]

    def test_multiple_networks(self):
        configs = [
            {
                "name": "LAN",
                "dhcpd_enabled": True,
                "ip_subnet": "10.0.0.0/24",
                "gateway_ip": "10.0.0.1",
                "dhcpd_dns_1": "1.1.1.1",
            },
            {
                "name": "Guest",
                "dhcpd_enabled": True,
                "ip_subnet": "192.168.100.0/24",
                "gateway_ip": "192.168.100.1",
                "dhcpd_dns_1": "8.8.8.8",
            },
            {
                "name": "Management",
                "dhcpd_enabled": False,
                "ip_subnet": "172.16.0.0/24",
            },
        ]
        site = FakeSiteObj(configs, site_id="site-004")
        ranges = main.extract_dhcp_ranges_from_unifi(site)

        # Only 2 DHCP-enabled networks
        assert len(ranges) == 2
        info = main._unifi_network_info["site-004"]
        assert len(info) == 2
        assert info[0]["name"] == "LAN"
        assert info[1]["name"] == "Guest"

    def test_skips_non_dhcp_networks(self):
        configs = [
            {
                "name": "Static VLAN",
                "dhcpd_enabled": False,
                "ip_subnet": "10.10.0.0/24",
                "gateway_ip": "10.10.0.1",
            }
        ]
        site = FakeSiteObj(configs, site_id="site-005")
        ranges = main.extract_dhcp_ranges_from_unifi(site)

        assert len(ranges) == 0
        assert "site-005" not in main._unifi_network_info

    def test_integration_api_field_names(self):
        """Test with camelCase field names used by Integration API."""
        configs = [
            {
                "name": "LAN",
                "dhcpdEnabled": True,
                "subnet": "10.0.0.0/24",
                "gateway": "10.0.0.1",
                "dhcpdDns1": "1.1.1.1",
                "dhcpdDns2": "8.8.8.8",
            }
        ]
        site = FakeSiteObj(configs, site_id="site-006")
        ranges = main.extract_dhcp_ranges_from_unifi(site)

        assert len(ranges) == 1
        info = main._unifi_network_info["site-006"]
        assert info[0]["gateway"] == "10.0.0.1"
        assert info[0]["dns"] == ["1.1.1.1", "8.8.8.8"]


# ---------------------------------------------------------------------------
#  is_ip_in_dhcp_range
# ---------------------------------------------------------------------------

class TestIsIpInDhcpRange:
    def setup_method(self):
        main._unifi_dhcp_ranges.clear()

    def teardown_method(self):
        main._unifi_dhcp_ranges.clear()

    @patch.dict(os.environ, {}, clear=False)
    def test_ip_in_range(self):
        os.environ.pop("DHCP_RANGES", None)
        main._unifi_dhcp_ranges["site1"] = [ipaddress.ip_network("10.0.0.0/24")]
        assert main.is_ip_in_dhcp_range("10.0.0.50") is True

    @patch.dict(os.environ, {}, clear=False)
    def test_ip_not_in_range(self):
        os.environ.pop("DHCP_RANGES", None)
        main._unifi_dhcp_ranges["site1"] = [ipaddress.ip_network("10.0.0.0/24")]
        assert main.is_ip_in_dhcp_range("192.168.1.1") is False

    def test_invalid_ip_returns_false(self):
        assert main.is_ip_in_dhcp_range("not-an-ip") is False

    @patch.dict(os.environ, {}, clear=False)
    def test_empty_ranges_returns_false(self):
        os.environ.pop("DHCP_RANGES", None)
        assert main.is_ip_in_dhcp_range("10.0.0.1") is False
