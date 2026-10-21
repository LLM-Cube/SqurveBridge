# Domain Docs

This repository uses a single-context layout. These rules apply when engineering skills explore or change the codebase.

## Before exploring

- Read root `GLOSSARY.md` when it exists. If `GLOSSARY-MAP.md` exists, follow its pointers to relevant glossaries.
- Read root `CONTEXT.md` when it exists; it currently contains runtime-evidence vocabulary and remains a domain reference.
- Read ADRs under `docs/adr/` that touch the area being changed.
- If a referenced document is absent, proceed silently. Do not create empty glossaries or ADRs upfront; domain-modeling creates them when terms or decisions are resolved.

## Vocabulary

Use the terminology in the applicable glossary and `CONTEXT.md` in issue titles, proposals, hypotheses, tests, and code. Do not drift to synonyms the documents explicitly avoid.

If a concept is absent, record it as a domain-modeling gap rather than inventing competing names. If documents disagree, surface and resolve the conflict explicitly.

## ADR conflicts

If proposed work contradicts an existing ADR, identify the ADR and explain why the decision should be reopened rather than overriding it silently.
