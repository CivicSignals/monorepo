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
  // TODO Q2 — Product docs (tour, onboarding, ICP, saved searches, FOIA, integrations)
  productSidebar: [
    {
      type: "category",
      label: "Product",
      items: [
        "product/intro",
      ],
    },
  ],

  // TODO Q3 — API docs (generated from OpenAPI + guides)
  apiSidebar: [
    {
      type: "category",
      label: "API Reference",
      items: [
        "api/intro",
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

  // TODO Q5 — Recipe authoring guide
  recipesSidebar: [
    {
      type: "category",
      label: "Recipes",
      items: [
        "recipes/intro",
      ],
    },
  ],
};

export default sidebars;
