---
name: douyin-download
version: "2.0.0"
description: 抖音无水印视频下载, 浏览器拦截签名取直链, 已适配无root Linux。
---

# 技能：抖音无水印视频下载（浏览器签名 + 直链下载）

> 来源: 用户在 Windows 实测跑通后整理 (2026-10), 已适配 Linux 服务器。
> 原理: 用 Playwright 驱动 Chromium 打开页面, 浏览器自己完成 a_bogus/uifid/msToken 全套风控签名,
> 脚本只拦截接口响应取 play_addr 直链再下载。纯 Python 复刻签名已被 Argus 层挡死(403 Uifid Not Found), 别再试。

## 用法

```bash
python3 scripts/douyin_dl.py "7.94 复制打开抖音... https://v.douyin.com/xxxxx/"   # 分享文案整段粘贴也行
python3 scripts/douyin_dl.py "https://www.douyin.com/video/{id}"                  # 单条
python3 scripts/douyin_dl.py "https://www.douyin.com/user/{sec_uid}" --limit 20   # 主页批量(未登录约19条)
python3 scripts/douyin_dl.py --login                                              # 扫码登录解锁主页翻页(需headful)
python3 scripts/douyin_dl.py 链接 --codec h264                                    # 强制H.264(剪映兼容)
python3 scripts/douyin_dl.py 链接 --headful                                       # 被风控时显示浏览器手动过验证
```

默认保存目录: `/workspace/视频下载/抖音/`（固定, 不随工作目录变）。可用 `--out` 覆盖。

## 能力

- 分享短链/分享文案/单条/图文/主页批量(未登录上限约19条)
- 无水印 MP4, 按 bit_rate 像素数选最高档(可能拿到4K H.265); `--codec h264` 兼容模式
- .part 断点校验下载, 已存在跳过, 4线程并行(主页批量)

## 环境要求

