# 抖音视频下载（轻量版）

抖音**无水印**单条 / 主页批量下载，无需登录。浏览器负责风控签名，脚本只拦截接口拿直链，CDN 直连下载。

- 实测：2026-11-03 在 Linux 服务器（无 root、playwright 自带 Chromium）连续下载成功；Windows 本机 Chrome 同样可用
- 依赖：Python 3.9+、`playwright` + `requests`
  - Windows：本机装了 Chrome 即可（脚本走 `channel="chrome"` 复用，无需下载 chromium）
  - Linux / 无 Chrome 环境：`playwright install chromium`，脚本自动改用自带 Chromium

## 快速开始

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -U pip playwright requests

# 单条视频
.\.venv\Scripts\python.exe douyin_dl.py "https://www.douyin.com/video/7698635183570153209"

# 分享文案直接粘贴
.\.venv\Scripts\python.exe douyin_dl.py "7.94 复制打开抖音，看看... https://v.douyin.com/xxxx/"

# 主页批量（未登录上限约 19 条）
.\.venv\Scripts\python.exe douyin_dl.py "https://www.douyin.com/user/MS4wLjABAAAA..." --limit 19
```

## 下载失败的唯一常见原因：UA 与浏览器版本不一致（重要）

抖音会校验 **UA 声明的 Chrome 版本** 和 **实际 TLS/HTTP2 指纹（浏览器真实版本）** 是否一致，不一致直接返回
`aweme_detail: null, filter_reason: "core_dep"`（接口 200 但无数据）——这不是风控封禁，不是 IP 问题，也不用养环境。

**怎样能过：UA 里的版本号必须和实际用的浏览器一致。**

- Windows：脚本从注册表读本机 Chrome 版本拼 UA，天然一致，不用管
- Linux / 自带 Chromium：脚本 UA 硬编码为 `Chrome/156.0.0.0`，对应 playwright chromium 1248（Chrome for Testing 156）。**如果 `playwright install` 升级了浏览器，必须同步改 `douyin_dl.py` 里的 UA 版本号**（查版本：`chrome --version`）
- 报错里出现 `core_dep` → 99% 是 UA 版本不匹配，先对版本号再排查别的

不需要养环境、不需要登录、不需要换 IP。首次运行即成功是常态。

## 排错速查

| 现象 | 原因与处理 |
|---|---|
| `detail 接口被风控拦截` / `core_dep` | UA 版本 ≠ 浏览器实际版本，改 UA（见上节） |
| 接口 200 但 body 空 | UA 含 `HeadlessChrome`（脚本已覆写，别删 UA 覆写逻辑） |
| `403 Uifid Not Found` | 走了纯 HTTP 没过浏览器签名——必须用本脚本（浏览器拦截模式） |
| 主页只有 19 条 | 未登录上限，`--login` 扫码后可翻页 |
| 某条视频怎么都下不了 | 该视频本身受限（私密/仅特定人群），换条视频验证脚本 |

完整原理与已证伪路线见 [SKILL.md](./SKILL.md)。

## 说明

仅用于个人学习与技术研究，请勿用于侵权或违法用途。
