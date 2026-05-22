# License FAQ — AGPL-3.0-only

> **DRAFT — Pending legal review. Not legally authoritative until reviewed by counsel and published.**  
> This FAQ is for informational purposes. For binding legal advice, consult your own legal counsel.  
> **Last updated:** [DATE TO BE SET AT PUBLICATION]

CivicSignals Core is licensed under the **GNU Affero General Public License, version 3.0 only (AGPL-3.0-only)**. The full license text is in the [LICENSE](../../LICENSE) file in the root of the repository.

This FAQ explains what that means in plain language for the most common use cases.

---

## The Short Version

- **AGPL-3.0 is a strong copyleft license.** If you distribute or deploy modified versions of CivicSignals, you must release your modifications under AGPL-3.0.
- **"Use it over a network" counts as distribution.** Unlike GPL-3.0, AGPL-3.0 closes the "ASP loophole": offering the software as a service to users counts as distribution and triggers the copyleft obligations.
- **CivicSignals Cloud is the commercial service.** If you need to build a service on top of CivicSignals without releasing your proprietary modifications, you must purchase a commercial license from CivicSignals, Inc.
- **Unmodified use is simpler.** Running the unmodified CivicSignals Core for your own organization's internal use is permitted under AGPL-3.0 without the copyleft obligations being triggered (there is no "distribution" to outside users).

---

## Frequently Asked Questions

### Q1: I'm a mid-market company. Can I use CivicSignals Core for free?

**A:** Yes. You can run the unmodified CivicSignals Core (docker-compose / Helm) for your organization's internal use at no cost. You can also modify it for internal use — the AGPL copyleft is triggered only when you distribute the software or make it available to users outside your organization over a network.

---

### Q2: I'm a solo consultant or researcher. Can I use CivicSignals Core for free?

**A:** Yes, on the same terms as Q1. The AGPL-3.0 license is permissive for your own use; restrictions kick in when you make the software available to others.

---

### Q3: I work at a nonprofit or civic-tech organization. Same deal?

**A:** Yes. The license does not distinguish between commercial and non-commercial users. You may use, modify, and run CivicSignals Core for your organization's own use. If you make a service available to external users that is powered by CivicSignals, AGPL obligations apply (see Q5).

---

### Q4: What if I modify CivicSignals Core for my own internal use?

**A:** You are free to modify it. AGPL-3.0 only requires you to release your modifications if you distribute the software or offer it as a service to outside users. Internal modifications that never leave your organization (including your employees and contractors) are not subject to the release requirement.

---

### Q5: I want to build a SaaS product powered by CivicSignals. Do I need a commercial license?

**A:** It depends on what "powered by" means:

- **If you use CivicSignals Cloud API** to fetch signals and build your own product on top, you are using a REST API, not the open-source code. You are subject to the [Terms of Service](terms-of-service.md), not the AGPL.
- **If you run CivicSignals Core unmodified** and expose its functionality to external users over a network, AGPL-3.0 requires you to make the complete corresponding source code available to those users (including any configuration and deployment scripts, to the extent they are considered "source"). Because the software is unmodified, this essentially means publishing a link to our public repository.
- **If you run modified CivicSignals Core** and expose it to external users over a network, AGPL-3.0 requires you to publish your modifications under AGPL-3.0. If you do not want to publish those modifications, you must obtain a commercial license from CivicSignals, Inc.
- **If you build a competing hosted service** using CivicSignals code (modified or unmodified), you must comply with AGPL-3.0. If you want to avoid the open-source release requirement while running a commercial service on the code, contact licensing@civicsignals.io.

---

### Q6: What exactly must I release under the AGPL if I trigger the copyleft?

**A:** The "complete corresponding source code" for all AGPL-covered components of the service you offer. In practice, this means:
- All modifications to CivicSignals Core files
- Any additional code you add that is integrated with (not merely aggregated alongside) CivicSignals
- Reasonably, the deployment configuration that a user would need to build and run the code

