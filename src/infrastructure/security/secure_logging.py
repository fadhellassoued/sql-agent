from __future__ import annotations

import logging
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


SENSITIVE_QUERY_PARAMS = {
    "code",
    "state",
    "access_token",
    "refresh_token",
    "id_token",
    "client_secret",
}

_EMAIL_RE = re.compile(
    r"\b([A-Za-z0-9._%+-]{1,64})@([A-Za-z0-9.-]+\.[A-Za-z]{2,})\b"
)

_UUID_RE = re.compile(
    r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-5][0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}\b"
)


def _mask_email_match(match: re.Match) -> str:
    local = match.group(1)
    domain = match.group(2)
    return f"{local[:2]}***@{domain}"


def _mask_uuid_match(match: re.Match) -> str:
    value = match.group(0)
    return f"{value[:8]}…"


def redact_text(value: str) -> str:
    if not value:
        return value

    value = _EMAIL_RE.sub(
        _mask_email_match,
        value,
    )

    value = _UUID_RE.sub(
        _mask_uuid_match,
        value,
    )

    value = re.sub(
        r"(?i)(code|state|access_token|refresh_token|id_token|client_secret)=([^&\s\"]+)",
        r"\1=***",
        value,
    )

    return value


def redact_path(path: str) -> str:
    """Redact OAuth query secrets and UUID-like session IDs in access paths."""
    if not path:
        return path

    try:
        parsed = urlsplit(path)
        query = []

        for key, value in parse_qsl(
            parsed.query,
            keep_blank_values=True,
        ):
            query.append(
                (
                    key,
                    "***"
                    if key.lower() in SENSITIVE_QUERY_PARAMS
                    else value,
                )
            )

        safe = urlunsplit(
            (
                parsed.scheme,
                parsed.netloc,
                parsed.path,
                urlencode(query),
                parsed.fragment,
            )
        )

        return redact_text(safe)

    except Exception:
        return redact_text(path)


class UvicornAccessRedactionFilter(logging.Filter):
    """
    Uvicorn access records normally carry:
      (client_addr, method, full_path, http_version, status_code)
    """

    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args

        if (
            isinstance(args, tuple)
            and len(args) >= 5
        ):
            mutable = list(args)
            mutable[2] = redact_path(
                str(mutable[2])
            )
            record.args = tuple(mutable)

        return True


class GeneralRedactionFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if record.name == "uvicorn.access":
            return True

        try:
            rendered = record.getMessage()
            safe = redact_text(rendered)

            if safe != rendered:
                record.msg = safe
                record.args = ()
        except Exception:
            pass

        return True


def install_secure_logging_filters() -> None:
    access_filter = UvicornAccessRedactionFilter()
    general_filter = GeneralRedactionFilter()

    for logger_name in (
        "uvicorn.access",
        "uvicorn.error",
        "uvicorn",
    ):
        logger = logging.getLogger(logger_name)

        # Adding to logger AND handlers makes this work with Uvicorn's
        # configured access handler at application startup.
        if logger_name == "uvicorn.access":
            logger.addFilter(access_filter)

        logger.addFilter(general_filter)

        for handler in logger.handlers:
            if logger_name == "uvicorn.access":
                handler.addFilter(access_filter)
            handler.addFilter(general_filter)

    root = logging.getLogger()
    root.addFilter(general_filter)
    for handler in root.handlers:
        handler.addFilter(general_filter)