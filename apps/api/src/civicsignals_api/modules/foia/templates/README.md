# FOIA Template Library

This directory contains public-records-request template files for the CivicSignals FOIA
tracker (PRD F10.5). Each template is a YAML file with a Markdown body using named
placeholders (`{requester_name}`, `{records_description}`, etc.).

> **LEGAL NOTICE — ALL TEMPLATES ARE DRAFT / PENDING COUNSEL REVIEW**
>
> These templates were assembled from publicly available statutory text and sample
> requests. They have **not** been reviewed by a licensed attorney. Do not treat them
> as legal advice. Verify the current statutory text in your jurisdiction before use.

## Template schema

Each `.yaml` file must contain the following top-level keys:

| Key | Type | Description |
|---|---|---|
| `jurisdiction` | string | Short code, e.g. `US-FOIA`, `CA-PRA`, `TX-PIA` |
| `jurisdiction_name` | string | Human-readable name of the law |
| `state` | string or `null` | Two-letter USPS code (null for federal) |
| `statute` | string | Authoritative citation, e.g. `5 U.S.C. § 552` |
| `deadline_days` | integer | Statutory response deadline (calendar or business days — see `deadline_note`) |
| `deadline_note` | string | Clarification of how deadline is counted, and any extension provisions |
| `fee_waiver_language` | string | Boilerplate for requesting fee waivers under this statute |
| `submission_method_hint` | string | Common submission methods for this jurisdiction |
| `status` | string | MUST be `draft` — all templates await counsel review |
| `placeholders` | list[string] | All placeholder tokens present in `body` |
| `body` | string | Markdown request body with `{placeholder}` tokens |

## Placeholder conventions

| Placeholder | Meaning |
|---|---|
| `{requester_name}` | Full legal name of the requester |
| `{requester_address}` | Mailing address of the requester |
| `{requester_email}` | Email address of the requester |
| `{requester_phone}` | Phone number of the requester (optional) |
| `{requester_organization}` | Requester's organization, if any |
| `{entity_name}` | Name of the agency / public body being requested |
| `{records_officer_name}` | Name of the FOIA / records officer, if known |
| `{records_description}` | Description of the records sought |
| `{date}` | Date the request is submitted |
| `{fee_waiver_basis}` | Basis for fee waiver (e.g. "news media", "educational institution") |

All required placeholders are listed under the `placeholders` key in each template.
`{requester_phone}`, `{requester_organization}`, and `{records_officer_name}` are
optional and can be omitted from the rendered request if not supplied.

## Status

Included templates (8 jurisdictions):

| File | Jurisdiction | Status |
|---|---|---|
| `federal_foia.yaml` | Federal (5 U.S.C. § 552) | draft |
| `ca_pra.yaml` | California Public Records Act | draft |
| `tx_pia.yaml` | Texas Public Information Act | draft |
| `ny_foil.yaml` | New York Freedom of Information Law | draft |
| `fl_pra.yaml` | Florida Sunshine Law (Ch. 119) | draft |
| `wa_pra.yaml` | Washington Public Records Act | draft |
| `il_foia.yaml` | Illinois FOIA | draft |
| `co_cora.yaml` | Colorado Open Records Act | draft |

## Adding more jurisdictions

Approximately 42 additional US state/territory templates remain to be authored
(TODO M2-templates — do not fabricate authoritative statutory text; verify deadlines
and citations against official codified law before adding).

Template naming convention: `{state_abbr_lower}_{abbreviation}.yaml`, e.g.
`oh_pra.yaml` for the Ohio Public Records Act.

To add a template:
1. Copy an existing template as a starting point.
2. Update `jurisdiction`, `jurisdiction_name`, `state`, `statute`, `deadline_days`,
   `deadline_note`, `fee_waiver_language`, and `body`.
3. Set `status: draft` until counsel review is complete.
4. Add an entry to the table above.
5. Tests will automatically pick up the new file and validate its schema.
