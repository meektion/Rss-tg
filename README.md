# Rss-tg

把 RSS 源的新文章以**图片卡片**形式自动推送到 Telegram 频道。

基于 GitHub Actions 定时运行，**不需要服务器**，配置好之后全自动。

---

## 效果

每条消息包含：**封面图 + 标题 + 来源·时间 + 摘要 + 底部原文链接**

```
┌─────────────────────────────┐
│                             │
│          封面图片            │
│                             │
└─────────────────────────────┘

中国科学家首次用中国大模型攻克世界级数学难题

IT之家 · 10-15 11:23

据媒体报道，这项成果由国内团队完成，相关论文
已发表于国际期刊，评审认为该方法在效率上有显著提升。

🔗 阅读原文
```

文章自带配图时使用原文图；没有配图的会自动取一张**随机封面图**（`picsum.photos`，以文章链接哈希作 seed，同一篇文章封面固定且互不重复）。

英文标题与摘要会**自动翻译为中文**（有道公开翻译接口，无需 Key）；已经含中文的内容自动跳过。

---

## 特性

| 特性 | 说明 |
|---|---|
| **图片卡片** | `sendPhoto` 发送，图片 + 标题 + 来源 + 摘要 + 原文链接 |
| **富图片提取** | 依次尝试 `media_content` → `media_thumbnail` → `enclosure` → 正文 `<img>`，并过滤 favicon / emoji / 图标 / 1x1 像素等无效图 |
| **随机封面** | 无配图时用 `picsum.photos` 取随机封面，以链接哈希作 seed，稳定且不重复 |
| **自动翻译** | 英文标题/摘要自动译中文（有道公开接口），含中文的内容自动跳过 |
| **缓存去重** | `sent_articles.json` 按链接去重，同一条只推一次，30 天自动过期 |
| **失败可见** | 全部推送失败时 CI 直接变红，不会静默空跑 |
| **HTML 模式** | 规避 Markdown V1 特殊字符（`_` `*` `[`）导致发送失败的问题 |
| **防刷屏** | 单次运行最多推 40 条，超出部分留待下次 |
| **排版可调** | `MESSAGE_BLANK_LINES` 控制消息内部段落空行数，来源·时间用等宽字体（不用斜体） |

---

## 部署

### 1. Fork 或克隆本仓库

### 2. 创建 Telegram Bot

在 Telegram 里找 **@BotFather**：

1. 发送 `/newbot`，按提示创建机器人
2. 记下返回的 Token，格式形如 `123456789:AAHxxxxxxxxxxxxxxxxxxxxx`

验证 Token 是否有效（浏览器打开）：

```
https://api.telegram.org/bot<你的Token>/getMe
```

返回 `{"ok":true,...}` 即正常。

### 3. 把 Bot 加入频道

在频道设置里把机器人添加为**管理员**（否则无法发送消息）。

获取频道 ID（浏览器打开）：

```
https://api.telegram.org/bot<你的Token>/getUpdates
```

在返回的 JSON 中找到 `"chat":{"id":-100xxxxxxxxxx}`，那个 `-100` 开头的数字就是频道 ID。

> 也可以用频道的 `@username`，但要求频道是公开的。**推荐用 `-100` 数字 ID。**

### 4. 配置 Secrets

仓库 **Settings → Secrets and variables → Actions → Secrets** 标签页，添加两个：

| Name | Value |
|---|---|
| `TELEGRAM_BOT_TOKEN` | BotFather 给的 Token |
| `TELEGRAM_CHANNEL_ID` | 上面拿到的频道 ID |

> ⚠️ 必须建在 **Secrets** 区域，不能建在 Variables 区域。
> 粘贴 Token 时注意**不要带首尾空格或换行**。

### 5. 开启 Actions 写权限

**Settings → Actions → General → Workflow permissions**

选择 **Read and write permissions** 并保存。

> 去重缓存需要 commit 回仓库，不开这个权限去重会失效。

### 6. 手动跑一次

**Actions → Sync Articles to Telegram → Run workflow**

---

## 自定义

### 添加 / 删除 RSS 源

直接编辑 `rss_feeds.txt`，每行一个地址，`#` 开头为注释：

```
# ---------- 中文科技 ----------
https://sspai.com/feed
https://www.ithome.com/rss/

# ---------- 英文博客 ----------
https://blog.cloudflare.com/rss
```

改完提交即可，无需改代码。

> 脚本优先读取该文件；文件不存在时回退到 `sync_articles.py` 里的 `DEFAULT_FEEDS`。

### 调整推送行为

编辑 `sync_articles.py` 顶部的参数区：

