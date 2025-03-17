import feedparser
import requests
from datetime import datetime
import re

TELEGRAM_TOKEN = "your_telegram_bot_token"
TELEGRAM_CHAT_ID = "your_telegram_chat_id"
TELEGRAM_API_URL = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
BING_IMAGE_API_URL = "https://www.bing.com/HPImageArchive.aspx?format=js&idx=0&n=1&mkt=en-US"

RSS_SOURCES = [
    {"name": "FT中文网", "url": "http://feeds.feedburner.com/ftchina"},
    {"name": "极客公园", "url": "http://feeds.geekpark.net/"},
    {"name": "糗事百科", "url": "http://feed.feedsky.com/qiushi"}
]

SENT_ITEMS_FILE = "sent_items.txt"

def send_telegram_message(message):
    max_length = 4096
    while len(message) > max_length:
        segment = message[:max_length]
        message = message[max_length:]
        data = {
            "chat_id": TELEGRAM_CHAT_ID,
            "text": segment,
            "parse_mode": "MarkdownV2"
        }
        response = requests.post(TELEGRAM_API_URL, json=data)
        if response.status_code != 200:
            print(f"Failed to send message segment: {response.json()}")

    if message:
        data = {
            "chat_id": TELEGRAM_CHAT_ID,
            "text": message,
            "parse_mode": "MarkdownV2"
        }
        response = requests.post(TELEGRAM_API_URL, json=data)
        if response.status_code != 200:
            print(f"Failed to send final segment: {response.json()}")

def fetch_rss_feed(url):
    try:
        feed = feedparser.parse(url)
        if feed.bozo:
            print(f"Failed to parse RSS feed: {feed.bozo_exception}")
            return []
        return feed.entries[:5]
    except Exception as e:
        print(f"Error parsing RSS feed from {url}: {e}")
        return []

def get_bing_daily_image():
    response = requests.get(BING_IMAGE_API_URL)
    if response.status_code == 200:
        data = response.json()
        return f"https://www.bing.com{data['images'][0]['url']}"
    else:
        print("Failed to fetch Bing daily image")
        return "https://example.com/image.jpg"

def load_sent_items():
    try:
        with open(SENT_ITEMS_FILE, "r") as file:
            return file.read().splitlines()
    except FileNotFoundError:
        return []

def save_sent_items(sent_items):
    with open(SENT_ITEMS_FILE, "w") as file:
        file.write("\n".join(sent_items))

def escape_markdown(text):
    markdown_chars = r"\_*[]()~`>#+-=|.!{}"
    for char in markdown_chars:
        text = text.replace(char, f"\\{char}")
    return text

def main():
    sent_items = load_sent_items()
    bing_image_url = get_bing_daily_image()

    for source in RSS_SOURCES:
        print(f"Processing {source['name']}...")
        entries = fetch_rss_feed(source["url"])
        for entry in entries:
            link = entry.link
            if link in sent_items:
                print(f"Skipping already sent item: {link}")
                continue

            title = escape_markdown(entry.title[:100])  # 截断标题
            summary = escape_markdown(entry.summary[:200].replace("\n", " "))  # 截断摘要

            message = f"""
            *{source['name']}*
            [{title}]({link})
            ![Bing Daily Image]({bing_image_url})
            _{summary}_
            *{datetime.now().strftime('%Y-%m-%d %H:%M')}*
            """

            send_telegram_message(message)
            sent_items.append(link)
            print(f"Sent: {title}")

    save_sent_items(sent_items)

if __name__ == "__main__":
    main()
