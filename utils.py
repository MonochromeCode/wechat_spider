# -*- coding: utf-8 -*-
"""
微信公众号爬虫 - 工具函数
"""
import os
import time
import random
import logging
import functools
from datetime import datetime

import config


# ==================== 日志配置 ====================
def setup_logger(name="wechat_spider", level=None):
    """配置并返回 logger"""
    os.makedirs(config.LOG_DIR, exist_ok=True)
    log_file = os.path.join(
        config.LOG_DIR, f"{datetime.now().strftime('%Y-%m-%d')}.log"
    )

    logger = logging.getLogger(name)
    logger.setLevel(level or config.LOG_LEVEL)

    # 避免重复添加 handler
    if logger.handlers:
        return logger

    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # 控制台输出
    sh = logging.StreamHandler()
    sh.setFormatter(fmt)
    logger.addHandler(sh)

    # 文件输出
    fh = logging.FileHandler(log_file, encoding="utf-8")
    fh.setFormatter(fmt)
    logger.addHandler(fh)

    return logger


logger = setup_logger()


# ==================== 随机 User-Agent ====================
def get_random_ua():
    """返回随机 User-Agent"""
    return random.choice(config.USER_AGENTS)


def get_headers(referer=None, cookie=None):
    """构建请求头"""
    headers = {
        "User-Agent": get_random_ua(),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Connection": "keep-alive",
    }
    if referer:
        headers["Referer"] = referer
    if cookie:
        headers["Cookie"] = cookie
    return headers


# ==================== 请求间隔 ====================
def random_delay(min_sec=None, max_sec=None):
    """随机延迟，模拟人类行为"""
    min_s = min_sec if min_sec is not None else config.REQUEST_DELAY_MIN
    max_s = max_sec if max_sec is not None else config.REQUEST_DELAY_MAX
    delay = random.uniform(min_s, max_s)
    time.sleep(delay)


# ==================== 重试装饰器 ====================
def retry(max_retries=None, delay=None):
    """
    请求重试装饰器
    遇到异常时自动重试
    """
    max_retries = max_retries or config.MAX_RETRIES
    delay = delay or config.RETRY_DELAY

    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            last_exception = None
            for attempt in range(1, max_retries + 1):
                try:
                    result = func(*args, **kwargs)
                    return result
                except Exception as e:
                    last_exception = e
                    logger.warning(
                        f"{func.__name__} 第 {attempt}/{max_retries} 次尝试失败: {e}"
                    )
                    if attempt < max_retries:
                        time.sleep(delay * attempt)
            logger.error(f"{func.__name__} 重试 {max_retries} 次后仍失败")
            raise last_exception

        return wrapper

    return decorator


# ==================== HTML 文本清洗 ====================
def clean_text(text):
    """清洗文本：去除多余空白、换行"""
    if not text:
        return ""
    import re
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def html_to_plain(html_content):
    """将 HTML 片段转为纯文本"""
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html_content, "lxml")
    return clean_text(soup.get_text(separator="\n"))


# ==================== 时间格式化 ====================
def timestamp_str(fmt="%Y%m%d_%H%M%S"):
    """返回当前时间字符串，用于文件命名"""
    return datetime.now().strftime(fmt)


def now_iso(offset_seconds=0):
    """返回 ISO 格式当前时间（可选偏移）"""
    from datetime import timedelta
    dt = datetime.now() + timedelta(seconds=offset_seconds)
    return dt.isoformat()
