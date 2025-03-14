import os
import requests
import feedparser
from datetime import datetime
from bs4 import BeautifulSoup
import time

# Configuration
TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
TELEGRAM_CHANNEL_ID = os.getenv('TELEGRAM_CHANNEL_ID')
CACHE_FILE = 'cached_articles.txt'  # Cache file to store sent article links

RSS_FEEDS = [
    'https://feedx.net/rss/zhihudaily.xml',
    'http://dig.chouti.com/feed.xml',
    'https://36kr.com/feed',
    'https://sspai.com/feed',
    'https://www.huxiu.com/rss/0.xml',
    'http://www.tmtpost.com/feed',
    'https://wechat2rss.xlab.app/feed/923c0e2f33b6d39c8a826a90f185725f0edb10e8.xml',
    'https://feeds.appinn.com/appinns/',
    'http://blog.caixin.com/feed',
    'https://www.v2ex.com/feed/tab/tech.xml',
    'http://songshuhui.net/feed',
    'http://feed.yixieshi.com/',
]

MAX_MESSAGE_LENGTH = 4096
SUMMARY_MAX_LENGTH = 200
MAX_ARTICLES_PER_FEED = 5
BING_API_URL = "https://www.bing.com/HPImageArchive.aspx?format=js&idx=0&n=10&mkt=zh-CN"
RETRY_COUNT = 3

def load_cache():
    """Load the cached article links from the cache file."""
    if not os.path.exists(CACHE_FILE):
        return set()
    with open(CACHE_FILE, 'r', encoding='utf-8') as f:
        return set(line.strip() for line in f)

def update_cache(article_links):
    """Update the cache file with the new links."""
    with open(CACHE_FILE, 'a', encoding='utf-8') as f:
        for link in article_links:
            f.write(link + '\n')

def clean_html(html):
    """Clean HTML tags and extract plain text."""
    soup = BeautifulSoup(html, 'html.parser')
    return soup.get_text().strip()

def fetch_new_articles(rss_url):
    """Fetch today's new articles from the specified RSS feed."""
    for attempt in range(RETRY_COUNT):
        try:
            feed = feedparser.parse(rss_url)
            new_articles = []
            
            # Get today's date
            today = datetime.now()
            start_of_day = datetime(today.year, today.month, today.day)
            
            # Filter for today's articles, maximum of MAX_ARTICLES_PER_FEED
            for entry in feed.entries[:MAX_ARTICLES_PER_FEED]:
                published_time = datetime(*entry.published_parsed[:6])
                if published_time >= start_of_day:
                    title = clean_html(entry.title) if 'title' in entry else '无标题'
                    summary = clean_html(entry.summary) if 'summary' in entry else '暂无摘要'
                    summary = summary[:SUMMARY_MAX_LENGTH]  # Truncate to summary max length
                    if len(summary) == SUMMARY_MAX_LENGTH:
                        summary += '...'  # Add ellipsis
                    
                    new_articles.append({
                        'title': title,
                        'link': entry.link,
                        'summary': summary,
                        'source': feed.feed.title if 'title' in feed.feed else '未知来源'
                    })
            
            return new_articles
        except Exception as e:
            print(f"Attempt {attempt + 1} failed for {rss_url}: {e}")
            time.sleep(2)  # Wait 2 seconds before retry
    return []  # Return empty list after several failed attempts

def get_bing_image_urls():
    """Get a list of Bing daily image URLs."""
    try:
        response = requests.get(BING_API_URL)
        data = response.json()
        image_urls = ["https://www.bing.com" + image['url'] for image in data['images']]
        return image_urls
    except Exception as e:
        print(f"Failed to fetch Bing images: {e}")
        return []

def send_to_telegram(message, image_url=None):
    """Send a message to the Telegram channel."""
    if image_url:
        # Send image
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto"
        payload = {
            'chat_id': TELEGRAM_CHANNEL_ID,
            'photo': image_url,
        }
        response = requests.post(url, data=payload)
        
        # Check if sending was successful
        if response.status_code != 200:
            print(f"Failed to send image: {response.text}")
    
    # Send text message
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        'chat_id': TELEGRAM_CHANNEL_ID,
        'text': message,
        'parse_mode': 'Markdown'  # Use Markdown formatting
    }
    response = requests.post(url, data=payload)
    
    # Check if sending was successful
    if response.status_code != 200:
        print(f"Failed to send message: {response.text}")

def get_title_icon(source):
    """Return the emoji based on the source."""
    if '知乎' in source:
        return '📌'
    elif '36氪' in source:
        return '🔥'
    elif '抽屉' in source:
        return '🌟'
    elif '少数派' in source:
        return '📱'
    elif '虎嗅' in source:
        return '🐯'
    elif '钛媒体' in source:
        return '🚀'
    elif '微信' in source:
        return '💬'
    elif 'Appinn' in source:
        return '📲'
    elif '财新' in source:
        return '💰'
    elif 'V2EX' in source:
        return '💻'
    elif '松鼠会' in source:
        return '🐿️'
    elif '译言' in source:
        return '🌍'
    else:
        return '📰'

def split_message(articles):
    """Split article list into multiple messages ensuring each does not exceed max length."""
    messages = []
    current_message = "📰 **今日精选文章**\n\n"
    
    for article in articles:
        # Get title icon
        icon = get_title_icon(article['source'])
        
        # Build article markdown format
        article_text = (
            f"{icon} [{article['title']}]({article['link']})\n"
            f"📰 **来源**: {article['source']}\n\n"
            f"{article['summary']}\n\n"
            "--------------------\n\n"
        )
        
        # If adding a new article exceeds limits, send current message and reset
        if len(current_message) + len(article_text) > MAX_MESSAGE_LENGTH:
            messages.append(current_message)
            current_message = "📰 **今日精选文章（续）**\n\n"
        
        current_message += article_text
    
    # Add last message if not empty
    if current_message.strip() != "📰 **今日精选文章（续）**\n\n":
        messages.append(current_message)
    
    return messages

def main():
    """Main function to fetch new articles from multiple sites and send to Telegram."""
    all_articles = []
    cached_links = load_cache()  # Load previously sent articles cache

    for rss_url in RSS_FEEDS:
        new_articles = fetch_new_articles(rss_url)
        for article in new_articles:
            if article['link'] not in cached_links:  # Check if it's already sent
                all_articles.append(article)
    
    if all_articles:
        messages = split_message(all_articles)
        bing_image_urls = get_bing_image_urls()  # Fetch Bing daily images
        
        for i, message in enumerate(messages):
            # Choose different Bing image for each message
            image_url = bing_image_urls[i % len(bing_image_urls)] if bing_image_urls else None
            
            # Send image and text message
            send_to_telegram(message, image_url)
        
        # Update cache with newly sent article links
        update_cache([article['link'] for article in all_articles])
    else:
        print("今日没有新文章。")

if __name__ == "__main__":
    main()
