"""Environment configuration for the Agent Squeaky backend."""
import os

from dotenv import load_dotenv

load_dotenv()


def _require(key: str) -> str:
    val = os.environ.get(key)
    if not val:
        raise RuntimeError(f"Missing required environment variable: {key}")
    return val


SUPABASE_URL = _require("SUPABASE_URL")
SUPABASE_SERVICE_KEY = _require("SUPABASE_SERVICE_KEY")
DGCLAW_API_KEY = _require("DGCLAW_API_KEY")

# Table names (prefixed to avoid collision in shared Supabase)
TABLE_AGENTS = "scanner_agents"
TABLE_TRADES = "scanner_trades"
TABLE_FORUM_POSTS = "scanner_forum_posts"
TABLE_SCORES = "scanner_scores"
TABLE_LABELS = "scanner_labels"
TABLE_SIGNAL_LRS = "scanner_signal_lrs"
TABLE_CANDLES = "scanner_candles"
TABLE_ONCHAIN = "scanner_onchain"
