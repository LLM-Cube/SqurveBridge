# Architecture Decision Records

This directory contains Architecture Decision Records (ADRs) for SqurveBridge.

## About ADRs

Architecture Decision Records document significant architectural decisions made in the project, including the context, decision, and consequences of each choice.

## Numbering

ADRs are numbered sequentially starting from 0001. Each ADR filename follows the pattern:

```
<number>-<descriptive-title>.md
```

Example: `0001-demo-four-layer-eval-l-strip.md`

## Status Values

- **Proposed**: Decision is under consideration
- **Accepted**: Decision has been approved and is being implemented
- **Implemented**: Decision has been fully implemented
- **Deprecated**: Decision is no longer relevant or has been superseded
- **Superseded**: Replaced by a newer ADR (reference the new ADR number)

## Structure

Each ADR should include:

- **Title**: Clear, descriptive title of the decision
- **Status**: Current status (see above)
- **Date**: Date the ADR was created or last updated (YYYY-MM-DD)
- **Scope**: Components, modules, or areas affected
- **Context**: Background and motivation for the decision
- **Decision**: The actual decision made
- **Consequences**: Positive impacts, negative impacts/tradeoffs, and follow-up work
- **Non-goals**: What this ADR explicitly does not address
