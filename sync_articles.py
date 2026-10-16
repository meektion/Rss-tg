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
MAX_MESSAGE_LENGTH = 4096      # Telegram 文本消息上限
MESSAGE_BLANK_LINES = 3        # 消息内部各段落之间的空行数（调大更疏朗，2~4 较合适）
MAX_ITEMS_PER_CARD = 10        # 一个源（公司）的卡片里最多列几条动态
ITEM_GAP_LINES = 1             # 卡片内每条动态之间的空行数
KEEP_DAYS = 30                 # 缓存保留天数
REQUEST_TIMEOUT = 20           # 单次请求超时
TRANSLATE_ENABLED = True       # 英文标题/摘要自动翻译为中文
TRANSLATE_TIMEOUT = 10         # 单次翻译请求超时（秒）

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
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
    """把英文文本翻译成中文；失败或无需翻译时原样返回"""
    if not TRANSLATE_ENABLED or not text or is_mostly_chinese(text):
        return text

    key = text[:500]
    if key in _TRANSLATE_CACHE:
        return _TRANSLATE_CACHE[key]

    try:
        resp = requests.get(
            "https://aidemo.youdao.com/trans",
            params={"q": key, "from": "en", "to": "zh-CHS"},
            headers=HEADERS,
            timeout=TRANSLATE_TIMEOUT,
        )
        if resp.status_code == 200:
            data = resp.json()
            if data.get("errorCode") == "0":
                translated = data.get("translation") or []
                if translated:
                    result = "".join(translated).strip()
                    if result:
                        _TRANSLATE_CACHE[key] = result
                        return result
    except Exception as e:
        log(f"  [translate skipped] {type(e).__name__}: {e}")

    _TRANSLATE_CACHE[key] = text
    return text


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
    """构建「一个源（公司）」的卡片 caption（HTML）

    形态：来源名 / 该源的若干条动态（每条可点击）/ 每条后跟一行短摘要（可选）
    items: 同一来源的文章列表（已按时间排序）
    """
    source = esc(items[0]["source"])
    sep = "\n" * MESSAGE_BLANK_LINES
    gap = "\n" * ITEM_GAP_LINES

    parts = [f"<b>{source}</b>"]

    # 该源今天共有几条
    count_line = f"<code>今日 {len(items)} 条</code>"
    parts.append(count_line)

    lines = []
    body = []
    for art in items:
        title = esc(art["title"])
        link = html.escape(art["link"], quote=True)
        body.append(f'<a href="{link}">{title}</a>')

    # 动态列表用紧凑换行，条目之间只留少量空行
    if body:
        parts.append(gap.join(body))

    text = sep.join(parts)

    # 超长时逐条裁掉末尾动态，保证链接不被截断
    while len(text) > CAPTION_LIMIT and body:
        body.pop()
        rest = sep.join([f"<b>{source}</b>", count_line, gap.join(body)]) if body else sep.join([f"<b>{source}</b>", count_line])
        text = rest
    if len(text) > CAPTION_LIMIT:
        text = text[: CAPTION_LIMIT - 1].rstrip() + "…"
    return text


def build_text_message(items):
    """纯文本兜底：图片完全不可用时使用（来源 + 多条动态）"""
    source = esc(items[0]["source"])
    sep = "\n" * MESSAGE_BLANK_LINES
    gap = "\n" * ITEM_GAP_LINES

    parts = [f"<b>{source}</b>", f"<code>今日 {len(items)} 条</code>"]

    body = []
    for art in items:
        title = esc(art["title"])
        link = html.escape(art["link"], quote=True)
        body.append(f'<a href="{link}">{title}</a>')
    if body:
        parts.append(gap.join(body))

    text = sep.join(parts)
    while len(text) > MAX_MESSAGE_LENGTH and body:
        body.pop()
        parts = [f"<b>{source}</b>", f"<code>今日 {len(items)} 条</code>"]
        if body:
            parts.append(gap.join(body))
        text = sep.join(parts)
    if len(text) > MAX_MESSAGE_LENGTH:
        text = text[: MAX_MESSAGE_LENGTH - 1].rstrip() + "…"
    return text


def pick_random_cover(article):
    """为无配图文章取一张随机封面（Lorem Picsum）

    用文章链接的哈希作为 seed，保证同一篇文章每次拿到同一张图。
    """
    digest = hashlib.md5(article["key"].encode("utf-8")).hexdigest()
    seed = digest[:16]
    return f"https://picsum.photos/seed/{seed}/1200/630"


def send_photo(items, image_path_or_url):
    """发送一个源的卡片；本地文件走 multipart 上传"""
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

    返回 [(source, [article, ...]), ...]，按组内第一条的顺序排列。
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
    """推送「一个源」的卡片：取该组第一条的配图，无图则用随机图。返回是否成功"""
    if not items:
        return False

    # 封面：优先用该组第一条的文章配图
    photo = None
    for art in items:
        if art.get("image"):
            photo = art["image"]
            break
    if not photo:
        photo = pick_random_cover(items[0])

    if DRY_RUN:
        kind = "原文配图" if any(a.get("image") for a in items) else "随机封面"
        log(f"  [dry-run] [{kind}] {items[0]['source']} | {len(items)} 条")
        for art in items:
            log(f"              · {art['title'][:52]}")
            log(f"                {art['link']}")
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

    # 按来源（公司）分组：一个源一张卡片
    groups = group_by_source(articles)
    log(f"[group] {len(articles)} 条动态，合并为 {len(groups)} 张卡片（一个源一张）")

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
