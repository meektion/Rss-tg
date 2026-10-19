#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
RSS -> Telegram 频道推送（图片卡片版）

特性：
  - 每条新闻一张图片卡片：图片 + 标题(超链接) + 来源·时间 + 摘要
  - 富图片提取（media_content / enclosure / 正文 img / 站点 logo），无图则本地生成渐变占位图
  - sent_articles.json 去重，同一条新闻只推一次
  - parse_mode=HTML，规避 Markdown V1 特殊字符导致发送失败的问题

环境变量：
  TELEGRAM_BOT_TOKEN    必填，机器人 token
  TELEGRAM_CHANNEL_ID   必填，目标频道（@username 或 -100xxxx）
  DRY_RUN               可选，=1 时不发送、不写缓存，仅打印将要推送的内容
"""

import os
import re
import json
import time
import html
import hashlib
import datetime as dt

import requests
import feedparser
from bs4 import BeautifulSoup

# --------------------------------------------------------------------------
# 配置
# --------------------------------------------------------------------------

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHANNEL_ID = os.getenv("TELEGRAM_CHANNEL_ID")
DRY_RUN = os.getenv("DRY_RUN") == "1"

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
FEEDS_FILE = os.path.join(BASE_DIR, "rss_feeds.txt")
# 早报/晚报专用源（国际 AI 大厂），RUN_MODE=morning/evening 时读取
REPORT_FEEDS_FILE = os.path.join(BASE_DIR, "rss_feeds_report.txt")
CACHE_FILE = os.path.join(BASE_DIR, "sent_articles.json")

# 运行模式：normal（连续推送）/ morning（早报）/ evening（晚报）
# morning / evening 会读取 REPORT_FEEDS_FILE 并先发一张报头图
RUN_MODE = os.getenv("RUN_MODE", "normal")
# 报头图兜底频道名（取不到 Telegram 频道真实标题时使用）
DEFAULT_CHANNEL_TITLE = os.getenv("CHANNEL_TITLE", "AI 每日速递")
# 报头图中文渲染字体（子集化 Noto Sans SC，约 1.5MB）
FONT_PATH = os.path.join(BASE_DIR, "assets", "fonts", "NotoSansSC-subset.ttf")

# 报头图真实背景图（缺失时自动回退纯色渐变）
BACKGROUND_DIR = os.path.join(BASE_DIR, "assets", "backgrounds")
REPORT_BACKGROUNDS = {
    "morning": os.path.join(BACKGROUND_DIR, "morning.jpg"),
    "evening": os.path.join(BACKGROUND_DIR, "evening.jpg"),
}

# 早报/晚报编号消息导语（留空则不加，如 "👀 今日 AI 大厂动态"）
REPORT_INTRO = os.getenv("REPORT_INTRO", "")
# 编号序号 emoji（1~10）
REPORT_NUMBER_EMOJI = [
    "1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟",
    "⑪", "⑫", "⑬", "⑭", "⑮", "⑯", "⑰", "⑱", "⑲", "⑳",
]
# 来源名 -> 编号消息里显示的简短公司名（小写匹配 feed title）
COMPANY_ALIAS = {
    "openai": "OpenAI",
    "deepmind": "Google DeepMind",
    "google": "谷歌",
    "microsoft": "微软",
    "apple": "苹果",
    "nvidia": "英伟达",
    "meta": "Meta",
    "github": "GitHub",
    "aws": "AWS",
    "fb": "Meta",
    "cloudflare": "Cloudflare",
    "vercel": "Vercel",
    "qwen": "通义千问",
    "shopify": "Shopify",
    "netflix": "Netflix",
    "spotify": "Spotify",
    "dropbox": "Dropbox",
    "mozilla": "Mozilla",
    "theverge": "The Verge",
    "arstechnica": "Ars Technica",
    "wired": "Wired",
    "hnrss": "Hacker News",
}

# 内置兜底源列表（rss_feeds.txt 不存在时使用）
DEFAULT_FEEDS = [
    "https://sspai.com/feed",
    "https://www.ithome.com/rss/",
    "https://www.solidot.org/index.rss",
    "https://www.qbitai.com/feed",
    "https://www.ifanr.com/feed",
    "https://www.appinn.com/feed/",
    "https://blog.cloudflare.com/rss",
    "https://github.blog/feed/",
    "https://www.databricks.com/feed",
    "https://blog.google/rss",
    "https://yihui.org/cn/index.xml",
    "https://baoyu.io/feed.xml",
]

MAX_ARTICLES_PER_FEED = 5      # 每个源每次最多推送几条
SCAN_DEPTH = 20                # 每个源最多扫描多少条 entry（用于挑选）
PREFER_TODAY = True            # 优先推送当天发布的内容
MAX_ARTICLES_PER_RUN = 40      # 单次运行最多推送几条（防止一次刷屏）
FIRST_RUN_BOOTSTRAP = False    # 首次运行（空缓存）只预热缓存、不推送
SUMMARY_MAX_LENGTH = 100       # 摘要截断长度
RETRY_COUNT = 3                # 抓取重试次数
RETRY_DELAY = 2                # 重试间隔（秒）
CAPTION_LIMIT = 1024           # Telegram 图片 caption 上限
MAX_MESSAGE_LENGTH = 4096      # Telegram 文本消息上限
MESSAGE_BLANK_LINES = 2        # 卡片之间保留一行空白，整体保持适中密度
MAX_ITEMS_PER_CARD = 20        # 一张卡片最多列几条动态（实际会被 1024 字符上限自动压减）
ITEM_GAP_LINES = 1             # 卡片内「标题+摘要」组合之间的空行数
REPORT_ITEM_GAP = 1            # 早报/晚报「编号消息」之间的空行数（调大更疏朗）
TRAILING_BLANK_LINES = 1       # 末尾少量留白，避免占用过多 caption 容量
REPORT_TRAILING_BLANK_LINES = 2  # 早报/晚报末尾留白：避免最后一行与消息时间戳贴太近
SHOW_SOURCE_HEADER = True      # 卡片顶部显示来源（如 ▎OpenAI），False 为不显示
SOURCE_HEADER_GAP = 1           # 来源行与第一条消息之间的空行数（调大更疏朗）
KEEP_DAYS = 30                 # 缓存保留天数
REQUEST_TIMEOUT = 20           # 单次请求超时
TRANSLATE_ENABLED = True       # 英文标题/摘要自动翻译为中文
TRANSLATE_TIMEOUT = 20         # 单次翻译请求超时（秒）

# DeepSeek 翻译：在 GitHub Secrets 里配置 DEEPSEEK_API_KEY 即生效
# 未配置 Key 或调用失败时，英文原文照发，不做二次翻译
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
DEEPSEEK_API_BASE = os.getenv("DEEPSEEK_API_BASE", "https://api.deepseek.com")
DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")
TRANSLATE_PROMPT = (
    "你是科技资讯翻译。把用户给的英文标题或摘要译成简洁、地道的简体中文。"
    "要求：1) 品牌名、公司名、产品名、模型名（如 OpenAI、Gemini、Qwen、NVIDIA）保持英文原样，不要音译或意译；"
    "2) 只输出译文，不要解释、不要加引号；"
    "3) 保持原意，不要扩写或删减。"
)

HEADERS = {
    # 老实报身份：微软博客对伪装浏览器的 UA 直接 403，对 bot UA 反而放行
    "User-Agent": (
        "Mozilla/5.0 (compatible; RssTgBot/1.0; "
        "+https://github.com/meektion/Rss-tg)"
    )
}

# --------------------------------------------------------------------------
# 工具函数
# --------------------------------------------------------------------------

def log(msg):
    print(msg, flush=True)


# --------------------------------------------------------------------------
# 翻译（英文 → 中文）
# --------------------------------------------------------------------------

_CJK_RE = re.compile(r"[\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7af]")
_TRANSLATE_CACHE = {}


def is_mostly_chinese(text):
    """判断文本是否以中文为主（是则无需翻译）"""
    if not text:
        return True
    cjk = len(_CJK_RE.findall(text))
    letters = len(re.findall(r"[A-Za-z]", text))
    # 中文占比超过 20%，或汉字数不少于英文字母数，都视为中文内容
    if len(text) == 0:
        return True
    if cjk / len(text) > 0.2 or cjk >= letters:
        return True
    return False


def translate_to_zh(text):
    """把英文文本翻译成中文，只用 DeepSeek。

    未配置 Key 或调用失败时，原样返回英文，不再走其它翻译接口。
    """
    if not TRANSLATE_ENABLED or not text or is_mostly_chinese(text):
        return text

    key = text[:500]
    if key in _TRANSLATE_CACHE:
        return _TRANSLATE_CACHE[key]

    result = _translate_by_deepseek(key)

    _TRANSLATE_CACHE[key] = result or text
    return result or text


def _translate_by_deepseek(text):
    """用 DeepSeek 翻译；未配置 Key 或失败时返回 None（调用方会原样返回英文）"""
    if not DEEPSEEK_API_KEY:
        return None
    try:
        resp = requests.post(
            f"{DEEPSEEK_API_BASE}/chat/completions",
            headers={
                "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": DEEPSEEK_MODEL,
                "messages": [
                    {"role": "system", "content": TRANSLATE_PROMPT},
                    {"role": "user", "content": text},
                ],
                "temperature": 0.2,
                "max_tokens": 512,
            },
            timeout=TRANSLATE_TIMEOUT,
        )
        if resp.status_code == 200:
            data = resp.json()
            choices = data.get("choices") or []
            if choices:
                result = (choices[0].get("message") or {}).get("content", "").strip()
                # 去掉模型可能包裹的引号
                result = result.strip('"“”\'')
                if result:
                    return result
        else:
            log(f"  [deepseek] HTTP {resp.status_code}: {resp.text[:120]}")
    except Exception as e:
        log(f"  [deepseek skipped] {type(e).__name__}: {e}")
    return None


def load_feeds(feeds_file=FEEDS_FILE):
    """读取指定的源列表文件，不存在则回退内置列表"""
    if not os.path.exists(feeds_file):
        log(f"[feeds] {feeds_file} 不存在，使用内置源列表")
        return list(DEFAULT_FEEDS)

    feeds, seen = [], set()
    with open(feeds_file, "r", encoding="utf-8") as f:
        for line in f:
            url = line.strip()
            if not url or url.startswith("#"):
                continue
            if url not in seen:
                seen.add(url)
                feeds.append(url)

    if not feeds:
        log(f"[feeds] {feeds_file} 为空，使用内置源列表")
        return list(DEFAULT_FEEDS)
    log(f"[feeds] 从 {os.path.basename(feeds_file)} 读取到 {len(feeds)} 个源")
    return feeds


def load_cache():
    """读取已推送记录"""
    if not os.path.exists(CACHE_FILE):
        return {}
    try:
        with open(CACHE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception as e:
        log(f"[cache] 读取失败，按空缓存处理: {e}")
        return {}


def save_cache(cache, dry_run=None):
    """写入已推送记录，并清理过期条目"""
    if dry_run is None:
        dry_run = DRY_RUN
    if dry_run:
        log(f"[cache] dry-run 模式，不写入缓存（本次共 {len(cache)} 条）")
        return
    cutoff = (dt.datetime.now() - dt.timedelta(days=KEEP_DAYS)).strftime("%Y-%m-%d")
    pruned = {
        k: v for k, v in cache.items()
        if isinstance(v, dict) and v.get("pushed_at", "")[:10] >= cutoff
    }
    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(pruned, f, ensure_ascii=False, indent=2, sort_keys=True)
    log(f"[cache] 已保存 {len(pruned)} 条记录（清理前 {len(cache)} 条）")


def normalize_link(link):
    """归一化链接作为去重 key：去掉 utm 等追踪参数和末尾斜杠"""
    if not link:
        return ""
    link = link.strip()
    link = re.sub(r"[?&](utm_[^=&]+|from|spm|share_token|ref)=[^&]*", "", link)
    link = re.sub(r"[?&]+$", "", link)
    link = re.sub(r"#.*$", "", link)
    return link.rstrip("/")


def clean_html(raw):
    """HTML 转纯文本（无标签的纯文本直接返回，避免 BeautifulSoup 误判为文件名）"""
    if not raw:
        return ""
    raw = str(raw)
    # 不含任何标签时无需解析，直接规范化空白
    if "<" not in raw or ">" not in raw:
        return re.sub(r"\s+", " ", html.unescape(raw)).strip()
    soup = BeautifulSoup(raw, "html.parser")
    for tag in soup(["script", "style"]):
        tag.decompose()
    text = soup.get_text(separator=" ", strip=True)
    return re.sub(r"\s+", " ", text).strip()


def esc(text):
    """Telegram HTML 模式转义"""
    return html.escape(text or "", quote=False)


def parse_entry_time(entry):
    """解析发布时间，返回 (datetime|None, 展示字符串)"""
    for key in ("published_parsed", "updated_parsed", "created_parsed"):
        tm = entry.get(key)
        if tm:
            try:
                stamp = dt.datetime(*tm[:6])
                return stamp, stamp.strftime("%m-%d %H:%M")
            except Exception:
                pass
    return None, ""


def extract_image(entry, feed):
    """按优先级提取配图 URL"""
    # 1) media_content / media_thumbnail
    for key in ("media_content", "media_thumbnail"):
        for item in entry.get(key, []) or []:
            url = item.get("url")
            if url and url.startswith("http") and not is_bad_image(url, item.get("width"), item.get("height")):
                return url

    # 2) enclosure
    for item in entry.get("enclosures", []) or []:
        href = item.get("href")
        mime = (item.get("type") or "").lower()
        if href and href.startswith("http") and ("image" in mime or is_image_url(href)):
            if not is_bad_image(href, item.get("width"), item.get("height")):
                return href

    # 3) 正文里的第一张图
    for key in ("content", "summary", "description"):
        raw = ""
        if key == "content":
            contents = entry.get("content") or []
            if contents:
                raw = contents[0].get("value", "")
        else:
            raw = entry.get(key, "") or ""
        if not raw:
            continue
        soup = BeautifulSoup(raw, "html.parser")
        for img in soup.find_all("img"):
            w = _to_int(img.get("width"))
            h = _to_int(img.get("height"))
            for attr in ("src", "data-src", "data-original", "data-lazy-src"):
                url = (img.get(attr) or "").strip()
                if not url.startswith("http"):
                    continue
                if is_bad_image(url, w, h):
                    continue
                return url

    # 4) 站点 logo（仅在尺寸不明且 URL 不含明显图标特征时使用）
    href = (feed.get("image") or {}).get("href") or ""
    if href.startswith("http") and not is_bad_image(href, None, None):
        return href

    return None


def _to_int(value):
    """把 '32' / '32px' 这类宽度写法转成 int"""
    if value is None:
        return None
    m = re.search(r"\d+", str(value))
    return int(m.group()) if m else None


def is_image_url(url):
    return bool(re.search(r"\.(jpe?g|png|webp|gif|bmp)(\?|$)", url, re.I))


# URL 中出现这些词，几乎可以确定不是正文配图（表情、图标、占位、装饰）
BAD_IMAGE_PATTERNS = re.compile(
    r"("
    r"favicon|sprite|avatar|gravatar|emoji|emoticon|smiley|smilie|sticker"
    r"|spacer|blank\.|placeholder|loading|default[-_]?img|no[-_]?image"
    r"|pixel|beacon|1x1|transparent"
    r"|/(icon|icons|logo|logos|badge|badges|button|buttons|arrow|arrows)/"
    r"|[-_](icon|logo|badge|arrow)\b"
    r"|/static/(img|images)/(icon|logo)"
    r"|wechat|weixin|qrcode|qr[-_]?code|erweima"
    r"|share[-_]?(icon|img)|social[-_]?(icon|img)"
    r"|wordpress[-_]?logo|cms[-_]?logo"
    r")",
    re.I,
)

# 小尺寸标识，如 16x16 / 32x32 / 64x64
SMALL_SIZE_PATTERN = re.compile(r"\b(\d{1,3})x(\d{1,3})\b")


def is_bad_image(url, width=None, height=None):
    """判断这张图是否不适合当封面（小图标、表情、装饰图、追踪像素等）"""
    if not url:
        return True

    # 1) 声明尺寸过小
    w = _to_int(width)
    h = _to_int(height)
    if (w and w < 300) or (h and h < 200):
        return True

    # 2) URL 命中已知的图标/表情/装饰特征
    if BAD_IMAGE_PATTERNS.search(url):
        return True

    # 3) URL 里写着小尺寸（长边小于 300 的标识）
    for m in SMALL_SIZE_PATTERN.finditer(url):
        sw, sh = int(m.group(1)), int(m.group(2))
        if sw < 300 and sh < 300:
            return True

    # 4) 旧式表情/图标路径特征
    if re.search(r"/(smilies|emoticons|images/smilies)/", url, re.I):
        return True

    # 5) GIF 多为表情/动图，不适合当封面
    if re.search(r"\.gif(\?|$)", url, re.I):
        return True

    # 6) SVG 是矢量图标，不会是文章配图
    if re.search(r"\.svg(\?|$)", url, re.I):
        return True

    # 7) 箭头 / 装饰性小图（带 -icon、-arrow 等后缀）
    if re.search(r"[-_](right|left|up|down|next|prev|back)[-_]?(arrow|icon|btn)?\.(png|jpg|jpeg|webp)", url, re.I):
        return True

    # 8) 模板占位符未被替换（如 <link or path of image for opengraph>）
    if re.search(r"%3[cC]|%20or%20path|<link|>|<|>", url):
        return True

    return False



# --------------------------------------------------------------------------
# RSS 抓取
# --------------------------------------------------------------------------

def fetch_feed(url):
    """抓取并解析单个 RSS 源，失败重试"""
    for attempt in range(1, RETRY_COUNT + 1):
        try:
            resp = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
            if resp.status_code >= 400:
                raise RuntimeError(f"HTTP {resp.status_code}")
            parsed = feedparser.parse(resp.content)
            if parsed.entries:
                return parsed
            raise RuntimeError("no entries")
        except Exception as e:
            log(f"  [retry {attempt}/{RETRY_COUNT}] {url} -> {type(e).__name__}: {e}")
            if attempt < RETRY_COUNT:
                time.sleep(RETRY_DELAY)
    return None


def collect_articles(feeds, cache):
    """遍历所有源，返回本轮待推送的文章列表"""
    articles, stats = [], {"ok": 0, "fail": 0, "dup": 0, "with_img": 0, "translated": 0}

    today = dt.date.today()

    for url in feeds:
        parsed = fetch_feed(url)
        if not parsed:
            stats["fail"] += 1
            log(f"[fail] {url}")
            continue
        stats["ok"] += 1

        source = (parsed.feed.get("title") or url).strip()

        today_entries, other_entries = [], []
        for entry in parsed.entries[:SCAN_DEPTH]:
            stamp, _ = parse_entry_time(entry)
            if stamp and stamp.date() == today:
                today_entries.append(entry)
            else:
                other_entries.append(entry)

        # 优先当天发布的内容；不足时用未见过的历史内容补齐
        candidates = today_entries if PREFER_TODAY else []
        candidates += other_entries

        picked = 0
        for entry in candidates:
            if picked >= MAX_ARTICLES_PER_FEED:
                break

            raw_link = entry.get("link") or entry.get("id") or ""
            key = normalize_link(raw_link)
            if not key:
                continue

            if key in cache:
                stats["dup"] += 1
                continue

            _, stamp_str = parse_entry_time(entry)
            raw_title = clean_html(entry.get("title")) or "无标题"
            raw_summary = clean_html(entry.get("summary") or entry.get("description"))

            # 英文内容自动翻译为中文
            title = translate_to_zh(raw_title)
            summary = translate_to_zh(raw_summary) if raw_summary else ""
            if title != raw_title or summary != raw_summary:
                stats["translated"] += 1

            if len(summary) > SUMMARY_MAX_LENGTH:
                summary = summary[:SUMMARY_MAX_LENGTH].rstrip() + "…"

            image = extract_image(entry, parsed.feed)
            if image:
                stats["with_img"] += 1

            articles.append({
                "key": key,
                "link": raw_link,
                "title": title,
                "summary": summary,
                "source": source,
                "time_str": stamp_str,
                "image": image,
            })
            picked += 1

    log(f"[stats] 源 {stats['ok']} 成功 / {stats['fail']} 失败；"
        f"待推 {len(articles)} 条，跳过重复 {stats['dup']} 条，"
        f"自带配图 {stats['with_img']} 条，翻译 {stats['translated']} 条")
    return articles


# --------------------------------------------------------------------------
# Telegram 发送
# --------------------------------------------------------------------------

def build_caption(items):
    """构建一张卡片（一个公司的多条动态）的 caption（HTML）

    形态（卡片顶部写一次来源，逐条不再重复）：
        ▎OpenAI
        标题1
        摘要1

        标题2
        摘要2

        ...
    标题本身即原文链接。超出 caption 上限时从末尾整条裁掉，保证条目不残缺。
    """
    sep = "\n" * MESSAGE_BLANK_LINES
    gap = "\n" * ITEM_GAP_LINES
    return _compose(items, sep, gap, CAPTION_LIMIT)


def build_text_message(items):
    """纯文本兜底：图片完全不可用时使用，形态与 caption 一致"""
    sep = "\n" * MESSAGE_BLANK_LINES
    gap = "\n" * ITEM_GAP_LINES
    return _compose(items, sep, gap, MAX_MESSAGE_LENGTH)


def _source_header(items):
    """卡片顶部的来源行，如 ▎OpenAI；同卡片同来源"""
    if not items or not SHOW_SOURCE_HEADER:
        return ""
    source = (items[0].get("source") or "").strip()
    if not source:
        return ""
    return f"<b>▎{esc(source)}</b>"


def _html_text_length(value):
    """计算 HTML 解析后的可见字符数，避免把标签和 href 属性误算进 Telegram 限额"""
    return len(BeautifulSoup(value or "", "html.parser").get_text())


def _truncate_text(value, max_length):
    """安全截断纯文本；截断发生在 HTML 转义之前"""
    value = (value or "").strip()
    if max_length <= 0:
        return ""
    if len(value) <= max_length:
        return value
    if max_length == 1:
        return "…"
    return value[:max_length - 1].rstrip() + "…"


def _compose(items, sep, gap, limit):
    """把标题和摘要按条目拼装；超长时裁掉完整条目，不直接切割 HTML。"""
    if not items:
        return ""

    header = _source_header(items)
    head = (f"{header}\n" + "\n" * SOURCE_HEADER_GAP) if header else ""

    def render(articles, title_limit=None, summary_limit=None, include_header=True):
        blocks = []
        for index, art in enumerate(articles, 1):
            raw_title = (art.get("title") or "未命名文章").strip()
            raw_summary = (art.get("summary") or "").strip()
            if title_limit is not None:
                raw_title = _truncate_text(raw_title, title_limit)
            if summary_limit is not None:
                raw_summary = _truncate_text(raw_summary, summary_limit)

            title = esc(raw_title)
            link = html.escape(art.get("link") or "", quote=True)
            if link:
                title_line = f'<b>{index}. <a href="{link}">{title}</a></b>'
            else:
                title_line = f"<b>{index}. {title}</b>"
            block = [title_line]
            if raw_summary:
                block.append(esc(raw_summary))
            blocks.append(gap.join(block))

        prefix = head if include_header else ""
        return prefix + sep.join(blocks)

    selected = list(items[:MAX_ITEMS_PER_CARD])
    text = render(selected)

    # 优先移除完整文章块，不让标题、链接或 HTML 标签残缺。
    while _html_text_length(text) > limit and len(selected) > 1:
        selected.pop()
        text = render(selected)

    if _html_text_length(text) > limit and selected:
        # 极端情况下单条内容也超限：先缩短摘要，再缩短显示标题；原始链接不变。
        article = selected[0]
        title_len = len((article.get("title") or "").strip())
        summary_len = len((article.get("summary") or "").strip())
        candidates = [
            (min(title_len, 180), min(summary_len, 80)),
            (min(title_len, 140), min(summary_len, 60)),
            (min(title_len, 100), min(summary_len, 40)),
            (min(title_len, 70), min(summary_len, 20)),
            (min(title_len, 50), 0),
            (min(title_len, 30), 0),
            (min(title_len, 15), 0),
        ]
        for title_limit, summary_limit in candidates:
            text = render(selected, title_limit, summary_limit)
            if _html_text_length(text) <= limit:
                break

        # 来源名或其他极端长字段也不能导致 HTML 被硬切断。
        if _html_text_length(text) > limit:
            text = render(selected, 15, 0, include_header=False)

    # Telegram 会自动裁掉末尾空白；零宽字符保留少量、可控的底部留白。
    if TRAILING_BLANK_LINES > 0 and _html_text_length(text) + TRAILING_BLANK_LINES <= limit:
        text += "\n" * TRAILING_BLANK_LINES + "\u200b"
    return text

# 同一进程内避免重复使用相同的 Picsum seed。
_USED_COVER_SEEDS = set()


def pick_random_cover(article):
    """为无原文配图的来源卡片选择稳定的随机封面。

    每篇文章的封面保持稳定；同一次运行中尽量避免重复 seed。
    """
    article_key = article.get("key") or article.get("link") or article.get("title") or "rss-article"
    for salt in range(20):
        digest = hashlib.md5(f"{article_key}:{salt}".encode("utf-8")).hexdigest()
        seed = digest[:16]
        if seed not in _USED_COVER_SEEDS:
            _USED_COVER_SEEDS.add(seed)
            return f"https://picsum.photos/seed/{seed}/1200/630"

    # 极少数情况下候选 seed 都已用过，仍然返回可用封面地址。
    seed = hashlib.md5(article_key.encode("utf-8")).hexdigest()[:16]
    return f"https://picsum.photos/seed/{seed}/1200/630"

def send_photo(items, image_path_or_url):
    """发送一张卡片；本地文件走 multipart 上传"""
    api = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto"
    data = {
        "chat_id": TELEGRAM_CHANNEL_ID,
        "caption": build_caption(items),
        "parse_mode": "HTML",
    }

    if isinstance(image_path_or_url, str) and image_path_or_url.startswith("http"):
        data["photo"] = image_path_or_url
        resp = requests.post(api, data=data, timeout=60)
    else:
        with open(image_path_or_url, "rb") as fh:
            resp = requests.post(api, data=data, files={"photo": fh}, timeout=60)

    if resp.status_code != 200:
        raise RuntimeError(f"HTTP {resp.status_code}: {resp.text[:180]}")
    return resp.json()


def send_text(items):
    """发送纯文本消息（无配图时使用）"""
    api = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    resp = requests.post(api, data={
        "chat_id": TELEGRAM_CHANNEL_ID,
        "text": build_text_message(items),
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }, timeout=60)
    if resp.status_code != 200:
        raise RuntimeError(f"HTTP {resp.status_code}: {resp.text[:180]}")


def group_by_source(articles):
    """把文章按来源分组，每组内保持原有顺序（已按时间排序）

    返回 [(source, [article, ...]), ...]，按组内第一条出现的先后排列。
    """
    groups = {}
    order = []
    for art in articles:
        src = art["source"]
        if src not in groups:
            groups[src] = []
            order.append(src)
        groups[src].append(art)
    return [(src, groups[src][:MAX_ITEMS_PER_CARD]) for src in order]


def push_card(items):
    """推送一个来源的卡片：优先使用组内可用原图，否则使用随机封面。"""
    if not items:
        return False

    # 来源合并后，不一定是第一篇文章有图；优先取组内第一张可用原文配图。
    image_article = next((art for art in items if art.get("image")), None)
    photo = image_article["image"] if image_article else pick_random_cover(items[0])

    if DRY_RUN:
        kind = "原文配图" if image_article else "随机封面"
        log(f"  [dry-run] [{kind}] {items[0]['source']} | {len(items)} 条")
        for index, art in enumerate(items, 1):
            log(f"              {index}. {art['title'][:52]}")
        log(f"              image={photo}")
        return True

    try:
        send_photo(items, photo)
        return True
    except Exception as e:
        log(f"  [sendPhoto failed] {type(e).__name__}: {e}")
        try:
            send_text(items)
            log("  [fallback] 图片发送失败，已改为纯文本发送")
            return True
        except Exception as e2:
            log(f"  [sendMessage failed] {type(e2).__name__}: {e2}")
            return False


# --------------------------------------------------------------------------
# 报头图（早报 / 晚报）
# --------------------------------------------------------------------------

_CHANNEL_INFO = None


def _channel_info():
    """从 Telegram 获取频道标题与 username（带缓存）。返回 (title, username)"""
    global _CHANNEL_INFO
    if _CHANNEL_INFO is not None:
        return _CHANNEL_INFO
    title, username = DEFAULT_CHANNEL_TITLE, ""
    if TELEGRAM_BOT_TOKEN and TELEGRAM_CHANNEL_ID:
        try:
            resp = requests.get(
                f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getChat",
                params={"chat_id": TELEGRAM_CHANNEL_ID},
                timeout=REQUEST_TIMEOUT,
            )
            if resp.status_code == 200:
                data = resp.json()
                if data.get("ok"):
                    result = data.get("result") or {}
                    title = result.get("title") or title
                    username = result.get("username") or ""
        except Exception as e:
            log(f"  [getChat skipped] {type(e).__name__}: {e}")
    _CHANNEL_INFO = (title, username)
    return _CHANNEL_INFO


def get_channel_title():
    """频道真实名称；失败回退默认值"""
    return _channel_info()[0]


def get_channel_username():
    """频道公开 username（私有频道为空）"""
    return _channel_info()[1]


def _beijing_now():
    """返回北京时间（UTC+8）的 datetime"""
    return dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=8)


def generate_report_cover(mode):
    """生成早报 / 晚报报头图：真实背景图 + 遮罩 + 文字。缺失背景图时回退纯色渐变。"""
    if not os.path.exists(FONT_PATH):
        log(f"  [cover] 字体缺失 {FONT_PATH}，跳过报头图")
        return None
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        log("  [cover] 未安装 Pillow，跳过报头图")
        return None

    W, H = 1200, 630
    now = _beijing_now()
    label = "早报" if mode == "morning" else "晚报"
    channel = get_channel_title()
    month_en = ["January", "February", "March", "April", "May", "June",
                "July", "August", "September", "October", "November", "December"][now.month - 1]

    # 1) 背景：优先真实图片，缺失回退纯色渐变
    img = None
    bg_path = REPORT_BACKGROUNDS.get(mode)
    if bg_path and os.path.exists(bg_path):
        try:
            bg = Image.open(bg_path).convert("RGB")
            ratio = max(W / bg.width, H / bg.height)
            nw, nh = max(1, int(bg.width * ratio)), max(1, int(bg.height * ratio))
            bg = bg.resize((nw, nh), Image.LANCZOS)
            left, top = (nw - W) // 2, (nh - H) // 2
            img = bg.crop((left, top, left + W, top + H)).convert("RGBA")
        except Exception as e:
            log(f"  [cover] 背景图加载失败：{e}")
            img = None

    if img is None:
        top_c, bottom_c = ((255, 226, 196), (255, 178, 148)) if mode == "morning" else ((46, 46, 92), (16, 16, 38))
        img = Image.new("RGB", (W, H), bottom_c).convert("RGBA")
        d0 = ImageDraw.Draw(img)
        for y in range(H):
            t = y / H
            d0.line([(0, y), (W, y)],
                    fill=tuple(int(top_c[i] + (bottom_c[i] - top_c[i]) * t) for i in range(3)))

    # 2) 遮罩：早报左侧提亮（配深色字），晚报左侧压暗（配白字），保证文字可读
    if mode == "morning":
        shade, max_alpha = (255, 255, 255), 168
    else:
        shade, max_alpha = (0, 0, 0), 185
    overlay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    od = ImageDraw.Draw(overlay)
    for x in range(W):
        a = int(max_alpha * max(0.0, 1 - x / (W * 0.8)))
        if a > 0:
            od.line([(x, 0), (x, H)], fill=shade + (a,))
    img = Image.alpha_composite(img, overlay).convert("RGB")
    draw = ImageDraw.Draw(img)

    # 3) 文字配色
    if mode == "morning":
        fg_strong, fg_soft = (74, 46, 26), (108, 76, 48)
    else:
        fg_strong, fg_soft = (255, 255, 255), (216, 222, 236)

    try:
        font_channel = ImageFont.truetype(FONT_PATH, 54)
        font_big = ImageFont.truetype(FONT_PATH, 132)
        font_small = ImageFont.truetype(FONT_PATH, 36)
        font_en = ImageFont.truetype(FONT_PATH, 42)
    except Exception as e:
        log(f"  [cover] 字体加载失败：{e}")
        return None

    # 顶部：频道名 + 早报/晚报（大字）
    draw.text((80, 120), channel, font=font_channel, fill=fg_soft)
    draw.text((78, 206), label, font=font_big, fill=fg_strong)
    # 底部：中文日期 + 英文月份年份
    weekday = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"][now.weekday()]
    draw.text((82, 468), now.strftime("%Y 年 %m 月 %d 日") + "  " + weekday, font=font_small, fill=fg_soft)
    draw.text((82, 522), f"{month_en} {now.year}", font=font_en, fill=fg_soft)

    out = os.path.join(BASE_DIR, f"_report_cover_{mode}.jpg")
    img.save(out, "JPEG", quality=90, optimize=True)
    return out


def company_alias(source):
    """把 feed 的来源名映射成简短公司名"""
    s = (source or "").lower()
    for key, alias in COMPANY_ALIAS.items():
        if key in s:
            return alias
    return source


def _with_trailing_padding(text, limit):
    """末尾补空行 + 零宽字符，避免最后一行与 Telegram 时间戳/气泡底边贴太近。

    Telegram 会裁掉纯空白，必须用 U+200B 撑住；同时确保不超出当前消息上限。
    """
    if REPORT_TRAILING_BLANK_LINES <= 0:
        return text
    padding = "\n" * REPORT_TRAILING_BLANK_LINES + "\u200b"
    if _html_text_length(text + padding) <= limit:
        return text + padding
    return text


def build_report_messages(groups, mode):
    """构建完整早报/晚报消息列表；超长时分段发送，不丢弃末尾公司。"""
    now = _beijing_now()
    label = "早报" if mode == "morning" else "晚报"
    emoji = "🌅" if mode == "morning" else "🌃"
    heading = f"#News\n{emoji}{label}【{now.month}.{now.day}】"
    if REPORT_INTRO:
        heading += "\n" + esc(REPORT_INTRO)

    entries = []
    # 每个来源取最新一条作为本期摘要；其余文章不应被标记为已推送。
    for index, (source, items) in enumerate(groups, 1):
        if not items:
            continue
        art = items[0]
        company = esc(_truncate_text(company_alias(source), 50))
        raw_title = art.get("title") or "未命名文章"
        link = html.escape(art.get("link") or "", quote=True)
        title_limit = 300
        def render_entry(limit):
            title = esc(_truncate_text(raw_title, limit))
            number = REPORT_NUMBER_EMOJI[index - 1] if index <= len(REPORT_NUMBER_EMOJI) else f"{index}."
            if link:
                return f"{number} <b>{company}</b> <a href=\"{link}\">{title}</a>"
            return f"{number} <b>{company}</b> {title}"

        entry = render_entry(title_limit)
        # 单条链接标题也必须能装进 Telegram 消息；必要时仅缩短显示标题，不改链接。
        while _html_text_length(entry) > MAX_MESSAGE_LENGTH - 100 and title_limit > 20:
            title_limit = max(20, title_limit // 2)
            entry = render_entry(title_limit)
        entries.append((source, art, entry))

    messages = []
    current_limit = CAPTION_LIMIT
    current_lines = [heading, ""]
    current_items = 0

    for _, _, entry in entries:
        candidate_lines = current_lines + [entry, ""]
        candidate = "\n".join(candidate_lines)
        if _html_text_length(candidate) > current_limit and (current_items or current_limit == CAPTION_LIMIT):
            # Caption is limited to 1024 chars. If the first entry does not fit beside the cover,
            # send a heading-only cover caption and move the complete entry into a text continuation.
            if current_items or not messages:
                messages.append(_with_trailing_padding("\n".join(current_lines).strip(), current_limit))
            current_limit = MAX_MESSAGE_LENGTH
            current_lines = [f"{heading}\n（续）", ""]
            current_items = 0
            candidate_lines = current_lines + [entry, ""]
            candidate = "\n".join(candidate_lines)

        if _html_text_length(candidate) > current_limit:
            # Preserve valid HTML and article links; a pathological URL cannot be safely truncated.
            raise ValueError(f"报告单条内容超出 Telegram 限制：{entries[len(messages)][0] if len(messages) < len(entries) else 'unknown'}")

        current_lines = candidate_lines
        current_items += 1

    if current_items or not messages:
        messages.append(_with_trailing_padding("\n".join(current_lines).strip(), current_limit))
    return messages


def build_report_caption(groups, mode):
    """兼容旧调用：返回首条报告消息（完整发送请使用 build_report_messages）。"""
    return build_report_messages(groups, mode)[0]

def send_cover_photo(image_path, caption=""):
    """发送报头图（可选 caption），本地文件走 multipart 上传"""
    api = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto"
    data = {"chat_id": TELEGRAM_CHANNEL_ID, "parse_mode": "HTML"}
    if caption:
        data["caption"] = caption
    with open(image_path, "rb") as fh:
        resp = requests.post(api, data=data, files={"photo": fh}, timeout=60)
    if resp.status_code != 200:
        raise RuntimeError(f"HTTP {resp.status_code}: {resp.text[:180]}")
    return resp.json()


def send_plain_text(text):
    """发送纯文本（编号消息无报头图时的兜底）"""
    api = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    resp = requests.post(api, data={
        "chat_id": TELEGRAM_CHANNEL_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }, timeout=60)
    if resp.status_code != 200:
        raise RuntimeError(f"HTTP {resp.status_code}: {resp.text[:180]}")


def push_report(groups, mode):
    """早报/晚报：报头图 + 必要时的续篇消息；所有条目分段发送，不静默丢弃。"""
    messages = build_report_messages(groups, mode)
    cover = generate_report_cover(mode)
    if DRY_RUN:
        log(f"  [dry-run] 报头图：{cover}（{mode}）")
        log(f"  [dry-run] 报告共 {len(messages)} 条消息：")
        for message_index, message in enumerate(messages, 1):
            log(f"  [dry-run] 第 {message_index}/{len(messages)} 条：")
            for line in message.split("\n"):
                log("    " + line)
        return True
    try:
        if cover:
            send_cover_photo(cover, caption=messages[0])
        else:
            send_plain_text(messages[0])
        for message in messages[1:]:
            time.sleep(1.2)
            send_plain_text(message)
        return True
    except Exception as e:
        log(f"  [report failed] {type(e).__name__}: {e}")
        return False
    finally:
        if cover:
            try:
                os.remove(cover)
            except OSError:
                pass


# --------------------------------------------------------------------------
# 主流程
# --------------------------------------------------------------------------

def main():
    if not DRY_RUN and (not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHANNEL_ID):
        raise SystemExit("缺少环境变量 TELEGRAM_BOT_TOKEN / TELEGRAM_CHANNEL_ID")

    mode = RUN_MODE if RUN_MODE in ("morning", "evening") else "normal"
    feeds_file = REPORT_FEEDS_FILE if mode != "normal" else FEEDS_FILE

    feeds = load_feeds(feeds_file)
    cache = load_cache()
    log(f"[cache] 已加载 {len(cache)} 条历史记录")
    log(f"[mode] {mode}")

    articles = collect_articles(feeds, cache)

    # 首次运行（缓存为空）：只把当前存量标记为已见，不推送，避免一上来刷屏
    if not cache and FIRST_RUN_BOOTSTRAP and articles:
        log(f"[bootstrap] 缓存为空，将 {len(articles)} 条存量标记为已见，本次不推送")
        ts = dt.datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
        for article in articles:
            cache[article["key"]] = {
                "title": article["title"],
                "source": article["source"],
                "pushed_at": ts,
            }
        save_cache(cache)
        return

    if not articles:
        if mode != "normal":
            # 报告模式：无新内容时静默跳过，不打扰频道
            log(f"[mode] {mode} 今日无新动态，静默跳过（不推送）")
            return
        log("今日没有新文章。")
        return

    # 单次总量上限，避免一次刷屏
    if len(articles) > MAX_ARTICLES_PER_RUN:
        log(f"[limit] 待推 {len(articles)} 条，本次只推前 {MAX_ARTICLES_PER_RUN} 条，其余留待下次")
        articles = articles[:MAX_ARTICLES_PER_RUN]

    # 按来源（公司）分组：一个公司一张卡片
    groups = group_by_source(articles)
    log(f"[group] {len(articles)} 条动态，来自 {len(groups)} 家公司")

    if mode != "normal":
        # 早报/晚报：一张报头图 + 一条编号消息，一次发出
        if push_report(groups, mode):
            ts = dt.datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
            # 仅缓存报告中实际展示的每个来源最新一条；未展示的同源文章留待后续推送。
            for _, items in groups:
                if not items:
                    continue
                art = items[0]
                cache[art["key"]] = {"title": art["title"], "source": art["source"], "pushed_at": ts}
            save_cache(cache)
            log(f"[done] {mode} 一期推送完成，收录 {sum(1 for _, items in groups if items)} 家公司")
        else:
            log(f"[warn] {mode} 推送失败，未写缓存，下次运行重试")
        return

    ok = 0
    fail = 0
    for source, items in groups:
        if push_card(items):
            ok += 1
            # 整组发送成功才写入缓存
            for art in items:
                cache[art["key"]] = {
                    "title": art["title"],
                    "source": art["source"],
                    "pushed_at": dt.datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
                }
        else:
            fail += 1
        time.sleep(1.2)  # 温和限速，避免触发 Telegram 频率限制

    log(f"[done] 成功推送 {ok}/{len(groups)} 张卡片，失败 {fail} 张")
    save_cache(cache)

    # 全部失败通常意味着 Token / 频道配置有问题，必须让 CI 变红，避免静默空跑
    if groups and fail == len(groups):
        raise SystemExit(
            f"全部 {fail} 张卡片推送失败，请检查 TELEGRAM_BOT_TOKEN 与 TELEGRAM_CHANNEL_ID 是否正确"
        )
    if fail:
        log(f"[warn] 有 {fail} 张卡片推送失败，失败项未写入缓存，下次运行会自动重试")


if __name__ == "__main__":
    main()
