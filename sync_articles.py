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
CACHE_FILE = os.path.join(BASE_DIR, "sent_articles.json")
PLACEHOLDER_DIR = os.path.join(BASE_DIR, ".placeholder_cache")

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
SUMMARY_MAX_LENGTH = 160       # 摘要截断长度
RETRY_COUNT = 3                # 抓取重试次数
RETRY_DELAY = 2                # 重试间隔（秒）
CAPTION_LIMIT = 1024           # Telegram 图片 caption 上限
KEEP_DAYS = 30                 # 缓存保留天数
REQUEST_TIMEOUT = 20           # 单次请求超时

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
}

# 占位图渐变配色（深色系，与 Telegram 深色主题协调）
GRADIENTS = [
    ((43, 88, 118), (78, 67, 118)),
    ((30, 60, 114), (42, 115, 148)),
    ((118, 52, 84), (58, 42, 94)),
    ((26, 90, 82), (37, 60, 110)),
    ((122, 74, 42), (92, 40, 62)),
    ((44, 62, 110), (96, 66, 122)),
    ((70, 40, 100), (28, 78, 106)),
    ((36, 84, 60), (72, 92, 40)),
]

CJK_FONT_CANDIDATES = [
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/opentype/noto/NotoSerifCJK-Bold.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
]


# --------------------------------------------------------------------------
# 工具函数
# --------------------------------------------------------------------------

def log(msg):
    print(msg, flush=True)


def load_feeds():
    """读取 rss_feeds.txt，不存在则回退内置列表"""
    if not os.path.exists(FEEDS_FILE):
        log(f"[feeds] {FEEDS_FILE} 不存在，使用内置源列表")
        return list(DEFAULT_FEEDS)

    feeds, seen = [], set()
    with open(FEEDS_FILE, "r", encoding="utf-8") as f:
        for line in f:
            url = line.strip()
            if not url or url.startswith("#"):
                continue
            if url not in seen:
                seen.add(url)
                feeds.append(url)

    if not feeds:
        log("[feeds] rss_feeds.txt 为空，使用内置源列表")
        return list(DEFAULT_FEEDS)
    log(f"[feeds] 从 rss_feeds.txt 读取到 {len(feeds)} 个源")
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
            if url and url.startswith("http") and not is_tiny_icon(url, item.get("width"), item.get("height")):
                return url

    # 2) enclosure
    for item in entry.get("enclosures", []) or []:
        href = item.get("href")
        mime = (item.get("type") or "").lower()
        if href and href.startswith("http") and ("image" in mime or is_image_url(href)):
            if not is_tiny_icon(href, item.get("width"), item.get("height")):
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
            for attr in ("src", "data-src", "data-original", "data-lazy-src"):
                url = (img.get(attr) or "").strip()
                if not url.startswith("http"):
                    continue
                if is_tracking_pixel(url):
                    continue
                w = _to_int(img.get("width"))
                h = _to_int(img.get("height"))
                if is_tiny_icon(url, w, h):
                    continue
                return url

    # 4) 站点 logo（仅在尺寸不明且 URL 不含明显图标特征时使用）
    href = (feed.get("image") or {}).get("href") or ""
    if href.startswith("http") and not is_tiny_icon(href, None, None):
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


def is_tracking_pixel(url):
    return bool(re.search(r"(spacer|pixel|blank|1x1|stat\.|beacon)", url, re.I))


def is_tiny_icon(url, width=None, height=None):
    """判断是否为站点小图标（这类图当配图会非常难看）"""
    w = _to_int(width)
    h = _to_int(height)
    if (w and w < 200) or (h and h < 200):
        return True
    # 文件名里带尺寸标识的，如 icon-32x32.png、logo-64.png、favicon.ico
    if re.search(r"(favicon|icon|logo|avatar|sprite)[-_]?(\d{1,3})?", url, re.I):
        if re.search(r"\b\d{1,3}x\d{1,3}\b", url):
            return True
        if re.search(r"(favicon|/icon|_icon|icon\.)", url, re.I):
            return True
    if re.search(r"\b(16|24|32|48|64)x(16|24|32|48|64)\b", url):
        return True
    return False


