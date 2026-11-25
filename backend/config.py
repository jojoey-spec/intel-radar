"""Configuration for Intel Radar: curated feeds, MLX model, and scoring thresholds."""

from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
DB_PATH = DATA_DIR / "intel.db"
WEB_DIR = BASE_DIR / "web"

# Local Apple Silicon MLX model:
# Qwen3-4B-Instruct-2507-4bit is Alibaba's non-thinking 2507 instruction-tuned model:
# fast (~100+ tok/s on M5 Pro), low memory (~2.3 GB), strong Traditional Chinese & strict JSON adherence.
DEFAULT_MLX_MODEL = "mlx-community/Qwen3-4B-Instruct-2507-4bit"

# Major news earthquake scale thresholds (1 - 10)
MAJOR_NOTIFY_MIN_LEVEL = 9   # Lv. 9-10: Nuclear / world-shaking -> push alert!
MAJOR_ARCHIVE_MIN_LEVEL = 7  # Lv. 7-8:  Notable news -> silent archive on board, no alert.

# Rumor credibility trisection thresholds (0 - 100%)
RUMOR_LOW_MAX = 33           # 0%  - 33%: 🔴 不太可信 (Unlikely / Unverified single claim)
RUMOR_WATCH_MAX = 67         # 34% - 67%: 🟡 待觀察   (Plausible leak / Single insider, awaiting corroboration)
                             # 68% - 100%: 🟢 可能性極高 (Corroborated / Hard artifact / Tier-1 scoop)

# Background scan interval (minutes)
DEFAULT_SCAN_INTERVAL_MIN = 15
MAX_ITEMS_PER_FEED = 8
MAX_LLM_EVAL_PER_SCAN = 36

