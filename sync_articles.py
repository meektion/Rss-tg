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
    'https://sspai.com/feed',  # 第四个网站的 RSS 地址
    'https://www.huxiu.com/rss/0.xml',  # 第五个网站的 RSS 地址
    'http://www.tmtpost.com/feed',  # 第六个网站的 RSS 地址
    'https://wechat2rss.xlab.app/feed/923c0e2f33b6d39c8a826a90f185725f0edb10e8.xml',  # 第七个网站的 RSS 地址
    'https://feeds.appinn.com/appinns/',  # 第八个网站的 RSS 地址
    'http://blog.caixin.com/feed',  # 第九个网站的 RSS 地址
    'https://www.v2ex.com/feed/tab/tech.xml',  # 第十个网站的 RSS 地址
    'http://songshuhui.net/feed',  # 第十一个网站的 RSS 地址
    'https://www.gcores.com/rss',  # 第十二个网站的 RSS 地址
    'http://feed.yixieshi.com/',  # 第十三个网站的 RSS 地址
]
MAX_MESSAGE_LENGTH = 4096  # Telegram 消息长度限制
SUMMARY_MAX_LENGTH = 200  # 摘要最大长度
MAX_ARTICLES_PER_FEED = 5  # 每个网站最多抓取 5 条文章

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
    elif '机核' in source:
        return '🎮'  # 机核文章标记为游戏
    elif '译言' in source:
        return '🌍'  # 译言文章标记为国际
    else:
        return '📰'  # 默认标记为新闻

def split_message(articles):
    """将文章列表分割为多条消息，确保每条消息不超过最大长度"""
    messages = []
    current_message = "📰 **今日精选文章**\n\n"
    
    for article in articles:
        # 获取标题前的表情符号
        icon = get_title_icon(article['source'])
        
        # 构建单篇文章的 Markdown 格式
        article_text = (
            f"{icon} **{article['title']}**\n"  # 标题前加表情符号
            f"📰 **来源**: {article['source']}\n\n"  # 来源前加表情符号
            f"> {article['summary']}\n\n"  # 摘要使用引用格式
            "✨🌸✨🌸✨🌸✨🌸✨\n\n"  # 美化分隔线
        )
        
        # 如果当前消息加上新文章后超过限制，则发送当前消息并重置
        if len(current_message) + len(article_text) > MAX_MESSAGE_LENGTH:
            messages.append(current_message)
            current_message = "📰 **今日精选文章（续）**\n\n"
        
        current_message += article_text
    
    # 添加最后一条消息
    if current_message.strip() != "📰 **今日精选文章（续）**\n\n":
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
