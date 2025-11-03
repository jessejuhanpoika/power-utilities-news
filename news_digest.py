pythonimport feedparser
import google.generativeai as genai
import requests
from datetime import datetime
import os

# Configuration from GitHub Secrets
GEMINI_API_KEY = os.environ.get('GEMINI_KEY')
YOUR_EMAIL = os.environ.get('YOUR_EMAIL')
RESEND_API_KEY = os.environ.get('RESEND_KEY')

# Power & Utilities RSS Feeds
RSS_FEEDS = [
    'https://www.utilitydive.com/feeds/news/',
    'https://www.renewableenergyworld.com/feed/',
    'https://www.energy.gov/rss/news.xml',
    'https://www.powermag.com/feed/',
]

def get_news():
    """Fetch and process news articles"""
    genai.configure(api_key=GEMINI_API_KEY)
    model = genai.GenerativeModel('gemini-1.5-flash')
    
    # Collect all articles
    all_articles = []
    for feed_url in RSS_FEEDS:
        try:
            feed = feedparser.parse(feed_url)
            for entry in feed.entries[:5]:  # Get top 5 from each source
                article = {
                    'title': entry.title,
                    'link': entry.link,
                    'summary': entry.get('summary', '')[:300]
                }
                all_articles.append(f"Title: {article['title']}\nPreview: {article['summary']}\nURL: {article['link']}\n")
        except:
            continue
    
    # Ask Gemini to select and summarize the best articles
    prompt = f"""
    You are a Power & Utilities industry expert. From these articles, select the 7 most important news items for industry professionals.
    
    For each selected article, provide:
    - Title (keep original)
    - One clear sentence explaining what happened
    - One sentence on why this matters for the P&U industry
    - The original URL
    
    Format as a clean HTML email with sections for:
    1. Top Stories (3 most important)
    2. Market & Regulatory Updates
    3. Technology & Innovation
    
    Articles to review:
    {' '.join(all_articles)}
    
    Make it professional but easy to scan quickly.
    """
    
    response = model.generate_content(prompt)
    return response.text

def send_email_resend(content):
    """Send email using Resend"""
    
    full_content = f"""
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
            You're receiving this because you set up a P&U news digest. 
            Generated using Gemini AI from multiple industry sources.
        </p>
    </div>
    """
    
    response = requests.post(
        "https://api.resend.com/emails",
        headers={
            "Authorization": f"Bearer {RESEND_API_KEY}",
            "Content-Type": "application/json"
        },
        json={
            "from": "P&U News <onboarding@resend.dev>",
            "to": [YOUR_EMAIL],
            "subject": f"⚡ Power & Utilities Daily - {datetime.now().strftime('%B %d')}",
            "html": full_content
        }
    )
    
    if response.status_code == 200:
        print(f"✅ Email sent successfully to {YOUR_EMAIL}")
    else:
        print(f"❌ Error: {response.text}")

# Main execution
if __name__ == "__main__":
    try:
        print("📰 Fetching Power & Utilities news...")
        content = get_news()
        print("📧 Sending email digest...")
        send_email_resend(content)
    except Exception as e:
        print(f"❌ Error: {e}")
