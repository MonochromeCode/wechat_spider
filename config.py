# -*- coding: utf-8 -*-
"""
微信公众号爬虫 - 全局配置
"""
import os

# ==================== 项目路径 ====================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
LOG_DIR = os.path.join(BASE_DIR, "logs")

# ==================== 目标公众号列表 ====================
# 填入你要爬取的公众号名称（与搜狗微信搜索中显示的名称一致）
WECHAT_ACCOUNTS = [
    "人民日报",
    # "添加更多公众号名称..."
]

# ==================== 搜狗搜索配置 ====================
SOGOU_SEARCH_URL = "https://weixin.sogou.com/weixin"
SOGOU_ARTICLE_URL = "https://weixin.sogou.com/weixin?type=2&query={query}"

# 每个公众号爬取的最近文章数量
ARTICLES_PER_ACCOUNT = 2

# ==================== 请求配置 ====================
# 请求间隔（秒），避免触发反爬
REQUEST_DELAY_MIN = 60
REQUEST_DELAY_MAX = 120

# 请求超时（秒）
REQUEST_TIMEOUT = 15

# 最大重试次数
MAX_RETRIES = 3

# 重试间隔（秒）
RETRY_DELAY = 3

# ==================== User-Agent 池 ====================
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15",
]

# ==================== Cookie 配置 ====================
# 是否自动从浏览器提取 cookie（推荐开启，无需手动复制）
# 支持 Chrome / Edge / Firefox
AUTO_LOAD_BROWSER_COOKIE = True

# 手动配置的搜狗 cookie（留空则自动从浏览器提取）
# 打开 weixin.sogou.com -> F12 -> Network -> 复制 Cookie 头
SOGOU_COOKIE = ""
# 手动配置的微信文章页 cookie（留空则自动从浏览器提取）
WECHAT_COOKIE = ""

# ==================== 代理配置（可选）====================
# 如果被封IP，可以配置代理
PROXY = None  # 例如: {"http": "http://127.0.0.1:7890", "https": "http://127.0.0.1:7890"}

# ==================== 存储配置 ====================
# 输出格式: json / csv / both
OUTPUT_FORMAT = "json"

# 是否爬取评论
ENABLE_COMMENTS = True

# 每篇文章最多爬取的评论条数
MAX_COMMENTS_PER_ARTICLE = 100

# ==================== 日志配置 ====================
LOG_LEVEL = "INFO"  # DEBUG / INFO / WARNING / ERROR

# ==================== 定时循环爬取配置 ====================
# 是否启用定时循环模式（后台持续运行）
ENABLE_DAEMON_MODE = False

# 定时循环爬取间隔（小时），例如 6 表示每 6 小时运行一次
CRAWL_INTERVAL_HOURS = 6

# 每次循环最多运行的次数（0 表示无限循环）
MAX_LOOP_COUNT = 0

# ==================== 增量更新配置 ====================
# 是否启用增量更新（跳过已爬文章，只追加新评论）
ENABLE_INCREMENTAL = True

# 状态文件路径（记录已爬文章URL和评论ID，用于去重）
STATE_FILE = os.path.join(DATA_DIR, "crawl_state.json")

# 增量更新时，重新爬取文章正文的最大间隔（小时）
# 超过此间隔会重新爬取正文（以防文章被修改），0 表示从不重新爬取正文
RECRAWL_ARTICLE_HOURS = 0

# 增量更新评论时，对已有文章重新获取评论的最大间隔（小时）
# 例如 1 表示每 1 小时更新一次评论，0 表示每次都更新
COMMENT_UPDATE_INTERVAL_HOURS = 1

# 每个公众号仅对最新 N 篇文章更新评论（搜狗搜索按时间降序返回）
# 排名 > N 的已爬文章保留文档但不再更新评论
COMMENT_UPDATE_LATEST_COUNT = 2

# 是否保留历史文章文档（True=保留所有历史文章，False=超出最新N篇后删除旧文件）
KEEP_HISTORY_ARTICLES = True
