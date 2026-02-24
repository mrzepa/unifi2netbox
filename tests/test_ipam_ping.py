from unittest.mock import patch

from sync import ipam


def test_ping_ip_rejects_invalid_ip_without_subprocess():
    with patch("sync.ipam.subprocess.run") as run_mock:
        assert ipam.ping_ip("not-an-ip") is False
        run_mock.assert_not_called()


def test_ping_ip_clamps_count_and_timeout():
    class _Result:
        returncode = 1

    with patch("sync.ipam.subprocess.run", return_value=_Result()) as run_mock:
        assert ipam.ping_ip("192.0.2.10", count=999, timeout=0) is False
        cmd = run_mock.call_args.args[0]
        assert cmd[:2] == ["ping", "-c"]
        assert cmd[2] == "5"
        assert cmd[3:5] == ["-W", "1"]
        assert cmd[5] == "192.0.2.10"
