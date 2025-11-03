#!/usr/bin/env python3
"""
Power & Utilities News Digest with Email Delivery

Fetches RSS feeds, summarizes with Gemini (or fallback to headlines), 
and sends via Resend email API.
"""

import os
import sys
import time
import textwrap
from datetime import datetime, timezone

# ---- Python 3.9 compat ----
try:
    from importlib.metadata import packages_distributions
except Exception:
    try:
        from importlib_metadata import packages_distributions
    except Exception:
        packages_distributions = None

# ---- Dependencies ----
try:
    import feedparser
    import requests
except ImportError as e:
    print(f"Missing dependency: {e}. Install with: pip install feedparser requests", file=sys.stderr)
    sys.exit(1)

# ---- Config from environment ----
GOOGLE_API_KEY = os.getenv("GEMINI_KEY", "").strip()  # Note: using GEMINI_KEY to match your secrets
YOUR_EMAIL = os.getenv("YOUR_EMAIL", "").strip()
RESEND_API_KEY = os.getenv("RESEND_KEY", "").strip()

if not YOUR_EMAIL or not RESEND_API_KEY:
    print("ERROR: YOUR_EMAIL and RESEND_KEY environment variables are required!", file=sys.stderr)
    sys.exit(1)

# Gemini is optional
GEMINI_AVAILABLE = False
genai = None
gen_exceptions = None
try:
    if GOOGLE_API_KEY:
        import google.generativeai as genai
        from google.api_core import exceptions as gen_exceptions
        genai.configure(api_key=GOOGLE_API_KEY)
        GEMINI_AVAILABLE = True
except Exception as e:
    print(f"⚠️  Gemini SDK not available: {e}")
    GEMINI_AVAILABLE = False

# ---- RSS Feeds ----
RSS_FEEDS = [
    "https://www.utilitydive.com/feeds/news/",
    "https://www.renewableenergyworld.com/feed/",
    "https://www.energy.gov/rss/news.xml",
    "https://www.powermag.com/feed/",
    "https://www.canarymedia.com/rss-feed",
    "https://renewablesnow.com/feed/",
    "https://www.tdworld.com/rss",
    "https://tanddworld.podbean.com/feed.xml",
    "http://electrical-engineering-portal.com/category/protection/feed",
    "https://iec61850.blogspot.com/feeds/posts/default?alt=rss",
    "https://www.inmr.com/feed/",
    "https://www.energy-storage.news/feed/",
    "https://www.pv-magazine.com/feed/",
    "https://utilityweek.co.uk/feed/",
    "https://www.entsoe.eu/feed/",
    "https://www.ofgem.gov.uk/rss.xml",
]

MAX_PER_FEED = 5
MAX_ARTICLES = 40

GEMINI_MODELS = [
    "models/gemini-1.5-flash",  # Highest free quota
    "models/gemini-1.5-flash-8b",
    "models/gemini-1.5-pro",
]

# ---- Functions ----
def log(msg: str):
    print(msg, flush=True)

def fetch_articles():
    log("=" * 50)
    log("🚀 Starting Power & Utilities News Digest")
    log("=" * 50)
    log("📰 Fetching articles from RSS feeds...")

    all_items = []
    for url in RSS_FEEDS:
        try:
            d = feedparser.parse(url)
            entries = d.entries[:MAX_PER_FEED] if hasattr(d, "entries") else []
            log(f"  ✓ Found {len(entries)} articles from {url}")
            for e in entries:
                title = (getattr(e, "title", "") or "").strip()
                link = (getattr(e, "link", "") or "").strip()
                summary = (getattr(e, "summary", "") or getattr(e, "description", "") or "").strip()
                all_items.append({
                    "title": title or "(no title)",
                    "link": link,
                    "summary": summary[:600],  # Cap length
                })
        except Exception as ex:
            log(f"  ✗ Error reading {url}: {ex}")

    return all_items[:MAX_ARTICLES]

def build_prompt(items):
    prompt = """You are a Power & Utilities industry expert. From these articles, select the 7 most important news items for industry professionals.

For each selected article, provide:
- Title (keep original)
- One clear sentence explaining what happened
- One sentence on why this matters for the P&U industry
- The original URL

Format as clean HTML for email with sections:
1. <h3>Top Stories</h3> (3 most important)
2. <h3>Market & Regulatory Updates</h3>
3. <h3>Technology & Innovation</h3>

Each article should be formatted as:
<div style="margin-bottom: 20px;">
  <h4 style="margin-bottom: 8px; color: #2c3e50;"><a href="URL" style="color: #3498db; text-decoration: none;">TITLE</a></h4>
  <p style="margin: 4px 0;">WHAT HAPPENED</p>
  <p style="margin: 4px 0; color: #7f8c8d; font-size: 14px;">WHY IT MATTERS</p>
</div>

Articles to review:
"""
    for i, it in enumerate(items, 1):
        prompt += f"\n{i}. {it['title']}\n   Summary: {it['summary']}\n   URL: {it['link']}\n"
    
    return prompt

