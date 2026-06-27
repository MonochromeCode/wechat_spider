# -*- coding: utf-8 -*-
"""
爬虫状态管理模块
负责：
  1. 记录已爬取的文章（基于 article_id 去重，而非 URL）
  2. 记录每篇文章的评论签名（增量更新评论）
  3. 记录最后爬取时间（控制重新爬取频率）
  4. 状态持久化到 JSON 文件
"""

import json
import os
from datetime import datetime, timedelta

import config
from utils import logger, now_iso


def comment_signature(comment):
    """
    为单条评论生成唯一签名，用于去重
    使用 nick_name + "|" + content + "|" + create_time 组合
    """
    nick = comment.get("nick_name", "")
    content = comment.get("content", "")
    ctime = comment.get("create_time", "")
    return f"{nick}|{content}|{ctime}"


class CrawlState:
    """
    爬虫状态管理器

    状态文件结构:
    {
        "version": 2,
        "last_full_crawl": "2026-06-27T20:00:00",
        "articles": {
            "<article_id>": {
                "url": "最新文章URL",
                "title": "文章标题",
                "account": "公众号名称",
                "first_crawled": "2026-06-27T20:00:00",
                "last_crawled": "2026-06-27T20:00:00",
                "last_comment_update": "2026-06-27T20:00:00",
                "comment_signatures": ["sig1", "sig2", ...],
                "data_file": "path/to/saved/file.json"
            }
        }
    }

    注意: 使用 article_id（appmsgid_idx）作为键，而非 URL。
    同一篇文章的 URL 可能因 timestamp/signature 参数不同而变化。
    """

    def __init__(self, state_file=None):
        self.state_file = state_file or config.STATE_FILE
        self.state = self._load()

    def _load(self):
        """从磁盘加载状态"""
        if not os.path.exists(self.state_file):
            return {
                "version": 2,
                "last_full_crawl": None,
                "articles": {},
            }
        try:
            with open(self.state_file, "r", encoding="utf-8") as f:
                state = json.load(f)
            # 版本迁移：v1（URL键）→ v2（article_id键）
            if state.get("version", 1) < 2:
                logger.warning("检测到旧版状态文件(v1)，将重新初始化")
                return {
                    "version": 2,
                    "last_full_crawl": state.get("last_full_crawl"),
                    "articles": {},
                }
            logger.info(f"已加载爬虫状态: {len(state.get('articles', {}))} 篇文章记录")
            return state
        except Exception as e:
            logger.warning(f"状态文件加载失败({e})，将创建新状态")
            return {
                "version": 2,
                "last_full_crawl": None,
                "articles": {},
            }

    def save(self):
        """持久化状态到磁盘"""
        try:
            os.makedirs(os.path.dirname(self.state_file), exist_ok=True)
            with open(self.state_file, "w", encoding="utf-8") as f:
                json.dump(self.state, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"状态文件保存失败: {e}")

    def is_article_crawled(self, article_id):
        """
        判断文章是否已爬取过（基于 article_id）

        Args:
            article_id: 文章唯一ID（appmsgid_idx 或 mid_idx）

        Returns:
            bool
        """
        return article_id in self.state["articles"]

    def should_update_comments(self, article_id):
        """
        判断是否需要更新该文章的评论
        根据 COMMENT_UPDATE_INTERVAL_HOURS 配置决定

        Args:
            article_id: 文章唯一ID

        Returns:
            bool
        """
        if not config.ENABLE_COMMENTS:
            return False
        if article_id not in self.state["articles"]:
            return True
        last = self.state["articles"][article_id].get("last_comment_update")
        if not last:
            return True
        try:
            last_dt = datetime.fromisoformat(last)
            threshold = datetime.now() - timedelta(
                hours=config.COMMENT_UPDATE_INTERVAL_HOURS
            )
            return last_dt < threshold
        except (ValueError, TypeError):
            return True

    def get_known_comment_signatures(self, article_id):
        """
        获取某文章已知道的评论签名集合
        用于增量去重

        Args:
            article_id: 文章唯一ID

        Returns:
            set: 评论签名集合
        """
        if article_id not in self.state["articles"]:
            return set()
        return set(self.state["articles"][article_id].get("comment_signatures", []))

    def filter_new_comments(self, article_id, comments):
        """
        从评论列表中过滤出新增评论

        Args:
            article_id: 文章唯一ID
            comments: 本次获取到的评论列表

        Returns:
            list: 新增评论列表
            set: 新增评论签名集合
        """
        known = self.get_known_comment_signatures(article_id)
        new_comments = []
        new_sigs = set()

        for c in comments:
            sig = comment_signature(c)
            if sig not in known:
                new_comments.append(c)
                new_sigs.add(sig)

        return new_comments, new_sigs

    def update_article_state(
        self, article_id, url, title, account, data_file, comments,
        publish_time=""
    ):
        """
        更新某篇文章的状态记录

        Args:
            article_id: 文章唯一ID
            url: 文章URL（可能变化，仅用于引用）
            title: 文章标题
            account: 公众号名称
            data_file: 文章数据文件路径
            comments: 本次获取到的全部评论列表
            publish_time: 发布时间（用于生成文件名）
        """
        now = now_iso()
        existing = self.state["articles"].get(article_id, {})

        # 合并评论签名
        old_sigs = set(existing.get("comment_signatures", []))
        new_sigs = set()
        for c in comments:
            new_sigs.add(comment_signature(c))
        all_sigs = old_sigs | new_sigs  # 取并集

        self.state["articles"][article_id] = {
            "url": url,
            "title": title,
            "account": account,
            "publish_time": publish_time,
            "first_crawled": existing.get("first_crawled", now),
            "last_crawled": now,
            "last_comment_update": now,
            "comment_signatures": list(all_sigs),
            "data_file": data_file,
        }
        self.save()

    def mark_comment_updated(self, article_id, new_comments):
        """
        仅更新评论时间，并追加新评论签名
        用于增量更新模式（不重新爬正文）
        """
        if article_id not in self.state["articles"]:
            return
        now = now_iso()
        entry = self.state["articles"][article_id]
        entry["last_comment_update"] = now

        # 追加新评论签名
        old_sigs = set(entry.get("comment_signatures", []))
        for c in new_comments:
            old_sigs.add(comment_signature(c))
        entry["comment_signatures"] = list(old_sigs)
        self.save()

    def get_crawled_article_ids(self):
        """返回所有已爬取的文章 article_id 集合"""
        return set(self.state["articles"].keys())

    def get_article_count(self):
        """返回已爬取的文章总数"""
        return len(self.state["articles"])

    def update_last_full_crawl(self):
        """更新全局最后完整爬取时间"""
        self.state["last_full_crawl"] = now_iso()
        self.save()

    def remove_article(self, article_id):
        """移除某篇文章的记录（用于清理）"""
        if article_id in self.state["articles"]:
            del self.state["articles"][article_id]
            self.save()

    def get_articles_by_account(self, account):
        """
        获取某公众号的所有已爬文章，按爬取时间降序排列

        Returns:
            list[dict]: [{article_id, title, url, first_crawled, ...}, ...]
        """
        result = []
        for aid, info in self.state["articles"].items():
            if info.get("account") == account:
                result.append({
                    "article_id": aid,
                    "url": info.get("url", ""),
                    "title": info.get("title", ""),
                    "publish_time": info.get("publish_time", ""),
                    "first_crawled": info.get("first_crawled", ""),
                    "last_crawled": info.get("last_crawled", ""),
                })
        # 按首次爬取时间降序（最新的在前）
        result.sort(key=lambda x: x.get("first_crawled", ""), reverse=True)
        return result

    def get_outdated_article_ids(self, account, keep_count=2):
        """
        获取某公众号超出保留数量的旧文章 ID（用于清理）

        Args:
            account: 公众号名称
            keep_count: 保留最新几篇（默认 2）

        Returns:
            list[str]: 需要清理的 article_id 列表
        """
        articles = self.get_articles_by_account(account)
        if len(articles) <= keep_count:
            return []
        # 保留最新的 keep_count 篇，其余的需要清理
        return [a["article_id"] for a in articles[keep_count:]]

    def summary(self):
        """返回状态摘要"""
        articles = self.state["articles"]
        total = len(articles)
        with_comments = sum(
            1 for a in articles.values() if a.get("comment_signatures")
        )
        return {
            "total_articles": total,
            "articles_with_comments": with_comments,
            "last_full_crawl": self.state.get("last_full_crawl"),
        }
