#!/usr/bin/env python3
"""
Douyin comment monitoring and auto-reply client.
Uses Playwright for browser automation.

Usage:
    python douyin_client.py login <profile_url>    # First-time login
    python douyin_client.py check                  # Check for new comments
    python douyin_client.py reply <video_id> <comment_id> <text>    # Post a reply
    python douyin_client.py comment <video_id> <text>              # Post a top-level comment
    python douyin_client.py status                   # Show monitoring stats
"""

import json
import logging
import os
import random
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from playwright.sync_api import sync_playwright
try:
    from playwright_stealth.stealth import Stealth
    HAS_STEALTH = True
except ImportError:
    HAS_STEALTH = False
    logger.warning("playwright-stealth not installed, anti-detection disabled")

logger = logging.getLogger("douyin_bot")

SKILL_DIR = Path(__file__).resolve().parent.parent
STATE_FILE = SKILL_DIR / "state.json"
COOKIES_FILE = SKILL_DIR / "cookies.json"
CONFIG_FILE = SKILL_DIR / "config.json"

DEFAULT_CONFIG = {
    "profile_url": "",
    "user_id": "",
    "nickname": "",
    "reply_style": "真诚、友好、有信息量，结合视频内容回答观众问题或回应评论",
    "max_videos_to_check": 10,
    "max_comments_per_video": 50,
    "auto_reply_enabled": False,
    "check_interval_minutes": 60,
    "reply_interval_min": 8,
    "reply_interval_max": 18,
    "replied_comments": {},
}

COMMENTS_API_PATTERN = re.compile(r"aweme/v\d/web/comment/list/")


def load_state() -> dict:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    return {"replied": {}, "videos": {}, "last_check": None}


def save_state(state: dict):
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def load_config() -> dict:
    if CONFIG_FILE.exists():
        cfg = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        for k, v in DEFAULT_CONFIG.items():
            cfg.setdefault(k, v)
        return cfg
    return dict(DEFAULT_CONFIG)


def save_config(cfg: dict):
    CONFIG_FILE.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")


def get_browser_context(playwright, headless: bool = True, use_cookies: bool = False):
    browser = playwright.chromium.launch(
        headless=headless,
        args=[
            "--disable-blink-features=AutomationControlled",
            "--proxy-server=direct://",
        ],
    )
    context = browser.new_context(
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        locale="zh-CN",
        viewport={"width": 1920, "height": 1080},
    )
    if use_cookies and COOKIES_FILE.exists():
        cookies = json.loads(COOKIES_FILE.read_text(encoding="utf-8"))
        context.add_cookies(cookies)
        logger.info(f"Loaded {len(cookies)} cookies")
    return browser, context


def new_page(context, **kwargs):
    """Create a new page with stealth anti-detection applied."""
    page = context.new_page(**kwargs)
    if HAS_STEALTH:
        Stealth().apply_stealth_sync(page)
    return page


def login(profile_url: str):
    """First-time login: opens browser, waits for manual QR scan, then saves full session."""
    print("=== Douyin Login ===")
    print("A browser window will open. Please scan the QR code to log in.")
    print("The script will wait and auto-detect when login succeeds.")
    print("Press Ctrl+C in terminal if you need to cancel.")

    with sync_playwright() as p:
        browser, context = get_browser_context(p, headless=False, use_cookies=False)
        page = new_page(context)

        page.goto("https://www.douyin.com/", wait_until="domcontentloaded", timeout=60000)
        time.sleep(2)

        # Check if already logged in (has sessionid cookie)
        for attempt in range(180):  # 3 minutes
            time.sleep(1)
            cookies = context.cookies()
            has_session = any("sessionid" in c["name"] for c in cookies)
            if has_session:
                print(f"\nLogin detected! (session cookie found)")
                break
            if attempt % 30 == 0 and attempt > 0:
                print(f"  Still waiting for login... ({attempt // 30} min)")

        # Save all cookies
        cookies = context.cookies()
        COOKIES_FILE.write_text(json.dumps(cookies, ensure_ascii=False), encoding="utf-8")
        print(f"Saved {len(cookies)} cookies")

        # Extract user_id from cookies or page
        page.goto(profile_url, wait_until="domcontentloaded", timeout=60000)
        time.sleep(3)
        current_url = page.url
        user_id = _extract_user_id(current_url) or ""

        # Get nickname from page
        nickname = ""
        try:
            nickname = page.evaluate("""() => {
                const el = document.querySelector('.account-avatar-info .nickname, span[class*="nickname"], [class*="user-info"] [class*="name"]');
                return el ? el.textContent.trim() : '';
            }""")
        except Exception:
            pass

        cfg = load_config()
        cfg["profile_url"] = profile_url
        cfg["user_id"] = user_id
        cfg["nickname"] = nickname or user_id or "unknown"
        save_config(cfg)

        print(f"User: {cfg['nickname']} (ID: {user_id})")
        print("Login complete!")
        browser.close()

    return True


