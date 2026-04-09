"""Unit tests for onchain_enricher Basescan HTML parser."""
import os
import sys
from pathlib import Path

_BACKEND = Path(__file__).resolve().parents[1]
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "test")
os.environ.setdefault("DGCLAW_API_KEY", "test")

from onchain_enricher import (  # noqa: E402
    _parse_basescan_html,
    _parse_relative_age_days,
)

FIX = Path(__file__).parent / "fixtures"


def _load(name: str) -> str:
    return (FIX / name).read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Relative-age parser
# ---------------------------------------------------------------------------

def test_relative_age_years_and_days():
    assert _parse_relative_age_days("1 yr 351 days ago") == 1 * 365 + 351


def test_relative_age_days_only():
    assert _parse_relative_age_days("57 days ago") == 57


def test_relative_age_single_day():
    assert _parse_relative_age_days("1 day ago") == 1


def test_relative_age_hours_is_zero_days():
    assert _parse_relative_age_days("3 hrs ago") == 0
    assert _parse_relative_age_days("12 mins ago") == 0


def test_relative_age_none_and_garbage():
    assert _parse_relative_age_days(None) is None
    assert _parse_relative_age_days("") is None
    assert _parse_relative_age_days("garbage") is None


# ---------------------------------------------------------------------------
# Basescan HTML parser
# ---------------------------------------------------------------------------

def test_parse_denisigin_octet_full_metadata():
    html = _load("basescan_denisigin_octet.html")
    row = _parse_basescan_html(
        owner_wallet="0x5Ae31b437851B57a491603D6C7A845B61c88F1f5",
        html=html,
    )
    assert row is not None
    assert row["owner_wallet"].lower() == "0x5ae31b437851b57a491603d6c7a845b61c88f1f5"
    # Balance values drift daily; just assert the range we observed
    # when the fixture was captured (single-digit dollars).
    assert 1.0 <= row["balance_usd"] <= 50.0
    assert row["chains_active"] == 5
    assert row["total_tx_count"] == 30
    assert row["age_days"] == 716  # 1 yr 351 days
    assert row["address_kind"] == "Authority"
    assert row["source"] == "basescan.org"


def test_parse_ggbots_full_metadata_large_balance():
    html = _load("basescan_ggbots.html")
    row = _parse_basescan_html(
        owner_wallet="0x4a24d4a7c36257E0bF256EA2970708817C597A2C",
        html=html,
    )
    assert row is not None
    # Thousands-separator handling is what we actually care about here;
    # the balance drifts daily on the live site.
    assert row["balance_usd"] is not None
    assert 1000.0 <= row["balance_usd"] <= 10000.0
    assert row["chains_active"] == 8
    assert row["total_tx_count"] == 75
    assert row["age_days"] == 1 * 365 + 242


def test_parse_dead_eoa_zero_txs():
    """A legitimate dead EOA owner with 0 Base activity.
    Should parse cleanly with all zeros — NOT marked throwaway.
    """
    html = _load("basescan_dead_eoa_monyet.html")
    row = _parse_basescan_html(
        owner_wallet="0x92D8bE4172bc2d3a06DB730E7E8bb0895233E090",
        html=html,
    )
    assert row is not None
    assert row["balance_usd"] == 0.0
    assert row["chains_active"] == 0
    assert row["total_tx_count"] == 0
    assert row["age_days"] is None  # no First: block on an empty wallet


def test_parse_returns_none_on_cloudflare_page():
    cf_body = """<html><head><title>Just a moment...</title></head>
    <body><h1>Attention Required!</h1><p>Cloudflare Ray ID: 12345</p></body></html>"""
    row = _parse_basescan_html(owner_wallet="0x1", html=cf_body)
    assert row is None


def test_parse_returns_none_on_malformed_meta():
    garbage = "<html><head><meta name='Description' content='no match here' /></head></html>"
    row = _parse_basescan_html(owner_wallet="0x2", html=garbage)
    assert row is None


def test_parse_handles_commas_in_tx_count():
    """Make sure 'Transactions: 1,234' is parsed as 1234, not 1."""
    snippet = (
        '<meta name="Description" '
        'content="Address (EOA) | Balance: $500.00 across 4 Chains | '
        'Transactions: 1,234 | As at Apr-09-2026 05:50:29 PM (UTC)" />'
    )
    row = _parse_basescan_html(owner_wallet="0x3", html=snippet)
    assert row is not None
    assert row["total_tx_count"] == 1234
    assert row["balance_usd"] == 500.00
    assert row["chains_active"] == 4
