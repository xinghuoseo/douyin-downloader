"""抖音单条 / 主页批量下载（无需登录、无水印）。

原理：用本机 Chrome 打开页面，浏览器自己会完成 a_bogus、x-secsdk-web-signature、
uifid、msToken 等整套风控签名（纯 Python 复刻已经被 Argus 层挡住，实测 403），
脚本只负责拦截浏览器发出的接口响应，取 play_addr 直链再下载。CDN 不限速、不需要签名。

用法：
    python douyin_dl.py "7.94 复制打开抖音... https://v.douyin.com/xxxxx/"
    python douyin_dl.py https://www.douyin.com/video/7389015133525118249
    python douyin_dl.py https://www.douyin.com/user/MS4wLjABAAAAxxxx --limit 20
    python douyin_dl.py --login          # 扫码登录一次，解锁主页翻页（未登录只有第一页）
    python douyin_dl.py 链接1 链接2 --out D:\videos --headful
    python douyin_dl.py 链接 --codec h264   # 强制 H.264（老播放器/剪映兼容）
"""

from __future__ import annotations

import argparse
import os
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import requests
from playwright.sync_api import TimeoutError as PWTimeout
from playwright.sync_api import sync_playwright

BASE_DIR = Path(__file__).resolve().parent
PROFILE_DIR = BASE_DIR / ".profile"


def chrome_ua() -> str:
    """按本机 Chrome 版本拼 UA。

    无头模式默认 UA 带 "HeadlessChrome"，抖音会静默返回空包（接口 200 但 body 长度 0），
    必须覆写成普通 Chrome UA，否则 detail / aweme/post 全拿不到数据。
    """
    ver = ""
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Google\Chrome\BLBeacon") as k:
            ver = str(winreg.QueryValueEx(k, "version")[0])
    except OSError:
        ver = ""
    return (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        f"(KHTML, like Gecko) Chrome/{ver or '131.0.0.0'} Safari/537.36"
    )


DESKTOP_UA = chrome_ua()
MOBILE_UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"
)
URL_RE = re.compile(r"https?://[^\s，,。\u4e00-\u9fff]+")
VID_RE = re.compile(r"/(?:video|note)/(\d{8,})")
USER_RE = re.compile(r"/user/([A-Za-z0-9_\-]{10,})")

DETAIL_API = "/aweme/v1/web/aweme/detail/"
POST_API = "/aweme/v1/web/aweme/post/"


def extract_link(text: str) -> str:
    """从分享文案里抠出第一条链接（分享文案前后都是中文）。"""
    m = URL_RE.search(text)
    return m.group(0) if m else text.strip()


def resolve_target(session: requests.Session, raw: str) -> tuple[str, str]:
    """返回 ("video", aweme_id) 或 ("user", sec_uid)。短链先跟跳转。"""
    url = extract_link(raw)
    m = USER_RE.search(url)
    if m:
        return "user", m.group(1)
    m = VID_RE.search(url)
    if m:
        return "video", m.group(1)
    resp = session.get(url, headers={"User-Agent": MOBILE_UA}, timeout=20, allow_redirects=True)
    final = resp.url + " " + resp.text
    m = USER_RE.search(final)
    if m:
        return "user", m.group(1)
    m = VID_RE.search(final)
    if m:
        return "video", m.group(1)
    raise RuntimeError(f"无法识别链接类型：{raw}（最终地址 {resp.url}）")


def pick_play_url(video: dict, codec: str = "auto") -> str:
    """按分辨率选最高档；同分辨率优先 H.264（兼容性最好），再比文件大小。

    codec: auto = 最高分辨率（可能是 H.265）；h264 = 只要 H.264 档位。
    """
    cands = []

    def add(addr: dict | None, h265: bool) -> None:
        urls = [u for u in ((addr or {}).get("url_list") or []) if u.startswith("http")]
        if not urls:
            return
        if codec == "h264" and h265:
            return
        pixels = ((addr or {}).get("width") or 0) * ((addr or {}).get("height") or 0)
        cands.append((pixels, 0 if h265 else 1, (addr or {}).get("data_size") or 0, urls[0]))

    for key, h265 in (("play_addr", False), ("play_addr_h264", False), ("play_addr_265", True)):
        add(video.get(key), h265)
    for br in video.get("bit_rate") or []:
        add(br.get("play_addr"), bool(br.get("is_h265")))
    if not cands and codec == "h264":
        return pick_play_url(video, "auto")
    if not cands:
        raise RuntimeError("响应里没有可用的视频直链")
    return max(cands)[3]


