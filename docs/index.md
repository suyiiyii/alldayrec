---
layout: home
# the README's tagline, written once: the page title reuses it
title: &tagline 开源心力球，私有第二大脑
titleTemplate: "alldayrec（全天录）：:title"
hero:
  name: alldayrec（全天录）
  text: *tagline
  tagline: 全平台可用，不用买硬件，手机就能录。
  image:
    src: https://raw.githubusercontent.com/suyiiyii/alldayrec/assets/img/live-captions-20261026b.gif
    alt: 实时页：边说边出字幕，AI 实时摘要跟着更新（示例数据）
  actions:
    - theme: brand
      text: 快速开始
      link: /tutorial/first-run
    - theme: alt
      text: GitHub
      link: https://github.com/suyiiyii/alldayrec
features:
  - title: 全平台，不用买硬件
    details: 安卓 App、iPhone App 和浏览器都能直接录。
    link: /how-to/install-app
  - title: 两层转写
    details: 定稿交给云端语音识别（默认阿里云百炼千问，约 ¥0.05/小时音频），页面开着时几秒内出初稿；也可以 <code>ENGINE=local</code> 在本机转写。
    link: /how-to/change-asr-engine
  - title: 认人
    details: 认得出你自己，记得你给别人起的名字。
  - title: 记忆
    details: 对话摘要、待办和截止日期、每日回顾、全文搜索。
  - title: 开放
    details: 网页、远程 MCP 端点（Claude Code、Codex、Cursor）、命令行。
    link: /how-to/connect-mcp
  - title: 导入已有录音
    details: 录音文件可以通过 HTTP 接口导入。
    link: /how-to/import-recordings
---

<div class="vp-doc" style="max-width: 688px; margin: 48px auto 0; padding: 0 24px">

## 文档

- [教程](./tutorial/first-run.md)：从零开始用 Docker 跑起服务器，在网页上看到第一句转写。
- [操作指南](./how-to/deploy-docker.md)：部署、让手机连上、绑定录音设备、导入录音、接入 MCP、升级和备份。
- [参考](./reference/configuration.md)：配置项、HTTP API、MCP 工具、命令行、数据目录、模型文件和硬件。
- [说明](./explanation/intro.md)：它是什么、和市面产品比、架构与数据流、隐私、语音识别评测、局限与路线图。

给 AI Agent 的入口：<a href="./llms.txt" target="_self">llms.txt</a>（全部页面的目录）、<a href="./llms-full.txt" target="_self">llms-full.txt</a>（全部内容合成一个文件）、<a href="./SKILL.md" target="_self">SKILL.md</a>（部署并连接 alldayrec 的 Agent 技能文件）。每一页把地址里的 `.html` 换成 `.md` 就是它的 Markdown 原文。

</div>