# Curated high-signal RSS / API feeds (all verified HTTP 200 without anti-bot blocks)
DEFAULT_FEEDS = [
    # --- Tier-1 World & Financial Breaking News ---
    {
        "id": "bbc_world",
        "name": "BBC World",
        "url": "https://feeds.bbci.co.uk/news/world/rss.xml",
        "kind": "rss",
        "stream": "major",
        "domain": "國際政經",
        "authority": "tier1",
    },
    {
        "id": "nyt_world",
        "name": "NYT World",
        "url": "https://rss.nytimes.com/services/xml/rss/nyt/World.xml",
        "kind": "rss",
        "stream": "major",
        "domain": "國際政經",
        "authority": "tier1",
    },
    {
        "id": "guardian_world",
        "name": "The Guardian World",
        "url": "https://www.theguardian.com/world/rss",
        "kind": "rss",
        "stream": "major",
        "domain": "國際政經",
        "authority": "tier1",
    },
    {
        "id": "cnbc_world",
        "name": "CNBC Markets & World",
        "url": "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=100727362",
        "kind": "rss",
        "stream": "major",
        "domain": "金融市場",
        "authority": "tier1",
    },
    {
        "id": "bbc_business",
        "name": "BBC Business",
        "url": "https://feeds.bbci.co.uk/news/business/rss.xml",
        "kind": "rss",
        "stream": "major",
        "domain": "金融市場",
        "authority": "tier1",
    },
    # --- Tier-1 Tech, AI & Semiconductor News ---
    {
        "id": "techmeme",
        "name": "TechMeme",
        "url": "https://www.techmeme.com/feed.xml",
        "kind": "rss",
        "stream": "major",
        "domain": "AI・科技巨頭",
        "authority": "tier1",
    },
    {
        "id": "nyt_tech",
        "name": "NYT Technology",
        "url": "https://rss.nytimes.com/services/xml/rss/nyt/Technology.xml",
        "kind": "rss",
        "stream": "major",
        "domain": "AI・科技巨頭",
        "authority": "tier1",
    },
    {
        "id": "cnbc_tech",
        "name": "CNBC Tech",
        "url": "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=19854910",
        "kind": "rss",
        "stream": "major",
        "domain": "AI・科技巨頭",
        "authority": "tier1",
    },
    {
        "id": "verge",
        "name": "The Verge",
        "url": "https://www.theverge.com/rss/index.xml",
        "kind": "rss",
        "stream": "both",
        "domain": "AI・科技巨頭",
        "authority": "tier1",
    },
    {
        "id": "arstechnica",
        "name": "Ars Technica",
        "url": "https://feeds.arstechnica.com/arstechnica/index",
        "kind": "rss",
        "stream": "both",
        "domain": "科學・科技",
        "authority": "tier1",
    },
    {
        "id": "hn_front",
        "name": "Hacker News Frontpage",
        "url": "https://hn.algolia.com/api/v1/search?tags=front_page&hitsPerPage=15",
        "kind": "hn_json",
        "stream": "both",
        "domain": "AI・科技巨頭",
        "authority": "community_signal",
    },
    {
        "id": "hn_megashock",
        "name": "Hacker News 核彈高分榜 (>800pts)",
        "url": "https://hn.algolia.com/api/v1/search?tags=story&hitsPerPage=15",
        "kind": "hn_megashock",
        "stream": "major",
        "domain": "科學・能源",
        "authority": "tier1",
    },
    # --- Tier-1 Fundamental Science & Mathematics ---
    {
        "id": "nature_news",
        "name": "Nature",
        "url": "https://www.nature.com/nature.rss",
        "kind": "rss",
        "stream": "major",
        "domain": "科學・能源",
        "authority": "tier1",
    },
    {
        "id": "quanta",
        "name": "Quanta Magazine",
        "url": "https://www.quantamagazine.org/feed/",
        "kind": "rss",
        "stream": "major",
        "domain": "科學・能源",
        "authority": "tier1",
    },
    {
        "id": "new_scientist",
        "name": "New Scientist",
        "url": "https://www.newscientist.com/feed/home/",
        "kind": "rss",
        "stream": "major",
        "domain": "科學・能源",
        "authority": "tier1",
    },
    # --- Rumor Mill, Supply Chain, Silicon & Gaming Leaks ---
    {
        "id": "macrumors",
        "name": "MacRumors",
        "url": "https://feeds.macrumors.com/MacRumors-All",
        "kind": "rss",
        "stream": "rumor",
        "domain": "Apple・供應鏈",
        "authority": "insider_media",
    },
    {
        "id": "9to5mac",
        "name": "9to5Mac",
        "url": "https://9to5mac.com/feed/",
        "kind": "rss",
        "stream": "rumor",
        "domain": "Apple・供應鏈",
        "authority": "insider_media",
    },
    {
        "id": "9to5google",
        "name": "9to5Google",
        "url": "https://9to5google.com/feed/",
        "kind": "rss",
        "stream": "rumor",
        "domain": "行動・AI生態",
        "authority": "insider_media",
    },
    {
        "id": "wccftech",
        "name": "Wccftech",
        "url": "https://wccftech.com/feed/",
        "kind": "rss",
        "stream": "rumor",
        "domain": "半導體・晶片",
        "authority": "leak_aggregator",
    },
    {
        "id": "tomshardware",
        "name": "Tom's Hardware",
        "url": "https://www.tomshardware.com/feeds/all",
        "kind": "rss",
        "stream": "both",
        "domain": "半導體・晶片",
        "authority": "specialist_media",
    },
    {
        "id": "techpowerup",
        "name": "TechPowerUp",
        "url": "https://www.techpowerup.com/rss/news",
        "kind": "rss",
        "stream": "both",
        "domain": "半導體・晶片",
        "authority": "specialist_media",
    },
    {
        "id": "insider_gaming",
        "name": "Insider Gaming",
        "url": "https://insider-gaming.com/feed/",
        "kind": "rss",
        "stream": "rumor",
        "domain": "遊戲・娛樂爆料",
        "authority": "insider_media",
    },
]
