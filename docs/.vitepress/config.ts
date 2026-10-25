import { writeFileSync } from "node:fs";
import { join } from "node:path";
import footnote from "markdown-it-footnote";
import { defineConfig, type DefaultTheme } from "vitepress";
import llmstxt from "vitepress-plugin-llms";

// Where the site is served: suyiiyii.github.io/alldayrec/ by default; at the root of an org site, DOCS_BASE=/
// (the docs workflow passes what actions/configure-pages reports)
const base = process.env.DOCS_BASE || "/alldayrec/";
const host = (process.env.DOCS_HOST || "https://suyiiyii.github.io").replace(/\/$/, "");
const REPO = "https://github.com/suyiiyii/alldayrec";
// social card for every page, also the repo's social preview; binaries live on the assets branch, not main
const OG_IMAGE = "https://raw.githubusercontent.com/suyiiyii/alldayrec/assets/img/og-card-20261025.png";

type Item = [text: string, link: string];
const group = (text: string, items: Item[]): DefaultTheme.SidebarItem => ({
  text,
  items: items.map(([text, link]) => ({ text, link })),
});

// one page tree for both languages: [zh title, en title, path]
const SECTIONS: [zh: string, en: string, pages: [string, string, string][]][] = [
  ["教程", "Tutorials", [["第一次跑起来", "Your first transcript", "tutorial/first-run"]]],
  [
    "操作指南",
    "How-to guides",
    [
      ["如何用 uvx 或 pip 运行", "How to run with uvx or pip", "how-to/run-uvx"],
      ["如何用 Docker 部署", "How to deploy with Docker", "how-to/deploy-docker"],
      ["如何从源码运行", "How to run from source", "how-to/deploy-without-docker"],
      ["如何让手机连上服务器", "How to reach the server from a phone", "how-to/remote-access"],
      ["如何安装并连接手机 App", "How to install and connect the phone app", "how-to/install-app"],
      ["如何绑定录音设备", "How to bind a recorder", "how-to/bind-recorder"],
      ["如何导入已有录音", "How to import recordings you already have", "how-to/import-recordings"],
      ["如何更换语音识别引擎", "How to change the ASR engine", "how-to/change-asr-engine"],
      ["如何把 AI 助手接入 MCP", "How to connect an AI agent over MCP", "how-to/connect-mcp"],
      ["如何给另一个人开账号", "How to add another person", "how-to/add-person"],
      ["如何升级与回滚", "How to upgrade and roll back", "how-to/upgrade"],
      ["如何备份与恢复", "How to back up and restore", "how-to/backup"],
      ["如何用 deploy.sh 部署（维护者）", "How to deploy with deploy.sh (maintainers)", "how-to/deploy-sh"],
      ["如何排查常见问题", "How to troubleshoot common problems", "how-to/troubleshooting"],
      ["如何参与开发", "How to contribute", "how-to/contribute"],
    ],
  ],
  [
    "参考",
    "Reference",
    [
      ["配置项", "Configuration", "reference/configuration"],
      ["HTTP API", "HTTP API", "reference/api"],
      ["MCP 工具", "MCP tools", "reference/mcp"],
      ["命令行 lr.py", "CLI: lr.py", "reference/cli"],
      ["网页与 App", "Web pages and app settings", "reference/web-and-app"],
      ["数据目录", "Data layout", "reference/data"],
      ["模型文件", "Model files", "reference/models"],
      ["录音设备硬件", "Recorder hardware", "reference/hardware"],
    ],
  ],
  [
    "说明",
    "Explanation",
    [
      ["简介与定位", "What alldayrec is", "explanation/intro"],
      ["架构与数据流", "Architecture and data flow", "explanation/architecture"],
      ["隐私与数据去向", "Privacy: where your data goes", "explanation/privacy"],
      ["语音识别评测", "ASR benchmark", "explanation/asr-benchmark"],
      ["局限与路线图", "Limitations and roadmap", "explanation/roadmap"],
    ],
  ],
];

const sidebar = (en: boolean): DefaultTheme.SidebarItem[] =>
  SECTIONS.map(([zh, e, pages]) =>
    group(
      en ? e : zh,
      pages.map(([z, t, p]) => [en ? t : z, `${en ? "/en/" : "/"}${p}`]),
    ),
  );
const nav = (en: boolean): DefaultTheme.NavItem[] =>
  SECTIONS.map(([zh, e, pages]) => ({ text: en ? e : zh, link: `${en ? "/en/" : "/"}${pages[0][2]}` }));

const KEYWORDS =
  "alldayrec, 全天录, self-hosted all-day recorder, lifelog, speech-to-text, speaker diarization, MCP, open-source Plaud alternative, Limitless alternative, 录音豆, 心力球, 开源录音豆";

