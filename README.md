# 抖音视频下载（轻量版）

抖音**无水印**单条 / 主页批量下载，无需登录。浏览器负责风控签名，脚本只拦截接口拿直链，CDN 直连下载。

- 实测日期：2026-10-27（验证通过）
- 依赖：Python 3.9+、`playwright` + `requests`、本机 Chrome（复用系统安装，无需下载 chromium）
- 轻量：单文件脚本约 14 KB；运行依赖约 140 MB（Playwright 本体），不常驻、无 Docker

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

> 不要运行 `playwright install chromium`，脚本走 `channel="chrome"` 复用本机 Chrome。

完整原理、已证伪路线、排错表见 [SKILL.md](./SKILL.md)。

## 说明

仅用于个人学习与技术研究，请勿用于侵权或违法用途。