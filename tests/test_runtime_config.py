"""Tests for runtime config env precedence and aliases."""
import os
from unittest.mock import patch

import main


class TestRuntimeConfig:
    @patch.dict(
        os.environ,
        {
            "NETBOX_IMPORT_TENANT": "Import Tenant",
            "NETBOX_TENANT": "Legacy Tenant",
        },
        clear=False,
    )
    def test_netbox_import_tenant_overrides_netbox_tenant(self):
        config = main.load_runtime_config(config_path="/tmp/does-not-exist.yaml")
        assert config["NETBOX"]["TENANT"] == "Import Tenant"

    @patch.dict(os.environ, {"NETBOX_TENANT": "Legacy Tenant"}, clear=False)
    def test_netbox_tenant_used_when_import_tenant_missing(self):
        os.environ.pop("NETBOX_IMPORT_TENANT", None)
        config = main.load_runtime_config(config_path="/tmp/does-not-exist.yaml")
        assert config["NETBOX"]["TENANT"] == "Legacy Tenant"

    @patch.dict(
        os.environ,
        {"NETBOX_IMPORT_TENANT": '"Quoted Tenant"'},
        clear=False,
    )
    def test_netbox_import_tenant_strips_wrapping_quotes(self):
        os.environ.pop("NETBOX_TENANT", None)
        config = main.load_runtime_config(config_path="/tmp/does-not-exist.yaml")
        assert config["NETBOX"]["TENANT"] == "Quoted Tenant"

    @patch.dict(
        os.environ,
        {"NETBOX_URL": '"https://netbox.example.com"'},
        clear=False,
    )
    def test_netbox_url_strips_wrapping_quotes(self):
        config = main.load_runtime_config(config_path="/tmp/does-not-exist.yaml")
        assert config["NETBOX"]["URL"] == "https://netbox.example.com"