def fetch_video_list(context, user_id: str, max_videos: int = 10) -> List[dict]:
    """Fetch recent videos from user's profile page."""
    profile_url = f"https://www.douyin.com/user/{user_id}"
    videos = []
    api_responses = []

    def handle_response(response):
        if "aweme/v1/web/aweme/post" in response.url:
            try:
                data = response.json()
                api_responses.append(data)
                logger.info(f"Captured video list API response")
            except Exception as e:
                logger.debug(f"Failed to parse video list response: {e}")

    page = new_page(context)
    page.on("response", handle_response)

    try:
        page.goto(profile_url, wait_until="domcontentloaded", timeout=30000)
        for _ in range(10):
            if api_responses:
                time.sleep(1)
                break
            time.sleep(1)

        for i in range(3):
            try:
                page.evaluate("window.scrollBy(0, 1000)")
            except Exception:
                break
            time.sleep(1)

        for resp in api_responses:
            aweme_list = resp.get("aweme_list", [])
            for aweme in aweme_list[:max_videos]:
                video = {
                    "id": aweme.get("aweme_id", ""),
                    "desc": aweme.get("desc", ""),
                    "create_time": aweme.get("create_time", 0),
                    "statistics": {
                        "comment_count": aweme.get("statistics", {}).get("comment_count", 0),
                        "digg_count": aweme.get("statistics", {}).get("digg_count", 0),
                    },
                }
                if video["id"]:
                    videos.append(video)

        if not videos:
            time.sleep(2)
            page.goto(profile_url, wait_until="networkidle", timeout=30000)
            time.sleep(3)

    finally:
        page.close()

    return videos


def fetch_comments(context, video_id: str, max_comments: int = 50) -> List[dict]:
    """Fetch comments for a video via API."""
    comments = []
    api_responses = []

    def handle_response(response):
        if COMMENTS_API_PATTERN.search(response.url):
            try:
                data = response.json()
                api_responses.append(data)
                logger.info(f"Captured comments API for video {video_id}")
            except Exception:
                pass

    page = new_page(context)
    page.on("response", handle_response)

    try:
        video_url = f"https://www.douyin.com/video/{video_id}"
        page.goto(video_url, wait_until="domcontentloaded", timeout=30000)

        for _ in range(15):
            if api_responses:
                time.sleep(1)
                break
            time.sleep(1)

        for _ in range(3):
            try:
                page.evaluate("window.scrollBy(0, 800)")
            except Exception:
                break
            time.sleep(1)

        for resp in api_responses:
            comments_list = resp.get("comments", [])
            for c in comments_list:
                comment = {
                    "id": c.get("cid", ""),
                    "text": c.get("text", ""),
                    "user_id": c.get("user", {}).get("uid", ""),
                    "nickname": c.get("user", {}).get("nickname", ""),
                    "avatar": c.get("user", {}).get("avatar_medium", {}).get("url_list", [""])[0] if c.get("user", {}).get("avatar_medium") else "",
                    "create_time": c.get("create_time", 0),
                    "digg_count": c.get("digg_count", 0),
                    "reply_count": c.get("reply_comment", 0),
                    "video_id": video_id,
                }
                comments.append(comment)
                if len(comments) >= max_comments:
                    break
            if len(comments) >= max_comments:
                break

    finally:
        page.close()

    return comments


