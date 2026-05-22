---
id: getting-started
title: Getting started
sidebar_label: Getting started
slug: /product/getting-started
---

# Getting started

This guide walks you from creating your account to landing on your first signal feed. The whole flow takes about 10–15 minutes.

## 1. Create an account

Go to the CivicSignals sign-up page (`/signup`) and choose one of two methods:

- **Email and password** — enter your work email, choose a password, and click **Create account**.
- **Google OAuth** — click **Continue with Google** and authorise the OAuth consent screen.

After submitting, CivicSignals sends a verification email to the address you provided.

:::info Trial plan
Every new workspace starts on a **14-day free trial** of the Starter plan. No credit card is required during the trial.
:::

## 2. Verify your email

Click the link in the verification email. The link is valid for 72 hours. If it expires, request a new one from the `/verify-email` page.

**Common issues:**

| Problem | Resolution |
|---|---|
| Email already registered | The sign-in page offers a one-click password-reset link. |
| Token expired | Use the "Resend verification" button on the verify-email page. |
| Google OAuth scope denied | Fall back to email + password; your form state is preserved. |

## 3. Create a workspace

After email verification you are prompted to name your workspace (typically your company name). Workspaces are the unit of billing and data isolation — signals, saved searches, ICP definitions, and pipeline items all live inside a workspace.

You can belong to multiple workspaces and switch between them from the header workspace switcher.

## 4. Complete the onboarding wizard

Once your workspace is created you land on the **onboarding wizard** at `/onboarding`. This is where you define your Ideal Customer Profile so CivicSignals can score and filter signals for you. See the [onboarding walkthrough](/product/onboarding) for the full step-by-step.

## 5. Switching workspaces

If you belong to multiple workspaces, use the workspace switcher in the top navigation bar to change the active workspace. All API calls and data views are scoped to the currently active workspace via the `X-Workspace-Id` header sent automatically by the app.

## Roles

There are three roles in a workspace:

| Role | What they can do |
|---|---|
| **Admin** | Full access: ICP, members, integrations, audit log. Admins who created the workspace are also the **owner** — only the owner can manage billing and change the subscription plan. |
| **Member** | Create saved searches, push signals to CRM, manage their own pipeline view, file FOIA requests, export contacts (within quota). |
| **Viewer** | Read-only across all entities and signals; cannot create searches or push to CRM. |

**Owner vs Admin:** The workspace owner has the admin role plus exclusive access to billing settings (changing plans, adding payment methods, managing seats). A workspace can have multiple admins but only one owner. The owner designation can be transferred to another admin.

Admins invite new members via **Settings → Members → Invite member**. Invitation links are valid for 7 days.

## Next steps

- Set up your ICP: [Onboarding walkthrough](/product/onboarding)
- Browse entities: [Entity directory](/product/entities)
- Track opportunities: [Pipeline](/product/pipeline)
