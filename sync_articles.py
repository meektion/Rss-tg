import os
import requests
import feedparser
from datetime import datetime
from bs4 import BeautifulSoup
import time

# 配置
TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
TELEGRAM_CHANNEL_ID = os.getenv('TELEGRAM_CHANNEL_ID')
RSS_FEEDS = [
    'https://feedx.net/rss/zhihudaily.xml',  # 第一个网站的 RSS 地址
    'http://dig.chouti.com/feed.xml',  # 第二个网站的 RSS 地址
    'https://36kr.com/feed',  # 第三个网站的 RSS 地址
    'https://sspai.com/feed',  # 第四个网站的 RSS 地址
    'https://www.huxiu.com/rss/0.xml',  # 第五个网站的 RSS 地址
    'http://www.tmtpost.com/feed',  # 第六个网站的 RSS 地址
    'https://feeds.appinn.com/appinns/',  # 第八个网站的 RSS 地址
    'http://blog.caixin.com/feed',  # 第九个网站的 RSS 地址
    'https://www.v2ex.com/feed/tab/tech.xml',  # 第十个网站的 RSS 地址
    'http://songshuhui.net/feed',  # 第十一个网站的 RSS 地址
    'http://feed.yixieshi.com/',  # 第十二个网站的 RSS 地址
    'https://www.jiqizhixin.com/rss',
    'https://deepmind.com/blog/feed/basic/',
    'https://azure.microsoft.com/en-us/blog/feed/',
    'https://netflixtechblog.com/feed',
    'https://engineering.fb.com/feed/',
    'https://www.solidot.org/index.rss',
    'http://blog.stackoverflow.com/feed/',
    'https://yihui.org/cn/index.xml',
    'https://baoyu.io/feed.xml',
    'https://blog.langchain.dev/rss/',
    'https://www.qbitai.com/feed',
    'https://huggingface.co/blog/feed.xml',
    'https://aws.amazon.com/blogs/amazon-ai/feed/',
    'https://machinelearning.apple.com/rss.xml',
    'https://blog.timescale.com/feed',
    'https://grafana.com/categories/engineering/index.xml',
    'https://blog.ycombinator.com/feed/',
    'https://www.databricks.com/feed',
]
MAX_MESSAGE_LENGTH = 4096  # Telegram 消息长度限制
SUMMARY_MAX_LENGTH = 200  # 摘要最大长度
MAX_ARTICLES_PER_FEED = 5  # 每个网站最多抓取 5 条文章
RETRY_COUNT = 3  # RSS 源抓取重试次数

def clean_html(html):
    """清理 HTML 标签，提取纯文本"""
    soup = BeautifulSoup(html, 'html.parser')
    return soup.get_text().strip()

def extract_image_url(entry):
    """从文章内容中提取图片 URL"""
    if 'content' in entry:
        soup = BeautifulSoup(entry.content[0].value, 'html.parser')
        img_tag = soup.find('img')
        if img_tag and 'src' in img_tag.attrs:
            return img_tag['src']
    return None

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
                    summary = summary[:SUMMARY_MAX_LENGTH]  # 截取前 200 字符
                    if len(summary) == SUMMARY_MAX_LENGTH:
                        summary += '...'  # 添加省略号
                    
                    # 提取图片 URL
                    image_url = extract_image_url(entry)
                    
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
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto"
        payload = {
            'chat_id': TELEGRAM_CHANNEL_ID,
            'photo': image_url,
            'caption': message[:MAX_MESSAGE_LENGTH],  # 确保消息长度不超过限制
            'parse_mode': 'Markdown'  # 使用 Markdown 格式
        }
    else:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        payload = {
            'chat_id': TELEGRAM_CHANNEL_ID,
            'text': message[:MAX_MESSAGE_LENGTH],  # 确保消息长度不超过限制
            'parse_mode': 'Markdown'  # 使用 Markdown 格式
        }
    
    response = requests.post(url, data=payload)
    
    # 检查是否发送成功
    if response.status_code != 200:
        print(f"Failed to send message: {response.text}")

def split_message(articles):
    """将文章列表分割为多条独立的消息，每条消息包含一篇文章"""
    messages = []
    
    for article in articles:
        # 构建单篇文章的 Markdown 格式
        article_text = (
            f"[{article['title']}]({article['link']})\n"  # 标题改为超链接
            f"**来源**: {article['source']}\n\n"  # 来源
            f"{article['summary']}\n\n"  # 摘要
            "--------------------\n\n"  # 分隔线
        )
        
        # 将单篇文章作为一条独立的消息
        messages.append({
            'text': article_text,
            'image_url': article['image_url']
        })
    
    return messages

def main():
    """主函数：获取多个网站的新文章并发送到 Telegram"""
    all_articles = []
    for rss_url in RSS_FEEDS:
        new_articles = fetch_new_articles(rss_url)
        all_articles.extend(new_articles)
    
    if all_articles:
        messages = split_message(all_articles)
        
        for message in messages:
            # 发送图片和文字消息
            send_to_telegram(message['text'], message['image_url'])
    else:
        print("今日没有新文章。")

if __name__ == "__main__":
    main()