def _api_post(context, video_id: str, text: str, reply_id: str = "") -> dict:
    """Post via in-page fetch to inherit all auth/security headers."""
    page = new_page(context)
    result = {}

    try:
        page.goto(f"https://www.douyin.com/video/{video_id}", wait_until="domcontentloaded", timeout=30000)
        time.sleep(4)

        api_url = "https://www.douyin.com/aweme/v1/web/comment/publish"
        params = {"app_name": "aweme", "enter_from": "video_detail",
                  "previous_page": "video_detail", "device_platform": "webapp",
                  "aid": "6383", "channel": "channel_pc_web"}
        data = {"aweme_id": video_id, "text": text, "text_extra": "[]",
                "one_level_comment_rank": "-1"}
        if reply_id:
            data["reply_id"] = reply_id

        url = api_url + "?" + "&".join(f"{k}={v}" for k, v in params.items())
        js_data = json.dumps(data)

        result = page.evaluate("""(args) => {
            return fetch(args.url, {
                method: 'POST', credentials: 'include',
                headers: {'Content-Type': 'application/x-www-form-urlencoded'},
                body: new URLSearchParams(JSON.parse(args.data)).toString()
            }).then(r => r.status === 200 ? r.json().catch(() => ({})) : Promise.reject(r.status));
        }""", {"url": url, "data": js_data})

        if result:
            logger.info(f"API success: status_code={result.get('status_code')}")
    except Exception as e:
        logger.warning(f"API error: {e}")
    finally:
        page.close()

    return result if result else {}


def post_reply(context, video_id: str, comment_id: str, reply_text: str) -> bool:
    """Reply to a comment via direct API call."""
    result = _api_post(context, video_id, reply_text, reply_id=comment_id)
    return bool(result) and result.get("status_code") == 0


def post_comment(context, video_id: str, text: str) -> bool:
    """Post a top-level comment via direct API call."""
    result = _api_post(context, video_id, text)
    return bool(result) and result.get("status_code") == 0


def manual_comment(video_id: str, text: str):
    """Open browser, let user login if needed, then fill comment."""
    print("=== 手动发留言 ===")
    print("1. 浏览器将打开抖音首页")
    print("2. 如果没登录，请扫码或用手机号登录")
    print("3. 登录成功后等候几秒，会自动跳转到视频页")
    print("4. 评论会自动填入输入框，手动按 Enter 发送")
    print("5. 发送后关闭浏览器窗口")
    print()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context(
            user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            locale='zh-CN',
            viewport={'width': 1280, 'height': 900},
        )
        # Try loading existing cookies
        if COOKIES_FILE.exists():
            cookies = json.loads(COOKIES_FILE.read_text(encoding="utf-8"))
            context.add_cookies(cookies)

        page = new_page(context)

        # Navigate to douyin.com first
        page.goto("https://www.douyin.com/", wait_until="domcontentloaded", timeout=30000)
        time.sleep(3)

        # Check if already logged in
        has_session = any("sessionid" in c["name"] for c in context.cookies())
        if not has_session:
            print("未登录，请在浏览器中扫码或手机号登录...")
            # Wait up to 3 minutes for login
            for i in range(180):
                time.sleep(1)
                cookies_now = context.cookies()
                if any("sessionid" in c["name"] for c in cookies_now):
                    print("登录成功!")
                    # Save new cookies
                    COOKIES_FILE.write_text(json.dumps(cookies_now, ensure_ascii=False), encoding="utf-8")
                    break
                if i % 30 == 0 and i > 0:
                    print(f"  等待登录中... ({i//30}分钟)")
        else:
            print("已登录")

        # Navigate to video
        page.goto(f"https://www.douyin.com/video/{video_id}", wait_until="domcontentloaded", timeout=30000)
        time.sleep(4)

        # Click comment placeholder
        page.evaluate("""() => {
            const el = document.querySelector('.comment-input-inner-container');
            if (el) { el.scrollIntoView({block:'center'}); el.click(); }
        }""")
        time.sleep(2)
        page.keyboard.type(text, delay=20)

        print(f"\n评论已填入: {text}")
        print("手动按 Enter 发送，然后关闭浏览器窗口。")
        page.wait_for_event("close", timeout=0)
        print("浏览器已关闭")


