---
layout: home
# Keep the SEO title aligned with the README hook; the hero uses the brand tagline.
title: "An open-source Plaud for your whole day, so your agent knows you"
titleTemplate: "AllDayRec — :title"
hero:
  name: AllDayRec（全天录）
  text: "Record your whole day, so your agent knows you"
  tagline: 'Record every day with the devices you already have: wear a recorder, use your phone, or import recordings from other devices. It all becomes a personal memory on your own server that your agent can query.<br>Pronounced "all day rec".'
  image:
    src: https://raw.githubusercontent.com/suyiiyii/alldayrec/assets/img/live-captions-en-summary-20261026.gif
    alt: "The Live page: captions as people speak, with the AI live summary keeping up (made-up data)"
  actions:
    # the online demo is pages of its own next to the docs: target keeps the docs' router from taking the link
    - theme: brand
      text: Try the demo
      link: /demo/
      target: _self
    - theme: alt
      text: Quickstart
      link: /en/tutorial/first-run
    - theme: alt
      text: GitHub
      link: https://github.com/suyiiyii/alldayrec
features:
  - title: Use the devices you already have
    details: The Feishu recorder bean (Android only) and the open-source recorder connect to the app and sync live; recordings from other devices can be imported; with no device at all, the Android app, the iPhone app and a browser record directly.
    link: /en/how-to/bind-recorder
  - title: Transcribes in two layers
    details: The final transcript comes from a cloud ASR engine (default Alibaba Cloud Bailian Qwen, about ¥0.05 per hour of audio), and drafts show within seconds while a page is open; <code>ENGINE=local</code> transcribes on your machine instead.
    link: /en/how-to/change-asr-engine
  - title: Knows who spoke
    details: It learns your own voice and remembers the names you give others.
  - title: Remembers
    details: Conversation summaries, to-dos with due dates, a daily recap, and full-text search.
  - title: Opens up
    details: A web UI, a remote MCP endpoint (Claude Code, Codex, Cursor), a CLI.
    link: /en/how-to/connect-mcp
  - title: Imports recordings
    details: Audio files you already have can be imported from the web page.
    link: /en/how-to/import-recordings
---

<div class="vp-doc" style="max-width: 688px; margin: 48px auto 0; padding: 0 24px">

## Docs

- [Tutorials](./tutorial/first-run.md): start from scratch, run the server with Docker, and see your first transcribed sentence in the web UI.
- [How-to guides](./how-to/deploy-docker.md): deploy, reach the server from a phone, bind a recorder, import recordings, connect MCP, upgrade and back up.
- [Reference](./reference/configuration.md): configuration, HTTP API, MCP tools, the CLI, data layout, model files and hardware.
- [Explanation](./explanation/intro.md): what it is, how it compares, architecture and data flow, privacy, the ASR benchmark, limitations and roadmap.

For AI agents: <a href="../llms.txt" target="_self">llms.txt</a> (an index of all pages), <a href="../llms-full.txt" target="_self">llms-full.txt</a> (all the content in one file), <a href="../SKILL.md" target="_self">SKILL.md</a> (an agent skill file to deploy and connect AllDayRec). To get any page's Markdown source, replace `.html` in its address with `.md`.

</div>
