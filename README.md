# 微信公众号文章与评论爬虫

基于搜狗微信搜索和微信公众号文章页的抓取工具，支持：

- 按公众号名称抓取最新文章
- 解析文章正文、作者、发布时间
- 抓取微信公众号精选评论
- 生成 `JSON` 和 `Markdown` 文件
- 增量更新评论，并对已删除评论做标记
- 守护进程模式下按间隔循环抓取
- 自动读取浏览器 Cookie，或手动刷新 / 输入 Cookie

## 功能概览

- 搜索指定公众号的最新文章列表
- 解析文章正文，保留纯文本和 Markdown 版本
- 调用微信评论接口获取精选评论及作者回复
- 增量更新已抓取文章的评论
- 对消失的评论标记 `deleted=true`，并同步刷新 Markdown
- 使用 `article_id` 去重，避免 URL 动态参数导致重复
- 支持单篇文章直连抓取
- 支持定时循环抓取

## 环境要求

- Python 3.10+
- Windows 环境下建议使用 Chrome / Edge / Firefox，便于自动读取 Cookie

## 安装

```bash
pip install -r requirements.txt
```

依赖：

- `requests`
- `beautifulsoup4`
- `lxml`
- `fake-useragent`
- `html2text`
- `browser-cookie3`

## 快速开始

### 1. 配置目标公众号

编辑 `config.py`，修改 `WECHAT_ACCOUNTS`：

```python
WECHAT_ACCOUNTS = [
    "人民日报",
]
```

### 2. 单次抓取

```bash
python main.py
```

### 3. 指定公众号或文章数量

```bash
python main.py --accounts 人民日报 央视新闻
python main.py --account 人民日报 --count 10
```

### 4. 直接抓取单篇文章

```bash
python main.py --url https://mp.weixin.qq.com/s/xxxxx
```

### 5. 不抓取评论

```bash
python main.py --no-comments
```

## 命令行参数

```bash
python main.py [options]
```

可用参数：

- `--accounts <name1> <name2> ...` 指定多个公众号
- `--account <name>` 指定单个公众号
- `--count <n>` 指定每个公众号抓取文章数
- `--url <article_url>` 直接抓取单篇文章
- `--no-comments` 不抓取评论
- `--daemon` 启动定时循环模式
- `--interval <hours>` 覆盖 `config.CRAWL_INTERVAL_HOURS`
- `--max-loops <n>` 限制定时循环次数，`0` 表示无限循环
- `--no-incremental` 关闭增量更新
- `--refresh-cookie` 清空 Cookie 缓存并重新获取
- `--input-cookie sogou|wechat|all` 手动输入 Cookie

## 输出文件

默认输出目录：

- `data/` 抓取结果
- `logs/` 日志

单篇文章会生成：

- `data/YYYY-MM-DD_标题.json`
- `data/YYYY-MM-DD_标题.md`

批量抓取还会生成：

- `data/wechat_articles_时间戳.json`

状态文件：

- `data/crawl_state.json`

Cookie 缓存文件：

- `data/cookie_cache.json`

## JSON 结构示例

```json
{
  "title": "文章标题",
  "author": "作者",
  "account": "公众号名称",
  "publish_time": "2026-06-27 10:00:00",
  "content": "正文纯文本",
  "content_html": "<div>...</div>",
  "content_md": "Markdown 正文",
  "url": "https://mp.weixin.qq.com/s/xxx",
  "article_id": "appmsgid_idx",
  "comment_params": {
    "__biz": "...",
    "appmsgid": "...",
    "idx": "1",
    "comment_id": "..."
  },
  "comments": [
    {
      "nick_name": "评论者",
      "content": "评论内容",
      "like_num": 12,
      "create_time": "2026-06-27 10:30:00",
      "reply": "作者回复",
      "reply_nick_name": "公众号"
    }
  ],
  "comment_count": 1,
  "saved_at": "2026-06-27T20:00:00"
}
```

## 增量更新机制

启用 `ENABLE_INCREMENTAL=True` 时，程序会使用 `state_manager.py` 维护抓取状态。

规则如下：

- 文章使用 `article_id` 去重，不直接依赖 URL
- 新文章会完整抓取正文和评论
- 已抓取文章只更新最新评论
- 仅对每个公众号最新 `COMMENT_UPDATE_LATEST_COUNT` 篇文章更新评论
- 超出范围的历史文章保留文件，但默认不再刷新评论
- 评论合并时会处理：
  - 新增评论追加保存
  - 点赞数或回复变化时覆盖更新
  - 已消失评论标记为删除，不直接物理删除

