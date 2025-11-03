#!/usr/bin/env python3
"""
Power & Utilities News Digest
- Works on Python 3.9+ (uses importlib_metadata backport if needed)
- Fetches RSS articles
- Summarizes with Gemini if available (fallback models + graceful 429 handling)
- Degrades gracefully to "headlines-only" digest if no quota or no API key
"""

import os
import sys
import time
import textwrap
from datetime import datetime, timezone

# ---- Python 3.9 compat for importlib.metadata (some Google libs expect newer) ----
try:
    from importlib.metadata import packages_distributions  # noqa: F401
except Exception:  # Py<3.10
    try:
        from importlib_metadata import packages_distributions  # type: ignore # noqa: F401
    except Exception:
        # Not strictly required for this script, but prevents AttributeError in some envs
        packages_distributions = None  # type: ignore

# ---- Third-party deps (install: pip install feedparser google-generativeai) ----
try:
    import feedparser
except ImportError:
    print("Missing dependency: feedparser. Install with: pip install feedparser", file=sys.stderr)
    sys.exit(1)

# Gemini is optional; we handle absence gracefully
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY", "").strip()
GEMINI_AVAILABLE = False
try:
    if GOOGLE_API_KEY:
        import google.generativeai as genai  # type: ignore
        GEN_EXC = None
        from google.api_core import exceptions as gen_exceptions  # type: ignore
        genai.configure(api_key=GOOGLE_API_KEY)
        GEMINI_AVAILABLE = True
except Exception as e:
    GEN_EXC = e
    GEMINI_AVAILABLE = False

# ---------------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------------

# Feeds (feel free to edit)
RSS_FEEDS = [
    # Industry & grid
    "https://www.utilitydive.com/feeds/news/",
    "https://www.renewableenergyworld.com/feed/",
    "https://www.energy.gov/rss/news.xml",
    "https://www.powermag.com/feed/",
    "https://www.canarymedia.com/rss-feed",
    "https://renewablesnow.com/feed/",
    "https://www.tdworld.com/rss",
    "https://tanddworld.podbean.com/feed.xml",

    # Protection, automation & IEC 61850
    "http://electrical-engineering-portal.com/category/protection/feed",
    "https://iec61850.blogspot.com/feeds/posts/default?alt=rss",
    "https://www.inmr.com/feed/",

    # Storage / DERs
    "https://www.energy-storage.news/feed/",
    "https://www.pv-magazine.com/feed/",

    # Europe/UK policy & utility news
    "https://utilityweek.co.uk/feed/",
    "https://www.entsoe.eu/feed/",
    "https://www.ofgem.gov.uk/rss.xml",
]

# Per-feed limits to keep token use predictable
MAX_PER_FEED = 5
# Overall cap before summarization
MAX_ARTICLES = 40

# Gemini model fallback chain (tries in order)
GEMINI_MODELS = [
    "models/gemini-2.5-pro-preview-03-25",
    "models/gemini-1.5-pro",
    "models/gemini-1.5-flash",
    "models/gemini-1.5-flash-8b",
]

# ---------------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------------

def log(msg: str):
    print(msg, flush=True)

def fetch_articles():
    log("=" * 50)
    log("🚀 Starting Power & Utilities News Digest")
    log("=" * 50)
    log("📰 Fetching Power & Utilities news...")
    all_items = []
    for url in RSS_FEEDS:
        try:
            d = feedparser.parse(url)
            entries = d.entries[:MAX_PER_FEED] if getattr(d, "entries", None) else []
            log(f"  ✓ Found {len(entries)} articles from {url}")
            for e in entries:
                title = getattr(e, "title", "").strip()
                link = getattr(e, "link", "").strip()
                summary = (getattr(e, "summary", "") or getattr(e, "description", "")).strip()
                published = getattr(e, "published", "") or getattr(e, "updated", "")
                # Normalize pubdate
                ts = None
                if hasattr(e, "published_parsed") and e.published_parsed:
                    ts = datetime(*e.published_parsed[:6], tzinfo=timezone.utc)
                elif hasattr(e, "updated_parsed") and e.updated_parsed:
                    ts = datetime(*e.updated_parsed[:6], tzinfo=timezone.utc)

                all_items.append({
                    "title": title or "(no title)",
                    "link": link,
                    "summary": summary,
                    "published": published,
                    "ts": ts or datetime.now(timezone.utc),
                    "source": url,
                })
        except Exception as ex:
            log(f"  ! Error reading {url}: {ex}")

    # Sort newest first and cap
    all_items.sort(key=lambda x: x["ts"], reverse=True)
    return all_items[:MAX_ARTICLES]

