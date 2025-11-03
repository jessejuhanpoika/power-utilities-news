#!/usr/bin/env python3
"""
Power & Utilities News Digest

- Python 3.9+ compatible (uses importlib_metadata backport if needed)
- Fetches RSS from curated feeds (Power/Utilities, P&C/IEC 61850, DER/Storage, Policy)
- Summarizes with Gemini if GOOGLE_API_KEY is set and quota available
- Graceful fallback to "headlines-only" when no LLM or quota
- --html flag to emit HTML for email/newsletter

Usage:
  python news_digest.py            # plaintext to stdout
  python news_digest.py --html     # HTML to stdout (for email)
"""

import os
import sys
import time
import textwrap
import argparse
from datetime import datetime, timezone

# ---- Python 3.9 compat for importlib.metadata (some Google libs expect newer) ----
try:
    from importlib.metadata import packages_distributions  # noqa: F401
except Exception:  # Py<3.10
    try:
        from importlib_metadata import packages_distributions  # type: ignore # noqa: F401
    except Exception:
        packages_distributions = None  # type: ignore

# ---- Third-party deps -----------------------------------------------------------
try:
    import feedparser
except ImportError:
    print("Missing dependency: feedparser. Install with: pip install feedparser", file=sys.stderr)
    sys.exit(1)

# Gemini is optional; we handle absence gracefully
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY", "").strip()
GEMINI_AVAILABLE = False
GEN_EXC = None
try:
    if GOOGLE_API_KEY:
        import google.generativeai as genai  # type: ignore
        from google.api_core import exceptions as gen_exceptions  # type: ignore
        genai.configure(api_key=GOOGLE_API_KEY)
        GEMINI_AVAILABLE = True
except Exception as e:
    GEN_EXC = e
    GEMINI_AVAILABLE = False

# ---- Config --------------------------------------------------------------------
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

MAX_PER_FEED = 5     # keep token usage & runtime predictable
MAX_ARTICLES = 40

GEMINI_MODELS = [
    "models/gemini-2.5-pro-preview-03-25",
    "models/gemini-1.5-pro",
    "models/gemini-1.5-flash",
    "models/gemini-1.5-flash-8b",
]

# ---- Helpers -------------------------------------------------------------------
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
                title = (getattr(e, "title", "") or "").strip()
                link = (getattr(e, "link", "") or "").strip()
                summary = (getattr(e, "summary", "") or getattr(e, "description", "") or "").strip()
                published = getattr(e, "published", "") or getattr(e, "updated", "") or ""
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

    all_items.sort(key=lambda x: x["ts"], reverse=True)
    return all_items[:MAX_ARTICLES]

def build_prompt(items):
    lines = []
    lines.append(
        "You are a utility-industry analyst. Summarize these articles into a crisp daily digest for a busy CCO in power & utilities focused on protection & control, IEC 61850, T&D grid, DER/storage, and regulation. "
        "Be factual, brief, and actionable. Use bullets and short sections (Generation, T&D & Grid Ops, Protection & Control, DER/Storage, Policy/Regulatory, M&A/Financing). "
        "For each bullet: include the headline (tightened), the publisher if obvious, and 1–2 key takeaways with concrete metrics/dates. End with 3 ‘What matters’ bullets."
    )
    lines.append("\nArticles:\n")
    for i, it in enumerate(items, 1):
        # cap per-article context to reduce tokens
        lines.append(f"{i}. {it['title']} — {it['link']}\nSummary: {it['summary'][:600]}")
    return "\n".join(lines)

def try_gemini_summarize(prompt):
    assert GEMINI_AVAILABLE, "Gemini not available"
    last_err = None
    for model_name in GEMINI_MODELS:
        try:
            model = genai.GenerativeModel(model_name)
            for attempt in range(3):
                try:
                    resp = model.generate_content(prompt)
                    if hasattr(resp, "text") and resp.text:
                        return resp.text.strip()
                    if getattr(resp, "candidates", None):
                        parts = resp.candidates[0].content.parts
                        text = "".join(getattr(p, "text", "") for p in parts)
                        if text.strip():
                            return text.strip()
                    raise RuntimeError("Empty response from Gemini")
                except gen_exceptions.ResourceExhausted as e:
                    last_err = e
                    time.sleep(2 + attempt * 2)
                except gen_exceptions.GoogleAPIError as e:
                    last_err = e
                    break
        except Exception as e:
            last_err = e
            continue
    raise RuntimeError(f"All Gemini attempts failed. Last error: {last_err}")

def format_headlines_only(items):
    out = []
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    out.append("Power & Utilities — Headlines Digest (LLM unavailable)")
    out.append(now)
    out.append("")
    for it in items:
        line = f"- {it['title']} ({it['link']})"
        out.append(textwrap.shorten(line, width=220, placeholder="…"))
    out.append("")
    out.append("What matters:")
    out.append("• Watch protection & control updates impacting relay settings/misops.")
    out.append("• Track interconnection/transmission constraints affecting project timelines.")
    out.append("• Monitor regulatory moves (NERC/ENTSO-E/Ofgem) that change compliance scope.")
    return "\n".join(out)

def to_html(body_text: str, title: str = "PowerBrief — Utilities & P&C") -> str:
    # Minimal inline CSS for email clients
    ts = datetime.now().strftime("%Y-%m-%d %H:%M")
    return f"""<!doctype html>
<meta charset="utf-8">
<title>{title}</title>
<style>
body{{font-family:system-ui,-apple-system,Segoe UI,Roboto,Arial,sans-serif;line-height:1.45;margin:24px auto;max-width:820px;padding:0 16px}}
h1{{font-size:1.5rem;margin:0 0 4px}}
small{{color:#444}}
pre{{white-space:pre-wrap;word-wrap:break-word}}
a{{text-decoration:none}}
</style>
<body>
  <h1>{title}</h1>
  <p><small>Generated {ts}</small></p>
  <hr>
  <pre>{body_text}</pre>
</body>
"""

def main():
    ap = argparse.ArgumentParser(description="Power & Utilities News Digest")
    ap.add_argument("--html", action="store_true", help="Output HTML instead of plaintext")
    args = ap.parse_args()

    items = fetch_articles()
    log(f"📊 Total articles collected: {len(items)}")
    if not items:
        log("No articles found. Exiting 0.")
        print("No articles found.")
        return 0

    prompt = build_prompt(items)
    log("🤖 Generating digest...")

    digest_text = None
    if GEMINI_AVAILABLE:
        try:
            digest_text = try_gemini_summarize(prompt)
        except Exception as e:
            log(f"❌ Gemini unavailable or quota exceeded. Falling back. ({e})")
    else:
        if GOOGLE_API_KEY:
            log(f"❌ Gemini SDK error at import: {GEN_EXC}. Falling back.")
        else:
            log("ℹ️ GOOGLE_API_KEY not set. Using headlines-only digest.")

    if not digest_text:
        digest_text = format_headlines_only(items)

    if args.html:
        html = to_html(digest_text)
        # Ensure UTF-8 output
        sys.stdout.reconfigure(encoding="utf-8")
        print(html)
    else:
        print("\n" + "=" * 50)
        print(digest_text)
        print("=" * 50 + "\n")
    return 0

if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
