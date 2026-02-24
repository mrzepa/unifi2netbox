"""Shared fixtures for unifi2netbox tests."""
import sys
import os
import pytest

# Ensure the project root is on sys.path so we can import main
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture
def sample_device():
    """A typical UniFi device dict."""
    return {
        "name": "IT-SW01",
        "model": "US48PRO",
        "mac": "aa:bb:cc:dd:ee:ff",
        "ip": "10.0.0.10",
        "serial": "ABC123456",
        "state": "CONNECTED",
    }


@pytest.fixture
def sample_device_minimal():
    """A device with minimal fields."""
    return {
        "macAddress": "11:22:33:44:55:66",
        "ipAddress": "192.168.1.1",
        "id": "device-uuid-001",
    }
