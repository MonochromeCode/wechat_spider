# -*- coding: utf-8 -*-
"""
微信公众号爬虫 - 主程序入口

用法:
    python main.py                          # 单次爬取 config.WECHAT_ACCOUNTS 中配置的公众号
    python main.py --accounts 人民日报 夜读   # 指定公众号
    python main.py --account 人民日报 --count 10  # 指定单个公众号和文章数
    python main.py --no-comments             # 不爬取评论
    python main.py --url <微信文章链接>        # 直接爬取单篇文章
    python main.py --daemon                  # 定时循环模式（按 config.CRAWL_INTERVAL_HOURS 间隔）
    python main.py --daemon --interval 2     # 定时循环，每 2 小时运行一次
    python main.py --no-incremental         # 关闭增量更新（每次都全量重新爬取）
"""

import argparse
import time

import config
from article_parser import ArticleParser
from comment_fetcher import CommentFetcher
from sogou_searcher import SogouSearcher
from state_manager import CrawlState
from storage import Storage
from utils import logger, random_delay, now_iso


class WeChatSpider:
    """微信公众号爬虫主控制器（支持增量更新和定时循环）"""

    def __init__(self):
        self.searcher = SogouSearcher()
        self.parser = ArticleParser()
        self.comment_fetcher = CommentFetcher()
        self.storage = Storage()
        self.state = CrawlState() if config.ENABLE_INCREMENTAL else None
        self.stats = {
            "accounts": 0,
            "articles_found": 0,
            "articles_new": 0,
            "articles_skipped": 0,
            "articles_comment_updated": 0,
            "articles_preserved": 0,
            "articles_parsed": 0,
            "comments_fetched": 0,
            "comments_new": 0,
            "comments_deleted": 0,
            "errors": 0,
        }

    # ==================== 公众号爬取（增量模式）====================

    def crawl_account(self, account_name, count=None):
        """
        爬取单个公众号的文章和评论（增量模式）

        策略:
          1. 搜索文章列表（搜狗按发布时间降序返回）
          2. 对每篇文章获取正文 + article_id
          3. 新文章：完整保存（正文 + 评论 + Markdown）
          4. 已爬文章 + 排名 ≤ COMMENT_UPDATE_LATEST_COUNT：增量更新评论
          5. 已爬文章 + 排名 > COMMENT_UPDATE_LATEST_COUNT：保留文档，跳过评论更新
          6. 不删除任何历史文章文件
        """
        logger.info(f"\n{'='*60}")
        logger.info(f"开始爬取公众号: {account_name}")
        logger.info(f"{'='*60}")

        self.stats["accounts"] += 1

        # 第一步：搜索文章列表
        try:
            articles = self.searcher.fetch_account_articles(
                account_name, count=count
            )
        except Exception as e:
            logger.error(f"搜索公众号 [{account_name}] 失败: {e}")
            self.stats["errors"] += 1
            return []

        if not articles:
            logger.warning(f"公众号 [{account_name}] 未获取到文章")
            return []

        self.stats["articles_found"] += len(articles)
        records = []
        latest_n = config.COMMENT_UPDATE_LATEST_COUNT

        # 第二步：逐篇处理
        for i, article_meta in enumerate(articles, 1):
            url = article_meta.get("url", "")
            title = article_meta.get("title", f"文章{i}")

            logger.info(f"\n--- [{i}/{len(articles)}] {title[:40]} ---")

            if not url:
                logger.warning("文章链接为空，跳过")
                continue

            # 获取文章正文（需要 article_id 做去重判断）
            random_delay()
            try:
                article = self.parser.fetch_article(url)
            except Exception as e:
                logger.error(f"文章抓取失败: {e}")
                self.stats["errors"] += 1
                continue

            if not article:
                logger.warning("文章解析为空，跳过")
                continue

            article_id = article.get("article_id", "")
            if not article_id:
                logger.warning("无法生成 article_id，将强制保存")
                article_id = f"unknown_{i}_{now_iso()}"

            # 增量模式：用 article_id 去重
            if self.state and self.state.is_article_crawled(article_id):
                if i <= latest_n:
                    # 最新 N 篇：增量更新评论
                    logger.info(
                        f"✓ 已爬取过（{article_id}），"
                        f"最新第{i}篇，增量更新评论..."
                    )
                    merge_result = self._update_comments_only(
                        article_id, article, url
                    )
                    self.stats["articles_comment_updated"] += 1
                    self.stats["comments_new"] += merge_result.get("added", 0)
                    self.stats["comments_deleted"] += merge_result.get("deleted", 0)
                else:
                    # 非最新 N 篇：保留文档，不更新评论
                    logger.info(
                        f"✓ 已爬取过（{article_id}），"
                        f"非最新{latest_n}篇（第{i}篇），保留文档不更新评论"
                    )
                    self.stats["articles_preserved"] += 1
                self.stats["articles_skipped"] += 1
            else:
                # 新文章：完整保存
                logger.info(f"✚ 新文章，完整保存...")
                self._save_new_article(article, article_id, url, account_name)
                self.stats["articles_new"] += 1
                self.stats["articles_parsed"] += 1
                records.append(article)

        logger.info(
            f"公众号 [{account_name}] 处理完成: "
            f"新增 {self.stats['articles_new']} 篇, "
            f"评论更新 {self.stats['articles_comment_updated']} 篇, "
            f"保留 {self.stats['articles_preserved']} 篇"
        )
        return [r for r in records if r is not None]

    def _save_new_article(self, article, article_id, url, account_name):
        """保存新文章（正文 + 评论）"""
        # 补充信息
        if not article["account"]:
            article["account"] = account_name

        # 抓取评论
        comments = []
        if config.ENABLE_COMMENTS:
            random_delay()
            try:
                comment_params = article.get("comment_params", {})
                comments = self.comment_fetcher.fetch_comments(
                    comment_params, article_url=url,
                    session=self.parser.session
                )
                self.stats["comments_fetched"] += len(comments)
            except Exception as e:
                logger.error(f"评论抓取失败: {e}")
                self.stats["errors"] += 1

        article["comments"] = comments
        article["comment_count"] = len(comments)

        # 保存
        try:
            data_file = self.storage.save_article(article, comments)
        except Exception as e:
            logger.error(f"保存失败: {e}")
            self.stats["errors"] += 1
            data_file = ""

        # 更新状态
        if self.state:
            self.state.update_article_state(
                article_id=article_id,
                url=url,
                title=article.get("title", ""),
                account=article.get("account", account_name),
                data_file=data_file,
                comments=comments,
                publish_time=article.get("publish_time", ""),
            )

    def _update_comments_only(self, article_id, article, url):
        """
        对已爬文章仅增量更新评论
        使用 merge_comments 实现新增/更新/删除标记

        Returns:
            dict: {"added": N, "updated": N, "deleted": N, "total": N}
        """
        if not config.ENABLE_COMMENTS:
            return {"added": 0, "updated": 0, "deleted": 0, "total": 0}

        # 检查是否需要更新
        if self.state and not self.state.should_update_comments(article_id):
            logger.info("评论已是最新，跳过")
            return {"added": 0, "updated": 0, "deleted": 0, "total": 0}

        # 获取评论参数
        comment_params = article.get("comment_params", {})
        if not comment_params.get("comment_id"):
            logger.info("该文章未开启评论，跳过")
            if self.state:
                self.state.mark_comment_updated(article_id, [])
            return {"added": 0, "updated": 0, "deleted": 0, "total": 0}

        # 获取最新评论
        random_delay()
        try:
            fetched_comments = self.comment_fetcher.fetch_comments(
                comment_params, article_url=url,
                session=self.parser.session
            )
        except Exception as e:
            logger.error(f"评论更新失败: {e}")
            self.stats["errors"] += 1
            return {"added": 0, "updated": 0, "deleted": 0, "total": 0}

        # 即使没有获取到评论，也要调用 merge_comments 来标记删除
        # （之前有的评论现在获取不到了，说明可能被删除了）
        result = self.storage.merge_comments(
            article.get("publish_time", ""),
            article.get("title", ""),
            fetched_comments,
        )

        self.stats["comments_new"] += result.get("added", 0)

        # 更新状态
        if self.state:
            self.state.mark_comment_updated(article_id, fetched_comments)

        return result

    def _cleanup_old_articles(self, account_name, keep_count=2):
        """
        清理旧文章（仅在 KEEP_HISTORY_ARTICLES=False 时调用）
        每号保留最新 keep_count 篇，超出的旧文章删除状态记录和文件
        """
        if not self.state:
            return
        if config.KEEP_HISTORY_ARTICLES:
            # 保留历史文章模式：不删除任何文件
            return

        outdated_ids = self.state.get_outdated_article_ids(
            account_name, keep_count=keep_count
        )
        if not outdated_ids:
            return

        logger.info(
            f"公众号 [{account_name}] 清理旧文章: "
            f"保留最新 {keep_count} 篇，清理 {len(outdated_ids)} 篇"
        )
        for aid in outdated_ids:
            article_info = self.state.state["articles"].get(aid, {})
            old_title = article_info.get("title", "")
            old_publish_time = article_info.get("publish_time", "")

            if old_title:
                self.storage.delete_article_files(old_publish_time, old_title)

            self.state.remove_article(aid)
            logger.info(f"  已清理: {old_title[:30]} ({aid})")

    # ==================== 单篇文章爬取 ====================

    def crawl_single_article(self, url):
        """直接爬取单篇文章（跳过搜索步骤）"""
        logger.info(f"直接爬取文章: {url[:80]}")

        # 先获取文章（需要 article_id）
        try:
            article = self.parser.fetch_article(url)
        except Exception as e:
            logger.error(f"文章抓取失败: {e}")
            return None

        if not article:
            return None

        article_id = article.get("article_id", "")

        # 增量模式：若已爬过，仅更新评论
        if self.state and article_id and self.state.is_article_crawled(article_id):
            logger.info("该文章已爬取过，将增量更新评论...")
            self._update_comments_only(article_id, article, url)
            return self.storage.load_article(
                article.get("publish_time", ""), article.get("title", "")
            )

        # 新文章：完整保存
        if not article_id:
            article_id = f"single_{now_iso()}"

        self.stats["articles_parsed"] += 1
        self.stats["articles_new"] += 1

        self._save_new_article(article, article_id, url, article.get("account", ""))
        return article

    # ==================== 运行入口 ====================

    def run(self, accounts=None, count=None, url=None):
        """运行单次爬取"""
        logger.info("=" * 60)
        logger.info("微信公众号爬虫启动（单次模式）")
        logger.info(f"时间: {now_iso()}")
        logger.info("=" * 60)

        all_records = []

        if url:
            record = self.crawl_single_article(url)
            if record:
                all_records.append(record)
        else:
            accounts = accounts or config.WECHAT_ACCOUNTS
            if not accounts:
                logger.error("未配置公众号列表！请在 config.py 中设置 WECHAT_ACCOUNTS")
                return []

            for account in accounts:
                try:
                    records = self.crawl_account(account, count=count)
                    all_records.extend(records)
                except Exception as e:
                    logger.error(f"爬取公众号 [{account}] 时出错: {e}")
                    self.stats["errors"] += 1

                random_delay()

        # 批量保存汇总
        if all_records:
            summary_file = self.storage.save_batch(all_records)
        else:
            summary_file = None

        if self.state:
            self.state.update_last_full_crawl()

        self._print_stats(summary_file)
        return all_records

    def run_daemon(self, accounts=None, count=None, interval_hours=None):
        """
        定时循环模式：按间隔反复执行爬取任务

        Args:
            accounts: 公众号列表
            count: 每个公众号的文章数
            interval_hours: 爬取间隔（小时），覆盖 config 中的设置
        """
        interval_hours = interval_hours or config.CRAWL_INTERVAL_HOURS
        interval_seconds = int(interval_hours * 3600)

        accounts = accounts or config.WECHAT_ACCOUNTS
        max_loops = config.MAX_LOOP_COUNT
        loop_count = 0

        logger.info("=" * 60)
        logger.info("微信公众号爬虫启动（定时循环模式）")
        logger.info(f"  公众号: {', '.join(accounts) or '未配置'}")
        logger.info(f"  爬取间隔: {interval_hours} 小时")
        logger.info(f"  最大循环: {'无限' if max_loops == 0 else max_loops} 次")
        logger.info(f"  增量更新: {'开启' if config.ENABLE_INCREMENTAL else '关闭'}")
        logger.info("=" * 60)

        if not accounts:
            logger.error("未配置公众号列表！请在 config.py 中设置 WECHAT_ACCOUNTS")
            return

        try:
            while True:
                loop_count += 1
                if max_loops > 0 and loop_count > max_loops:
                    logger.info(f"已达到最大循环次数 ({max_loops})，退出")
                    break

                logger.info(
                    f"\n{'#'*60}\n# 第 {loop_count} 轮爬取"
                    f"  时间: {now_iso()}\n{'#'*60}"
                )

                # 重置本轮统计
                self.stats = {
                    "accounts": 0,
                    "articles_found": 0,
                    "articles_new": 0,
                    "articles_skipped": 0,
                    "articles_parsed": 0,
                    "comments_fetched": 0,
                    "comments_new": 0,
                    "errors": 0,
                }

                all_records = []
                for account in accounts:
                    try:
                        records = self.crawl_account(account, count=count)
                        all_records.extend(records)
                    except Exception as e:
                        logger.error(f"爬取公众号 [{account}] 时出错: {e}")
                        self.stats["errors"] += 1
                    random_delay()

                # 保存本轮汇总
                if all_records:
                    self.storage.save_batch(all_records)

                if self.state:
                    self.state.update_last_full_crawl()

                self._print_stats()

                # 计算下次运行时间
                next_time = now_iso(offset_seconds=interval_seconds)
                logger.info(
                    f"\n本轮完成，下次运行时间: {next_time}"
                    f"（{interval_hours} 小时后）"
                )
                logger.info("按 Ctrl+C 停止爬虫")

                # 等待下次运行
                if max_loops == 0 or loop_count < max_loops:
                    logger.info(f"睡眠 {interval_hours} 小时...")
                    time.sleep(interval_seconds)

        except KeyboardInterrupt:
            logger.info("\n\n收到停止信号，爬虫已退出")
            self._print_stats()

    # ==================== 统计输出 ====================

    def _print_stats(self, summary_file=None):
        """打印爬取统计"""
        logger.info("\n" + "=" * 60)
        logger.info("爬取统计:")
        logger.info(f"  爬取公众号数:   {self.stats['accounts']}")
        logger.info(f"  发现文章数:     {self.stats['articles_found']}")
        logger.info(f"  新增文章数:     {self.stats['articles_new']}")
        logger.info(f"  评论更新文章:   {self.stats['articles_comment_updated']}")
        logger.info(f"  保留不更新文章: {self.stats['articles_preserved']}")
        logger.info(f"  跳过文章数:     {self.stats['articles_skipped']}")
        logger.info(f"  成功解析数:     {self.stats['articles_parsed']}")
        logger.info(f"  总评论数:       {self.stats['comments_fetched']}")
        logger.info(f"  新增评论数:     {self.stats['comments_new']}")
        logger.info(f"  删除标记评论:   {self.stats['comments_deleted']}")
        logger.info(f"  错误次数:       {self.stats['errors']}")
        if self.state:
            summary = self.state.summary()
            logger.info(
                f"  历史总文章:   {summary['total_articles']}"
            )
        if summary_file:
            logger.info(f"  汇总文件:     {summary_file}")
        logger.info("=" * 60)