export default defineConfig({
  base,
  lastUpdated: true,
  // the benchmark keeps its path in the repo (older links point at it), and shows under 说明 on the site
  rewrites: { "asr-benchmark.md": "explanation/asr-benchmark.md" },
  srcExclude: ["**/README.md", "parts/**", "public/**"], // parts/: pieces other pages include; public/: served as is
  sitemap: { hostname: host + base },
  head: [["meta", { name: "keywords", content: KEYWORDS }]],
  // per page: the canonical address (the one the sitemap lists) and the tags link previews read
  transformHead: ({ page, siteData, title, description }) => {
    if (page === "404.md") return [];
    const url = host + base + page.replace(/(^|\/)index\.md$/, "$1").replace(/\.md$/, ".html");
    return [
      ["link", { rel: "canonical", href: url }],
      ["meta", { property: "og:type", content: "website" }],
      ["meta", { property: "og:url", content: url }],
      ["meta", { property: "og:title", content: title }],
      ["meta", { property: "og:description", content: description }],
      ["meta", { property: "og:image", content: OG_IMAGE }],
      ["meta", { property: "og:image:width", content: "1280" }],
      ["meta", { property: "og:image:height", content: "640" }],
      ["meta", { property: "og:locale", content: siteData.lang.replace("-", "_") }],
      ["meta", { name: "twitter:card", content: "summary_large_image" }],
    ];
  },
  markdown: { config: (md) => void md.use(footnote) },
  vite: {
    plugins: [
      llmstxt({
        domain: host,
        ignoreFiles: ["index.md", "en/index.md", "parts/**"],
        customTemplateVariables: {
          title: "alldayrec docs / alldayrec 文档",
          description:
            "alldayrec (全天录) is a self-hosted all-day recorder and personal memory layer. Pages exist in Chinese (default, at the root) and English (under /en/).",
          details: `To deploy and connect alldayrec step by step, read the agent skill file first: ${host}${base}SKILL.md. Every page below is also served as Markdown at the same path with .md; the whole site as one file: ${host}${base}llms-full.txt.`,
        },
      }),
    ],
  },

  themeConfig: {
    socialLinks: [{ icon: "github", link: REPO }],
    editLink: { pattern: `${REPO}/edit/main/docs/:path` },
    search: {
      provider: "local",
      options: {
        locales: {
          root: {
            translations: {
              button: { buttonText: "搜索", buttonAriaLabel: "搜索" },
              modal: {
                noResultsText: "没有结果",
                resetButtonTitle: "清除",
                footer: { selectText: "选择", navigateText: "切换", closeText: "关闭" },
              },
            },
          },
        },
      },
    },
  },
  locales: {
    root: {
      label: "简体中文",
      lang: "zh-CN",
      title: "alldayrec 文档",
      titleTemplate: "alldayrec（全天录）：自己部署的全天录音", // the bare name is ambiguous in search: say what it is
      description:
        "alldayrec（全天录）文档：自己部署的全天录音和个人记忆层，开源版 Plaud、Limitless、录音豆、心力球替代。部署、手机 App、硬件、API 与 MCP。",
      themeConfig: {
        nav: nav(false),
        sidebar: sidebar(false),
        editLink: { pattern: `${REPO}/edit/main/docs/:path`, text: "在 GitHub 上编辑此页" },
        lastUpdated: { text: "最后更新" },
        outline: { label: "本页内容", level: [2, 3] },
        docFooter: { prev: "上一页", next: "下一页" },
        returnToTopLabel: "回到顶部",
        sidebarMenuLabel: "目录",
        darkModeSwitchLabel: "深色模式",
        langMenuLabel: "语言",
      },
    },
    en: {
      label: "English",
      lang: "en-US",
      link: "/en/",
      title: "alldayrec docs",
      titleTemplate: "alldayrec — self-hosted all-day recorder",
      description:
        "Docs for alldayrec (全天录), a self-hosted all-day recorder and personal memory layer: an open-source alternative to Plaud, Limitless and similar recorders. Deployment, phone app, hardware, API and MCP.",
      themeConfig: {
        nav: nav(true),
        sidebar: sidebar(true),
        editLink: { pattern: `${REPO}/edit/main/docs/:path`, text: "Edit this page on GitHub" },
        outline: { level: [2, 3] },
      },
    },
  },
  // robots.txt names the sitemap, whose URL depends on where the site is served
  buildEnd: ({ outDir }) =>
    writeFileSync(join(outDir, "robots.txt"), `User-agent: *\nAllow: /\n\nSitemap: ${host}${base}sitemap.xml\n`),
});
