"""integrations module — Salesforce, HubSpot, Slack, webhooks.

K1 ships the generic outbound-integration framework: workspace-scoped
connections with OAuth2 (authorization-code) + token refresh, encrypted token
storage, a provider abstraction/registry, scope-aware typed errors, and the
push-log with retry/dead-letter. Concrete provider clients land in K2
(Salesforce), K3 (HubSpot) and L1 (Slack); K4 adds idempotent push; K5 the
recovery UI; L3 generic webhooks.
"""