def build_prompt(items):
    lines = []
    lines.append(
        "You are a utility-industry analyst. Summarize these articles into a crisp daily digest for a busy CCO in power & utilities focused on protection & control, IEC 61850, T&D grid, DER/storage, and regulation. "
        "Be factual, brief, and actionable. Use bullets and short sections (Generation, T&D & Grid Ops, Protection & Control, DER/Storage, Policy/Regulatory, M&A/Financing). "
        "For each bullet: include the headline (tightened), the **publisher** or site if obvious, and 1–2 key takeaways. Prefer concrete metrics/dates. End with 3 ‘What matters’ bullets."
    )
    lines.append("\nArticles:\n")
    for i, it in enumerate(items, 1):
        lines.append(f"{i}. {it['title']} — {it['link']}\nSummary: {it['summary'][:600]}")
    return "\n".join(lines)

def try_gemini_summarize(prompt):
    """
    Attempts Gemini with fallbacks and basic retry for 429.
    Returns text or raises last error.
    """
    assert GEMINI_AVAILABLE, "Gemini not available"
    last_err = None
    for model_name in GEMINI_MODELS:
        try:
            model = genai.GenerativeModel(model_name)
            # Minimal retry loop for transient 429
            for attempt in range(3):
                try:
                    resp = model.generate_content(prompt)
                    if hasattr(resp, "text") and resp.text:
                        return resp.text.strip()
                    # Some SDK versions use candidates
                    if getattr(resp, "candidates", None):
                        parts = resp.candidates[0].content.parts
                        text = "".join(getattr(p, "text", "") for p in parts)
                        if text.strip():
                            return text.strip()
                    raise RuntimeError("Empty response from Gemini")
                except gen_exceptions.ResourceExhausted as e:
                    last_err = e
                    # Light backoff and try again (or next model)
                    time.sleep(2 + attempt * 2)
                except gen_exceptions.GoogleAPIError as e:
                    last_err = e
                    break  # try next model
            # if we reach here, move to next model
        except Exception as e:
            last_err = e
            continue
    raise RuntimeError(f"All Gemini attempts failed. Last error: {last_err}")

def format_headlines_only(items):
    """Fallback digest if no LLM or quota—still useful & readable."""
    out = []
    out.append("=" * 50)
    out.append("Power & Utilities — Headlines Digest (LLM unavailable)")
    out.append(datetime.now().strftime("%Y-%m-%d %H:%M %Z"))
    out.append("=" * 50)
    for it in items:
        line = f"- {it['title']} ({it['link']})"
        out.append(textwrap.shorten(line, width=220, placeholder="…"))
    out.append("\nWhat matters:")
    out.append("- Watch protection & control updates impacting relay settings/misops.")
    out.append("- Track interconnection/transmission constraints affecting project timelines.")
    out.append("- Monitor regulatory moves (NERC/ENTSO-E/Ofgem) that change compliance scope.")
    return "\n".join(out)

def main():
    items = fetch_articles()
    log(f"📊 Total articles collected: {len(items)}")

    if not items:
        log("No articles found. Exiting 0.")
        return 0

    prompt = build_prompt(items)
    log("🤖 Generating digest...")

    digest = None
    if GEMINI_AVAILABLE:
        try:
            digest = try_gemini_summarize(prompt)
        except Exception as e:
            log(f"❌ Gemini unavailable or quota exceeded. Falling back. ({e})")
    else:
        if GOOGLE_API_KEY:
            log(f"❌ Gemini SDK error at import: {GEN_EXC}. Falling back.")
        else:
            log("ℹ️ GOOGLE_API_KEY not set. Using headlines-only digest.")

    if not digest:
        digest = format_headlines_only(items)

    print("\n" + "=" * 50)
    print(digest)
    print("=" * 50 + "\n")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