关闭增量模式：

```bash
python main.py --no-incremental
```

## 定时循环模式

按固定时间间隔重复执行抓取：

```bash
python main.py --daemon
python main.py --daemon --interval 2
python main.py --daemon --max-loops 5
```

相关配置：

- `CRAWL_INTERVAL_HOURS` 抓取间隔，默认 `6`
- `MAX_LOOP_COUNT` 最大循环次数，默认 `0`，表示无限

## Cookie 获取策略

`cookie_manager.py` 的优先级：

1. 读取 `data/cookie_cache.json` 中的缓存 Cookie
2. 自动从浏览器读取 Cookie
3. 读取 `config.py` 中手动配置的 Cookie
4. 运行时手动输入 Cookie
5. 访问首页尝试获取 session Cookie

常用命令：

```bash
python main.py --refresh-cookie
python main.py --input-cookie sogou
python main.py --input-cookie wechat
python main.py --input-cookie all
```

`config.py` 中相关项：

- `AUTO_LOAD_BROWSER_COOKIE`
- `SOGOU_COOKIE`
- `WECHAT_COOKIE`

## 关键配置项

`config.py` 中常用参数：

| 配置项 | 默认值 | 说明 |
|---|---:|---|
| `WECHAT_ACCOUNTS` | `["人民日报"]` | 默认抓取的公众号 |
| `ARTICLES_PER_ACCOUNT` | `2` | 每个公众号抓取文章数 |
| `REQUEST_DELAY_MIN` | `60` | 最小请求间隔秒数 |
| `REQUEST_DELAY_MAX` | `120` | 最大请求间隔秒数 |
| `REQUEST_TIMEOUT` | `15` | 请求超时秒数 |
| `MAX_RETRIES` | `3` | 最大重试次数 |
| `ENABLE_COMMENTS` | `True` | 是否抓取评论 |
| `MAX_COMMENTS_PER_ARTICLE` | `100` | 每篇最大评论数 |
| `ENABLE_INCREMENTAL` | `True` | 是否启用增量更新 |
| `COMMENT_UPDATE_INTERVAL_HOURS` | `1` | 评论最短刷新间隔 |
| `COMMENT_UPDATE_LATEST_COUNT` | `2` | 每号仅更新最新 N 篇文章评论 |
| `KEEP_HISTORY_ARTICLES` | `True` | 是否保留历史文章文件 |
| `CRAWL_INTERVAL_HOURS` | `6` | 守护模式抓取间隔 |
| `MAX_LOOP_COUNT` | `0` | 守护模式最大循环次数 |
| `PROXY` | `None` | 代理配置 |

## 项目结构

```text
wechat_spider/
├── main.py
├── config.py
├── sogou_searcher.py
├── article_parser.py
├── comment_fetcher.py
├── cookie_manager.py
├── state_manager.py
├── storage.py
├── utils.py
├── requirements.txt
├── data/
└── logs/
```

## 常见问题

### 1. 搜狗搜索触发验证码

处理方式：

- 浏览器先访问 `https://weixin.sogou.com/` 完成验证
- 执行 `python main.py --refresh-cookie`
- 必要时手动执行 `python main.py --input-cookie sogou`
- 调大 `REQUEST_DELAY_MIN` 和 `REQUEST_DELAY_MAX`

### 2. 评论抓不到

常见原因：

- 文章未开启评论
- 微信评论接口需要有效 Cookie
- `key` 参数过期或缺失

处理方式：

- 浏览器打开任意微信文章页后刷新 Cookie
- 手动输入 `wechat` Cookie
- 重新抓取目标文章，确认文章页里能提取到 `comment_id`

### 3. 请求过快被限制

建议：

- 增大请求间隔
- 配置 `PROXY`
- 减少单次抓取公众号和文章数量

## 注意事项

- 本项目依赖第三方站点页面结构和接口参数，页面变更后可能需要修复解析逻辑。
- 微信评论接口可用性受 Cookie、`key` 参数、评论开关状态影响。
- Windows 文件名非法字符会自动清理，标题会截断到固定长度。

## 免责声明

本项目仅用于学习和研究。使用时请遵守目标网站服务条款、robots 协议及相关法律法规，不要用于违法或未经授权的数据采集。
