# 技能：抖音无水印视频下载（浏览器签名 + 直链下载）

> 实测环境：Windows 10/11 + 本机 Chrome 155 + Python 3.13 + Playwright，2026-10 验证通过。
> 目标读者：任何要复刻该能力的 AI Agent 或开发者。本文自包含，代码可直接落盘运行。

---

## 1. 能力范围

| 支持 | 说明 |
|---|---|
| 分享短链 `v.douyin.com/xxx` | 自动跟跳转解析 |
| 分享文案整段粘贴 | 自动抠出其中的 URL |
| `douyin.com/video/{id}`、`/note/{id}` | 单条视频 / 图文 |
| `douyin.com/user/{sec_uid}` | 主页批量，未登录上限约 19 条 |
| 无水印 MP4 / 图文 JPG | 取 `bit_rate` 最高分辨率档 |
| 断点续传式下载 | 先写 `.part`，校验长度后改名；已存在则跳过 |
| 4 线程并行下载 | 主页批量时生效 |

不支持：私密/已删除/地区限制作品、直播回放、付费内容、评论区图片、表情包。

---

## 2. 先看结论：这些路已经走不通（复刻者不要重复踩）

以下全部在**同一条视频**上实测（id `7698635183570153209`）：

| 尝试 | 结果 |
|---|---|
| 纯 Python 自算 `a_bogus`（f2/JoeanAmier 算法）请求 detail | `403 Blocked by ArgusSecurityPlugin Uifid Not Found` |
| 从分享页白嫖 `ttwid` + 自算 `a_bogus` | 同上 403 |
| 真浏览器导出全套 cookie（含 384 字符 UIFID、真 msToken）+ 自算 `a_bogus` | 同上 403 |
| 浏览器真实请求 URL + 全部请求头，requests 逐字节重放 | `200`，但 **body 长度 0** |
| 自算 `a_bogus`，去掉 `x-secsdk-web-signature` | `403 Signature Not Found` |
| 自算 `a_bogus` + 浏览器抓来的 `x-secsdk-web-signature` | `403 Sign Invalid`（两者必须同源同期生成） |
| 解析 `iesdouyin.com/share/video/{id}/` 的 `window._ROUTER_DATA` | `item_list` 为空数组 |
| 无头 Chromium 直接跑（默认 UA 含 `HeadlessChrome`） | 接口 `200` 但 body 0 字节，静默空包 |

**结论**：抖音风控三层——① X-Bogus（旧）② `a_bogus`（算法已破）③ Argus（需要真实浏览器环境养出的 `uifid` + 真 `msToken`）。第三层不是算法问题，而是环境+指纹问题，纯 HTTP 客户端（含 requests/curl/httpx）在 TLS/HTTP2 指纹上无法伪装成 Chrome。

**所以本技能的做法**：让浏览器自己发请求、自己算签名，脚本只做两件事——拦截接口 JSON 拿直链、用 requests 下载。CDN（`douyinvod.com`）不校验签名，只需 `Referer: https://www.douyin.com/` 和桌面 UA。

---

## 3. 架构

```
用户链接
  │
  ├─ 短链/分享文案 ──requests(Mobile UA) 跟跳转──> video_id 或 sec_uid
  │
  ├─ Playwright 启动本机 Chrome（持久化 profile，headless，UA 覆写）
  │     ├─ 打开 /video/{id}   → 拦截 /aweme/v1/web/aweme/detail/ → aweme_detail
  │     └─ 打开 /user/{sec_uid} → 拦截 /aweme/v1/web/aweme/post/ → aweme_list（滚动加载）
  │
  ├─ pick_play_url()：从 video.bit_rate + play_addr* 里按「像素数」选最高档
  │
  └─ requests 直连 CDN → .part → 校验 Content-Length → 改名 .mp4
```

---

## 4. 环境准备