def manual_reply(video_id: str, comment_id: str, text: str):
    """Open browser, fill reply, let user send it manually."""
    cfg = load_config()
    if not cfg.get("user_id"):
        print("Not configured. Run login first.")
        sys.exit(1)

    print("=== 手动回复评论 ===")
    print(f"打开浏览器: https://www.douyin.com/video/{video_id}")
    print("请在页面中找到目标评论，点击它的「回复」按钮。")
    print(f"回复内容: {text}")
    print("关闭浏览器窗口退出。")
    print()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context(
            user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            locale='zh-CN',
            viewport={'width': 1280, 'height': 900},
        )
        cookies = json.loads(COOKIES_FILE.read_text(encoding="utf-8"))
        context.add_cookies(cookies)

        page = new_page(context)
        page.goto(f"https://www.douyin.com/video/{video_id}", wait_until="domcontentloaded", timeout=30000)
        time.sleep(4)
        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        time.sleep(3)

        print("内容已准备就绪，请操作。关闭浏览器窗口退出。")
        page.wait_for_event("close", timeout=0)
        print("浏览器已关闭")


def get_new_comments(cfg: dict) -> List[dict]:
    """Check all recent videos and return new comments."""
    state = load_state()
    user_id = cfg.get("user_id")
    if not user_id:
        print("Error: user_id not configured. Run login first.")
        return []

    print(f"Checking comments for user: {cfg.get('nickname', user_id)}")
    print(f"Max videos: {cfg['max_videos_to_check']}, Max comments/video: {cfg['max_comments_per_video']}")

    with sync_playwright() as p:
        browser, context = get_browser_context(p, headless=True, use_cookies=True)

        try:
            print("\nFetching video list...")
            videos = fetch_video_list(context, user_id, cfg["max_videos_to_check"])
            print(f"Found {len(videos)} videos")

            if not videos:
                print("No videos found. Check your user_id or login status.")
                browser.close()
                return []

            for v in videos:
                vid = v["id"]
                if vid not in state["videos"]:
                    state["videos"][vid] = {
                        "desc": v["desc"][:80],
                        "create_time": v["create_time"],
                        "first_checked": datetime.now(timezone.utc).isoformat(),
                    }
                state["videos"][vid]["comment_count"] = v["statistics"]["comment_count"]

            all_new_comments = []
            for v in videos:
                vid = v["id"]
                if v["statistics"]["comment_count"] == 0:
                    continue

                print(f"\n  Video {vid[:8]}... ({v['desc'][:40]}...) - {v['statistics']['comment_count']} comments")
                comments = fetch_comments(context, vid, cfg["max_comments_per_video"])
                print(f"  Loaded {len(comments)} comments")

                for idx, c in enumerate(comments):
                    cid = c["id"]
                    if cid not in state["replied"]:
                        state["replied"][cid] = {
                            "status": "new",
                            "video_id": vid,
                            "text": c["text"],
                            "nickname": c["nickname"],
                            "create_time": c["create_time"],
                            "found_at": datetime.now(timezone.utc).isoformat(),
                            "comment_index": idx,
                        }
                        all_new_comments.append(c)
                    elif "comment_index" not in state["replied"][cid]:
                        state["replied"][cid]["comment_index"] = idx

            state["last_check"] = datetime.now(timezone.utc).isoformat()
            save_state(state)

            return all_new_comments

        finally:
            browser.close()