| 参数 | 默认 | 说明 |
|---|---|---|
| `MAX_ARTICLES_PER_FEED` | `5` | 每个源每次最多推几条 |
| `SCAN_DEPTH` | `20` | 每个源最多扫描多少条 entry |
| `PREFER_TODAY` | `True` | 优先推送当天发布的内容，不足时用未见过的历史内容补齐 |
| `MAX_ARTICLES_PER_RUN` | `40` | 单次运行总量上限，防止一次刷屏 |
| `FIRST_RUN_BOOTSTRAP` | `False` | 改为 `True`：首次运行只预热缓存、不推送 |
| `SUMMARY_MAX_LENGTH` | `160` | 摘要截断长度 |
| `KEEP_DAYS` | `30` | 去重缓存保留天数 |

### 调整推送时间

编辑 `.github/workflows/sync_articles.yml`：

```yaml
on:
  schedule:
    - cron: '0 8,16 * * *'   # UTC 8:00 / 16:00
```

> cron 用的是 **UTC 时间**，北京时间需 **减 8 小时**。
> `0 8,16 * * *` = 北京时间 **16:00 / 次日 0:00**。
> 想改成北京时间早 8 点，应写 `0 0 * * *`。

### 调整消息排版（空行间距）

编辑 `sync_articles.py` 里的 `MESSAGE_BLANK_LINES`，控制标题 / 来源 / 摘要 / 链接之间的空行数：

```python
MESSAGE_BLANK_LINES = 3   # 默认 3，即段落间空 2 行；想更紧凑可改 2，更疏朗可改 4
```

### 关闭自动翻译

编辑 `sync_articles.py`：

```python
TRANSLATE_ENABLED = False   # 关掉后英文标题/摘要保持原文
```

---

## 本地调试

```bash
pip install -r requirements.txt

# 只打印将要推送的内容，不实际发送、不写缓存
DRY_RUN=1 python sync_articles.py
```

---

## 常见问题

### 推送失败，报 `401 Unauthorized`

Token 格式正确但值无效。检查：

1. Secret 名字精确等于 `TELEGRAM_BOT_TOKEN`（注意尾部空格）
2. 建在 **Secrets** 区域而非 Variables
3. 值没有带首尾空格或换行
4. Token 未被 BotFather 重置过

用 `getMe` 接口验证 Token 本身是否有效。

### 报 `404 Not Found`

URL 里的 Token 格式就不对。正确格式是 `数字:字母数字`，形如 `123456789:AAH...`。

> 常见误操作：把 GitHub Token（`ghp_` 开头）当成 Telegram Token 填入。

### 报 `chat not found`

`TELEGRAM_CHANNEL_ID` 有问题，或**机器人没有加入频道**。确认已把 Bot 添加为频道管理员。

### 频道里图片文字变豆腐块

GitHub Actions 默认没有中文字体。workflow 里已有这一步，若报错请确认存在：

```yaml
- name: Install CJK fonts
  run: |
    sudo apt-get update -qq
    sudo apt-get install -y -qq fonts-noto-cjk
```

### Actions 成功但频道没消息

**先看 `Run sync script` 的日志**。新版脚本在全部推送失败时会直接让 CI 变红，正常情况下不会出现「显示成功但没推送」的情况。

若日志显示 `[done] 成功推送 0/0 条`，说明当日没有新文章（可能源更新慢，或缓存里已有记录）。

### 想清空去重记录重新推送

把 `sent_articles.json` 内容改成 `{}` 并提交。

---

## 工作原理

```
GitHub Actions (定时触发)
        │
        ▼
  读取 rss_feeds.txt
        │
        ▼
  抓取各 RSS 源 ──► 筛选当天文章 ──► 查缓存去重
        │
        ▼
  提取图片（media_content / media_thumbnail / enclosure / 正文 img）
        │
        ├── 有图 ──► 直接用原图
        └── 无图 ──► picsum.photos 随机封面（种子 = 链接哈希）
        │
        ▼
  英文标题/摘要 ──► 有道翻译为中文（含中文则跳过）
        │
        ▼
  sendPhoto 推送到 Telegram 频道
        │
        ▼
  发送成功 ──► 写入 sent_articles.json ──► commit 回仓库
```

---

## 文件说明

| 文件 | 作用 |
|---|---|
| `sync_articles.py` | 主脚本 |
| `rss_feeds.txt` | RSS 源列表 |
| `requirements.txt` | Python 依赖 |
| `sent_articles.json` | 去重缓存（自动生成、自动提交） |
| `.github/workflows/sync_articles.yml` | 定时任务配置 |

---

## 依赖

- Python 3.11
- feedparser / beautifulsoup4 / requests / Pillow

---

## License

MIT