def try_gemini_summarize(prompt):
    if not GEMINI_AVAILABLE:
        raise RuntimeError("Gemini not available")
    
    for model_name in GEMINI_MODELS:
        try:
            log(f"  Trying model: {model_name}")
            model = genai.GenerativeModel(model_name)
            
            for attempt in range(2):
                try:
                    resp = model.generate_content(prompt)
                    if hasattr(resp, "text") and resp.text:
                        log(f"  ✓ Success with {model_name}")
                        return resp.text.strip()
                    raise RuntimeError("Empty response")
                except gen_exceptions.ResourceExhausted as e:
                    if "quota" in str(e).lower():
                        log(f"  ✗ Quota exceeded for {model_name}")
                        raise  # Don't retry quota errors
                    time.sleep(2)
                except Exception as e:
                    if attempt == 0:
                        time.sleep(1)
                    else:
                        raise
        except gen_exceptions.ResourceExhausted:
            continue  # Try next model
        except Exception as e:
            log(f"  ✗ Failed with {model_name}: {e}")
            continue
    
    raise RuntimeError("All Gemini models failed or quota exceeded")

def format_headlines_fallback(items):
    html = "<div style='font-family: Arial, sans-serif;'>"
    html += "<h3 style='color: #e74c3c;'>⚠️ AI Summary Unavailable - Top Headlines</h3>"
    html += "<p style='color: #7f8c8d; font-size: 14px;'>The AI service is temporarily unavailable or quota exceeded. Here are today's top stories:</p>"
    
    for i, it in enumerate(items[:10], 1):
        html += f"""
        <div style="margin-bottom: 15px; padding-bottom: 10px; border-bottom: 1px solid #ecf0f1;">
            <strong>{i}. <a href="{it['link']}" style="color: #3498db; text-decoration: none;">{it['title']}</a></strong>
        </div>
        """
    
    html += "</div>"
    return html

def send_email(content):
    full_html = f"""
    <div style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto;">
        <h2 style="color: #2c3e50; border-bottom: 3px solid #3498db; padding-bottom: 10px;">
            ⚡ Power & Utilities Daily Brief
        </h2>
        <p style="color: #7f8c8d; font-size: 14px;">
            {datetime.now().strftime('%A, %B %d, %Y')}
        </p>
        {content}
        <hr style="margin-top: 30px; border: 1px solid #ecf0f1;">
        <p style="font-size: 12px; color: #95a5a6;">
            Generated using AI from multiple industry sources. 
        </p>
    </div>
    """
    
    log(f"📧 Sending email to {YOUR_EMAIL}...")
    
    response = requests.post(
        "https://api.resend.com/emails",
        headers={
            "Authorization": f"Bearer {RESEND_API_KEY}",
            "Content-Type": "application/json"
        },
        json={
            "from": "PowerBrief <onboarding@resend.dev>",
            "to": [YOUR_EMAIL],
            "subject": f"⚡ Power & Utilities Daily - {datetime.now().strftime('%B %d')}",
            "html": full_html
        }
    )
    
    if response.status_code == 200:
        log(f"✅ Email sent successfully!")
        log(f"   Response: {response.json()}")
        return True
    else:
        log(f"❌ Email failed with status {response.status_code}")
        log(f"   Error: {response.text}")
        return False

def main():
    # Fetch articles
    items = fetch_articles()
    log(f"📊 Total articles collected: {len(items)}")
    
    if not items:
        log("No articles found. Exiting.")
        return 0
    
    # Try to summarize with AI
    log("🤖 Generating digest with AI...")
    digest_html = None
    
    if GEMINI_AVAILABLE:
        try:
            prompt = build_prompt(items)
            digest_html = try_gemini_summarize(prompt)
        except Exception as e:
            log(f"⚠️  AI summarization failed: {e}")
            log("📋 Falling back to headlines-only format...")
            digest_html = format_headlines_fallback(items)
    else:
        log("ℹ️  GEMINI_KEY not set or SDK unavailable. Using headlines format.")
        digest_html = format_headlines_fallback(items)
    
    # Send email
    if send_email(digest_html):
        log("=" * 50)
        log("✅ Process completed successfully!")
        log("=" * 50)
        return 0
    else:
        log("=" * 50)
        log("❌ Email sending failed!")
        log("=" * 50)
        return 1

if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
    except Exception as e:
        log(f"❌ Fatal error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