# ==================== 命令行参数 ====================

def parse_args():
    """解析命令行参数"""
    parser = argparse.ArgumentParser(
        description="微信公众号文章+评论爬虫（支持增量更新和定时循环）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  # 单次爬取
  python main.py                              # 爬取config中配置的公众号
  python main.py --accounts 人民日报 央视新闻    # 指定多个公众号
  python main.py --account 人民日报 --count 10  # 指定公众号和文章数
  python main.py --url https://mp.weixin.qq.com/s/xxx  # 爬取单篇文章
  python main.py --no-comments                # 不爬取评论

  # 定时循环模式
  python main.py --daemon                     # 定时循环（间隔见 config.py）
  python main.py --daemon --interval 2        # 每 2 小时运行一次
  python main.py --daemon --max-loops 10     # 最多循环 10 次

  # 增量更新控制
  python main.py --no-incremental             # 关闭增量（每次全量重新爬取）
        """,
    )
    parser.add_argument(
        "--accounts",
        nargs="+",
        help="要爬取的公众号名称列表（空格分隔）",
    )
    parser.add_argument(
        "--account",
        help="单个公众号名称",
    )
    parser.add_argument(
        "--count",
        type=int,
        default=None,
        help=f"每个公众号爬取的文章数（默认 {config.ARTICLES_PER_ACCOUNT}）",
    )
    parser.add_argument(
        "--url",
        help="直接爬取单篇文章URL（跳过搜索步骤）",
    )
    parser.add_argument(
        "--no-comments",
        action="store_true",
        help="不爬取评论",
    )
    # ---- 定时循环相关 ----
    parser.add_argument(
        "--daemon",
        action="store_true",
        help="定时循环模式（按间隔反复运行，间隔见 config.CRAWL_INTERVAL_HOURS）",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=None,
        help="定时循环间隔（小时），覆盖 config 中的设置",
    )
    parser.add_argument(
        "--max-loops",
        type=int,
        default=None,
        help="定时循环最大次数（0=无限，覆盖 config.MAX_LOOP_COUNT）",
    )
    # ---- 增量更新相关 ----
    parser.add_argument(
        "--no-incremental",
        action="store_true",
        help="关闭增量更新（每次都全量重新爬取文章正文）",
    )
    # ---- Cookie 管理 ----
    parser.add_argument(
        "--refresh-cookie",
        action="store_true",
        help="刷新 cookie（清除缓存后重新获取）",
    )
    parser.add_argument(
        "--input-cookie",
        choices=["sogou", "wechat", "all"],
        help="交互式手动输入 cookie（从浏览器复制粘贴）",
    )
    return parser.parse_args()


def main():
    args = parse_args()

    # 交互式输入 cookie
    if args.input_cookie:
        from cookie_manager import input_cookie_interactive, save_cached_cookie
        if args.input_cookie in ("sogou", "all"):
            input_cookie_interactive("sogou")
        if args.input_cookie in ("wechat", "all"):
            input_cookie_interactive("wechat")
        return

    # 刷新 cookie 模式
    if args.refresh_cookie:
        from cookie_manager import refresh_cookies
        result = refresh_cookies()
        if result["sogou"]:
            print(f"搜狗 cookie: {result['sogou'][:60]}...")
        else:
            print("搜狗 cookie 获取失败")
        if result["wechat"]:
            print(f"微信 cookie: {result['wechat'][:60]}...")
        else:
            print("微信 cookie 获取失败")
        if not result["sogou"]:
            print(
                "\n提示：如需手动输入 cookie，请运行：\n"
                "  python main.py --input-cookie sogou"
            )
        return

    # 应用命令行参数覆盖配置
    if args.no_comments:
        config.ENABLE_COMMENTS = False
    if args.no_incremental:
        config.ENABLE_INCREMENTAL = False
    if args.max_loops is not None:
        config.MAX_LOOP_COUNT = args.max_loops

    accounts = args.accounts
    if args.account:
        accounts = [args.account]

    spider = WeChatSpider()

    if args.daemon:
        # 定时循环模式
        spider.run_daemon(
            accounts=accounts,
            count=args.count,
            interval_hours=args.interval,
        )
    else:
        # 单次运行模式
        spider.run(accounts=accounts, count=args.count, url=args.url)


if __name__ == "__main__":
    main()