def cmd_check():
    """Check for new comments and display them."""
    cfg = load_config()
    if not cfg.get("user_id"):
        print("Not configured. Run with: python douyin_client.py login <profile_url>")
        sys.exit(1)

    new_comments = get_new_comments(cfg)

    if not new_comments:
        print("\nNo new comments found.")
        sys.exit(0)

    print(f"\n=== {len(new_comments)} New Comments ===")
    for i, c in enumerate(new_comments, 1):
        print(f"\n--- Comment {i} ---")
        print(f"Video:    {c['video_id'][:12]}...")
        print(f"From:     {c['nickname']}")
        print(f"Comment:  {c['text'][:200]}")
        print(f"Likes:    {c['digg_count']}")
        print(f"ID:       {c['id']}")

    state = load_state()
    pending = sum(1 for v in state["replied"].values() if v["status"] == "new")
    replied = sum(1 for v in state["replied"].values() if v["status"] == "replied")
    print(f"\nSummary: {pending} pending, {replied} replied total")


def rate_limit_sleep(cfg: dict, state: dict):
    """Sleep for a random interval to avoid triggering risk control."""
    min_s = cfg.get("reply_interval_min", 8)
    max_s = cfg.get("reply_interval_max", 18)
    last_reply = state.get("last_reply_time")

    if last_reply:
        elapsed = time.time() - last_reply
        if elapsed < min_s:
            wait = min_s - elapsed
            logger.info(f"Rate limit: last reply {elapsed:.0f}s ago, waiting {wait:.0f}s more")
            time.sleep(wait)

    delay = random.uniform(min_s, max_s)
    logger.info(f"Rate limit: sleeping {delay:.0f}s")
    time.sleep(delay)

    state["last_reply_time"] = time.time()
    save_state(state)


def cmd_reply(video_id: str, comment_id: str, text: str):
    """Post a reply via API."""
    cfg = load_config()
    if not cfg.get("user_id"):
        print("Not configured. Run login first.")
        sys.exit(1)

    print(f"Posting reply to comment {comment_id[:12]}... on video {video_id[:12]}...")

    with sync_playwright() as p:
        browser, context = get_browser_context(p, headless=True, use_cookies=True)
        try:
            success = post_reply(context, video_id, comment_id, text)
            if success:
                print("Reply posted successfully!")
                state = load_state()
                if comment_id in state["replied"]:
                    state["replied"][comment_id]["status"] = "replied"
                    state["replied"][comment_id]["reply_text"] = text
                    state["replied"][comment_id]["replied_at"] = datetime.now(timezone.utc).isoformat()
                    save_state(state)
                rate_limit_sleep(cfg, state)
            else:
                print("Failed to post reply.")
                sys.exit(1)
        finally:
            browser.close()


def cmd_comment(video_id: str, text: str):
    """Post a top-level comment via API."""
    cfg = load_config()
    if not cfg.get("user_id"):
        print("Not configured. Run login first.")
        sys.exit(1)

    print(f"Posting comment on video {video_id[:12]}...")

    with sync_playwright() as p:
        browser, context = get_browser_context(p, headless=True, use_cookies=True)
        try:
            success = post_comment(context, video_id, text)
            if success:
                print("Comment posted successfully!")
                state = load_state()
                rate_limit_sleep(cfg, state)
            else:
                print("Failed to post comment.")
                sys.exit(1)
        finally:
            browser.close()


def cmd_batch_reply(video_id: str, comment_ids: List[str], texts: List[str]):
    """Reply to multiple comments via API."""
    cfg = load_config()

    for cid, text in zip(comment_ids, texts):
        print(f"\nReplying to comment {cid[:12]}...")

        with sync_playwright() as p:
            browser, context = get_browser_context(p, headless=True, use_cookies=True)
            try:
                success = post_reply(context, video_id, cid, text)
                if success:
                    print("Reply posted!")
                    state = load_state()
                    if cid in state["replied"]:
                        state["replied"][cid]["status"] = "replied"
                        state["replied"][cid]["reply_text"] = text
                        state["replied"][cid]["replied_at"] = datetime.now(timezone.utc).isoformat()
                        save_state(state)
                    rate_limit_sleep(cfg, state)
                else:
                    print("Failed to reply!")
            finally:
                browser.close()


def cmd_manual_comment(video_id: str, text: str):
    """Post a comment manually via visible browser."""
    manual_comment(video_id, text)


def cmd_manual_reply(video_id: str, comment_id: str, text: str):
    """Reply to a comment manually via visible browser."""
    manual_reply(video_id, comment_id, text)


