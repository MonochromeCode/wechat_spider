# -*- coding: utf-8 -*-
"""
评论获取模块
调用微信评论API获取文章下的评论
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


class CommentFetcher:
    """微信文章评论获取器"""

    COMMENT_API = "https://mp.weixin.qq.com/mp/appmsg_comment"

    def __init__(self):
        from cookie_manager import get_wechat_cookie
        self.session = requests.Session()
        self._wechat_cookie = get_wechat_cookie()
        self.session.headers.update(get_headers(cookie=self._wechat_cookie))
        if config.PROXY:
            self.session.proxies.update(config.PROXY)

    @retry(max_retries=2, delay=2)
    def fetch_comments(self, comment_params, article_url=None, session=None):
        """
        获取文章评论

        Args:
            comment_params: 文章解析出的评论参数（含 __biz, appmsgid, idx, comment_id 等）
            article_url: 文章URL（用于设置Referer）
            session: 复用的requests.Session（带文章页面cookie，提高成功率）

        Returns:
            list[dict]: 评论列表
            [
                {
                    "nick_name": "评论者昵称",
                    "content": "评论内容",
                    "like_num": 点赞数,
                    "reply": "作者回复内容" or "",
                    "reply_nick_name": "回复者" or "",
                    "create_time": "评论时间",
                },
                ...
            ]
        """
        comment_id = comment_params.get("comment_id")
        if not comment_id:
            logger.warning("文章未开启评论或无法获取 comment_id，跳过评论抓取")
            return []

        logger.info(f"获取评论，comment_id={comment_id}")

        # 优先使用传入的session（带文章页面cookie），否则用自己的
        req_session = session or self.session

        params = {
            "action": "getcomment",
            "__biz": comment_params.get("__biz", ""),
            "appmsgid": comment_params.get("appmsgid", ""),
            "idx": comment_params.get("idx", "1"),
            "comment_id": comment_id,
            "offset": "0",
            "limit": str(config.MAX_COMMENTS_PER_ARTICLE),
            "src": "0",
            "chordon": "0",
            "version": "1",
            "platform": "2",
        }

        # key 参数（如果有的话）
        if comment_params.get("key"):
            params["key"] = comment_params["key"]
        if comment_params.get("sn"):
            params["sn"] = comment_params["sn"]

        headers = get_headers(
            referer=article_url or "https://mp.weixin.qq.com/",
            cookie=self._wechat_cookie,
        )
        headers["X-Requested-With"] = "XMLHttpRequest"

        resp = req_session.get(
            self.COMMENT_API,
            params=params,
            headers=headers,
            timeout=config.REQUEST_TIMEOUT,
        )
        resp.encoding = "utf-8"

        # 检测验证页面（微信反爬）
        if "验证" in resp.text[:500] or "<title>验证</title>" in resp.text:
            logger.warning(
                "评论API触发微信验证，无法获取评论。"
                "解决方法：在config.WECHAT_COOKIE中配置有效的微信cookie，"
                "或通过微信客户端抓包获取key参数填入config"
            )
            return []

        # 尝试解析JSON
        try:
            data = resp.json()
        except json.JSONDecodeError:
            # 有些响应不是JSON，尝试从HTML中提取
            logger.debug("评论响应非JSON，尝试HTML解析")
            return self._parse_comments_from_html(resp.text)

        if data.get("base_resp", {}).get("ret") != 0:
            ret_code = data.get("base_resp", {}).get("ret", "")
            ret_msg = data.get("base_resp", {}).get("errmsg", "未知错误")
            logger.warning(f"评论API返回错误(ret={ret_code}): {ret_msg}")
            if ret_code in (-1, 1):
                logger.warning(
                    "可能原因: key参数缺失或过期。"
                    "请在微信客户端打开文章并抓包获取key，配置到config.WECHAT_COOKIE"
                )
            return []

        comments = self._parse_comment_data(data)
        logger.info(f"获取到 {len(comments)} 条评论")
        return comments

    def _parse_comment_data(self, data):
        """
        解析评论API的JSON响应

        评论数据结构（典型）:
        {
            "elected_comment": [
                {
                    "content": "评论内容",
                    "like_num": 10,
                    "nick_name": "昵称",
                    "create_time": 1234567890,
                    "reply_list": {
                        "reply_list": [
                            {
                                "content": "回复内容",
                                "nick_name": "回复者",
                                ...
                            }
                        ]
                    }
                }
            ]
        }
        """
        comments = []
        comment_list = data.get("elected_comment", [])

        for item in comment_list:
            comment = {
                "nick_name": item.get("nick_name", ""),
                "content": clean_text(item.get("content", "")),
                "like_num": item.get("like_num", 0),
                "create_time": self._format_timestamp(
                    item.get("create_time", 0)
                ),
                "reply": "",
                "reply_nick_name": "",
            }

            # 解析作者回复
            reply_list = item.get("reply_list", {})
            replies = reply_list.get("reply_list", [])
            if replies:
                first_reply = replies[0]
                comment["reply"] = clean_text(
                    first_reply.get("content", "")
                )
                comment["reply_nick_name"] = first_reply.get(
                    "nick_name", ""
                )

            comments.append(comment)

        return comments

    def _parse_comments_from_html(self, html):
        """
        备用方案：从文章页面HTML中提取已展示的评论
        （部分文章页面底部会直接渲染精选评论）
        """
        comments = []
        soup = BeautifulSoup(html, "lxml")

        # 评论可能在 js_comment_area 或 comment_section
        comment_items = soup.select("div.js_comment_area li") or soup.select(
            "div.comment_item"
        )

        for item in comment_items:
            nick = item.select_one(".comment_nickname, .nick_name")
            content = item.select_one(".comment_content, .content")
            like = item.select_one(".comment_like_num, .like_num")

            if content:
                comments.append(
                    {
                        "nick_name": clean_text(nick.get_text()) if nick else "",
                        "content": clean_text(content.get_text()),
                        "like_num": int(like.get_text()) if like and like.get_text().isdigit() else 0,
                        "create_time": "",
                        "reply": "",
                        "reply_nick_name": "",
                        "fetched_at": now_iso(),
                    }
                )

        return comments

    def _format_timestamp(self, ts):
        """时间戳转字符串"""
        if not ts:
            return ""
        try:
            from datetime import datetime
            return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")
        except (ValueError, OSError):
            return str(ts)