```powershell
mkdir douyin_dl; cd douyin_dl
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -U pip playwright requests
# 不需要 playwright install chromium —— 用 channel="chrome" 复用本机 Chrome
```

要求：本机已安装 Google Chrome。若目标机器没有 Chrome：
- 方案 A：`playwright install chromium`，然后把 `channel="chrome"` 删掉；**但必须**把 UA 覆写成对应 Chromium 版本、去掉 `HeadlessChrome`。
- 方案 B：改用 `channel="msedge"`（Edge 同样基于 Chromium）。

Linux/macOS 差异：`chrome_ua()` 里的 `winreg` 读版本会失败，直接回落到固定版本号即可（如 `Chrome/131.0.0.0`），不影响功能。

---

## 5. 完整代码（`douyin_dl.py`）

```python
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
```

---

## 6. 关键实现点（改代码前必读）

1. **UA 必须覆写**
   `launch_persistent_context(..., user_agent=DESKTOP_UA)`。不覆写时无头 Chrome 的 UA 是 `HeadlessChrome/155`，抖音**不报错**，直接回 `200` + 空 body，极难排查。

2. **`channel="chrome"` + 持久化 profile**
   复用本机 Chrome 与真实 TLS/HTTP2 指纹；profile 目录（默认 `.profile`）会累积 `ttwid`/`uifid`/`msToken`，越用越稳。第一次运行可能较慢，属正常。

3. **只拦截，不自己发请求**
   `page.on("response", handler)` 里判断 URL 含 `DETAIL_API` / `POST_API`，`resp.json()` 取数据。不要在 handler 里调 `resp.body()`，context 关闭时会有竞态（`TargetClosedError`）。

4. **区分「没发请求」和「发了被风控」**
   计数器 `called["n"]`：为 0 时说明页面根本没请求接口（作品删除/私密/登录弹窗），非 0 才可能是风控。这个区分能省掉大量误判。

5. **主页滚动**
   抖音主页 `window` 不滚动（`body` 是 `overflow: hidden`），必须找内部最大的可滚 `div` 设 `scrollTop`。用 `mouse.wheel` 或 `window.scrollTo` 都无效。

6. **清晰度选择**
   候选来源：`video.bit_rate[*].play_addr` + `video.play_addr` + `play_addr_h264` + `play_addr_265`。排序 key = `(像素数, 是否H.264, data_size)` 取最大。**不要只取 `video.play_addr`**，那只是默认档，会漏掉 1440p/4K。

7. **不要用 `download_addr`**
   它带水印且是方形 720×720，体积反而更大（124 秒视频 38MB），画质更差。

8. **直链有效期**
   `play_addr.url_list` 里的 CDN 链接带时效（约几分钟到几小时），拿到后尽快下载；不要缓存复用。

9. **下载校验**
   检查 `Content-Type` 含 `video/image/octet-stream`、`Content-Length` 与实际字节数一致，避免把错误页存成 mp4。写 `.part` 再 `os.replace`，中断不留半成品。

---

## 7. 使用

```powershell
cd douyin_dl

# 单条
.\.venv\Scripts\python.exe douyin_dl.py "https://www.douyin.com/video/7698635183570153209"

# 分享文案直接粘贴
.\.venv\Scripts\python.exe douyin_dl.py "7.94 复制打开抖音，看看【xxx】... https://v.douyin.com/xxxx/"

# 主页批量（未登录上限约 19 条）
.\.venv\Scripts\python.exe douyin_dl.py "https://www.douyin.com/user/MS4wLjABAAAA..." --limit 19 --out .\downloads

# 强制 H.264（剪映/老播放器兼容）
.\.venv\Scripts\python.exe douyin_dl.py "链接" --codec h264

# 被风控时：显示浏览器窗口，手动过验证
.\.venv\Scripts\python.exe douyin_dl.py "链接" --headful

# 解锁主页翻页：扫码登录一次（可选，用户不接受可跳过）
.\.venv\Scripts\python.exe douyin_dl.py --login
```