It does **not** require you to release:
- Code that interacts with CivicSignals only through its published REST API
- Separate services or applications that communicate with CivicSignals over a network but whose code is not integrated with CivicSignals's code
- Your proprietary data, ICP configurations, or content

---

### Q7: Can I write a private connector or recipe without releasing it?

**A:** Recipes (YAML files that define scraping logic) and connectors (code called by the recipe runner) that are integrated with CivicSignals's codebase are likely covered by AGPL-3.0 if you distribute or offer the system to outside users. Internal use is fine without release.

If your recipe is purely declarative YAML (no custom Python code), it may be considered data/configuration rather than software — this is a nuanced legal question on which you should seek your own counsel.

CivicSignals encourages contributing high-quality recipes to the community recipe repository.

---

### Q8: What is the "AGPL loophole" and does CivicSignals have one?

**A:** GPL-3.0 has an "ASP loophole" (also called the "SaaS loophole"): if you run GPL software on a server and let users interact with it over a network, you are not "distributing" the software and GPL copyleft is not triggered. AGPL-3.0 was specifically designed to close this loophole — if you offer the software to network users, that triggers the copyleft.

CivicSignals uses AGPL-3.0-only precisely because we want to prevent "cloud clone" forks that run our code as a competing service without contributing back. If you are operating a network service using CivicSignals code, AGPL-3.0 applies.

---

### Q9: Is there a commercial license for companies that cannot comply with AGPL?

**A:** Yes. If you need to build a proprietary product or service on top of CivicSignals Core without releasing your modifications under AGPL-3.0, contact licensing@civicsignals.io to discuss a commercial license. Enterprise Cloud customers who use CivicSignals only through our hosted API are not running the AGPL-covered code and do not need a separate commercial license.

---

### Q10: Can I use CivicSignals in a government or public institution deployment?

**A:** Yes. Government and public institutions may run CivicSignals Core for internal use without restriction. AGPL-3.0 does not impose any additional restrictions on government use. If you deploy it as a public-facing service offered to citizens or other agencies, the same AGPL terms apply.

---

### Q11: Does AGPL affect my data or API usage?

**A:** No. The AGPL applies to the software code, not to data you store or extract through the platform, and not to API calls you make to CivicSignals Cloud. Your signal data, ICP configurations, and other content are yours.

---

### Q12: I'm a developer contributing a pull request. What license do my contributions have?

**A:** Contributions to CivicSignals are accepted under the **Developer Certificate of Origin (DCO)**, not a CLA. When you submit a pull request, you sign off that you have the right to contribute the code and agree that it will be licensed under AGPL-3.0-only. Sign off with `git commit -s`. See [CONTRIBUTING.md](../../CONTRIBUTING.md) for details.

---

### Q13: Why AGPL-3.0 and not MIT or Apache-2.0?

**A:** CivicSignals is a venture-backed open-source company. We chose AGPL-3.0 because:

1. It lets anyone — individuals, researchers, journalists, small businesses — use the software freely.
2. It ensures that organizations building on the code contribute improvements back to the community (copyleft).
3. It closes the SaaS loophole that would allow a large cloud provider to run CivicSignals as a competing service without contributing back.
4. It is a true OSI-approved open-source license (unlike SSPL or the Elastic License), which matters for community trust and integration with other OSS projects.

We considered MIT, Apache-2.0, GPL-3.0, SSPL, and Elastic License v2. We chose AGPL-3.0 as the best balance of openness, community protection, and commercial sustainability.

---

### Q14: Can the license change in the future?

**A:** The AGPL-3.0 code already released cannot be "un-released" or re-licensed retroactively. We do not currently plan to change the license. If we ever did change it for future releases (which would require agreement from all copyright holders), the existing AGPL-3.0 releases would remain under AGPL-3.0.

---

## More Information

- Full license text: [LICENSE](../../LICENSE)
- Contribution process: [CONTRIBUTING.md](../../CONTRIBUTING.md)
- Commercial licensing inquiries: licensing@civicsignals.io
- Legal questions: legal@civicsignals.io
- OSI on AGPL-3.0: https://opensource.org/licenses/AGPL-3.0
