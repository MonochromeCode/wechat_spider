# -*- coding: utf-8 -*-
"""
文章正文解析模块
访问微信文章链接，解析正文内容和评论所需参数
"""
import re
import json

import requests
from bs4 import BeautifulSoup

import config
from utils import (
    logger,
    get_headers,
    random_delay,
    retry,
    clean_text,
    now_iso,
)


class ArticleParser:
    """微信文章解析器"""

    def __init__(self):
        from cookie_manager import get_wechat_cookie
        self.session = requests.Session()
        self._wechat_cookie = get_wechat_cookie()
        self.session.headers.update(get_headers(cookie=self._wechat_cookie))
        if config.PROXY:
            self.session.proxies.update(config.PROXY)

    @retry()
    def fetch_article(self, url):
        """
        获取并解析微信文章

        Args:
            url: 微信文章URL (mp.weixin.qq.com/s/xxx)

        Returns:
            dict: 文章完整信息
            {
                "title": "标题",
                "author": "作者",
                "account": "公众号",
                "publish_time": "发布时间",
                "content": "正文（纯文本）",
                "content_html": "正文HTML",
                "url": "原始URL",
                "comment_params": {...},  # 评论所需参数
                "fetched_at": "抓取时间",
            }
        """
        logger.info(f"抓取文章: {url}")

        resp = self.session.get(
            url,
            headers=get_headers(
                referer="https://mp.weixin.qq.com/",
                cookie=self._wechat_cookie,
            ),
            timeout=config.REQUEST_TIMEOUT,
        )
        resp.encoding = "utf-8"

        if resp.status_code != 200:
            logger.warning(f"文章请求失败，状态码: {resp.status_code}")
            return None

        html = resp.text
        soup = BeautifulSoup(html, "lxml")

        # 解析文章元信息
        article = {
            "url": url,
            "title": self._extract_title(soup),
            "author": self._extract_author(soup),
            "account": self._extract_account(soup),
            "publish_time": self._extract_publish_time(html, soup),
            "content": self._extract_content(soup),
            "content_html": self._extract_content_html(soup),
            "content_md": self._extract_content_markdown(soup),
            "fetched_at": now_iso(),
        }

        # 提取评论所需参数
        article["comment_params"] = self._extract_comment_params(html)

        # 生成文章唯一 ID（用于去重，基于 appmsgid_idx 或 mid_idx）
        cp = article["comment_params"]
        appmsgid = cp.get("appmsgid", "")
        mid = cp.get("mid", "")
        idx = cp.get("idx", "1")
        if appmsgid:
            article["article_id"] = f"{appmsgid}_{idx}"
        elif mid:
            article["article_id"] = f"{mid}_{idx}"
        else:
            from hashlib import md5
            article["article_id"] = "tit_" + md5(article["title"].encode("utf-8")).hexdigest()[:12]

        logger.info(f"文章解析完成: {article['title']} [id={article.get('article_id', '')}]")
        return article

    def _extract_title(self, soup):
        """提取标题"""
        title_tag = soup.select_one("#activity-name") or soup.select_one("h1")
        return clean_text(title_tag.get_text()) if title_tag else ""

    def _extract_author(self, soup):
        """提取作者"""
        # 微信文章作者在 #js_author_name 或 .rich_media_meta_text
        author_tag = soup.select_one("#js_author_name") or soup.select_one(
            ".rich_media_meta_text"
        )
        return clean_text(author_tag.get_text()) if author_tag else ""

    def _extract_account(self, soup):
        """提取公众号名称"""
        # 公众号名称在 #js_name
        name_tag = soup.select_one("#js_name") or soup.select_one(
            "a.weui-card__title"
        )
        return clean_text(name_tag.get_text()) if name_tag else ""

    def _extract_publish_time(self, html, soup):
        """提取发布时间"""
        # 先从页面DOM提取（JS动态填充可能为空）
        time_tag = soup.select_one("#publish_time")
        if time_tag:
            text = clean_text(time_tag.get_text())
            if text:
                return text

        # 从JS变量 ct 中提取（Unix时间戳）
        match = re.search(r'var\s+ct\s*=\s*["\'](\d{10})["\']', html)
        if match:
            from datetime import datetime
            ts = int(match.group(1))
            try:
                return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")
            except (ValueError, OSError):
                return str(ts)
        return ""

    def _extract_content(self, soup):
        """提取正文纯文本"""
        content_div = soup.select_one("#js_content") or soup.select_one(
            ".rich_media_content"
        )
        if not content_div:
            return ""
        # 移除 script 和 style
        for tag in content_div.find_all(["script", "style"]):
            tag.decompose()
        return clean_text(content_div.get_text(separator="\n"))

    def _extract_content_html(self, soup):
        """提取正文HTML"""
        content_div = soup.select_one("#js_content") or soup.select_one(
            ".rich_media_content"
        )
        if not content_div:
            return ""
        # 移除 script 和 style
        for tag in content_div.find_all(["script", "style"]):
            tag.decompose()
        return str(content_div)

    def _extract_content_markdown(self, soup):
        """
        将微信文章HTML正文转为Markdown格式，保持排版一致

        处理：
          - 图片：data-src / src 属性 → ![](url)
          - 加粗、斜体、标题、引用、列表、表格、链接
          - 微信 section 嵌套布局 → 按内容流展开
          - 移除空行、多余空白
        """
        content_div = soup.select_one("#js_content") or soup.select_one(
            ".rich_media_content"
        )
        if not content_div:
            return ""

        # 克隆避免修改原始 soup
        content = BeautifulSoup(str(content_div), "lxml")

        # 移除 script / style / noscript
        for tag in content.find_all(["script", "style", "noscript"]):
            tag.decompose()

        # 处理图片：微信用 data-src 做懒加载，统一改到 src
        for img in content.find_all("img"):
            data_src = img.get("data-src") or img.get("data-src-original")
            if data_src:
                img["src"] = data_src
            # 移除无src的图片
            if not img.get("src"):
                img.decompose()

        # 处理 mpvoice / mpvideosnap 等微信特殊媒体标签
        for tag in content.find_all(["mpvoice", "mpvideosnap", "mpcommonprofile",
                                      "mpweixinparameter", "mpprofile"]):
            tag.decompose()

        # 用 html2text 转换
        import html2text
        h = html2text.HTML2Text()
        h.body_width = 0          # 不自动换行
        h.unicode_snob = True     # 保留 Unicode 字符
        h.images_to_alt = False   # 图片显示为 ![](url)
        h.protect_links = True    # 保护链接不被截断
        h.wrap_links = False      # 链接不换行
        h.mark_code = True        # 代码块标记

        html_str = str(content)
        md_content = h.handle(html_str)

        # 清理多余空行（连续3+空行压缩为2行）
        md_content = re.sub(r"\n{3,}", "\n\n", md_content)
        # 去除首尾空白
        md_content = md_content.strip()

        return md_content

    def _extract_comment_params(self, html):
        """
        从文章页面提取评论API所需的参数

        评论API需要:
        - __biz: 公众号标识
        - appmsgid: 文章消息ID
        - idx: 文章在图文中的序号
        - comment_id: 评论ID（文章开启评论才有）
        - sn: 文章签名
        - mid: 消息ID
        - key: 访问密钥（有时效性，可能需要从微信客户端获取）
        """
        params = {}

        # __biz - 优先从JS对象 biz:"xxx" 格式提取（最可靠）
        # biz值通常是 Mz/Mj 开头的base64编码
        biz_matches = re.findall(r'biz["\']?\s*:\s*["\']([A-Za-z0-9+/=]{10,})["\']', html)
        if biz_matches:
            # 取第一个匹配（通常出现两次，值相同）
            params["__biz"] = biz_matches[0]

        # 备用：var __biz = "xxx" 格式（排除 window 等非值引用）
        if "__biz" not in params:
            match = re.search(r'var\s+__biz\s*=\s*["\']([A-Za-z0-9+/=]{10,})["\']', html)
            if match:
                params["__biz"] = match.group(1)

        # 备用：从URL参数提取
        if "__biz" not in params:
            match = re.search(r'__biz\s*=\s*([A-Za-z0-9%+]{10,})', html)
            if match and not match.group(1).startswith("window"):
                params["__biz"] = match.group(1)

        # mid
        match = re.search(r'var\s+mid\s*=\s*["\']?([^"\';\s]+)', html)
        if match:
            params["mid"] = match.group(1)

        # idx
        match = re.search(r'var\s+idx\s*=\s*["\']?([^"\';\s]+)', html)
        if match:
            params["idx"] = match.group(1)

        # sn
        match = re.search(r'var\s+sn\s*=\s*["\']([^"\']+)["\']', html)
        if match:
            params["sn"] = match.group(1)

        # appmsgid
        match = re.search(r'var\s+appmsgid\s*=\s*["\']?([^"\';\s]+)', html)
        if not match:
            match = re.search(r"appmsgid\s*[:=]\s*['\"]?(\d+)", html)
        if match:
            params["appmsgid"] = match.group(1)

        # comment_id - 关键参数，文章开启评论才有
        match = re.search(r'comment_id\s*=\s*["\']?(\d+)', html)
        if not match:
            match = re.search(r'"comment_id"\s*:\s*"?(\d+)', html)
        if match:
            params["comment_id"] = match.group(1)

        # key - 评论API的访问密钥（有时效性）
        match = re.search(r'appmsg_comment.*?key\s*=\s*([a-f0-9]+)', html, re.DOTALL)
        if not match:
            match = re.search(r'"key"\s*:\s*"([a-f0-9]+)"', html)
        if match:
            params["key"] = match.group(1)

        # 如果没从JS变量提取到，尝试从URL提取
        if "__biz" not in params:
            url_match = re.search(
                r'__biz=([A-Za-z0-9%]+).*?mid=(\d+).*?idx=(\d+).*?sn=([a-f0-9]+)',
                html,
            )
            if url_match:
                params["__biz"] = url_match.group(1)
                params["mid"] = url_match.group(2)
                params["idx"] = url_match.group(3)
                params["sn"] = url_match.group(4)

        logger.debug(f"评论参数: {params}")
        return params