---

## 8. 验证方法

```powershell
ffprobe -v error -show_entries format=duration,size ^
        -show_entries stream=codec_name,width,height -of default=nw=1 "文件名.mp4"
```

期望输出示例（实测）：

```
codec_name=aac
codec_name=h264
width=1080
height=1920
duration=124.733333
size=31782874
```

单条 12 秒视频约 2MB 属正常（见第 9 节码率天花板）。

---

## 9. 已知边界（实测数据，别当成 bug）

- **网页端码率天花板 ≈ 2 Mbps**：16 秒 4K（3840×2160 H.265）也只有 4.07MB；124.7 秒 1080p 是 31.78MB。文件大小主要由**时长**决定，不是清晰度丢失。
- **4K 档存在但少见**：`bit_rate` 里可能出现 `adapt_lowest_4_1`（3840×2160 H.265），必须按像素数选才能拿到。
- **未登录主页只有第一页**：页面底部显示「登录后查看更多作品」，约 19 条；滚动不会触发第二页。`--login` 后可翻页。
- **H.265 兼容性**：Windows 自带播放器无 HEVC 扩展时黑屏有声，剪映等软件通常正常；保守用 `--codec h264`。
- **同一作品偶发 `200` 空包**：页面自身有重试，脚本 30s 内等不到就报错，重跑通常恢复。
- **频率**：短时间大量请求会触发风控，建议单次 ≤ 20 条、任务之间留间隔。

---

## 10. 排错表

| 现象 | 原因 | 处理 |
|---|---|---|
| `403 Blocked by ArgusSecurityPlugin Uifid Not Found` | 用纯 HTTP 直接调接口 | 不要自己发请求，改走浏览器拦截 |
| 接口 `200` 但 body 为 0 | UA 含 `HeadlessChrome` / 被风控 | 确认覆写 UA；换 `--headful` |
| 页面没有发起 detail 请求 | 作品已删除/私密/地区限制 | 换链接验证，不是脚本问题 |
| `Sign Invalid` / `Signature Not Found` | `a_bogus` 与 `x-secsdk-web-signature` 不同源 | 放弃自算签名路线 |
| 主页只拿到 19 条 | 登录墙 | `--login` 扫码，或接受第一页 |
| 下载得到几 KB 的文件 | 直链过期或返回错误页 | 重新抓 detail，检查 `Content-Type` |
| 下到的视频很糊 | 只取了 `play_addr` 默认档 | 用本技能的 `pick_play_url` 按像素选最高档 |
| 浏览器启动报找不到 Chrome | 未安装 Chrome | `playwright install chromium` 并去掉 `channel` |

---

## 11. 体积说明（轻量程度）

| 目录/文件 | 大小 | 是否必需 |
|---|---|---|
| `douyin_dl.py` | ~15 KB | 必需，全部逻辑 |
| `.venv` | ~140 MB | 运行必需（`playwright` 包自带 driver 约 108MB）；可随时重建 |
| `.profile` | 清理缓存后 ~15 MB | 可删，首次运行自动重建；保留可累积 cookie 更稳 |
| `downloads/` | 视视频而定 | 产物 |

清理 profile 缓存（保留登录态/Cookies）：

```powershell
Remove-Item -Recurse -Force ".\.profile\Default\Cache", ".\.profile\Default\Code Cache", `
  ".\.profile\Default\GPUCache", ".\.profile\GrShaderCache", ".\.profile\ShaderCache"
```

与替代方案对比：Docker 全家桶（Douyin_TikTok_Download_API）镜像数百 MB 且需常驻；本方案是单文件脚本 + 140MB 依赖，不常驻、不联网服务化，属于**轻量**。但**不是纯 HTTP 轻量包**——必须有一个真实 Chrome 进程参与签名，这是抖音第三层风控决定的，绕不过去。
