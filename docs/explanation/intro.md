---
type: explanation
---

# 简介与定位

全天录是一个自己部署的全天录音和个人记忆层。戴一个录音设备（或者只带手机）从早录到晚，自己的服务器在一分钟内把音频变成带说话人的转写、对话摘要、待办和每日回顾。原始录音一直留着，每句话都能点开回放核对；你的 Agent 通过 MCP 直接查你的每一天。可以把它当成自己托管的 lifelog：Plaud、Limitless、Bee、Omi 的开源替代，没有订阅，对话也不放在别人的云上。（录音豆、心力球等产品名称归各自所有者，本项目与它们没有关联。）

## 为什么做这个

- **原始录音一直留着。** 心力球和 Bee 转写完就删音频，转写错了没有底稿可对。这里每句话都能点开回放，以后有了更好的模型，还能把过去重新转写一遍。
- **数据在你自己的服务器上。** 一台机器、一个 SQLite 数据库和一堆原始录音。音频只发给你选的语音识别服务，转写只发给你选的大模型，不需要注册任何账号。
- **没有订阅。** 软件免费（MIT）；默认的千问文件转写约 ¥0.05/小时音频，按量付给云厂商，没有每月分钟数上限。大模型费用按你选的服务计。
- **不挑设备，硬件开源。** iPhone 和安卓 App 直接录手机麦克风；想全天戴，就用几十块钱的现成模块焊一个蓝牙录音设备（`LR-P3`），零件表、接线图和 3D 打印外壳都在仓库里。
- **你的 Agent 能查你的每一天。** 远程 MCP 端点给 Claude Code、Codex、Cursor 用，也有命令行；数据就是 SQLite，自己就能读。

## 能做什么

- **全天录音。** 手机 App 上传 Opus 短分片；蓝牙录音设备一直写 SD 卡，同时推给手机，断开期间的部分回来后补齐。离线时什么都不丢：分片在设备上排队，之后再上传。
- **什么音源都能接。** 能 POST 音频的东西都能给服务器送数据（`POST /api/chunks`），已有的录音文件也可以，见[如何导入已有录音](../how-to/import-recordings.md)。
- **两层转写。** 定稿层在停顿处切开，每段最长 5 分钟交给云端语音识别引擎（默认阿里云百炼千问，约 ¥0.05/小时音频）；页面开着时，初稿层几秒内出字。没有语音识别的 key，可以用 `ENGINE=local` 在本机转写（[和云端的对比](./asr-benchmark.md)）。
- **认人。** 语音检测（FireRedVAD）和声纹（ReDimNet2）在本机 CPU 上跑（onnxruntime，不需要显卡），认得出你自己，分得清一段对话里的不同人，记得你起的名字。
- **记忆。** 一段安静之后对话结束，用你配置的大模型写摘要；待办和截止日期、每日回顾、全文搜索，以及对历史记录「问 AI」，回答带出处对话的链接。说话不到 60 秒的对话不调大模型，标题取第一句，没有摘要和待办（`server/alldayrec/summary.py` 里的 `MIN_SPEECH_S`）。
- **开放。** 网页、给 Claude Code、Codex、Cursor 等 Agent 用的远程 MCP 端点（`POST /mcp`）、一个小命令行（`cli/lr.py`），数据就是 SQLite，自己就能读。
- **数据归你。** 一台服务器、你自己的硬盘，愿意的话一台服务器可以给好几个人用（每人一个 token）。

## 和市面产品比

| | **alldayrec** | 飞书录音豆 | OPPO 心力球 | 钉钉 A1 | Plaud | Limitless / Bee | Omi |
|---|---|---|---|---|---|---|---|
| **价格 / 订阅** | 软件免费；转写按量付费，千问约 ¥0.05/小时；大模型另计 | ¥899 起；送 6 个月 AI 权益，之后会员 ¥69/月[^lrd] | ¥499 体验价，招募制；只配 OPPO、一加、realme 手机[^xlq][^xlq2] | ¥499 / ¥799[^a1c]；会员 ¥599 或 ¥1499/年[^a1] | NotePin S $179[^plaud-dev]；免费 300 分钟/月，Pro $17.99/月[^plaud-plan] | Limitless 已被 Meta 收购、停售[^limitless]；Bee $74.99 + Pro $11.99/月，仅美国[^bee] | 吊坠 $179（促销 $129）；免费 300 云端分钟/月，Pro $269/年[^omi-price] |
| **全天佩戴** | 是：手机 App，或自制录音设备边录边写卡 | 单次 8 小时，带充电仓 32 小时[^lrd] | 24 小时连续记录[^xlq] | 贴在手机背面的卡片，连续录 45 小时[^a1] | NotePin S 约 20 小时[^plaud-dev] | Bee 手环，7 天续航[^bee] | 吊坠 |
| **保留原音** | 是，每句可回放、可重新转写 | 设备上传后删除，云端保留；只能在妙记网页逐条下载[^lrd] | 否，「只留文字、不留原音」[^xlq] | 未知 | 是，可导出 MP3 / WAV[^plaud-exp] | Bee 否，处理后删除[^bee] | 可选，默认不存[^omi-audio] |
| **数据在哪** | 你自己的服务器 | 飞书云[^lrd] | OPPO PCC 私密云[^xlq] | 设备 64 GB + 钉钉云[^a1] | Plaud 云[^plaud-plan] | Bee 的云（Bee Private Compute）[^bee] | Omi 云，或自己部署后端[^omi-docs] |
| **说话人识别** | 本机声纹：认得出你自己，记得你起的名字 | 声纹区分说话人[^lrd2] | 声纹区分佩戴者本人[^xlq] | 注册声纹后显示姓名[^a1b] | 区分说话人，可查找替换说话人名[^plaud-rn] | 未知 | 区分说话人[^omi-docs] |
| **摘要 / 待办 / 回顾** | 摘要、待办和截止日期、决定、每日回顾 | 纪要、待办[^lrd2] | 今日摘要、待办、项目串联[^xlq] | 纪要、章节、分析与后续跟进[^a1c] | 模板摘要、To-dos[^plaud-app] | Bee 摘要（Pro 不限量）[^bee] | 摘要、待办、日报[^omi-home] |
| **问 AI / MCP** | 问 AI 带出处；远程 MCP | 跨纪要 AI 问答[^lrd2]；MCP 未知 | 在微信、飞书里问；MCP[^xlq] | 未知 | Ask Plaud；MCP[^plaud-mcp] | 未知 | Chat；MCP[^omi-mcp] |
| **开放硬件** | 是（`hardware/lr-p3/`） | 否 | 否 | 否 | 否 | 否 | 是[^omi-docs] |
| **开源** | 是（MIT：服务端、网页、App、固件） | 否 | 否 | 否 | 否 | 否 | 是（MIT）[^omi-docs] |

