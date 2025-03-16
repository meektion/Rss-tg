import os
import aiohttp
import asyncio
import feedparser
from datetime import datetime
from bs4 import BeautifulSoup
import logging

# 配置
TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
TELEGRAM_CHANNEL_ID = os.getenv('TELEGRAM_CHANNEL_ID')
RSS_FEEDS_FILE = "rss_feeds.txt"  # 存储 RSS 订阅链接的文件
MAX_MESSAGE_LENGTH = 4096  # Telegram 消息长度限制
SUMMARY_MAX_LENGTH = 100  # 摘要最大长度
MAX_ARTICLES_PER_FEED = 5  # 每个网站最多抓取 5 条文章
RETRY_COUNT = 3  # RSS 源抓取重试次数

# 配置日志记录
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

def load_rss_feeds():
    """从文件中加载 RSS 订阅链接"""
    if not os.path.exists(RSS_FEEDS_FILE):
        logging.error(f"RSS feeds file '{RSS_FEEDS_FILE}' not found.")
        return []
    
    with open(RSS_FEEDS_FILE, "r", encoding="utf-8") as file:
        feeds = [line.strip() for line in file if line.strip()]
    return feeds

def clean_html(html):
    """清理 HTML 标签，提取纯文本"""
    soup = BeautifulSoup(html, 'html.parser')
    return soup.get_text().strip()

def parse_entry_time(entry):
    """解析条目的发布时间"""
    if hasattr(entry, 'published_parsed'):
        return datetime(*entry.published_parsed[:6])
    elif hasattr(entry, 'updated_parsed'):
        return datetime(*entry.updated_parsed[:6])
    elif hasattr(entry, 'published'):
        try:
            return datetime.strptime(entry.published, '%a, %d %b %Y %H:%M:%S %z')
        except ValueError:
            pass
    elif hasattr(entry, 'updated'):
        try:
            return datetime.strptime(entry.updated, '%a, %d %b %Y %H:%M:%S %z')
        except ValueError:
            pass
    return None

async def fetch_feed(session, rss_url):
    """异步抓取 RSS 源"""
    for attempt in range(RETRY_COUNT):
        try:
            async with session.get(rss_url) as response:
                if response.status != 200:
                    logging.error(f"Attempt {attempt + 1} failed for {rss_url}: HTTP {response.status}")
                    continue
                content = await response.text()
                feed = feedparser.parse(content)
                return feed
        except Exception as e:
            logging.error(f"Attempt {attempt + 1} failed for {rss_url}: {e}")
            await asyncio.sleep(2)  # 等待 2 秒后重试
    return None

async def fetch_new_articles(session, rss_url):
    """从指定 RSS 源获取当天的新文章"""
    feed = await fetch_feed(session, rss_url)
    if not feed:
        return []
    
    new_articles = []
    today = datetime.now()
    start_of_day = datetime(today.year, today.month, today.day)
    
    for entry in feed.entries[:MAX_ARTICLES_PER_FEED]:
        published_time = parse_entry_time(entry)
        if published_time is None:
            logging.warning(f"Skipping entry due to missing time info: {entry.link}")
            continue
        
        if published_time >= start_of_day:
            title = clean_html(entry.title) if 'title' in entry else '无标题'
            summary = clean_html(entry.summary) if 'summary' in entry else '暂无摘要'
            summary = summary[:SUMMARY_MAX_LENGTH]  # 截取前 100 字符
            if len(summary) == SUMMARY_MAX_LENGTH:
                summary += '...'  # 添加省略号
            
            new_articles.append({
                'title': title,
                'link': entry.link,
                'summary': summary,
                'source': feed.feed.title if 'title' in feed.feed else '未知来源'
            })
    
    return new_articles

async def send_to_telegram(message):
    """发送消息到 Telegram 频道"""
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        'chat_id': TELEGRAM_CHANNEL_ID,
        'text': message,
        'parse_mode': 'Markdown'
    }
    
    async with aiohttp.ClientSession() as session:
        async with session.post(url, data=payload) as response:
            if response.status != 200:
                logging.error(f"Failed to send message: {await response.text()}")

def get_title_icon(source):
    """根据来源返回标题前的表情符号"""
    icon_map = {
        '知乎': '📌',
        '36氪': '🔥',
        '抽屉': '🌟',
        '少数派': '📱',
        '虎嗅': '🐯',
        '钛媒体': '🚀',
        '微信': '💬',
        'Appinn': '📲',
        '财新': '💰',
        'V2EX': '💻',
        '松鼠会': '🐿️',
        '译言': '🌍'
    }
    for key, icon in icon_map.items():
        if key in source:
            return icon
    return '📰'  # 默认标记为新闻

async def main():
    """主函数：获取多个网站的新文章并发送到 Telegram"""
    rss_feeds = load_rss_feeds()
    if not rss_feeds:
        logging.error("No RSS feeds found.")
        return
    
    all_articles = []
    async with aiohttp.ClientSession() as session:
        tasks = [fetch_new_articles(session, rss_url) for rss_url in rss_feeds]
        results = await asyncio.gather(*tasks)
        for articles in results:
            all_articles.extend(articles)
    
    if all_articles:
        for article in all_articles:
            icon = get_title_icon(article['source'])
            message = (
                f"{icon} [{article['title']}]({article['link']})\n"
                f"📰 **来源**: {article['source']}\n\n"
                f"{article['summary']}"
            )
            await send_to_telegram(message)
    else:
        logging.info("今日没有新文章。")

if __name__ == "__main__":
    asyncio.run(main())
