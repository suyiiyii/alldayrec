---
layout: home
# the README's tagline, written once here (also EN_TAGLINE in .vitepress/config.ts): the page title reuses it
title: &tagline "An open-source Plaud for your whole day: a private second brain"
titleTemplate: "alldayrec — :title"
hero:
  name: alldayrec（全天录）
  text: *tagline
  tagline: "Works on every platform, no hardware to buy: your phone is the recorder."
  image:
    src: https://raw.githubusercontent.com/suyiiyii/alldayrec/assets/img/live-captions-20261026b.gif
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
  - title: Every platform, no hardware to buy
    details: The Android app, the iPhone app and a browser all record directly.
    link: /en/how-to/install-app
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

For AI agents: <a href="../llms.txt" target="_self">llms.txt</a> (an index of all pages), <a href="../llms-full.txt" target="_self">llms-full.txt</a> (all the content in one file), <a href="../SKILL.md" target="_self">SKILL.md</a> (an agent skill file to deploy and connect alldayrec). To get any page's Markdown source, replace `.html` in its address with `.md`.

</div>
