import json
import logging
import os
import secrets
import threading
import time
import warnings
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

import pyotp
import requests
from urllib3.exceptions import InsecureRequestWarning

from .sites import Sites

file_lock = threading.Lock()

# Suppress only the InsecureRequestWarning
warnings.simplefilter("ignore", InsecureRequestWarning)

logger = logging.getLogger(__name__)
_JITTER_RANDOM = secrets.SystemRandom()


class Unifi:
    """
    Handles interactions with UniFi API for both:
    - Integration API v1 (API key)
    - Legacy / UniFi OS session login (username/password)
    """

    SESSION_FILE = os.path.expanduser("~/.unifi_session.json")
    DEFAULT_TIMEOUT = 15
    DEFAULT_HTTP_RETRIES = 3
    RETRY_BACKOFF_BASE = 1.0
    RETRY_BACKOFF_MAX = 30.0
    RETRYABLE_STATUS_CODES = {408, 425, 429, 500, 502, 503, 504}
    _session_data = {}

    AUTH_MODES = (
        {
            "name": "unifi_os",
            "login_endpoint": "/api/auth/login",
            "api_prefix": "/proxy/network",
        },
        {
            "name": "legacy",
            "login_endpoint": "/api/login",
            "api_prefix": "",
        },
    )

    def __init__(
        self,
        base_url=None,
        username=None,
        password=None,
        mfa_secret=None,
        api_key=None,
        api_key_header=None,
    ):
        logger.debug(f"Initializing UniFi connection to: {base_url}")
        self.base_url = base_url.rstrip("/") if base_url else base_url
        self.username = username
        self.password = password
        self.mfa_secret = mfa_secret
        self.api_key = api_key
        self.api_key_header = api_key_header

        self.session = requests.Session()
        self.csrf_token = None
        self.auth_mode = None
        self.api_prefix = ""

        self.api_style = None  # "integration" or "legacy"
        self.integration_api_base = None
        self.integration_auth_headers = {}
        self.verify_ssl = self._read_env_bool("UNIFI_VERIFY_SSL", True)
        self.persist_session = self._read_env_bool("UNIFI_PERSIST_SESSION", True)
        self.request_timeout = self._read_env_int(
            "UNIFI_REQUEST_TIMEOUT",
            self.DEFAULT_TIMEOUT,
            minimum=1,
        )
        self.default_max_retries = self._read_env_int(
            "UNIFI_HTTP_RETRIES",
            self.DEFAULT_HTTP_RETRIES,
            minimum=0,
        )
        self.retry_backoff_base = self._read_env_float(
            "UNIFI_RETRY_BACKOFF_BASE",
            self.RETRY_BACKOFF_BASE,
            minimum=0.1,
        )
        self.retry_backoff_max = self._read_env_float(
            "UNIFI_RETRY_BACKOFF_MAX",
            self.RETRY_BACKOFF_MAX,
            minimum=self.retry_backoff_base,
        )

        if not self.base_url:
            raise ValueError("Missing required configuration: UniFi base URL")

        logger.debug("Loading session from file")
        self.load_session_from_file()

        # Prefer Integration API when API key is provided.
        if self.api_key and self.configure_integration_api():
            self.api_style = "integration"
            logger.info(
                f"Using UniFi Integration API at {self.integration_api_base}"
            )
        else:
            if self.api_key:
                logger.warning(
                    "UNIFI_API_KEY provided but Integration API could not be validated. "
                    "Falling back to session-based login."
                )

            if not all([self.username, self.password]):
                raise ValueError(
                    "Missing credentials. Provide UNIFI_API_KEY or UNIFI_USERNAME + UNIFI_PASSWORD"
                )

            self.api_style = "legacy"
            logger.debug("Authenticating with UniFi controller via session login")
            self.authenticate()

        logger.debug("Fetching sites from UniFi controller")
        self.sites = self.get_sites()
        logger.debug(f"Initialized UniFi connection with {len(self.sites)} sites")

    def _parse_response_json(self, response):
        """Parse JSON from a response and return None for non-JSON bodies."""
        try:
            return response.json()
        except (ValueError, json.JSONDecodeError):
            return None

    @staticmethod
    def _read_env_bool(name, default):
        raw_value = os.getenv(name)
        if raw_value is None or raw_value == "":
            return default
        value = str(raw_value).strip().lower()
        if value in {"1", "true", "yes", "on"}:
            return True
        if value in {"0", "false", "no", "off"}:
            return False
        logger.warning(
            f"Invalid boolean value for {name}: {raw_value}. Using default {default}."
        )
        return default

    @staticmethod
    def _read_env_int(name, default, minimum=0):
        raw_value = os.getenv(name)
        if raw_value is None or raw_value == "":
            return default
        try:
            value = int(raw_value)
        except ValueError:
            logger.warning(
                f"Invalid integer value for {name}: {raw_value}. Using default {default}."
            )
            return default
        if value < minimum:
            logger.warning(
                f"Value for {name} must be >= {minimum}. Using default {default}."
            )
            return default
        return value

    @staticmethod
    def _read_env_float(name, default, minimum=0.0):
        raw_value = os.getenv(name)
        if raw_value is None or raw_value == "":
            return default
        try:
            value = float(raw_value)
        except ValueError:
            logger.warning(
                f"Invalid float value for {name}: {raw_value}. Using default {default}."
            )
            return default
        if value < minimum:
            logger.warning(
                f"Value for {name} must be >= {minimum}. Using default {default}."
            )
            return default
        return value

    def _effective_retries(self, max_retries):
        if max_retries is None:
            return self.default_max_retries
        try:
            return max(0, int(max_retries))
        except (TypeError, ValueError):
            logger.warning(
                f"Invalid max_retries value '{max_retries}'. "
                f"Using default {self.default_max_retries}."
            )
            return self.default_max_retries

    def _parse_retry_after_seconds(self, response):
        retry_after = response.headers.get("Retry-After")
        if not retry_after:
            return None
        retry_after = retry_after.strip()
        if retry_after.isdigit():
            return max(0.0, float(retry_after))
        try:
            retry_at = parsedate_to_datetime(retry_after)
        except (TypeError, ValueError):
            return None
        if retry_at.tzinfo is None:
            retry_at = retry_at.replace(tzinfo=timezone.utc)
        now = datetime.now(tz=retry_at.tzinfo)
        return max(0.0, (retry_at - now).total_seconds())

    def _compute_retry_delay_seconds(self, attempt_number, response=None):
        retry_after_seconds = None
        if response is not None:
            retry_after_seconds = self._parse_retry_after_seconds(response)
        if retry_after_seconds is not None:
            return min(self.retry_backoff_max, retry_after_seconds)
        base_delay = self.retry_backoff_base * (2 ** max(0, attempt_number))
        delay = min(self.retry_backoff_max, base_delay)
        jitter = delay * _JITTER_RANDOM.uniform(0.0, 0.25)
        return min(self.retry_backoff_max, delay + jitter)

    @staticmethod
    def _extract_request_path(response):
        request = getattr(response, "request", None)
        if request is None:
            return None
        return getattr(request, "path_url", None)

    def _build_error_payload(self, response, response_data=None):
        payload = {
            "statusCode": response.status_code,
            "statusName": response.reason,
            "requestPath": self._extract_request_path(response),
            "requestId": response.headers.get("X-Request-ID")
            or response.headers.get("x-request-id"),
        }

        if isinstance(response_data, dict):
            meta = response_data.get("meta")
            meta_message = meta.get("msg") if isinstance(meta, dict) else None
            if isinstance(response_data.get("statusCode"), int):
                payload["statusCode"] = response_data.get("statusCode")
            payload["statusName"] = (
                response_data.get("statusName")
                or payload.get("statusName")
            )
            payload["code"] = response_data.get("code")
            payload["message"] = (
                response_data.get("message")
                or meta_message
                or response.text
            )
            payload["timestamp"] = response_data.get("timestamp")
            payload["requestPath"] = (
                response_data.get("requestPath")
                or payload.get("requestPath")
            )
            payload["requestId"] = (
                response_data.get("requestId")
                or payload.get("requestId")
            )
        else:
            payload["message"] = response.text

        return {
            key: value
            for key, value in payload.items()
            if value is not None and value != ""
        }

    @staticmethod
    def _log_http_error(log_prefix, method, url, error_payload):
        status_code = error_payload.get("statusCode")
        error_code = error_payload.get("code")
        message = error_payload.get("message")
        request_id = error_payload.get("requestId")
        logger.error(
            f"{log_prefix} {method} {url} -> status={status_code} "
            f"code={error_code or 'n/a'} message={message or 'n/a'} "
            f"requestId={request_id or 'n/a'}"
        )

    @staticmethod
    def _normalize_success_response(response, response_data):
        if response_data is not None:
            return response_data
        if response.status_code == 204 or not response.text.strip():
            return {}
        return None

    def _build_api_url(self, endpoint):
        """Build URL for legacy APIs (session/cookie auth)."""
        if endpoint.startswith("http://") or endpoint.startswith("https://"):
            return endpoint
        normalized_endpoint = endpoint if endpoint.startswith("/") else f"/{endpoint}"
        if self.api_prefix and not normalized_endpoint.startswith(self.api_prefix):
            normalized_endpoint = f"{self.api_prefix}{normalized_endpoint}"
        return f"{self.base_url}{normalized_endpoint}"

    def _build_integration_url(self, endpoint):
        """Build URL for Integration API v1 based on discovered integration base path."""
        if endpoint.startswith("http://") or endpoint.startswith("https://"):
            return endpoint

        normalized = endpoint if endpoint.startswith("/") else f"/{endpoint}"

        # Accept either "/sites" style or "/v1/sites" style.
        if normalized.startswith("/proxy/network/integration/v1"):
            normalized = normalized[len("/proxy/network/integration/v1") :]
        elif normalized.startswith("/integration/v1"):
            normalized = normalized[len("/integration/v1") :]
        elif normalized.startswith("/v1"):
            normalized = normalized[len("/v1") :]

        if not normalized:
            normalized = "/"

        return f"{self.integration_api_base}{normalized}"

    def _get_auth_mode_candidates(self):
        """Prioritize the previously working auth mode to reduce retries."""
        modes = list(self.AUTH_MODES)
        if self.auth_mode:
            modes.sort(key=lambda mode: mode["name"] != self.auth_mode)
        return modes

    def _build_login_payload(self):
        """Build login payload with optional 2FA token."""
        payload = {
            "username": self.username,
            "password": self.password,
        }
        otp = None
        if self.mfa_secret:
            otp = pyotp.TOTP(self.mfa_secret)
            payload["ubic_2fa_token"] = otp.now()
        return payload, otp

    def _wait_for_next_totp(self, otp):
        """Wait for the next TOTP code to avoid immediate retry failures."""
        if not otp:
            return
        time_remaining = otp.interval - (int(time.time()) % otp.interval)
        logger.warning(
            f"Invalid 2FA token detected. Next token available in {time_remaining}s."
        )
        if time_remaining > 0:
            time.sleep(time_remaining)
        logger.info("Retrying UniFi authentication with next 2FA token.")

    def _refresh_session_metadata(self, response=None):
        """Refresh auth metadata from session cookies and response headers."""
        if response:
            self.csrf_token = (
                response.headers.get("X-CSRF-Token")
                or response.headers.get("x-csrf-token")
                or self.session.cookies.get("csrf_token")
                or self.csrf_token
            )

    def _integration_base_candidates(self):
        if "/integration/v1" in self.base_url:
            return [self.base_url]
        return [
            f"{self.base_url}/proxy/network/integration/v1",
            f"{self.base_url}/integration/v1",
        ]

    def _integration_header_candidates(self):
        if not self.api_key:
            return []

        candidates = []

        if self.api_key_header:
            header = self.api_key_header.strip()
            if header.lower() == "authorization":
                candidates.append({"Authorization": f"Bearer {self.api_key}"})
                candidates.append({"Authorization": f"Token {self.api_key}"})
                candidates.append({"Authorization": self.api_key})
            else:
                candidates.append({header: self.api_key})

        candidates.extend(
            [
                {"X-API-KEY": self.api_key},
                {"X-Api-Key": self.api_key},
                {"Authorization": f"Bearer {self.api_key}"},
                {"Authorization": f"Token {self.api_key}"},
                {"Authorization": self.api_key},
            ]
        )

        unique = []
        seen = set()
        for item in candidates:
            signature = tuple(sorted(item.items()))
            if signature not in seen:
                seen.add(signature)
                unique.append(item)
        return unique

    def configure_integration_api(self):
        """Detect working Integration API base URL + auth header format."""
        if not self.api_key:
            return False

        for base in self._integration_base_candidates():
            for auth_headers in self._integration_header_candidates():
                headers = {
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                    **auth_headers,
                }

                # Probe /info first (lightweight endpoint)
                info_url = f"{base}/info"
                try:
                    response = self.session.get(
                        info_url,
                        headers=headers,
                        verify=self.verify_ssl,
                        timeout=self.request_timeout,
                    )
                except requests.exceptions.RequestException as err:
                    logger.debug(
                        f"Integration probe failed for {info_url} with headers {list(auth_headers.keys())}: {err}"
                    )
                    continue

                response_data = self._parse_response_json(response)
                if response.status_code < 400 and isinstance(response_data, dict):
                    if response_data.get("applicationVersion"):
                        self.integration_api_base = base
                        self.integration_auth_headers = auth_headers
                        logger.debug(
                            f"Integration API validated via /info at {base} using {list(auth_headers.keys())}"
                        )
                        return True

                # Fallback probe: /sites
                sites_url = f"{base}/sites"
                try:
                    sites_response = self.session.get(
                        sites_url,
                        headers=headers,
                        params={"offset": 0, "limit": 1},
                        verify=self.verify_ssl,
                        timeout=self.request_timeout,
                    )
                except requests.exceptions.RequestException:
                    continue

                sites_data = self._parse_response_json(sites_response)
                if sites_response.status_code < 400 and isinstance(sites_data, dict):
                    if isinstance(sites_data.get("data"), list):
                        self.integration_api_base = base
                        self.integration_auth_headers = auth_headers
                        logger.debug(
                            f"Integration API validated via /sites at {base} using {list(auth_headers.keys())}"
                        )
                        return True

        return False

    def save_session_to_file(self):
        """Save session data to file, grouped by base_url."""
        if not self.persist_session:
            logger.debug("UniFi session persistence disabled (UNIFI_PERSIST_SESSION=false).")
            return
        logger.debug(f"Saving session data for {self.base_url}")
        self._session_data[self.base_url] = {
            "cookies": self.session.cookies.get_dict(),
            "csrf_token": self.csrf_token,
            "auth_mode": self.auth_mode,
            "api_prefix": self.api_prefix,
            "api_style": self.api_style,
            "integration_api_base": self.integration_api_base,
        }
        with file_lock:
            logger.debug(f"Acquired file lock for {self.SESSION_FILE}")
            fd = os.open(
                self.SESSION_FILE,
                os.O_WRONLY | os.O_CREAT | os.O_TRUNC,
                0o600,
            )
            with os.fdopen(fd, "w") as f:
                json.dump(self._session_data, f)
            try:
                os.chmod(self.SESSION_FILE, 0o600)
            except OSError as err:
                logger.warning(f"Could not enforce session file permissions: {err}")
            logger.info(f"Session data for {self.base_url} saved to file.")

    def load_session_from_file(self):
        """Load session data from file for the current base_url."""
        if not self.persist_session:
            logger.debug("UniFi session persistence disabled (UNIFI_PERSIST_SESSION=false).")
            return
        logger.debug(f"Checking for session file at {self.SESSION_FILE}")
        if not os.path.exists(self.SESSION_FILE):
            logger.debug("No session file found, will authenticate from scratch")
            return

        try:
            file_mode = os.stat(self.SESSION_FILE).st_mode & 0o777
            if file_mode & 0o077:
                logger.warning(
                    f"UniFi session file {self.SESSION_FILE} permissions are too open ({oct(file_mode)})."
                )
                try:
                    os.chmod(self.SESSION_FILE, 0o600)
                    logger.info(
                        f"Tightened UniFi session file permissions for {self.SESSION_FILE} to 0o600."
                    )
                except OSError as chmod_err:
                    logger.warning(
                        f"Could not tighten UniFi session file permissions for {self.SESSION_FILE}: {chmod_err}"
                    )
        except OSError as err:
            logger.debug(
                f"Could not stat UniFi session file permissions for {self.SESSION_FILE}: {err}"
            )

        try:
            with open(self.SESSION_FILE, "r") as f:
                self._session_data = json.load(f)
        except (json.JSONDecodeError, OSError) as err:
            logger.warning(
                f"Failed to load existing UniFi session cache, ignoring it: {err}"
            )
            self._session_data = {}
            return

        session_info = self._session_data.get(self.base_url)
        if not session_info:
            logger.debug(f"No session data found for {self.base_url}")
            return

        logger.debug(f"Found session data for {self.base_url}")
        cookies = session_info.get("cookies", {})
        if isinstance(cookies, dict):
            self.session.cookies.update(cookies)
        self.csrf_token = session_info.get("csrf_token")
        self.auth_mode = session_info.get("auth_mode")
        self.api_prefix = session_info.get("api_prefix", "")

        cached_style = session_info.get("api_style")
        if cached_style in {"legacy", "integration"}:
            self.api_style = cached_style
        self.integration_api_base = session_info.get("integration_api_base")
        self._refresh_session_metadata()
        logger.info(f"Loaded session data for {self.base_url} from file.")

    def authenticate(self, retry_count=0, max_retries=3):
        """Log in and prepare an authenticated legacy session."""
        logger.debug(f"Authentication attempt {retry_count + 1}/{max_retries + 1}")
        if retry_count >= max_retries:
            logger.error("Max authentication retries reached. Aborting authentication.")
            raise Exception("Authentication failed after maximum retries.")

        payload, otp = self._build_login_payload()
        auth_errors = []

        for mode in self._get_auth_mode_candidates():
            login_url = f"{self.base_url}{mode['login_endpoint']}"
            logger.debug(f"Trying auth mode '{mode['name']}' via {login_url}")

            try:
                response = self.session.post(
                    login_url,
                    json=payload,
                    verify=self.verify_ssl,
                    timeout=self.request_timeout,
                )
            except requests.exceptions.RequestException as err:
                logger.warning(f"Auth mode '{mode['name']}' request failed: {err}")
                auth_errors.append(f"{mode['name']}: request error ({err})")
                continue

            response_data = self._parse_response_json(response) or {}
            meta = response_data.get("meta", {}) if isinstance(response_data, dict) else {}
            msg = meta.get("msg")
            rc = meta.get("rc")
            self._refresh_session_metadata(response)

            if rc == "ok" or (
                response.ok and bool(self.session.cookies.get_dict())
            ):
                self.auth_mode = mode["name"]
                self.api_prefix = mode["api_prefix"]
                self._refresh_session_metadata(response)
                self.save_session_to_file()
                logger.info(
                    f"Logged in successfully using auth mode '{self.auth_mode}'."
                )
                return

            if msg == "api.err.Invalid2FAToken":
                logger.warning("Invalid 2FA token detected.")
                self._wait_for_next_totp(otp)
                return self.authenticate(
                    retry_count=retry_count + 1, max_retries=max_retries
                )

            if msg == "api.err.Invalid":
                logger.error("Login failed: invalid credentials.")
                raise ValueError("UniFi authentication failed: invalid credentials.")

            if response.status_code in (404, 405):
                logger.debug(
                    f"Auth mode '{mode['name']}' unavailable (status {response.status_code})."
                )
                auth_errors.append(
                    f"{mode['name']}: endpoint unavailable ({response.status_code})"
                )
                continue

            auth_errors.append(
                f"{mode['name']}: login failed (status={response.status_code}, msg={msg})"
            )

        logger.error("UniFi authentication failed for all auth modes.")
        raise Exception(
            "Authentication failed. "
            + ("; ".join(auth_errors) if auth_errors else "No auth mode succeeded.")
        )

    def _make_request_legacy(self, endpoint, method="GET", data=None, params=None, retry_count=0, max_retries=None):
        retries = self._effective_retries(max_retries)
        auth_max_attempts = max(1, retries + 1)
        method_upper = method.upper()
        url = self._build_api_url(endpoint)
        logger.debug(f"Making legacy {method_upper} request to: {url}")

        for attempt in range(retry_count, retries + 1):
            if not self.session.cookies.get_dict():
                logger.info("No valid session cookies present. Authenticating...")
                try:
                    self.authenticate(max_retries=auth_max_attempts)
                except Exception as err:
                    return {
                        "statusCode": 401,
                        "code": "api.authentication.failed",
                        "message": str(err),
                    }

            headers = {"Content-Type": "application/json"}
            if self.csrf_token:
                headers["X-CSRF-Token"] = self.csrf_token

            request_kwargs = {
                "headers": headers,
                "verify": self.verify_ssl,
                "timeout": self.request_timeout,
                "params": params,
            }
            if data is not None and method_upper in {"POST", "PUT", "PATCH"}:
                request_kwargs["json"] = data

            try:
                response = self.session.request(method_upper, url, **request_kwargs)
            except requests.exceptions.RequestException as err:
                if attempt < retries:
                    delay = self._compute_retry_delay_seconds(attempt)
                    logger.warning(
                        f"Legacy request exception ({method_upper} {url}): {err}. "
                        f"Retrying in {delay:.1f}s ({attempt + 1}/{retries})."
                    )
                    time.sleep(delay)
                    continue
                logger.error(f"Legacy request exception: {err}")
                logger.debug(f"Request failed: {method_upper} {url}", exc_info=True)
                return {
                    "statusCode": 0,
                    "code": "request.exception",
                    "message": str(err),
                }

            self._refresh_session_metadata(response)
            response_data = self._parse_response_json(response)
            logger.debug(f"Response status code: {response.status_code}")

            if response.status_code == 401 and attempt < retries:
                logger.warning("Session expired or unauthorized. Re-authenticating...")
                try:
                    self.authenticate(max_retries=auth_max_attempts)
                except Exception as err:
                    return {
                        "statusCode": 401,
                        "code": "api.authentication.failed",
                        "message": str(err),
                    }
                continue

            if response.status_code in self.RETRYABLE_STATUS_CODES and attempt < retries:
                delay = self._compute_retry_delay_seconds(attempt, response=response)
                logger.warning(
                    f"Legacy request got transient status {response.status_code} "
                    f"for {method_upper} {url}. Retrying in {delay:.1f}s "
                    f"({attempt + 1}/{retries})."
                )
                time.sleep(delay)
                continue

            if response.status_code >= 400:
                error_payload = self._build_error_payload(response, response_data)
                self._log_http_error("Legacy request failed:", method_upper, url, error_payload)
                return error_payload

            normalized_response = self._normalize_success_response(response, response_data)
            if normalized_response is None:
                logger.error("Received non-JSON response from UniFi API.")
                return {
                    "statusCode": response.status_code,
                    "code": "api.response.invalid-json",
                    "message": "Received non-JSON response from UniFi API",
                }
            if isinstance(normalized_response, dict):
                logger.debug(
                    f"Request successful, response keys: {list(normalized_response.keys())}"
                )
            return normalized_response

        return {
            "statusCode": 503,
            "code": "api.request.retries-exhausted",
            "message": f"Legacy request failed after {retries + 1} attempts",
        }

    def _make_request_integration(self, endpoint, method="GET", data=None, params=None, retry_count=0, max_retries=None):
        retries = self._effective_retries(max_retries)
        if not self.integration_api_base:
            if not self.configure_integration_api():
                logger.error("Integration API is not configured.")
                return {
                    "statusCode": 401,
                    "code": "api.authentication.missing-credentials",
                    "message": "Integration API not configured",
                }

        method_upper = method.upper()
        url = self._build_integration_url(endpoint)
        logger.debug(f"Making integration {method_upper} request to: {url}")
        reconfigured = False

        for attempt in range(retry_count, retries + 1):
            headers = {
                "Accept": "application/json",
                "Content-Type": "application/json",
                **self.integration_auth_headers,
            }
            request_kwargs = {
                "headers": headers,
                "verify": self.verify_ssl,
                "timeout": self.request_timeout,
                "params": params,
            }
            if data is not None and method_upper in {"POST", "PUT", "PATCH"}:
                request_kwargs["json"] = data

            try:
                response = self.session.request(method_upper, url, **request_kwargs)
            except requests.exceptions.RequestException as err:
                if attempt < retries:
                    delay = self._compute_retry_delay_seconds(attempt)
                    logger.warning(
                        f"Integration request exception ({method_upper} {url}): {err}. "
                        f"Retrying in {delay:.1f}s ({attempt + 1}/{retries})."
                    )
                    time.sleep(delay)
                    continue
                logger.error(f"Integration request exception: {err}")
                logger.debug(f"Request failed: {method_upper} {url}", exc_info=True)
                return {
                    "statusCode": 0,
                    "code": "request.exception",
                    "message": str(err),
                }

            response_data = self._parse_response_json(response)

            if response.status_code == 401 and attempt < retries:
                # Retry header/base detection once in case auth header format changed.
                if not reconfigured and self.configure_integration_api():
                    reconfigured = True
                    continue

            if response.status_code in self.RETRYABLE_STATUS_CODES and attempt < retries:
                delay = self._compute_retry_delay_seconds(attempt, response=response)
                logger.warning(
                    f"Integration request got transient status {response.status_code} "
                    f"for {method_upper} {url}. Retrying in {delay:.1f}s "
                    f"({attempt + 1}/{retries})."
                )
                time.sleep(delay)
                continue

            if response.status_code >= 400:
                error_payload = self._build_error_payload(response, response_data)
                self._log_http_error("Integration request failed:", method_upper, url, error_payload)
                return error_payload

            normalized_response = self._normalize_success_response(response, response_data)
            if normalized_response is None:
                logger.error("Received non-JSON response from Integration API.")
                return {
                    "statusCode": response.status_code,
                    "code": "api.response.invalid-json",
                    "message": "Received non-JSON response from Integration API",
                }
            return normalized_response

        return {
            "statusCode": 503,
            "code": "api.request.retries-exhausted",
            "message": f"Integration request failed after {retries + 1} attempts",
        }

    def make_request(self, endpoint, method="GET", data=None, params=None, retry_count=0, max_retries=None):
        """Make an authenticated request to the selected UniFi API style."""
        logger.debug(f"API request ({self.api_style}): {method} {endpoint}")
        if self.api_style == "integration":
            return self._make_request_integration(
                endpoint,
                method=method,
                data=data,
                params=params,
                retry_count=retry_count,
                max_retries=max_retries,
            )
        return self._make_request_legacy(
            endpoint,
            method=method,
            data=data,
            params=params,
            retry_count=retry_count,
            max_retries=max_retries,
        )

    def _get_sites_integration(self):
        """Fetch sites from Integration API (/sites)."""
        offset = 0
        limit = 200
        sites = []

        while True:
            response = self.make_request(
                "/sites",
                "GET",
                params={"offset": offset, "limit": limit},
            )
            if not isinstance(response, dict):
                raise ValueError("No sites found (invalid response shape)")

            data = response.get("data")
            if not isinstance(data, list):
                raise ValueError(
                    f"No sites found (missing data list): {response}"
                )

            sites.extend(data)
            logger.debug(f"Retrieved {len(data)} sites at offset {offset}")

            if not data:
                break

            offset += len(data)
            total_count = response.get("totalCount")
            if isinstance(total_count, int) and offset >= total_count:
                break
            if len(data) < response.get("limit", limit):
                break

        site_dict = {}
        for site in sites:
            site_obj = Sites(self, site)
            key = site.get("name") or site.get("internalReference") or site.get("id")
            if key:
                site_dict[key] = site_obj

        return site_dict

    def _get_sites_legacy(self):
        """Fetch sites from legacy/UniFi OS APIs."""
        response = self.make_request("/api/self/sites", "GET")

        if not response:
            logger.error("No response received when fetching sites")
            raise ValueError("No sites found.")

        logger.debug(f"Sites response meta: {response.get('meta', {})}")
        if response.get("meta", {}).get("rc") == "ok":
            sites = response.get("data", [])
            logger.debug(f"Found {len(sites)} sites on controller")
            site_dict = {site["desc"]: Sites(self, site) for site in sites}
            return site_dict

        error_msg = response.get("meta", {}).get("msg")
        logger.error(f"Failed to get sites: {error_msg}")
        return {}

    def get_sites(self) -> dict:
        """Fetch and return all sites from the selected UniFi API style."""
        logger.debug(f"Fetching sites from UniFi controller at {self.base_url}")
        if self.api_style == "integration":
            return self._get_sites_integration()
        return self._get_sites_legacy()

    def site(self, name):
        """Get a single site by name, internal reference, or API id."""
        site = self.sites.get(name)
        if site:
            return site

        for site_obj in self.sites.values():
            if name in {
                site_obj.name,
                getattr(site_obj, "desc", None),
                getattr(site_obj, "internal_reference", None),
                getattr(site_obj, "api_id", None),
            }:
                return site_obj
        return None

    def __getitem__(self, name):
        """Shortcut for accessing a site."""
        return self.site(name)
