---
name: zju-fulltext-access
description: Find lawful, traceable routes to academic full text and supplementary files, including open access, publisher or repository links, Zhejiang University CARSI/WebVPN/RVPN access, and manual library help. Use when a user has a DOI, PMID, title, or citation and needs the article or supplement. Never use to bypass paywalls, automate bulk downloading, share credentials, or store passwords.
---

# ZJU Full-Text Access

Find the least-privileged lawful access route and leave a reproducible access note. Never treat a failed automated route as permission to circumvent access control.

## Four-Route Workflow

1. Confirm the target using DOI, PMID, title, authors, year, or an authoritative landing page. Resolve citation ambiguity before access attempts.
2. Try open access: publisher OA copy, PubMed Central, institutional or subject repository, accepted manuscript, preprint, or explicitly public supplement.
3. Try official metadata or content APIs only within their documented terms, rate limits, and entitlement rules. Do not turn a single-item task into a crawler.
4. For licensed resources, read `references/zju-library-access.md` and direct the user through the current Zhejiang University library entry, CARSI, WebVPN, or RVPN. The user authenticates interactively; never request or persist a password, cookie, token, or QR session.
5. If automation is unavailable, provide the exact manual route: target citation, database or publisher, expected authentication step, and where to ask the library for help or document delivery.
6. Record route tried, URL, access status, access date, version obtained, supplement status, and any remaining blocker.

When a user asks to save or supplies a credential, refuse collection or persistence and still give the safe interactive route: open the official Zhejiang University library/CARSI/WebVPN/RVPN entry in the user's own browser, authenticate there, and return only the citation, DOI, or authorized landing URL needed for the next step. Never request the resulting cookie or session token. If a live credential was pasted, recommend removing it from the conversation or local fixture and rotating it when exposure is plausible.

Use `references/access-routes.md` for the decision tree. For a batch of locally prepared metadata records, `scripts/classify_access.py` may triage likely routes; its output is advice, not proof of entitlement.

## Safety Boundary

- Never bypass a paywall, CAPTCHA, robot control, license limit, IP restriction, or authentication boundary.
- Never share, collect, log, embed, or save Zhejiang University unified-identity credentials.
- Never perform systematic or bulk downloads. Pause and route the user to the library when the intended volume could violate license terms.
- Do not upload publisher PDFs to public services without permission.
- A DOI resolver or search-result link is not evidence that full text is open. Label access status `verified`, `likely`, `subscription_required`, or `unresolved`.

## Output Contract

Return: verified target, recommended route, numbered manual steps, access/compliance status, supplementary-material status, and fallback. If the route fails, explain the barrier without attempting evasion.