「否」指没有公开的设计文件或源码，「未知」指没查到可靠资料；价格和功能以各家官网为准，查于 2026-10-24。文中的产品名称和商标归各自所有者所有，本项目与它们没有任何关联。

## 名字和历史

「LR」「lr」是项目的内部代号，硬件和代码里还在用：录音设备型号 `LR-P3`、固件工具 `lr.sh`、命令行 `lr.py`。

仓库公开前改写过 git 历史，去掉了作者私人部署的细节，所以历史很短；提交说明里的 `lr#123` 指的是以前私有仓库的 issue，不是这个仓库的。

项目以 MIT 许可发布，打包进来的第三方代码（Omi devkit、Opus、FireRedVAD 的后处理等）保留各自的许可，见 [THIRD_PARTY_NOTICES.md](https://github.com/suyiiyii/alldayrec/blob/main/THIRD_PARTY_NOTICES.md)。

[^lrd]: [爱搞机，安克 AI 录音豆评测（价格、AI 权益与会员、续航、音频导出、云端存储）](https://m.igao7.com/news/202601/M0rs0SQkAdD05EZY.html)
[^lrd2]: [52audio，安克 AI 录音豆（声纹、纪要与待办、AI 问答）](https://www.52audio.com/archives/267283.html)
[^xlq]: [极客公园，OPPO AI 心力球与白鲸 AI（不留原音、PCC 私密云、声纹、MCP、适配机型）](https://www.geekpark.net/news/372428)
[^xlq2]: [新浪科技，OPPO 心力球 499 元体验价](https://finance.sina.com.cn/tech/digi/2026-09-22/doc-inistexc1962351.shtml)
[^a1]: [量子位，DingTalk A1 发布（价格、会员、续航、存储）](https://www.qbitai.com/2025/09/331938.html)
[^a1b]: [钉钉 AI 听记（注册声纹后标注姓名）](https://www.dingtalk.io/zh/blog/ai-minutes-redefining-notes)
[^a1c]: [极客公园，DingTalk A1 体验（转写、纪要、章节、分析）](https://www.geekpark.net/news/353280)
[^plaud-dev]: [Plaud 设备对比（NotePin S 价格、续航）](https://www.plaud.ai/pages/compare-plaud-devices)
[^plaud-plan]: [Plaud AI 会员价格](https://www.plaud.ai/pages/plaud-ai-plan-pricing)
[^plaud-exp]: [Plaud 帮助中心，导出文件（MP3 / WAV）](https://support.plaud.ai/hc/en-us/articles/50835453223705-Export-files)
[^plaud-rn]: [Plaud 更新日志（查找替换说话人名）](https://www.plaud.ai/pages/plaud-release-notes)
[^plaud-app]: [Plaud App 3.0（Highlights、Summary、To-dos、Ask）](https://www.plaud.ai/blogs/news/plaud-app-3-design-intelligence)
[^plaud-mcp]: [Plaud MCP 文档](https://docs.plaud.ai/plaud-mcp-cli/mcp)
[^limitless]: [TechInformed，Meta 收购 Limitless，吊坠停止向新用户销售（2025-12-09）](https://techinformed.com/meta-acquires-limitless-pendant-users-moved-to-free-unlimited-plan/)
[^bee]: [The Next Web，Amazon 重新推出 Bee（价格、订阅、音频处理后删除）](https://thenextweb.com/news/amazon-bee-frontier-eclipse-subscription)
[^omi-price]: [Omi 价格](https://www.omi.me/pages/pricing)
[^omi-docs]: [Omi 文档（硬件、固件、App、后端全部开源，可自己部署，说话人识别）](https://docs.omi.me/doc/get_started/introduction)
[^omi-audio]: [Omi 后端，`store_recording_permission` 默认关闭](https://github.com/BasedHardware/omi/blob/main/backend/database/users.py)
[^omi-home]: [Omi 的 README（摘要和待办）](https://github.com/BasedHardware/omi)；[首页源码（Daily Recaps）](https://github.com/BasedHardware/omi/blob/main/app/lib/pages/home/home_content.dart)
[^omi-mcp]: [Omi MCP 文档](https://docs.omi.me/doc/developer/MCP)