def safe_name(text: str, limit: int = 60) -> str:
    text = re.sub(r"[\\/:*?\"<>|\r\n\t#]", " ", text or "douyin")
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit] or "douyin"


_local = threading.local()


def http() -> requests.Session:
    """每个线程一个 Session（下载是多线程的）。"""
    s = getattr(_local, "s", None)
    if s is None:
        s = requests.Session()
        s.headers.update({"User-Agent": DESKTOP_UA})
        _local.s = s
    return s


def download(url: str, dest: Path) -> bool:
    if dest.exists() and dest.stat().st_size > 0:
        print(f"  [跳过] {dest.name} 已存在")
        return True
    headers = {"User-Agent": DESKTOP_UA, "Referer": "https://www.douyin.com/"}
    session = http()
    with session.get(url, headers=headers, timeout=60, stream=True) as r:
        r.raise_for_status()
        ctype = r.headers.get("Content-Type", "")
        if not any(k in ctype for k in ("video", "image", "octet-stream")):
            raise RuntimeError(f"返回的不是媒体文件（Content-Type={ctype}）")
        total = int(r.headers.get("Content-Length") or 0)
        tmp = dest.with_suffix(dest.suffix + ".part")
        got = 0
        with open(tmp, "wb") as f:
            for chunk in r.iter_content(chunk_size=1 << 16):
                if chunk:
                    f.write(chunk)
                    got += len(chunk)
        if got == 0 or (total and got != total):
            tmp.unlink(missing_ok=True)
            raise RuntimeError(f"下载不完整：{got}/{total}")
        os.replace(tmp, dest)
    print(f"  [完成] {dest.name}  {got / 1024 / 1024:.2f} MB")
    return True


def save_item(item: dict, out_dir: Path, codec: str = "auto") -> None:
    aweme_id = item.get("aweme_id") or ""
    name = f"{safe_name(item.get('desc') or aweme_id)}_{aweme_id}"
    if item.get("images"):
        for i, img in enumerate(item["images"], 1):
            url = (img.get("url_list") or [None])[-1]
            if url:
                download(url, out_dir / f"{name}_{i:02d}.jpg")
        return
    download(pick_play_url(item.get("video") or {}, codec), out_dir / f"{name}.mp4")


def fetch_detail(page, aweme_id: str, timeout: float = 30.0) -> dict:
    """打开视频页并拦截 detail 接口响应。"""
    captured: dict = {}
    called = {"n": 0}

    def on_response(resp):
        if DETAIL_API not in resp.url or captured:
            return
        called["n"] += 1
        try:
            body = resp.json()
        except Exception:
            return
        detail = body.get("aweme_detail")
        if detail:
            captured.update(detail)

    page.on("response", on_response)
    page.goto(f"https://www.douyin.com/video/{aweme_id}", wait_until="domcontentloaded", timeout=60000)

    deadline = time.time() + timeout
    while time.time() < deadline and not captured:
        page.wait_for_timeout(500)
    page.remove_listener("response", on_response)
    if not captured:
        if called["n"] == 0:
            raise RuntimeError("页面没有发起 detail 请求：视频可能已删除/私密/地区限制")
        raise RuntimeError("detail 接口被风控拦截（试试 --headful 手动过验证）")
    return captured


SCROLL_JS = """() => {
  // 页面用固定视口 + 内部滚动容器，window 本身不滚动，必须滚最大的可滚容器
  let best = null;
  document.querySelectorAll('div').forEach(e => {
    const r = e.getBoundingClientRect();
    if (e.scrollHeight > e.clientHeight + 200 && e.clientHeight > 400 && r.width > 600) {
      if (!best || e.clientHeight > best.clientHeight) best = e;
    }
  });
  if (!best) { window.scrollTo(0, document.body.scrollHeight); return false; }
  best.scrollTop = best.scrollHeight;
  return true;
}"""

LOGIN_WALL_JS = "() => document.body.innerText.includes('登录后查看更多作品')"


