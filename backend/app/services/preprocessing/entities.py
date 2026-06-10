"""
Regex-based entity extractors for user queries.

Used by Tier 0 routing to detect strict-format entities (phone, MAC, IP, IDs,
email, dates) without needing an LLM. Heavy-NER (names, addresses) is handled
separately via the keyword_extract entity type in tier0_router.

All extractors return lists (a query may mention multiple values).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

# ---------------------------------------------------------------------------
# Phone numbers
# ---------------------------------------------------------------------------
# Covers Ukrainian phone formats: +380XXXXXXXXX, 380XXXXXXXXX, 0XXXXXXXXX,
# and the international-ish with optional separators (space, dash, paren).
# Captures the digits-only normalized form.
_PHONE_RE = re.compile(
    r"""
    (?<![\d\w])              # not part of a larger token
    (?:\+?38)?               # optional +38 / 38 prefix
    [\s\-]*\(?               # spacing + optional paren
    (0[3-9]\d)               # mandatory area code (operator prefix)
    \)?[\s\-]*               # close paren + spacing
    (\d{3})                  # subscriber 1
    [\s\-]*
    (\d{2})                  # subscriber 2
    [\s\-]*
    (\d{2})                  # subscriber 3
    (?![\d])
    """,
    re.VERBOSE,
)


def extract_phones(text: str) -> list[str]:
    """Return phone numbers normalized to +380XXXXXXXXX form."""
    out: list[str] = []
    for m in _PHONE_RE.finditer(text or ""):
        digits = "".join(m.groups())
        # Always 10 digits after extraction (operator + 7 subscriber)
        if len(digits) == 10:
            out.append("+38" + digits)
    return out


# ---------------------------------------------------------------------------
# MAC addresses
# ---------------------------------------------------------------------------
# Accept all common formats: AA:BB:CC:DD:EE:FF, AA-BB-..., AABB.CCDD.EEFF,
# AABBCCDDEEFF (12 hex, no separators).
_MAC_RE = re.compile(
    r"""
    (?<![\dA-Fa-f])
    (?:
        [0-9A-Fa-f]{2}(?:[:\-][0-9A-Fa-f]{2}){5}    # AA:BB:CC:DD:EE:FF / AA-BB-...
      | [0-9A-Fa-f]{4}\.[0-9A-Fa-f]{4}\.[0-9A-Fa-f]{4}  # aabb.ccdd.eeff
    )
    (?![\dA-Fa-f])
    """,
    re.VERBOSE,
)
# Raw "AABBCCDDEEFF" (no separators) only when it is preceded by an explicit
# MAC keyword — otherwise it collides with phone digits and other hex blobs.
_MAC_RAW_RE = re.compile(
    r"""
    \b(?:mac|мак|маком|hwaddr)[\s:=]*
    ([0-9A-Fa-f]{12})
    \b
    """,
    re.IGNORECASE | re.VERBOSE,
)


def extract_macs(text: str) -> list[str]:
    """Return MAC addresses in canonical aabb.ccdd.eeff form (Cisco/BDCOM)."""
    out: list[str] = []
    seen: set[str] = set()
    text = text or ""
    for m in _MAC_RE.finditer(text):
        raw = m.group(0)
        hex_only = re.sub(r"[^0-9A-Fa-f]", "", raw).lower()
        if len(hex_only) == 12 and hex_only not in seen:
            seen.add(hex_only)
            out.append(f"{hex_only[0:4]}.{hex_only[4:8]}.{hex_only[8:12]}")
    # Raw 12-hex only after an explicit "mac"-like keyword
    for m in _MAC_RAW_RE.finditer(text):
        hex_only = m.group(1).lower()
        if hex_only not in seen:
            seen.add(hex_only)
            out.append(f"{hex_only[0:4]}.{hex_only[4:8]}.{hex_only[8:12]}")
    return out


# ---------------------------------------------------------------------------
# IPv4 addresses (private + public)
# ---------------------------------------------------------------------------
_IP_RE = re.compile(
    r"(?<![\d.])"
    r"((?:25[0-5]|2[0-4]\d|[01]?\d?\d)\.(?:25[0-5]|2[0-4]\d|[01]?\d?\d)"
    r"\.(?:25[0-5]|2[0-4]\d|[01]?\d?\d)\.(?:25[0-5]|2[0-4]\d|[01]?\d?\d))"
    r"(?!\d)"
)


def extract_ips(text: str) -> list[str]:
    return _IP_RE.findall(text or "")


# ---------------------------------------------------------------------------
# Service / Client / Ticket numeric IDs
# ---------------------------------------------------------------------------
# Patterns: #123, № 456, service 789, ticket 12, договор № 321, абонент 1234.
# Conservative: requires an explicit prefix marker so we don't grab random
# numbers (port counts, byte sizes, etc).
_ID_PREFIX_RE = re.compile(
    r"""
    \b
    (?:
        \#                                                          # bare hash
      | (?: №\s*| номер\s+| n[ou]m\.?\s*)                           # "№ ..."
      | (?: услуг[аи]?|service)\s+                                  # service N
      | (?: тикет[аи]?|заявк[аи]?|ticket|task)\s+(?:№\s*|\#\s*)?    # ticket N
      | (?: договор[аи]?|contract)\s+(?:№\s*|\#\s*)?                # contract N
      | (?: абонент[аи]?|клиент[аи]?|client)\s+(?:№\s*|\#\s*)?      # client N
    )
    (\d{1,10})
    \b
    """,
    re.IGNORECASE | re.VERBOSE,
)


def extract_numeric_ids(text: str) -> list[str]:
    return [m.group(1) for m in _ID_PREFIX_RE.finditer(text or "")]


# ---------------------------------------------------------------------------
# Email addresses
# ---------------------------------------------------------------------------
_EMAIL_RE = re.compile(
    r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b"
)


def extract_emails(text: str) -> list[str]:
    """Return email addresses found in text."""
    return _EMAIL_RE.findall(text or "")


# ---------------------------------------------------------------------------
# Dates
# ---------------------------------------------------------------------------
# Supports:
#   DD.MM.YYYY, DD/MM/YYYY, DD-MM-YYYY (Ukrainian/European format)
#   YYYY-MM-DD  (ISO format)
#   "17 мая 2024", "17 мая"  (Slavic named months)
# All normalized to ISO YYYY-MM-DD (or MM-DD if year is absent).

_DATE_NUMERIC_RE = re.compile(
    r"""
    \b
    (?:
        (\d{4})-(\d{1,2})-(\d{1,2})       # ISO: YYYY-MM-DD
      | (\d{1,2})[./\-](\d{1,2})[./\-](\d{2,4})  # EU: DD.MM.YYYY
    )
    \b
    """,
    re.VERBOSE,
)

_MONTH_RU: dict[str, str] = {
    "января": "01", "февраля": "02", "марта": "03", "апреля": "04",
    "мая": "05", "июня": "06", "июля": "07", "августа": "08",
    "сентября": "09", "октября": "10", "ноября": "11", "декабря": "12",
}
_DATE_NAMED_RE = re.compile(
    r"\b(\d{1,2})\s+(" + "|".join(_MONTH_RU) + r")(?:\s+(\d{4}))?\b",
    re.IGNORECASE,
)


def extract_dates(text: str) -> list[str]:
    """Return dates normalized to YYYY-MM-DD (or MM-DD if year absent)."""
    out: list[str] = []
    seen: set[str] = set()
    text = text or ""

    for m in _DATE_NUMERIC_RE.finditer(text):
        if m.group(1):  # ISO: YYYY-MM-DD
            y, mo, d = m.group(1), m.group(2), m.group(3)
        else:           # EU: DD.MM.YYYY
            d, mo, y = m.group(4), m.group(5), m.group(6)
            if len(y) == 2:
                y = "20" + y
        norm = f"{y}-{mo.zfill(2)}-{d.zfill(2)}"
        if norm not in seen:
            seen.add(norm)
            out.append(norm)

    for m in _DATE_NAMED_RE.finditer(text):
        d_str = m.group(1)
        mo = _MONTH_RU[m.group(2).lower()]
        y_str = m.group(3)
        norm = (f"{y_str}-{mo}-{d_str.zfill(2)}" if y_str
                else f"{mo}-{d_str.zfill(2)}")
        if norm not in seen:
            seen.add(norm)
            out.append(norm)

    return out


# ---------------------------------------------------------------------------
# All-in-one extractor
# ---------------------------------------------------------------------------

@dataclass
class ExtractedEntities:
    """Strict-format entities found in a user query."""
    phones: list[str]
    macs: list[str]
    ips: list[str]
    numeric_ids: list[str]
    emails: list[str] = field(default_factory=list)
    dates: list[str] = field(default_factory=list)

    def has_any(self) -> bool:
        return bool(
            self.phones or self.macs or self.ips or self.numeric_ids
            or self.emails or self.dates
        )

    def as_dict(self) -> dict:
        return {
            "phones": self.phones,
            "macs": self.macs,
            "ips": self.ips,
            "numeric_ids": self.numeric_ids,
            "emails": self.emails,
            "dates": self.dates,
        }


def extract_entities(text: str) -> ExtractedEntities:
    return ExtractedEntities(
        phones=extract_phones(text),
        macs=extract_macs(text),
        ips=extract_ips(text),
        numeric_ids=extract_numeric_ids(text),
        emails=extract_emails(text),
        dates=extract_dates(text),
    )
