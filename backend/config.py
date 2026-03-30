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
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
TELEGRAM_ENABLED = bool(TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID)
HYPERLIQUID_API_URL = os.environ.get(
    "HYPERLIQUID_API_URL", "https://api.hyperliquid.xyz/info"
)

# Table names (prefixed to avoid collision in shared Supabase)
TABLE_AGENTS = "scanner_agents"
TABLE_TRADES = "scanner_trades"
TABLE_FORUM_POSTS = "scanner_forum_posts"
TABLE_SCORES = "scanner_scores"
