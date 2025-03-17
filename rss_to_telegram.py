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
        print(f"Failed to send message: {response.text}")

def fetch_rss_feed(url):
    """解析RSS源并提取前5条内容"""
    feed = feedparser.parse(url)
    if feed.bozo:
        print(f"Failed to parse RSS feed: {feed.bozo_exception}")
        return []
    return feed.entries[:5]

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
    return re.sub(r"([_*
 $$$$ ()~`>\#\+\-=|\.!])", r"\\\1", text)

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

            title = escape_markdown(entry.title)
            summary = escape_markdown(entry.summary[:100].replace("\n", " "))  # 截取摘要的前100个字符，并处理换行符

            # 美化消息内容（使用Markdown格式）
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

    # 保存已推送的消息链接
    save_sent_items(sent_items)

if __name__ == "__main__":
    main()
