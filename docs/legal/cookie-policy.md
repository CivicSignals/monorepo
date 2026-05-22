# Cookie Policy

> **DRAFT — Pending legal review. Not legally effective until reviewed by counsel and published.**  
> **Last updated:** [DATE TO BE SET AT PUBLICATION]

This Cookie Policy explains how CivicSignals, Inc. ("CivicSignals," "we," "us," or "our") uses cookies and similar technologies on `civicsignals.io` and related subdomains.

---

## 1. What Are Cookies?

Cookies are small text files stored on your device by your web browser when you visit a website. They help websites remember your preferences and provide functionality. Similar technologies include local storage, session storage, and pixels.

---

## 2. Summary

We keep our cookie use minimal:

| Surface | Cookies Used | Third-Party Cookies |
|---|---|---|
| App (`app.civicsignals.io`) | Functional and security only | None |
| Marketing site (`civicsignals.io`) | Functional only | None (Plausible is cookieless) |
| Docs site (`docs.civicsignals.io`) | Functional only | None |

We do **not** use advertising cookies, tracking pixels, or cross-site tracking anywhere on our properties.

---

## 3. Cookies on the App (`app.civicsignals.io`)

### 3.1 Strictly Necessary Cookies

These cookies are required for the app to function. They cannot be disabled.

| Cookie Name | Purpose | Duration | Provider |
|---|---|---|---|
| `cs_session` | Maintains your authenticated session. HttpOnly, Secure, SameSite=Lax. | 24 hours (sliding), 30-day absolute maximum | CivicSignals |
| `cs_csrf` | CSRF protection token used for cookie-authenticated state-changing requests (double-submit cookie pattern). | Session | CivicSignals |
| `cs_workspace` | Remembers your last active workspace for redirect after login. HttpOnly, Secure. | 30 days | CivicSignals |

No other cookies are set by the app.

### 3.2 Local Storage (App)

The app uses browser local storage for:
- UI preferences (theme, column widths, sidebar state)
- Draft form data (auto-saved ICP wizard state)

This data never leaves your device and is never transmitted to our servers.

---

## 4. Cookies on the Marketing Site (`civicsignals.io`)

### 4.1 Analytics

We use **Plausible Analytics** for privacy-respecting, cookieless analytics on the marketing site. Plausible:

- Does **not** use cookies
- Does **not** collect personal identifiers
- Does **not** perform cross-site tracking
- Uses an anonymized, aggregated model (IP address is not stored; a daily rotating hash is used for session counting)

No cookie consent banner is required for Plausible, as it uses no cookies.

### 4.2 Consent Banner

A cookie consent banner is shown to visitors in the EU/UK/California on the marketing site. The only "preference" category offered is whether you consent to Plausible analytics — which, as noted above, uses no cookies. This banner is provided for regulatory completeness.

---

## 5. Third-Party Cookies

We do **not** embed any third-party advertising, social, or tracking scripts that set their own cookies on any CivicSignals-operated domain. Our Content Security Policy (CSP) blocks unauthorized third-party script execution.

If you connect a third-party integration (e.g., log in with Google via OAuth), those third parties may set their own cookies according to their privacy policies during the authentication flow on their own domains. This is outside our control.

---

## 6. Managing Cookies

### Browser Controls

You can manage or delete cookies through your browser settings:
- **Chrome:** Settings → Privacy and security → Cookies
- **Firefox:** Settings → Privacy & Security → Cookies and Site Data
- **Safari:** Settings → Privacy → Manage Website Data
- **Edge:** Settings → Cookies and site permissions

### Impact of Disabling Cookies

Disabling the strictly necessary cookies (`cs_session`, `cs_csrf`) will prevent you from logging in to the app, as these are required for authentication and security. Disabling the `cs_workspace` cookie will simply mean you are always redirected to workspace selection after login.

---

## 7. Updates

We may update this Cookie Policy from time to time. We will update the "Last updated" date at the top of this page when we make material changes.

---

## Contact

For questions about our use of cookies, contact privacy@civicsignals.io.

**CivicSignals, Inc.**  
civicsignals.io