def fetch_user_items(page, sec_uid: str, limit: int, timeout: float = 90.0) -> list[dict]:
    """打开主页并拦截作品列表接口，边滚边收，直到够数或没有更多。"""
    items: dict[str, dict] = {}
    state = {"more": True, "hits": 0}

    def on_response(resp):
        if POST_API not in resp.url:
            return
        state["hits"] += 1
        try:
            body = resp.json()
        except Exception:
            return
        for it in body.get("aweme_list") or []:
            aid = it.get("aweme_id")
            if aid:
                items.setdefault(aid, it)
        state["more"] = bool(body.get("has_more", 0))

    page.on("response", on_response)
    page.goto(f"https://www.douyin.com/user/{sec_uid}", wait_until="domcontentloaded", timeout=60000)

    deadline = time.time() + timeout
    last = -1
    walled = False
    while time.time() < deadline and len(items) < limit and (state["more"] or last < 0):
        page.evaluate(SCROLL_JS)
        page.wait_for_timeout(1500)
        if len(items) == last:
            if not walled and page.evaluate(LOGIN_WALL_JS):
                walled = True
                break
            page.wait_for_timeout(1200)
        last = len(items)

    page.remove_listener("response", on_response)
    if not items:
        if state["hits"] == 0:
            raise RuntimeError("主页没有发起作品列表请求：账号可能注销/私密，或打开了登录弹窗")
        raise RuntimeError("作品列表被风控拦截（试试 --headful 手动过验证）")
    if walled and len(items) < limit:
        print(f"  [提示] 未登录时抖音只给第一页（{len(items)} 条），要翻更多先跑 --login 扫码登录")
    print(f"  [列表] 共捕获 {len(items)} 条作品")
    return list(items.values())[:limit]


def launch(p, headless: bool):
    return p.chromium.launch_persistent_context(
        str(PROFILE_DIR),
        channel="chrome",
        headless=headless,
        user_agent=DESKTOP_UA,  # 去掉 HeadlessChrome，否则接口全返回空包
        viewport={"width": 1280, "height": 900},
        locale="zh-CN",
        args=["--disable-blink-features=AutomationControlled", "--mute-audio"],
    )


def do_login(wait: float = 180.0) -> None:
    """开一个可见浏览器扫码登录，会话存进 .profile，之后翻页不再被登录墙挡住。"""
    with sync_playwright() as p:
        ctx = launch(p, headless=False)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto("https://www.douyin.com/", wait_until="domcontentloaded", timeout=60000)
        print("请在弹出的 Chrome 窗口里扫码登录（最多等 %d 秒）…" % wait)
        deadline = time.time() + wait
        while time.time() < deadline:
            names = {c["name"] for c in ctx.cookies() if c.get("value")}
            if {"sessionid", "sid_tt"} & names:
                print("登录成功，会话已保存到 .profile")
                ctx.close()
                return
            page.wait_for_timeout(2000)
        print("超时未检测到登录，会话未保存")
        ctx.close()


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="抖音视频下载（浏览器签名 + 直链下载）")
    ap.add_argument("links", nargs="*", help="分享链接 / video 链接 / 主页链接 / 分享文案")
    ap.add_argument("--out", default=str(BASE_DIR / "downloads"), help="保存目录")
    ap.add_argument("--limit", type=int, default=20, help="主页模式最多下多少条，默认 20")
    ap.add_argument("--headful", action="store_true", help="显示浏览器窗口（验证风控时用）")
    ap.add_argument("--login", action="store_true", help="扫码登录一次，解锁主页翻页")
    ap.add_argument("--codec", choices=("auto", "h264"), default="auto",
                    help="auto=最高分辨率（可能是 H.265），h264=只取 H.264 档")
    args = ap.parse_args(argv)

    if args.login:
        do_login()
        return 0
    if not args.links:
        ap.error("需要至少一个链接（或用 --login 先登录）")

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    session = requests.Session()
    session.headers.update({"User-Agent": DESKTOP_UA})

    targets: list[tuple[str, str]] = []
    for raw in args.links:
        try:
            targets.append(resolve_target(session, raw))
        except Exception as e:
            print(f"[跳过] {raw}\n  {e}")
    if not targets:
        return 1

    print("任务：" + "，".join(f"{k}:{v}" for k, v in targets))

    ok = fail = 0
    with sync_playwright() as p:
        ctx = launch(p, headless=not args.headful)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        try:
            for kind, value in dict.fromkeys(targets):
                print(f"[处理] {kind} {value}")
                try:
                    batch = [fetch_detail(page, value)] if kind == "video" else fetch_user_items(
                        page, value, args.limit
                    )
                except (RuntimeError, PWTimeout) as e:
                    print(f"  [失败] {e}")
                    fail += 1
                    continue
                with ThreadPoolExecutor(max_workers=4) as ex:
                    futs = [ex.submit(save_item, item, out_dir, args.codec) for item in batch]
                    for f in as_completed(futs):
                        try:
                            f.result()
                            ok += 1
                        except Exception as e:
                            fail += 1
                            print(f"  [失败] {e}")
        finally:
            ctx.close()

    print(f"完成：成功 {ok}，失败 {fail}，目录 {out_dir}")
    return 0 if fail == 0 else 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
