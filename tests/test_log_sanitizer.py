import logging

from sync.log_sanitizer import REDACTED, SensitiveDataFormatter, redact_text


def test_redact_authorization_header_value():
    text = "Authorization: Bearer super-secret-token"
    redacted = redact_text(text)
    assert "super-secret-token" not in redacted
    assert f"Authorization: Bearer {REDACTED}" in redacted


def test_redact_token_in_mapping_style_text():
    text = "{'NETBOX_TOKEN': 'abc123', 'other': 'ok'}"
    redacted = redact_text(text)
    assert "'NETBOX_TOKEN': '[REDACTED]'" in redacted
    assert "'other': 'ok'" in redacted


def test_redact_query_parameters():
    text = "https://example.test/api?token=abc123&keep=value"
    redacted = redact_text(text)
    assert "token=abc123" not in redacted
    assert f"token={REDACTED}" in redacted
    assert "keep=value" in redacted


def test_redact_basic_auth_in_url():
    text = "Request to https://admin:supersecret@example.test/proxy/network"
    redacted = redact_text(text)
    assert "supersecret" not in redacted
    assert "https://admin:[REDACTED]@example.test/proxy/network" in redacted


def test_formatter_redacts_final_rendered_message():
    record = logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="Request failed: Authorization=Token abcd1234",
        args=(),
        exc_info=None,
    )
    formatter = SensitiveDataFormatter("%(levelname)s:%(message)s")
    rendered = formatter.format(record)
    assert "abcd1234" not in rendered
    assert f"Authorization=Token {REDACTED}" in rendered