# --------------------------------------------------------------------------
# 占位图生成
# --------------------------------------------------------------------------

_FONT_CACHE = {}


def get_font(size):
    """加载可显示中文的字体，带缓存"""
    if size in _FONT_CACHE:
        return _FONT_CACHE[size]
    from PIL import ImageFont
    for path in CJK_FONT_CANDIDATES:
        if os.path.exists(path):
            try:
                font = ImageFont.truetype(path, size)
                _FONT_CACHE[size] = font
                return font
            except Exception:
                continue
    font = ImageFont.load_default()
    _FONT_CACHE[size] = font
    return font


def wrap_text(text, font, max_width, draw, max_lines):
    """按像素宽度折行（中英混排）"""
    lines, current = [], ""
    for ch in text:
        if ch == "\n":
            lines.append(current)
            current = ""
            continue
        test = current + ch
        if draw.textlength(test, font=font) <= max_width:
            current = test
        else:
            lines.append(current)
            current = ch
            if len(lines) >= max_lines:
                break
    if current and len(lines) < max_lines:
        lines.append(current)
    if len(lines) >= max_lines and draw.textlength(lines[-1] + "…", font=font) <= max_width:
        lines[-1] = lines[-1][: max(0, len(lines[-1]) - 1)] + "…"
    return lines


def make_placeholder(title, source, out_path):
    """生成标题哈希渐变占位图，返回文件路径"""
    from PIL import Image, ImageDraw

    W, H = 1280, 640
    digest = hashlib.md5((source + "|" + title).encode("utf-8")).hexdigest()
    idx = int(digest[:8], 16) % len(GRADIENTS)
    top, bottom = GRADIENTS[idx]

    img = Image.new("RGB", (W, H), top)
    draw = ImageDraw.Draw(img)

    # 垂直渐变
    for y in range(H):
        ratio = y / (H - 1)
        color = tuple(int(top[i] + (bottom[i] - top[i]) * ratio) for i in range(3))
        draw.line([(0, y), (W, y)], fill=color)

    # 左侧色条
    accent = tuple(min(255, c + 70) for c in bottom)
    draw.rectangle([0, 0, 14, H], fill=accent)

    # 站点名
    font_src = get_font(38)
    draw.text((64, 56), source[:24], font=font_src, fill=(255, 255, 255, 235))

    # 标题
    font_title = get_font(58)
    lines = wrap_text(title, font_title, W - 128, draw, max_lines=4)
    y = 190
    for line in lines:
        draw.text((64, y), line, font=font_title, fill=(255, 255, 255))
        y += 78

    # 底部装饰
    font_foot = get_font(28)
    draw.text((64, H - 72), "RSS · Telegram", font=font_foot, fill=(255, 255, 255, 150))

    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    img.save(out_path, "JPEG", quality=88)
    return out_path


def get_placeholder(title, source):
    """占位图带本地缓存，避免重复渲染"""
    key = hashlib.md5((source + "|" + title).encode("utf-8")).hexdigest()[:16]
    path = os.path.join(PLACEHOLDER_DIR, f"{key}.jpg")
    if os.path.exists(path):
        return path
    return make_placeholder(title, source, path)


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
    articles, stats = [], {"ok": 0, "fail": 0, "dup": 0, "with_img": 0}

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
            title = clean_html(entry.get("title")) or "无标题"
            summary = clean_html(entry.get("summary") or entry.get("description"))
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
        f"待推 {len(articles)} 条，跳过重复 {stats['dup']} 条，其中自带配图 {stats['with_img']} 条")
    return articles


# --------------------------------------------------------------------------
# Telegram 发送
# --------------------------------------------------------------------------

