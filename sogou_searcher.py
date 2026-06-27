# -*- coding: utf-8 -*-
"""
搜狗微信搜索模块
通过 weixin.sogou.com 搜索公众号，获取最新文章列表
支持自动从浏览器提取 cookie，反爬时自动刷新
"""
import re
import time
from urllib.parse import quote, unquote

import requests
from bs4 import BeautifulSoup

import config
from utils import (
    logger,
    get_headers,
    random_delay,
    retry,
    clean_text,
)
from cookie_manager import get_sogou_cookie, refresh_cookies, save_cached_cookie


class SogouSearcher:
    """搜狗微信搜索器（支持自动 cookie 管理）"""

    def __init__(self):
        self.session = requests.Session()
        self._cookie = ""
        self._search_referer = ""
        self._init_cookie()
        if config.PROXY:
            self.session.proxies.update(config.PROXY)

    def _init_cookie(self):
        """初始化 cookie：优先从浏览器提取"""
        self._cookie = get_sogou_cookie()
        if self._cookie:
            self.session.headers.update(get_headers(cookie=self._cookie))
            logger.info("搜狗 cookie 初始化成功")
        else:
            logger.warning("未获取到搜狗 cookie，首次请求可能触发验证")

    def _refresh_cookie(self):
        """刷新 cookie（清除缓存后重新从浏览器提取）"""
        logger.info("尝试刷新搜狗 cookie...")
        from cookie_manager import clear_cached_cookie
        clear_cached_cookie("sogou")

        self._cookie = get_sogou_cookie()
        if self._cookie:
            self.session.headers.update(get_headers(cookie=self._cookie))
            logger.info("搜狗 cookie 刷新成功")
            return True
        else:
            logger.warning("搜狗 cookie 刷新失败")
            return False

    def _is_blocked(self, resp):
        """检测是否被反爬拦截"""
        if "antispider" in resp.url:
            return True
        if "用户您好" in resp.text[:500]:
            return True
        if "请输入验证码" in resp.text[:500]:
            return True
        if resp.status_code == 302 and "antispider" in resp.headers.get("Location", ""):
            return True
        return False

    def _get_full_cookie(self):
        """获取 session 中所有 cookie 的字符串（包含搜索后新增的）"""
        return "; ".join([f"{c.name}={c.value}" for c in self.session.cookies])

    @retry(max_retries=3, delay=5)
    def search_articles(self, account_name, page=1):
        """
        搜索指定公众号的最新文章

        重试策略：
          1. 首次搜索
          2. 被拦截 → 等待5秒 → 用刷新后的 cookie 重试
          3. 仍被拦截 → 等待10秒 → 再次重试
          4. 都失败 → 提示手动输入 cookie
        """
        logger.info(f"搜索公众号 [{account_name}] 第 {page} 页文章...")

        params = {
            "type": "2",
            "query": account_name,
            "page": page,
            "ie": "utf8",
        }

        resp = self.session.get(
            config.SOGOU_SEARCH_URL,
            params=params,
            headers=get_headers(
                referer="https://weixin.sogou.com/",
                cookie=self._cookie,
            ),
            timeout=config.REQUEST_TIMEOUT,
        )
        resp.encoding = "utf-8"

        # 检查是否被反爬拦截
        if self._is_blocked(resp):
            logger.warning(f"搜狗触发反爬验证（第{page}页），等待后重试...")
            # 等待更长时间再重试
            random_delay(5, 10)
            # 刷新 cookie
            self._refresh_cookie()
            # 重新搜索
            resp = self.session.get(
                config.SOGOU_SEARCH_URL,
                params=params,
                headers=get_headers(
                    referer="https://weixin.sogou.com/",
                    cookie=self._cookie,
                ),
                timeout=config.REQUEST_TIMEOUT,
            )
            resp.encoding = "utf-8"
            if self._is_blocked(resp):
                logger.error(
                    "搜狗持续拦截。请手动获取 cookie：\n"
                    "  1. 浏览器打开 https://weixin.sogou.com\n"
                    "  2. 搜索任意公众号，完成验证码\n"
                    "  3. F12 → Network → 点击搜索请求 → Headers → 复制 Cookie\n"
                    "  4. 运行: python main.py --input-cookie sogou\n"
                    "  5. 粘贴 Cookie 值（会缓存2小时）"
                )
                return []

        # 保存搜索页 URL 作为后续 link 请求的 referer
        self._search_referer = resp.url

        articles = self._parse_search_results(resp.text, account_name)
        logger.info(f"公众号 [{account_name}] 获取到 {len(articles)} 篇文章")
        return articles

    def _parse_search_results(self, html, account_name):
        """解析搜狗搜索结果页"""
        soup = BeautifulSoup(html, "lxml")
        articles = []

        news_items = soup.select("div.news-box > div[data-newsid]")
        if not news_items:
            news_items = soup.select("div.txt-box")

        for item in news_items:
            try:
                article = self._parse_single_item(item, account_name)
                if article:
                    articles.append(article)
            except Exception as e:
                logger.debug(f"解析单条搜索结果失败: {e}")

        return articles

    def _parse_single_item(self, item, account_name):
        """解析单条搜索结果"""
        title_tag = item.select_one("h3 a") or item.select_one("a")
        if not title_tag:
            return None

        title = clean_text(title_tag.get_text())
        url = title_tag.get("href", "")

        if url and not url.startswith("http"):
            url = "https://weixin.sogou.com" + url

        summary_tag = item.select_one("p.txt-info") or item.select_one("p")
        summary = clean_text(summary_tag.get_text()) if summary_tag else ""

        account_tag = item.select_one("div.s-p a.account")
        account = clean_text(account_tag.get_text()) if account_tag else account_name

        time_str = ""
        time_span = item.select_one("span.s2") or item.select_one("span")
        if time_span:
            time_str = clean_text(time_span.get_text())

        img_tag = item.select_one("img")
        img_url = ""
        if img_tag:
            img_url = img_tag.get("src") or img_tag.get("data-src") or ""

        return {
            "title": title,
            "url": url,
            "summary": summary,
            "account": account,
            "publish_time": time_str,
            "img": img_url,
            "crawled_at": "",
        }

    def resolve_article_url(self, sogou_url):
        """
        解析搜狗跳转链接，获取微信文章的真实URL

        使用 session 中搜索后积累的完整 cookies（含 SNUID/PHPSESSID 等）
        如果被拦截返回 None，调用方会使用标题搜索备用方案
        """
        try:
            # 使用 session 的完整 cookies（搜索后新增的）
            full_cookie = self._get_full_cookie() or self._cookie

            resp = self.session.get(
                sogou_url,
                headers=get_headers(
                    referer=self._search_referer or "https://weixin.sogou.com/",
                    cookie=full_cookie,
                ),
                timeout=config.REQUEST_TIMEOUT,
                allow_redirects=True,
            )

            # 方式1：直接302跳转到了微信
            if "mp.weixin.qq.com" in resp.url:
                return resp.url

            # 方式2：从JS拼接代码中提取真实URL
            url_fragments = re.findall(r"url\s*\+=\s*'([^']*)'", resp.text)
            if url_fragments:
                real_url = "".join(url_fragments)
                if "mp.weixin.qq.com" in real_url:
                    return real_url

            # 方式3：window.location
            match = re.search(
                r"window\.location(?:\.href)?\s*=\s*['\"](https?://mp\.weixin\.qq\.com[^'\"]+)",
                resp.text,
            )
            if match:
                return match.group(1)

            # 方式4：直接搜索完整URL
            match = re.search(
                r"(https?://mp\.weixin\.qq\.com/s[^'\"\s<>]+)", resp.text
            )
            if match:
                return match.group(1)

            # 被拦截或无法解析
            if "antispider" in resp.url:
                logger.debug(f"link URL 被拦截，将使用标题搜索备用方案")
            else:
                logger.warning(f"无法解析文章真实URL: {sogou_url[:80]}")
            return None
        except Exception as e:
            logger.warning(f"解析文章URL失败: {e}")
            return None

    def search_article_by_title(self, title, account=""):
        """
        备用方案：通过文章标题在搜狗搜索中获取文章链接
        当 /link?url= 被拦截时使用

        Args:
            title: 文章标题
            account: 公众号名称（可选，用于精确匹配）

        Returns:
            str or None: 微信文章真实URL
        """
        try:
            # 用标题搜索，搜索结果中的链接有时可直接解析
            params = {
                "type": "2",
                "query": title[:50],
                "page": 1,
                "ie": "utf8",
            }

            full_cookie = self._get_full_cookie() or self._cookie
            resp = self.session.get(
                config.SOGOU_SEARCH_URL,
                params=params,
                headers=get_headers(
                    referer="https://weixin.sogou.com/",
                    cookie=full_cookie,
                ),
                timeout=config.REQUEST_TIMEOUT,
            )
            resp.encoding = "utf-8"

            if self._is_blocked(resp):
                return None

            soup = BeautifulSoup(resp.text, "lxml")
            items = soup.select("div.txt-box")

            for item in items:
                h3_a = item.select_one("h3 a")
                if not h3_a:
                    continue
                item_title = clean_text(h3_a.get_text())
                # 模糊匹配标题
                if title[:15] in item_title or item_title[:15] in title:
                    url = h3_a.get("href", "")
                    if url and not url.startswith("http"):
                        url = "https://weixin.sogou.com" + url
                    # 尝试解析这个链接
                    real_url = self.resolve_article_url(url)
                    if real_url:
                        return real_url

            return None
        except Exception:
            return None

    def fetch_account_articles(self, account_name, count=None):
        """
        获取公众号的最新文章列表（自动翻页）

        Args:
            account_name: 公众号名称
            count: 需要的文章数量

        Returns:
            list[dict]: 文章列表（已解析真实URL）
        """
        count = count or config.ARTICLES_PER_ACCOUNT
        all_articles = []
        page = 1
        max_pages = 5

        while len(all_articles) < count and page <= max_pages:
            articles = self.search_articles(account_name, page=page)
            if not articles:
                break

            for art in articles:
                if len(all_articles) >= count:
                    break
                # 解析真实URL
                if art["url"] and "sogou" in art["url"]:
                    random_delay(1, 2)
                    real_url = self.resolve_article_url(art["url"])
                    if real_url:
                        art["url"] = real_url
                    else:
                        # 备用：用标题搜索
                        logger.info(f"尝试用标题搜索: {art['title'][:30]}")
                        random_delay(2, 4)
                        real_url = self.search_article_by_title(
                            art["title"], art.get("account", "")
                        )
                        if real_url:
                            art["url"] = real_url
                            logger.info(f"标题搜索成功: {real_url[:60]}")
                        else:
                            logger.debug(f"跳过无法解析的链接: {art['title'][:30]}")
                            continue
                all_articles.append(art)
                random_delay()

            page += 1
            random_delay()

        logger.info(f"公众号 [{account_name}] 共获取 {len(all_articles)} 篇文章")
        return all_articles[:count]
