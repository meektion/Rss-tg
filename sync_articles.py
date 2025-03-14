import requests
import feedparser
import os
from datetime import datetime, timedelta

# 配置
TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
TELEGRAM_CHANNEL_ID = os.getenv('TELEGRAM_CHANNEL_ID')
RSS_FEED_URL = 'https://example.com/feed'  # 替换为你要同步的网站 RSS 地址

def fetch_new_articles():
    # 解析 RSS 源
    feed = feedparser.parse(RSS_FEED_URL)
    new_articles = []
    
    # 获取当天日期
    today = datetime.now()
    start_of_day = datetime(today.year, today.month, today.day)
    
    # 筛选当天的文章
    for entry in feed.entries:
        published_time = datetime(*entry.published_parsed[:6])
        if published_time >= start_of_day:
            new_articles.append(entry)
    
    return new_articles

def send_to_telegram(article):
    message = f"{article.title}\n\n{article.link}"
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        'chat_id': TELEGRAM_CHANNEL_ID,
        'text': message
    }
    requests.post(url, data=payload)

def main():
    new_articles = fetch_new_articles()
    for article in new_articles:
        send_to_telegram(article)

if __name__ == "__main__":
    main()
