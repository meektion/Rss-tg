import os
import requests
import feedparser
from datetime import datetime
from bs4 import BeautifulSoup
import time

# 配置
TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
TELEGRAM_CHANNEL_ID = os.getenv('TELEGRAM_CHANNEL_ID')
RSS_FEEDS_FILE = "rss_feeds.txt"  # 存储 RSS 订阅链接的文件
MAX_MESSAGE_LENGTH = 4096  # Telegram 消息长度限制
SUMMARY_MAX_LENGTH = 100  # 摘要最大长度
MAX_ARTICLES_PER_FEED = 5  # 每个网站最多抓取 5 条文章
BING_API_URL = "https://www.bing.com/HPImageArchive.aspx?format=js&idx=0&n=10&mkt=zh-CN"  # Bing 每日一图 API
RETRY_COUNT = 3  # RSS 源抓取重试次数

def load_rss_feeds():
    """从文件中加载 RSS 订阅链接"""
    if not os.path.exists(RSS_FEEDS_FILE):
        print(f"RSS feeds file '{RSS_FEEDS_FILE}' not found.")
        return []
    
    with open(RSS_FEEDS_FILE, "r", encoding="utf-8") as file:
        feeds = [line.strip() for line in file if line.strip()]
    return feeds

def clean_html(html):
    """清理 HTML 标签，提取纯文本"""
    soup = BeautifulSoup(html, 'html.parser')
    return soup.get_text().strip()

def fetch_new_articles(rss_url):
    """从指定 RSS 源获取当天的新文章"""
    for attempt in range(RETRY_COUNT):
        try:
            feed = feedparser.parse(rss_url)
            new_articles = []
            
            # 获取当天日期
            today = datetime.now()
            start_of_day = datetime(today.year, today.month, today.day)
            
            # 筛选当天的文章，最多抓取 MAX_ARTICLES_PER_FEED 条
            for entry in feed.entries[:MAX_ARTICLES_PER_FEED]:
                published_time = datetime(*entry.published_parsed[:6])
                if published_time >= start_of_day:
                    # 清理标题和摘要
                    title = clean_html(entry.title) if 'title' in entry else '无标题'
                    summary = clean_html(entry.summary) if 'summary' in entry else '暂无摘要'
                    summary = summary[:SUMMARY_MAX_LENGTH]  # 截取前 100 字符
                    if len(summary) == SUMMARY_MAX_LENGTH:
                        summary += '...'  # 添加省略号
                    
                    # 提取图片（如果有）
                    image_url = None
                    if 'media_content' in entry:
                        for media in entry.media_content:
                            if media.get('type', '').startswith('image'):
                                image_url = media['url']
                                break
                    
                    new_articles.append({
                        'title': title,
                        'link': entry.link,
                        'summary': summary,
                        'source': feed.feed.title if 'title' in feed.feed else '未知来源',
                        'image_url': image_url
                    })
            
            return new_articles
        except Exception as e:
            print(f"Attempt {attempt + 1} failed for {rss_url}: {e}")
            time.sleep(2)  # 等待 2 秒后重试
    return []  # 重试多次后仍失败，返回空列表

def send_to_telegram(message, image_url=None):
    """发送消息到 Telegram 频道"""
    if image_url:
        # 发送图片和文字组合消息
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto"
        payload = {
            'chat_id': TELEGRAM_CHANNEL_ID,
            'photo': image_url,
            'caption': message,
            'parse_mode': 'Markdown'  # 使用 Markdown 格式
        }
    else:
        # 仅发送文字消息
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        payload = {
            'chat_id': TELEGRAM_CHANNEL_ID,
            'text': message,
            'parse_mode': 'Markdown'  # 使用 Markdown 格式
        }
    
    response = requests.post(url, data=payload)
    
    # 检查是否发送成功
    if response.status_code != 200:
        print(f"Failed to send message: {response.text}")

def get_title_icon(source):
    """根据来源返回标题前的表情符号"""
    if '知乎' in source:
        return '📌'  # 知乎文章标记为重要
    elif '36氪' in source:
        return '🔥'  # 36氪文章标记为热门
    elif '抽屉' in source:
        return '🌟'  # 抽屉文章标记为推荐
    elif '少数派' in source:
        return '📱'  # 少数派文章标记为科技
    elif '虎嗅' in source:
        return '🐯'  # 虎嗅文章标记为商业
    elif '钛媒体' in source:
        return '🚀'  # 钛媒体文章标记为创新
    elif '微信' in source:
        return '💬'  # 微信文章标记为社交
    elif 'Appinn' in source:
        return '📲'  # Appinn 文章标记为应用
    elif '财新' in source:
        return '💰'  # 财新文章标记为财经
    elif 'V2EX' in source:
        return '💻'  # V2EX 文章标记为技术
    elif '松鼠会' in source:
        return '🐿️'  # 松鼠会文章标记为科普
    elif '译言' in source:
        return '🌍'  # 译言文章标记为国际
    else:
        return '📰'  # 默认标记为新闻

def main():
    """主函数：获取多个网站的新文章并发送到 Telegram"""
    rss_feeds = load_rss_feeds()
    if not rss_feeds:
        print("No RSS feeds found.")
        return
    
    all_articles = []
    for rss_url in rss_feeds:
        new_articles = fetch_new_articles(rss_url)
        all_articles.extend(new_articles)
    
    if all_articles:
        for article in all_articles:
            # 获取标题前的表情符号
            icon = get_title_icon(article['source'])
            
            # 构建单篇文章的 Markdown 格式
            message = (
                f"{icon} [{article['title']}]({article['link']})\n"  # 标题改为超链接
                f"📰 **来源**: {article['source']}\n\n"  # 来源前加表情符号
                f"{article['summary']}"  # 摘要
            )
            
            # 发送消息
            send_to_telegram(message, article.get('image_url'))
    else:
        print("今日没有新文章。")

if __name__ == "__main__":
    main()
