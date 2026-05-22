# Data Processing Addendum (DPA)

> **DRAFT — Pending legal review. Not legally effective until reviewed by counsel and published.**  
> This DPA is provided as a template. It becomes effective when countersigned or when you accept it through the in-product DPA acceptance flow.  
> **Last updated:** [DATE TO BE SET AT PUBLICATION]

This Data Processing Addendum ("DPA") is entered into between CivicSignals, Inc. ("Processor") and the organization identified in your CivicSignals account ("Controller") and supplements the [Terms of Service](terms-of-service.md).

This DPA applies when CivicSignals processes personal data on your behalf as your data processor under applicable data protection law, including the EU General Data Protection Regulation (GDPR 2016/679), the UK GDPR, and the California Consumer Privacy Act (CCPA).

---

## 1. Definitions

- **"Applicable Data Protection Law"** means GDPR, UK GDPR, CCPA, and any other data protection law applicable to the processing under this DPA.
- **"Controller"** means the entity (you) that determines the purposes and means of processing personal data.
- **"Processor"** means CivicSignals, Inc., which processes personal data on behalf of the Controller.
- **"Personal Data"** has the meaning given in Applicable Data Protection Law.
- **"Processing"** has the meaning given in Applicable Data Protection Law.
- **"Sub-processor"** means any third party engaged by CivicSignals to process personal data in connection with the Service.
- **"Standard Contractual Clauses" or "SCCs"** means the clauses issued by the European Commission under Decision 2021/914 for the transfer of personal data to third countries.

---

## 2. Roles and Instructions

### 2.1 Role of Parties

Each party will comply with Applicable Data Protection Law in its respective role. CivicSignals acts as:

- **Processor** with respect to Customer Data (your ICP configurations, saved searches, pipeline data, workspace member data, FOIA records) that you submit to the Service.
- **Independent Controller** with respect to data it collects for its own purposes (account administration, billing, security, analytics) as described in the Privacy Policy.

### 2.2 Processing Instructions

CivicSignals will process Personal Data only on your documented instructions and as described in this DPA and the Terms of Service. The primary instruction is to provide the Service as described.

### 2.3 No Further Use

CivicSignals will not use Customer Data to train machine learning models, advertise, or process for any purpose other than providing and improving the Service.

---

## 3. Description of Processing

| Element | Description |
|---|---|
| **Subject matter** | Provision of the CivicSignals platform (signal intelligence, CRM integration, FOIA tracking) |
| **Duration** | For the term of your subscription and the 90-day post-cancellation data retention window |
| **Nature and purpose** | Storage, retrieval, analysis, and export of Customer Data to provide the Service |
| **Type of Personal Data** | Workspace member names and email addresses; ICP configurations; FOIA request content; pipeline items; customer-provided contact data; API tokens (hashed) |
| **Categories of data subjects** | Your organization's employees and contractors who are workspace members |

---

## 4. Confidentiality

CivicSignals will ensure that persons authorized to process Personal Data are bound by appropriate confidentiality obligations.

---

## 5. Security Measures

CivicSignals will implement and maintain technical and organizational measures appropriate to the risk to protect Personal Data against unauthorized or unlawful processing, accidental loss, destruction, or damage, including the measures described in the [Security page](security.md) and our OWASP ASVS Level 2 self-assessment. These include:

- Encryption of Personal Data at rest (AES-256) and in transit (TLS 1.2+)
- Access controls and authentication (RBAC, MFA for admins)
- Multi-tenant workspace isolation at the ORM layer
- Audit logging of access and processing actions
- Vulnerability management (Dependabot, Trivy, gitleaks)
- Annual external penetration testing (from month 6 post-GA)
- Incident response and breach notification procedures

---

## 6. Sub-processors

### 6.1 Current Sub-processors

CivicSignals uses the sub-processors listed at [docs/legal/subprocessors.md](subprocessors.md) (published at `civicsignals.io/legal/subprocessors`). CivicSignals imposes data protection obligations on all sub-processors substantially equivalent to those in this DPA.

