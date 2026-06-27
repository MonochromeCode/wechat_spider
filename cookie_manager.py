# -*- coding: utf-8 -*-
"""
Cookie 自动管理模块
策略优先级：
  1. 缓存文件中的 cookie（未过期）
  2. 浏览器自动提取（需管理员权限或已配置）
  3. config 手动配置
  4. 交互式输入（运行时手动粘贴）
  5. 自动访问首页获取 session cookie
"""
import os
import json
import time

from utils import logger

# cookie 缓存文件
COOKIE_CACHE_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data",
    "cookie_cache.json",
)

# cookie 有效期（秒）
COOKIE_TTL = 7200  # 2 小时


def load_cached_cookie(key):
    """从缓存文件加载 cookie"""
    if not os.path.exists(COOKIE_CACHE_FILE):
        return None
    try:
        with open(COOKIE_CACHE_FILE, "r", encoding="utf-8") as f:
            cache = json.load(f)
        entry = cache.get(key)
        if not entry:
            return None
        if time.time() - entry.get("ts", 0) > COOKIE_TTL:
            logger.info(f"缓存cookie已过期({key})")
            return None
        return entry.get("cookie", "")
    except Exception:
        return None


def save_cached_cookie(key, cookie):
    """保存 cookie 到缓存文件"""
    try:
        os.makedirs(os.path.dirname(COOKIE_CACHE_FILE), exist_ok=True)
        cache = {}
        if os.path.exists(COOKIE_CACHE_FILE):
            with open(COOKIE_CACHE_FILE, "r", encoding="utf-8") as f:
                cache = json.load(f)
        cache[key] = {"cookie": cookie, "ts": time.time()}
        with open(COOKIE_CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(cache, f, ensure_ascii=False, indent=2)
        logger.info(f"cookie已缓存({key})")
    except Exception as e:
        logger.warning(f"缓存cookie失败: {e}")


def clear_cached_cookie(key):
    """清除指定 key 的缓存 cookie"""
    if not os.path.exists(COOKIE_CACHE_FILE):
        return
    try:
        with open(COOKIE_CACHE_FILE, "r", encoding="utf-8") as f:
            cache = json.load(f)
        if key in cache:
            del cache[key]
            with open(COOKIE_CACHE_FILE, "w", encoding="utf-8") as f:
                json.dump(cache, f, ensure_ascii=False, indent=2)
            logger.info(f"已清除缓存cookie({key})")
    except Exception:
        pass


def _try_browser_cookies(domain):
    """尝试从浏览器提取 cookie（可能需要管理员权限）"""
    try:
        import browser_cookie3
    except ImportError:
        return ""

    browsers = [
        ("Chrome", browser_cookie3.chrome),
        ("Edge", browser_cookie3.edge),
        ("Firefox", browser_cookie3.firefox),
    ]

    for browser_name, load_func in browsers:
        try:
            cj = load_func(domain_name=domain)
            cookies = []
            for c in cj:
                cookies.append(f"{c.name}={c.value}")
            if cookies:
                cookie_str = "; ".join(cookies)
                logger.info(f"从{browser_name}提取到 {len(cookies)} 个 cookie ({domain})")
                return cookie_str
        except PermissionError:
            logger.debug(f"从{browser_name}提取cookie需要管理员权限")
        except Exception as e:
            logger.debug(f"从{browser_name}提取cookie失败: {e}")

    return ""


def _try_session_cookie(url, domain):
    """
    访问目标网站首页，自动获取 session cookie
    这通常足够避免首次访问的反爬检测
    """
    import requests
    from utils import get_headers

    try:
        s = requests.Session()
        s.headers.update(get_headers())
        r = s.get(url, timeout=10)
        cookies = []
        for c in s.cookies:
            cookies.append(f"{c.name}={c.value}")
        if cookies:
            cookie_str = "; ".join(cookies)
            logger.info(f"从{domain}首页获取到 {len(cookies)} 个 session cookie")
            return cookie_str
    except Exception as e:
        logger.debug(f"获取session cookie失败({domain}): {e}")
    return ""


def get_sogou_cookie():
    """
    获取搜狗 cookie（按优先级尝试多种方式）

    Returns:
        str: cookie 字符串
    """
    import config

    # 1. 尝试缓存
    cached = load_cached_cookie("sogou")
    if cached:
        logger.info("使用缓存的搜狗cookie")
        return cached

    # 2. 尝试从浏览器提取
    if config.AUTO_LOAD_BROWSER_COOKIE:
        browser_cookie = _try_browser_cookies("sogou.com")
        if browser_cookie:
            save_cached_cookie("sogou", browser_cookie)
            return browser_cookie

    # 3. 使用 config 中的手动配置
    if config.SOGOU_COOKIE:
        logger.info("使用config中配置的搜狗cookie")
        save_cached_cookie("sogou", config.SOGOU_COOKIE)
        return config.SOGOU_COOKIE

    # 4. 访问首页获取 session cookie
    logger.info("尝试访问搜狗首页获取session cookie...")
    session_cookie = _try_session_cookie(
        "https://weixin.sogou.com/", "sogou.com"
    )
    if session_cookie:
        save_cached_cookie("sogou", session_cookie)
        return session_cookie

    logger.warning("未获取到搜狗cookie，可能触发反爬验证")
    return ""


def get_wechat_cookie():
    """
    获取微信 cookie（按优先级尝试多种方式）

    Returns:
        str: cookie 字符串
    """
    import config

    # 1. 尝试缓存
    cached = load_cached_cookie("wechat")
    if cached:
        logger.info("使用缓存的微信cookie")
        return cached

    # 2. 尝试从浏览器提取
    if config.AUTO_LOAD_BROWSER_COOKIE:
        browser_cookie = _try_browser_cookies("qq.com")
        if browser_cookie:
            save_cached_cookie("wechat", browser_cookie)
            return browser_cookie

    # 3. 使用 config 中的手动配置
    if config.WECHAT_COOKIE:
        logger.info("使用config中配置的微信cookie")
        save_cached_cookie("wechat", config.WECHAT_COOKIE)
        return config.WECHAT_COOKIE

    # 4. 访问首页获取 session cookie
    session_cookie = _try_session_cookie(
        "https://mp.weixin.qq.com/", "qq.com"
    )
    if session_cookie:
        save_cached_cookie("wechat", session_cookie)
        return session_cookie

    return ""


def refresh_cookies():
    """
    手动刷新 cookie（清除缓存，重新获取）

    Returns:
        dict: {"sogou": "cookie_str", "wechat": "cookie_str"}
    """
    # 清除缓存
    clear_cached_cookie("sogou")
    clear_cached_cookie("wechat")

    result = {
        "sogou": get_sogou_cookie(),
        "wechat": get_wechat_cookie(),
    }

    return result


def input_cookie_interactive(key):
    """
    交互式输入 cookie（用户从浏览器复制粘贴）

    Args:
        key: "sogou" 或 "wechat"

    Returns:
        str: 用户输入的 cookie 字符串
    """
    if key == "sogou":
        print("\n" + "=" * 60)
        print("请手动获取搜狗 cookie：")
        print("  1. 打开浏览器访问 https://weixin.sogou.com")
        print("  2. 如有验证码，完成验证")
        print("  3. 按 F12 → Network → 刷新页面")
        print("  4. 点击任意请求 → Headers → 找到 Cookie")
        print("  5. 复制完整 Cookie 值，粘贴到下方：")
        print("=" * 60)
    else:
        print("\n" + "=" * 60)
        print("请手动获取微信 cookie：")
        print("  1. 打开浏览器访问任意 https://mp.weixin.qq.com 文章")
        print("  2. 按 F12 → Network → 刷新页面")
        print("  3. 点击文章请求 → Headers → 找到 Cookie")
        print("  4. 复制完整 Cookie 值，粘贴到下方：")
        print("=" * 60)

    try:
        cookie = input("Cookie: ").strip()
        if cookie:
            save_cached_cookie(key, cookie)
            print(f"cookie 已保存 ({key})")
            return cookie
    except (EOFError, KeyboardInterrupt):
        print("\n已取消")

    return ""
