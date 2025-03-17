import feedparser
import requests
from datetime import datetime
import re

# 配置
TELEGRAM_TOKEN = "your_telegram_bot_token"  # 替换为你的Telegram Bot Token
TELEGRAM_CHAT_ID = "your_telegram_chat_id"  # 替换为你的Telegram频道Chat ID
TELEGRAM_API_URL = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
BING_IMAGE_API_URL = "https://www.bing.com/HPImageArchive.aspx?format=js&idx=0&n=1&mkt=en-US"

# RSS源列表
RSS_SOURCES = [
    {"name": "FT中文网", "url": "http://feeds.feedburner.com/ftchina"},
    {"name": "极客公园", "url": "http://feeds.geekpark.net/"},
    {"name": "糗事百科", "url": "http://feed.feedsky.com/qiushi"}
]

# 用于记录已推送的消息
SENT_ITEMS_FILE = "sent_items.txt"

def send_telegram_message(message):
    """发送Markdown格式的消息到Telegram频道"""
    data = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "MarkdownV2"  # 使用MarkdownV2格式
    }
    response = requests.post(TELEGRAM_API_URL, json=data)
    if response.status_code != 200:
        print(f"Failed to send message: {response.json()}")

def fetch_rss_feed(url):
    """解析RSS源并提取前5条内容"""
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
    """获取Bing每日一图"""
    response = requests.get(BING_IMAGE_API_URL)
    if response.status_code == 200:
        data = response.json()
        image_url = f"https://www.bing.com{data['images'][0]['url']}"
        return image_url
    else:
        print("Failed to fetch Bing daily image")
        return "https://example.com/image.jpg"  # 默认图片

def load_sent_items():
    """加载已推送的消息链接"""
    try:
        with open(SENT_ITEMS_FILE, "r") as file:
            return file.read().splitlines()
    except FileNotFoundError:
        return []

def save_sent_items(sent_items):
    """保存已推送的消息链接"""
    with open(SENT_ITEMS_FILE, "w") as file:
        file.write("\n".join(sent_items))

def escape_markdown(text):
    """转义MarkdownV2中的特殊字符"""
    markdown_chars = r"\_*[]()~`>#+-=|.!{}"
    for char in markdown_chars:
        text = text.replace(char, f"\\{char}")
    return text

def main():
    sent_items = load_sent_items()
    bing_image_url = get_bing_daily_image()

