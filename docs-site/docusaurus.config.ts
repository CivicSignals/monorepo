import { themes as prismThemes } from "prism-react-renderer";
import type { Config } from "@docusaurus/types";
import type * as Preset from "@docusaurus/preset-classic";

const config: Config = {
  title: "CivicSignals",
  tagline: "Open-source public-sector sales intelligence",
  favicon: "img/favicon.ico",

  url: "https://docs.civicsignals.io",
  baseUrl: "/",

  organizationName: "CivicSignals",
  projectName: "docs",

  onBrokenLinks: "throw",
  markdown: {
    hooks: {
      onBrokenMarkdownLinks: "warn",
    },
  },

  i18n: {
    defaultLocale: "en",
    locales: ["en"],
  },

  presets: [
    [
      "classic",
      {
        docs: {
          routeBasePath: "/",
          sidebarPath: "./sidebars.ts",
          editUrl: "https://github.com/CivicSignals/monorepo/tree/main/docs-site/",
        },
        blog: false,
        theme: {
          customCss: "./src/css/custom.css",
        },
      } satisfies Preset.Options,
    ],
  ],

  themeConfig: {
    image: "img/civicsignals-social.png",
    navbar: {
      title: "CivicSignals Docs",
      logo: {
        alt: "CivicSignals Logo",
        src: "img/logo.svg",
      },
      items: [
        {
          type: "docSidebar",
          sidebarId: "productSidebar",
          position: "left",
          label: "Product",
        },
        {
          type: "docSidebar",
          sidebarId: "apiSidebar",
          position: "left",
          label: "API",
        },
        {
          type: "docSidebar",
          sidebarId: "selfHostSidebar",
          position: "left",
          label: "Self-host",
        },
        {
          type: "docSidebar",
          sidebarId: "recipesSidebar",
          position: "left",
          label: "Recipes",
        },
        {
          href: "https://github.com/CivicSignals/monorepo",
          label: "GitHub",
          position: "right",
        },
      ],
    },
    footer: {
      style: "dark",
      links: [
        {
          title: "Docs",
          items: [
            { label: "Product", to: "/product/intro" },
            { label: "API", to: "/api/intro" },
            { label: "Self-host", to: "/self-host/intro" },
            { label: "Recipes", to: "/recipes/intro" },
          ],
        },
        {
          title: "Community",
          items: [
            { label: "GitHub", href: "https://github.com/CivicSignals/monorepo" },
            { label: "Discord", href: "https://discord.gg/civicsignals" },
          ],
        },
        {
          title: "Legal",
          items: [
            { label: "License (AGPL-3.0)", href: "https://github.com/CivicSignals/monorepo/blob/main/LICENSE" },
          ],
        },
      ],
      copyright: `Copyright ${new Date().getFullYear()} CivicSignals contributors. Licensed AGPL-3.0-only.`,
    },
    prism: {
      theme: prismThemes.github,
      darkTheme: prismThemes.dracula,
      additionalLanguages: ["bash", "yaml", "json", "python", "typescript"],
    },
  } satisfies Preset.ThemeConfig,
};

export default config;
