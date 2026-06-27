# -*- coding: utf-8 -*-
"""
数据存储模块
支持 JSON / CSV / Markdown 格式输出
支持增量更新（按文章固定路径存储）
支持评论删除标记 + Markdown 同步刷新
"""

import os
import re
import csv
import json
import hashlib

import config
from utils import logger, timestamp_str, now_iso


def _safe_filename(text, max_len=40):
    """清理字符串中的非法文件名字符"""
    # 替换 Windows/Linux 文件名非法字符
    text = re.sub(r'[\\/:*?"<>|\n\r\t]', "", text)
    # 去除首尾空格和点
    text = text.strip(". ")
    # 截断
    return text[:max_len].strip()


def _date_from_publish_time(publish_time):
    """从发布时间提取 YYYY-MM-DD"""
    if not publish_time:
        return "未知日期"
    # 支持 "2026-06-27 10:00:00" / "2026-06-27" / 时间戳
    m = re.match(r"(\d{4}-\d{2}-\d{2})", str(publish_time))
    if m:
        return m.group(1)
    return "未知日期"


def _article_filename(publish_time, title, ext):
    """
    生成文章文件名：{发布日期}_{标题}.{ext}

    示例: 2026-06-27_停车61分钟按2小时计费.json
    """
    date_str = _date_from_publish_time(publish_time)
    title_str = _safe_filename(title) if title else "未知标题"
    return f"{date_str}_{title_str}.{ext}"


def _article_path(publish_time, title, ext):
    """返回某篇文章的存储路径"""
    filename = _article_filename(publish_time, title, ext)
    return os.path.join(config.DATA_DIR, filename)


def comment_signature(comment):
    """为单条评论生成唯一签名（nick_name|content|create_time）"""
    nick = comment.get("nick_name", "")
    content = comment.get("content", "")
    ctime = comment.get("create_time", "")
    return f"{nick}|{content}|{ctime}"