- python3 + `pip install playwright requests`
- Chromium: `python3 -m playwright install chromium`(服务器无Chrome; 国内用 PLAYWRIGHT_DOWNLOAD_HOST=https://npmmirror.com/mirrors/playwright/)
- Windows 本机有 Chrome 时脚本自动走 channel="chrome"(TLS指纹更真); Linux 走自带 chromium
- UA 已强制覆写去掉 HeadlessChrome —— 这是核心, 不覆写接口返回200但空body

## 已验证走不通的路(别重复踩)

纯Python自算 a_bogus / ttwid+a_bogus / 全套真cookie重放 / iesdouyin分享页_ROUTER_DATA → 全部被 Argus 层挡(403 Uifid Not Found / Sign Invalid / 空body)。详见 references/风控分析与原版文档.md；签名算法本身见 references/a_bogus算法.md（分析接口/验算用，别用它绕风控）。

**服务器机房 IP 上连真浏览器也被拦**: 把用户电脑跑通的完整 .profile(含登录态/uifid)迁到服务器, 接口照样 detail 被拦 —— 风控绑定 IP 属性(机房 vs 住宅)和设备指纹, 换机器 cookie 即作废。结论: 下载这步只在住宅网络的桌面机上跑; 服务器负责拿到文件后的后续处理, 或让用户下载后把文件传上来。

## ✅ 已解决（2026-11-03）：core_dep 过滤的根因是 UA 与浏览器二进制不匹配

之前的 `core_dep` 过滤不是 IP/环境信誉/浏览器二进制问题 —— 是 **UA 版本与实际浏览器不一致**：UA 硬编码 Chrome/131, playwright chromium 实际是 156, TLS/HTTP2 指纹与 UA 声明对不上。UA 改成 156 后连续 3 条视频全部下载成功（含之前判被风控的 7698635183570153209）。同一条 7419136929172425486 在任何 UA 下都 null —— 那条视频本身受限（非风控）, 不要拿它当测试样本。**规则：UA 版本必须与 playwright chromium 实际版本一致；playwright 升级后要同步改 UA。** Windows 原版从注册表读本机 Chrome 版本天然一致, Linux 硬编码才踩坑。李四超在 Win 上用 Chrome for Testing 156 也能下（无本机 Chrome）, 交叉证实二进制发行渠道无关。

以下此前记录的"风控绑 IP""环境信誉"结论全部作废（当时是 UA 不匹配的误诊）, 保留仅为历史:

## 关键实现点(改代码前必读)

1. UA 必须覆写, 去掉 HeadlessChrome
2. channel="chrome" + 持久化 profile(.profile 目录累积 ttwid/uifid/msToken, 越用越稳)
3. 只拦截响应不自己发请求; handler 里不要调 resp.body()(竞态)
4. 区分"没发请求"(called=0, 作品删除/私密) 和"发了被风控"(called>0)
5. 主页滚动必须滚内部最大可滚 div(window 不滚)
6. 清晰度按像素数从 bit_rate 全量候选里选, 不要只取 play_addr
7. 不要用 download_addr(带水印且更糊)
8. 直链有时效(几分钟~几小时), 拿到尽快下
9. 下载校验 Content-Type + Content-Length, .part 写完再改名

## 已知边界(实测)

- 网页端码率天花板 ≈2Mbps(时长决定大小, 非清晰度丢失)
- 未登录主页只有第一页(约19条); --login 扫码可翻页
- H.265 老播放器可能黑屏有声; 保守用 --codec h264
- 短时间大量请求触发风控, 单次 ≤20 条

## 环境积累（服务器养号）与静默通知

家用宽带 IP 上指纹要慢慢养：定期用无头浏览器访问抖音，让 ttwid/s_v_web_id/msToken/uifid 自然累积进 scripts/.profile。

**铁律：只浏览，禁止下载测试**——失败的下载尝试会在风控侧留下可疑记录，反复撞枪口反而把环境养脏。需要真正下载时先在本技能目录手工跑，且等指纹积累充分后。

- cron 两班（02:00 / 12:15）已改为纯浏览模式：只跑 accumulate.py + 记录 cookie 状态，不碰 douyin_dl.py
- 2026-11-03: accumulate.py 跑通（90s/33 次滚动），cookie 34 条，关键指纹 ttwid、s_v_web_id 已写入 .profile。注：accumulate.py 的 playwright 装在 ~/.local（hermeswebui 用户级），cron 若用不同 PATH 的 python 会报 ModuleNotFoundError，统一用 /usr/local/bin/python3
- 2026-10-29 起执行新策略（此前 4 轮"浏览+下载测试"模式已终止，怀疑失败尝试污染环境）
- 2026-11-02 12:15 积累成功：浏览 90s / 36 次滚动，cookie 总数 34，关键风控指纹 ttwid、s_v_web_id 均在；指纹已写入 .profile
- `scripts/accumulate.py --seconds 90`：无头打开首页慢速滚动，指纹写入 profile；末尾打印 cookie 关键指纹清单做健康检查
- cron 养号 ×2（每天两班，agent 模式，deliver=weixin:<用户chat_id>）：积累→固定测试链接验证→结果覆盖写进本文件。**通知规则（实测可行）**：agent 模式下最终回复为空字符串 → 微信完全不投递；非空 → 自动投递。所以成功→回复成功通知，失败/报错→回复空串（prompt 里严格写死，绝不能附带任何文字），一个任务内同时实现"成了才响铃、败了静默"，无需额外 watchdog 任务。failure_deliver 设为 local 避免引擎错误也发微信

## 排错速查

| 现象 | 处理 |
|---|---|
| 403 Uifid Not Found | 走了纯HTTP, 改浏览器拦截 |
| 200 但 body 0 | UA 含 HeadlessChrome, 确认覆写 |
| 页面没发起 detail 请求 | 作品删除/私密/地区限制 |
| Sign Invalid | a_bogus 与 x-secsdk-web-signature 不同源, 放弃自算 |
| 主页只有19条 | 登录墙, --login |
| 找不到 Chrome | playwright install chromium, 去掉 channel |
| 启动即崩 TargetClosedError | 无root容器缺系统库, 见 references/无root部署chromium.md |
| SkFontMgr FATAL / SIGTRAP 崩溃 | fontconfig 配置不兼容, 换最小 fonts.conf(同上参考) |