### 6.2 Changes to Sub-processors

CivicSignals will provide 30 days' advance notice of changes to the sub-processor list via email to the workspace's billing contact and by updating the published list. If you reasonably object to a new sub-processor on data protection grounds, contact privacy@civicsignals.io within 30 days. We will work in good faith to address your concerns or allow you to terminate the Service without penalty.

---

## 7. Data Subject Rights

CivicSignals will promptly notify you of any data subject rights requests (access, erasure, portability, objection, restriction) received directly from your data subjects that relate to Customer Data, and will cooperate with you to respond as required by law. CivicSignals will not respond to such requests independently without your authorization, except to direct the individual to you.

---

## 8. Data Breach Notification

CivicSignals will notify you without undue delay, and in any case within 72 hours, of becoming aware of a Personal Data breach affecting Customer Data. Notification will be sent to the workspace's security contact email address. The notification will include, to the extent available: nature of the breach, categories and approximate number of data subjects and records affected, likely consequences, and measures taken or proposed to address the breach.

---

## 9. Data Protection Impact Assessment

CivicSignals will provide reasonable assistance to you in carrying out data protection impact assessments (DPIAs) or prior consultations with supervisory authorities where required by Applicable Data Protection Law, to the extent such assistance requires information in CivicSignals's possession.

---

## 10. Deletion and Return of Data

Upon termination of the Terms of Service or your written request, CivicSignals will:

1. Make Customer Data available for export through the in-product export function for 90 days after termination.
2. After the 90-day recovery window, permanently delete Customer Data from all systems, including backups (deletion from backups may take up to 35 days due to backup rotation cycles).
3. On request, provide a written certification of deletion.

---

## 11. Audits and Compliance

CivicSignals will make available to you, on reasonable request (and subject to a confidentiality agreement), the information reasonably necessary to demonstrate compliance with this DPA, including:

- This DPA itself
- Relevant third-party audit reports (SOC 2, once available)
- Security questionnaire responses

CivicSignals may satisfy audit rights by providing current certifications or audit reports rather than granting direct audit access. If you require a direct on-site audit, contact privacy@civicsignals.io to discuss scheduling and cost-sharing.

---

## 12. International Data Transfers

### 12.1 Transfers from the EEA/UK

If you are established in the European Economic Area or United Kingdom and transfer Personal Data to CivicSignals for processing in the United States, such transfers are subject to the EU Standard Contractual Clauses (Module 2: Controller to Processor) issued under Commission Decision 2021/914, which are incorporated by reference. In the event of a conflict between the SCCs and this DPA, the SCCs prevail for EEA/UK transfers.

### 12.2 Transfers by CivicSignals to Sub-processors

CivicSignals ensures that international transfers to its sub-processors are covered by appropriate transfer mechanisms (SCCs or adequacy decision) as applicable.

---

## 13. CCPA

If Applicable Data Protection Law includes CCPA:

- CivicSignals processes Personal Data as a "service provider" under CCPA.
- CivicSignals will not sell or share (as defined in CCPA) Personal Data it receives from you.
- CivicSignals will not retain, use, or disclose Personal Data for any commercial purpose other than providing the contracted services.
- CivicSignals will cooperate with you to enable you to fulfill CCPA data subject rights requests.

---

## 14. Governing Law

This DPA is governed by the law specified in the Terms of Service (State of Delaware), except that the SCCs are governed by the law of the relevant EU member state or the UK as applicable.

---

## 15. Order of Precedence

In the event of a conflict: SCCs (for EEA/UK transfers) > this DPA > Terms of Service, with respect to the processing of Personal Data subject to Applicable Data Protection Law.

---

## 16. Execution

This DPA is effective upon:
- Your electronic acceptance through the in-product DPA acceptance flow (where available); or
- Counter-signature by authorized representatives of both parties.

For Enterprise customers requiring a signed DPA, contact privacy@civicsignals.io.

---

**CivicSignals, Inc.**  
Signed: [AUTHORIZED SIGNATORY]  
Title: [TITLE]  
Date: [DATE]
