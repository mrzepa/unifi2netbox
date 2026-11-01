"""Log redaction helpers for secrets and credentials."""

from __future__ import annotations

import logging
import re

REDACTED = "[REDACTED]"

_REDACTION_RULES = (
    (
        re.compile(
            r"(?i)(\bAuthorization\b['\"]?\s*[:=]\s*['\"]?(?:Bearer|Token)\s+)([^'\",\s}]+)"
        ),
        rf"\1{REDACTED}",
    ),
    (
        re.compile(
            r"(?i)(\bX-API-KEY\b['\"]?\s*[:=]\s*['\"]?)([^'\",\s}]+)"
        ),
        rf"\1{REDACTED}",
    ),
    (
        re.compile(
            r"(?i)(['\"]?(?:NETBOX_TOKEN|UNIFI_API_KEY|UNIFI_PASSWORD|UNIFI_MFA_SECRET|API_KEY|ACCESS_TOKEN|TOKEN|PASSWORD|SECRET|CSRF_TOKEN|X_CSRF_TOKEN)['\"]?\s*[:=]\s*['\"]?)([^'\",&\s}]+)"
        ),
        rf"\1{REDACTED}",
    ),
    (
        re.compile(
            r"(?i)([?&](?:api_key|apikey|access_token|token|password|secret)=)([^&#\s]+)"
        ),
        rf"\1{REDACTED}",
    ),
    (
        re.compile(r"(?i)(https?://[^/\s:@]+:)([^@\s/]+)(@)"),
        rf"\1{REDACTED}\3",
    ),
)


def redact_text(value: str) -> str:
    """Redact likely secret values in an arbitrary text block."""
    if not value:
        return value

    redacted = value
    for pattern, replacement in _REDACTION_RULES:
        redacted = pattern.sub(replacement, redacted)
    return redacted


class SensitiveDataFormatter(logging.Formatter):
    """Formatter that redacts sensitive values from rendered log output."""

    def format(self, record: logging.LogRecord) -> str:
        rendered = super().format(record)
        return redact_text(rendered)