def cmd_status():
    """Show monitoring status."""
    cfg = load_config()
    state = load_state()

    print("=== Douyin Auto-Reply Status ===")
    print(f"Configured user: {cfg.get('nickname', '(not set)')}")
    print(f"Auto-reply: {'ON' if cfg['auto_reply_enabled'] else 'OFF'}")
    print(f"Reply style: {cfg['reply_style']}")
    print(f"Reply interval: {cfg.get('reply_interval_min', 8)}-{cfg.get('reply_interval_max', 18)}s (randomized)")
    print()

    if state["replied"]:
        total = len(state["replied"])
        pending = sum(1 for v in state["replied"].values() if v["status"] == "new")
        replied = sum(1 for v in state["replied"].values() if v["status"] == "replied")
        print(f"Comments tracked: {total}")
        print(f"  Pending:  {pending}")
        print(f"  Replied:  {replied}")

    print(f"\nVideos tracked: {len(state['videos'])}")
    print(f"Last check: {state.get('last_check', 'never')}")


def _extract_user_id(url: str) -> Optional[str]:
    """Extract user ID from Douyin URL."""
    match = re.search(r"user/([^/?\s]+)", url)
    if match:
        return match.group(1)
    return None


def main():
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    elif hasattr(sys.stdout, 'buffer'):
        sys.stdout = open(sys.stdout.fileno(), mode='w', encoding='utf-8', buffering=1, errors='replace')

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )

    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    cmd = sys.argv[1]

    if cmd == "login":
        if len(sys.argv) < 3:
            print("Usage: python douyin_client.py login <profile_url>")
            print("Example: python douyin_client.py login https://www.douyin.com/user/MS4wLjABAAA...")
            sys.exit(1)
        login(sys.argv[2])

    elif cmd == "check":
        cmd_check()

    elif cmd == "reply":
        if len(sys.argv) < 5:
            print("Usage: python douyin_client.py reply <video_id> <comment_id> <reply_text>")
            sys.exit(1)
        cmd_reply(sys.argv[2], sys.argv[3], sys.argv[4])

    elif cmd == "comment":
        if len(sys.argv) < 4:
            print("Usage: python douyin_client.py comment <video_id> <comment_text>")
            sys.exit(1)
        cmd_comment(sys.argv[2], sys.argv[3])

    elif cmd == "manual-comment":
        if len(sys.argv) < 4:
            print("Usage: python douyin_client.py manual-comment <video_id> <comment_text>")
            sys.exit(1)
        cmd_manual_comment(sys.argv[2], sys.argv[3])

    elif cmd == "manual-reply":
        if len(sys.argv) < 5:
            print("Usage: python douyin_client.py manual-reply <video_id> <comment_id> <reply_text>")
            sys.exit(1)
        cmd_manual_reply(sys.argv[2], sys.argv[3], sys.argv[4])

    elif cmd == "batch-reply":
        if len(sys.argv) < 4:
            print("Usage: python douyin_client.py batch-reply <video_id> <json_string>")
            print("Example: python douyin_client.py batch-reply 7650... '{\"cid1\":\"reply1\",\"cid2\":\"reply2\"}'")
            sys.exit(1)
        data = json.loads(sys.argv[3])
        cids, texts = list(data.keys()), list(data.values())
        cmd_batch_reply(sys.argv[2], cids, texts)

    elif cmd == "status":
        cmd_status()

    elif cmd == "config":
        cfg = load_config()
        if len(sys.argv) >= 4:
            key = sys.argv[2]
            value = sys.argv[3]
            if key in cfg:
                if isinstance(cfg[key], bool):
                    cfg[key] = value.lower() in ("true", "1", "yes")
                elif isinstance(cfg[key], int):
                    cfg[key] = int(value)
                else:
                    cfg[key] = value
                save_config(cfg)
                print(f"Config updated: {key} = {cfg[key]}")
            else:
                print(f"Unknown config key: {key}")
                print(f"Available keys: {', '.join(cfg.keys())}")
        else:
            for k, v in cfg.items():
                print(f"{k}: {v}")
    else:
        print(f"Unknown command: {cmd}")
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()
