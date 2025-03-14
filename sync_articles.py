import os
import requests
import feedparser
from datetime import datetime
from bs4 import BeautifulSoup

# 配置
TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
TELEGRAM_CHANNEL_ID = os.getenv('TELEGRAM_CHANNEL_ID')
RSS_FEEDS = [
    'https://feedx.net/rss/zhihudaily.xml',  # 第一个网站的 RSS 地址
    'http://dig.chouti.com/feed.xml',  # 第二个网站的 RSS 地址
    'https://36kr.com/feed',  # 第三个网站的 RSS 地址
    'https://sspai.com/feed',  # 第三个网站的 RSS 地址
    'https://www.huxiu.com/rss/0.xml',  # 第三个网站的 RSS 地址
    'http://www.tmtpost.com/feed',  # 第三个网站的 RSS 地址
    'https://a.jiemian.com/index.php?m=article&a=rss',  # 第三个网站的 RSS 地址
    'https://wechat2rss.xlab.app/feed/923c0e2f33b6d39c8a826a90f185725f0edb10e8.xml',  
    'https://feeds.appinn.com/appinns/',  # 第三个网站的 RSS 地址
    'http://blog.caixin.com/feed',  # 第三个网站的 RSS 地址
    'https://www.v2ex.com/feed/tab/tech.xml',  # 第三个网站的 RSS 地址
    'http://songshuhui.net/feed',  # 第三个网站的 RSS 地址
    'https://www.gcores.com/rss',  # 第三个网站的 RSS 地址
    'http://feed.yixieshi.com/',  # 第三个网站的 RSS 地址
]
MAX_MESSAGE_LENGTH = 4096  # Telegram 消息长度限制
SUMMARY_MAX_LENGTH = 200  # 摘要最大长度

def clean_html(html):
    """清理 HTML 标签，提取纯文本"""
    soup = BeautifulSoup(html, 'html.parser')
    return soup.get_text().strip()

def fetch_new_articles(rss_url):
    """从指定 RSS 源获取当天的新文章"""
    feed = feedparser.parse(rss_url)
    new_articles = []
    
    # 获取当天日期
    today = datetime.now()
    start_of_day = datetime(today.year, today.month, today.day)
    
    # 筛选当天的文章
    for entry in feed.entries:
        published_time = datetime(*entry.published_parsed[:6])
        if published_time >= start_of_day:
            # 清理标题和摘要
            title = clean_html(entry.title) if 'title' in entry else '无标题'
            summary = clean_html(entry.summary) if 'summary' in entry else '暂无摘要'
            summary = summary[:SUMMARY_MAX_LENGTH]  # 截取前 200 字符
            if len(summary) == SUMMARY_MAX_LENGTH:
                summary += '...'  # 添加省略号
            
            new_articles.append({
                'title': title,
                'link': entry.link,
                'summary': summary,
                'source': feed.feed.title if 'title' in feed.feed else '未知来源'
            })
    
    return new_articles

def send_to_telegram(message):
    """发送消息到 Telegram 频道"""
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

def split_message(articles):
    """将文章列表分割为多条消息，确保每条消息不超过最大长度"""
    messages = []
    current_message = " **今日精选文章**\n\n"
    
    for article in articles:
        # 构建单篇文章的 Markdown 格式
        article_text = (
            f" [{article['title']}]({article['link']})\n"
            f"**来源**: {article['source']}\n\n"
            f"{article['summary']}\n\n"
            "---\n\n"  # 添加分隔线
        )
        
        # 如果当前消息加上新文章后超过限制，则发送当前消息并重置
        if len(current_message) + len(article_text) > MAX_MESSAGE_LENGTH:
            messages.append(current_message)
            current_message = " **今日精选文章（续）**\n\n"
        
        current_message += article_text
    
    # 添加最后一条消息
    if current_message.strip() != " **今日精选文章（续）**\n\n":
        messages.append(current_message)
    
    return messages

def main():
    """主函数：获取多个网站的新文章并发送到 Telegram"""
    all_articles = []
    for rss_url in RSS_FEEDS:
        try:
            new_articles = fetch_new_articles(rss_url)
            all_articles.extend(new_articles)
        except Exception as e:
            print(f"Error fetching {rss_url}: {e}")
    
    if all_articles:
        messages = split_message(all_articles)
        for message in messages:
            send_to_telegram(message)
    else:
        print("今日没有新文章。")

if __name__ == "__main__":
    main()
