import type { SidebarsConfig } from "@docusaurus/plugin-content-docs";

/**
 * Sidebar configuration for docs.civicsignals.io.
 *
 * Sections correspond to future doc tasks:
 *   Product  → Q2
 *   API      → Q3
 *   Self-host → Q4
 *   Recipes  → Q5
 */
const sidebars: SidebarsConfig = {
  productSidebar: [
    {
      type: "category",
      label: "Product",
      items: [
        "product/intro",
        {
          type: "category",
          label: "Get started",
          collapsed: false,
          items: [
            "product/getting-started",
            "product/onboarding",
          ],
        },
        {
          type: "category",
          label: "Core features",
          collapsed: false,
          items: [
            "product/icp",
            "product/entities",
            "product/pipeline",
            "product/foia",
            "product/integrations",
          ],
        },
        {
          type: "category",
          label: "Signal discovery",
          collapsed: false,
          items: [
            "product/feed",
            "product/saved-searches",
            "product/smart-search",
          ],
        },
      ],
    },
  ],

  // Q3 — API docs (generated from OpenAPI + guides)
  apiSidebar: [
    {
      type: "category",
      label: "API Reference",
      collapsible: false,
      items: [
        "api/intro",
        "api/authentication",
        "api/workspace-scoping",
        "api/pagination",
        "api/errors",
        "api/rate-limits",
      ],
    },
  ],

  // Q4 — Self-host docs
  selfHostSidebar: [
    {
      type: "category",
      label: "Self-host",
      items: [
        "self-host/intro",
        "self-host/quickstart",
        "self-host/production",
        "self-host/configuration",
        "self-host/upgrade",
        "self-host/hardening",
        "self-host/backup",
        "self-host/kubernetes",
      ],
    },
  ],

  // Q5 — Recipe authoring guide
  recipesSidebar: [
    {
      type: "category",
      label: "Recipes",
      items: [
        "recipes/intro",
        "recipes/connectors",
        "recipes/schema",
        "recipes/selectors",
        "recipes/fixtures",
        "recipes/cli",
        "recipes/politeness",
        "recipes/example",
        "recipes/contributing",
      ],
    },
  ],
};

export default sidebars;