def build_caption(article):
    """构建 HTML 格式 caption"""
    title = esc(article["title"])
    link = article["link"]
    parts = [f'<b><a href="{html.escape(link, quote=True)}">{title}</a></b>']

    meta = esc(article["source"])
    if article["time_str"]:
        meta += f" · {esc(article['time_str'])}"
    parts.append(f"<i>{meta}</i>")

    if article["summary"]:
        parts.append(esc(article["summary"]))

    caption = "\n\n".join(parts)
    if len(caption) > CAPTION_LIMIT:
        caption = caption[: CAPTION_LIMIT - 1].rstrip() + "…"
    return caption


def send_photo(article, image_path_or_url):
    """发送图片卡片；本地文件走 multipart 上传"""
    api = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto"
    caption = build_caption(article)
    data = {
        "chat_id": TELEGRAM_CHANNEL_ID,
        "caption": caption,
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


def send_text(article):
    """纯文本兜底（图片上传失败或源带有超长内容时）"""
    api = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    resp = requests.post(api, data={
        "chat_id": TELEGRAM_CHANNEL_ID,
        "text": build_caption(article),
        "parse_mode": "HTML",
        "disable_web_page_preview": False,
    }, timeout=60)
    if resp.status_code != 200:
        raise RuntimeError(f"HTTP {resp.status_code}: {resp.text[:180]}")


def push_article(article):
    """推送单条，返回是否成功"""
    if DRY_RUN:
        log(f"  [dry-run] {article['source']} | {article['title'][:40]}")
        log(f"            link={article['link']}")
        log(f"            image={article['image'] or '(需生成占位图)'}")
        return True

    photo = article["image"]
    if not photo:
        photo = get_placeholder(article["title"], article["source"])

    try:
        send_photo(article, photo)
        return True
    except Exception as e:
        log(f"  [sendPhoto failed] {type(e).__name__}: {e}")
        # 图片链接失效时改用占位图重试一次
        if isinstance(photo, str) and photo.startswith("http"):
            try:
                send_photo(article, get_placeholder(article["title"], article["source"]))
                return True
            except Exception as e2:
                log(f"  [placeholder failed] {type(e2).__name__}: {e2}")
        try:
            send_text(article)
            log("  [fallback] 已改为纯文本发送")
            return True
        except Exception as e3:
            log(f"  [text failed] {type(e3).__name__}: {e3}")
            return False


# --------------------------------------------------------------------------
# 主流程
# --------------------------------------------------------------------------

def main():
    if not DRY_RUN and (not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHANNEL_ID):
        raise SystemExit("缺少环境变量 TELEGRAM_BOT_TOKEN / TELEGRAM_CHANNEL_ID")

    feeds = load_feeds()
    cache = load_cache()
    log(f"[cache] 已加载 {len(cache)} 条历史记录")

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
        log("今日没有新文章。")
        return

    # 单次总量上限，避免一次刷屏
    if len(articles) > MAX_ARTICLES_PER_RUN:
        log(f"[limit] 待推 {len(articles)} 条，本次只推前 {MAX_ARTICLES_PER_RUN} 条，其余留待下次")
        articles = articles[:MAX_ARTICLES_PER_RUN]

    ok = 0
    fail = 0
    for article in articles:
        if push_article(article):
            ok += 1
            cache[article["key"]] = {
                "title": article["title"],
                "source": article["source"],
                "pushed_at": dt.datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
            }
        else:
            fail += 1
        time.sleep(1.2)  # 温和限速，避免触发 Telegram 频率限制

    log(f"[done] 成功推送 {ok}/{len(articles)} 条，失败 {fail} 条")
    save_cache(cache)

    # 全部失败通常意味着 Token / 频道配置有问题，必须让 CI 变红，避免静默空跑
    if articles and fail == len(articles):
        raise SystemExit(
            f"全部 {fail} 条推送失败，请检查 TELEGRAM_BOT_TOKEN 与 TELEGRAM_CHANNEL_ID 是否正确"
        )
    if fail:
        log(f"[warn] 有 {fail} 条推送失败，失败项未写入缓存，下次运行会自动重试")


if __name__ == "__main__":
    main()