class Storage:
    """数据存储器（支持增量更新 + Markdown 输出）"""

    def __init__(self):
        os.makedirs(config.DATA_DIR, exist_ok=True)

    # ==================== 基础保存接口 ====================

    def save_article(self, article, comments=None):
        """
        保存单篇文章及其评论（JSON + Markdown）
        文件名格式：{发布日期}_{标题}.{ext}

        Args:
            article: 文章信息字典
            comments: 评论列表

        Returns:
            str: 保存的 JSON 文件路径
        """
        comments = comments or []

        # 构建完整记录
        record = dict(article)
        record["comments"] = comments
        record["comment_count"] = len(comments)
        record["saved_at"] = now_iso()

        json_path = self._save_json(record)
        self._save_markdown(record)

        return json_path

    def load_article(self, publish_time, title):
        """
        加载已保存的文章数据（用于增量更新时读取已有评论）

        Args:
            publish_time: 发布时间
            title: 文章标题

        Returns:
            dict or None
        """
        path = _article_path(publish_time, title, "json")
        if not os.path.exists(path):
            return None
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.warning(f"加载文章数据失败({title[:30]}): {e}")
            return None

    # ==================== 评论增量更新（含删除标记）====================

    def merge_comments(self, publish_time, title, fetched_comments):
        """
        合并评论：新增追加 + 已有更新 + 消失标记删除

        Args:
            publish_time: 发布时间（用于定位文件）
            title: 文章标题（用于定位文件）
            fetched_comments: 本次从微信获取到的评论列表

        Returns:
            dict: {"added": N, "updated": N, "deleted": N, "total": N}
        """
        existing = self.load_article(publish_time, title)
        if not existing:
            logger.warning(f"文章数据不存在，无法合并评论: {title[:30]}")
            return {"added": 0, "updated": 0, "deleted": 0, "total": 0}

        old_comments = existing.get("comments", [])

        # 构建签名 → 旧评论索引
        old_map = {}
        for i, c in enumerate(old_comments):
            sig = comment_signature(c)
            old_map[sig] = i

        # 构建签名 → 新评论
        new_sigs = set()
        for c in fetched_comments:
            new_sigs.add(comment_signature(c))

        now = now_iso()
        added = 0
        updated = 0
        deleted = 0

        # 1. 更新已有评论 + 标记删除
        for sig, idx in old_map.items():
            old_c = old_comments[idx]
            if sig in new_sigs:
                # 两边都有 → 更新字段（点赞数可能变化）
                for c in fetched_comments:
                    if comment_signature(c) == sig:
                        changed = False
                        if c.get("like_num", 0) != old_c.get("like_num", 0):
                            old_c["like_num"] = c.get("like_num", 0)
                            changed = True
                        if c.get("reply", "") != old_c.get("reply", ""):
                            old_c["reply"] = c.get("reply", "")
                            old_c["reply_nick_name"] = c.get("reply_nick_name", "")
                            changed = True
                        if changed:
                            old_c["updated_at"] = now
                            updated += 1
                        # 如果之前被标记删除但现在又出现了，恢复
                        if old_c.get("deleted"):
                            old_c["deleted"] = False
                            old_c.pop("deleted_at", None)
                            old_c["restored_at"] = now
                        break
            else:
                # 旧评论不在新列表中 → 标记删除
                if not old_c.get("deleted"):
                    old_c["deleted"] = True
                    old_c["deleted_at"] = now
                    deleted += 1

        # 2. 追加新评论
        for c in fetched_comments:
            sig = comment_signature(c)
            if sig not in old_map:
                c["fetched_at"] = now
                old_comments.append(c)
                added += 1

        # 3. 保存
        existing["comments"] = old_comments
        existing["comment_count"] = len(
            [c for c in old_comments if not c.get("deleted")]
        )
        existing["comment_total_count"] = len(old_comments)
        existing["saved_at"] = now
        existing["comment_updated_at"] = now

        json_path = _article_path(
            existing.get("publish_time", ""), existing.get("title", ""), "json"
        )
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(existing, f, ensure_ascii=False, indent=2)

        # 同步更新 Markdown
        self._save_markdown(existing)

        logger.info(
            f"评论合并完成 [{existing.get('title', '')[:30]}]: "
            f"新增 {added}, 更新 {updated}, 删除标记 {deleted}, "
            f"总计 {len(old_comments)}"
        )
        return {
            "added": added,
            "updated": updated,
            "deleted": deleted,
            "total": len(old_comments),
        }

    # ==================== 批量保存 ====================

    def save_batch(self, all_records):
        """批量保存所有文章数据到一个汇总 JSON 文件"""
        filename = f"wechat_articles_{timestamp_str()}.json"
        filepath = os.path.join(config.DATA_DIR, filename)

        output = {
            "crawl_time": now_iso(),
            "total_articles": len(all_records),
            "total_comments": sum(
                r.get("comment_count", 0) for r in all_records
            ),
            "articles": all_records,
        }

        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(output, f, ensure_ascii=False, indent=2)

        logger.info(f"批量数据已保存: {filepath}")
        return filepath

    # ==================== Markdown 生成 ====================

    def _save_markdown(self, record):
        """
        将文章 + 评论生成为格式优美的 Markdown 文件

        评论展示规则:
          - 正常评论: 正常显示
          - 已删除评论: 删除线标记 + [已删除] 标签 + 删除时间
          - 已恢复评论: [已恢复] 标签
        """
        url = record.get("url", "")
        publish_time = record.get("publish_time", "")
        title = record.get("title", "")
        if publish_time or title:
            filepath = _article_path(publish_time, title, "md")
        else:
            filepath = os.path.join(
                config.DATA_DIR, f"article_unknown_{timestamp_str()}.md"
            )

        lines = []

        # ---- 标题 ----
        title = record.get("title", "未知标题")
        lines.append(f"# {title}")
        lines.append("")

        # ---- 元信息 ----
        lines.append("| 字段 | 值 |")
        lines.append("|------|------|")
        lines.append(f"| 公众号 | {record.get('account', '')} |")
        lines.append(f"| 作者 | {record.get('author', '')} |")
        lines.append(f"| 发布时间 | {record.get('publish_time', '')} |")
        lines.append(f"| 抓取时间 | {record.get('saved_at', '')} |")
        lines.append(f"| 评论更新 | {record.get('comment_updated_at', record.get('saved_at', ''))} |")
        if url:
            lines.append(f"| 原文链接 | [查看原文]({url}) |")
        lines.append("")
        lines.append("---")
        lines.append("")

        # ---- 正文 ----
        # 优先使用 content_md（保持公众号排版），否则回退到 content（纯文本）
        content_md = record.get("content_md", "")
        content_plain = record.get("content", "")
        if content_md:
            lines.append("## 正文")
            lines.append("")
            lines.append(content_md)
            lines.append("")
            lines.append("---")
            lines.append("")
        elif content_plain:
            lines.append("## 正文")
            lines.append("")
            lines.append(content_plain)
            lines.append("")
            lines.append("---")
            lines.append("")

        # ---- 评论 ----
        comments = record.get("comments", [])
        active_comments = [c for c in comments if not c.get("deleted")]
        deleted_comments = [c for c in comments if c.get("deleted")]

        lines.append(f"## 评论（共 {len(active_comments)} 条")
        if deleted_comments:
            lines.append(f"，其中 {len(deleted_comments)} 条已删除")
        lines.append("）")
        lines.append("")

        if not comments:
            lines.append("*暂无评论*")
            lines.append("")
        else:
            for i, c in enumerate(comments, 1):
                nick = c.get("nick_name", "匿名")
                content_text = c.get("content", "")
                like_num = c.get("like_num", 0)
                ctime = c.get("create_time", "")
                reply = c.get("reply", "")
                reply_nick = c.get("reply_nick_name", "")
                is_deleted = c.get("deleted", False)

                if is_deleted:
                    # 已删除评论：删除线 + 标签
                    lines.append(f"### ~~{i}. {nick}~~ `[已删除]`")
                    lines.append("")
                    lines.append(f"~~{content_text}~~")
                    lines.append("")
                    lines.append(f"- 点赞: ~~{like_num}~~")
                    if ctime:
                        lines.append(f"- 评论时间: {ctime}")
                    if c.get("deleted_at"):
                        lines.append(f"- 删除时间: {c['deleted_at']}")
                    if c.get("restored_at"):
                        lines.append(f"- 恢复时间: {c['restored_at']}")
                else:
                    lines.append(f"### {i}. {nick}")
                    lines.append("")
                    lines.append(content_text)
                    lines.append("")
                    meta_parts = [f"点赞: {like_num}"]
                    if ctime:
                        meta_parts.append(f"时间: {ctime}")
                    if c.get("updated_at"):
                        meta_parts.append(f"更新: {c['updated_at']}")
                    lines.append(f"- {' | '.join(meta_parts)}")

                # 作者回复
                if reply:
                    lines.append("")
                    lines.append(
                        f"> **{reply_nick or '作者'} 回复**: {reply}"
                    )

                lines.append("")

        # ---- 页脚 ----
        lines.append("---")
        lines.append("")
        lines.append(
            f"*本文档由微信公众号爬虫自动生成，"
            f"最后更新: {now_iso()}*"
        )

        with open(filepath, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))

        logger.info(f"文章Markdown已保存: {os.path.basename(filepath)}")
        return filepath

    # ==================== 内部 JSON/CSV 方法 ====================

    def _save_json(self, record):
        """保存为 JSON 文件（按 日期_标题 命名，支持覆盖更新）"""
        publish_time = record.get("publish_time", "")
        title = record.get("title", "")
        if publish_time or title:
            filepath = _article_path(publish_time, title, "json")
        else:
            filepath = os.path.join(
                config.DATA_DIR, f"article_unknown_{timestamp_str()}.json"
            )

        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(record, f, ensure_ascii=False, indent=2)

        logger.info(f"文章JSON已保存: {os.path.basename(filepath)}")
        return filepath

    def _save_csv(self, record):
        """保存为 CSV 文件（按 日期_标题 命名，支持覆盖更新）"""
        publish_time = record.get("publish_time", "")
        title = record.get("title", "")
        if publish_time or title:
            filepath = _article_path(publish_time, title, "csv")
        else:
            filepath = os.path.join(
                config.DATA_DIR, f"article_unknown_{timestamp_str()}.csv"
            )

        with open(filepath, "w", encoding="utf-8-sig", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["=== 文章信息 ==="])
            writer.writerow(["标题", record.get("title", "")])
            writer.writerow(["公众号", record.get("account", "")])
            writer.writerow(["作者", record.get("author", "")])
            writer.writerow(["发布时间", record.get("publish_time", "")])
            writer.writerow(["URL", record.get("url", "")])
            writer.writerow(["评论数", record.get("comment_count", 0)])
            writer.writerow(["保存时间", record.get("saved_at", "")])
            writer.writerow([])

            comments = record.get("comments", [])
            if comments:
                writer.writerow(["=== 评论列表 ==="])
                writer.writerow(
                    ["序号", "昵称", "评论内容", "点赞数", "评论时间",
                     "作者回复", "回复者", "是否删除", "删除时间"]
                )
                for i, c in enumerate(comments, 1):
                    writer.writerow([
                        i,
                        c.get("nick_name", ""),
                        c.get("content", ""),
                        c.get("like_num", 0),
                        c.get("create_time", ""),
                        c.get("reply", ""),
                        c.get("reply_nick_name", ""),
                        "是" if c.get("deleted") else "否",
                        c.get("deleted_at", ""),
                    ])
            else:
                writer.writerow(["（无评论）"])

        logger.info(f"文章CSV已保存: {os.path.basename(filepath)}")
        return filepath

    # ==================== 查询/清理接口 ====================

    def get_article_json_path(self, publish_time, title):
        """返回某篇文章的 JSON 文件路径"""
        return _article_path(publish_time, title, "json")

    def get_article_md_path(self, publish_time, title):
        """返回某篇文章的 Markdown 文件路径"""
        return _article_path(publish_time, title, "md")

    def delete_article_files(self, publish_time, title):
        """删除某篇文章的所有存储文件（JSON + MD + CSV）"""
        for ext in ["json", "md", "csv"]:
            path = _article_path(publish_time, title, ext)
            if os.path.exists(path):
                os.remove(path)
                logger.info(f"已删除文件: {os.path.basename(path)}")
