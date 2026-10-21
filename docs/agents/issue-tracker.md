# Issue tracker: GitHub

Issues and specs live in GitHub Issues for `LLM-Cube/SqurveBridge`. Use the `gh` CLI with `--repo LLM-Cube/SqurveBridge` for issue operations. Confirm `git remote -v` if repository identity is unclear.

Existing `.scratch/` files are historical working notes, not the active issue tracker. Do not automatically publish them or create duplicate tickets.

## Conventions

- Create: `gh issue create --repo LLM-Cube/SqurveBridge --title "..." --body-file <file> --label needs-triage`.
- Read: `gh issue view <number> --repo LLM-Cube/SqurveBridge --json number,title,body,labels,comments,assignees,state`.
- List: `gh issue list --repo LLM-Cube/SqurveBridge --state open --json number,title,body,labels,assignees`, adding label filters and pagination as needed.
- Update: `gh issue edit <number> --repo LLM-Cube/SqurveBridge --body-file <file>`.
- Comment: `gh issue comment <number> --repo LLM-Cube/SqurveBridge --body-file <file>`.
- Labels: `gh issue edit <number> --repo LLM-Cube/SqurveBridge --add-label "..." --remove-label "..."`. Use [triage-labels.md](triage-labels.md).
- Close: `gh issue close <number> --repo LLM-Cube/SqurveBridge`. Record the result and validation before closing.

Write multiline bodies and comments to temporary files and use `--body-file`, preserving newlines and literal text. Read the current issue before editing its body and preserve unrelated content. Follow repository publication privacy rules for issue bodies and attachments.

## When a skill says "publish to the issue tracker"

Create or update the relevant GitHub issue. Search for existing tickets first. A spec belongs in its issue body; implementation tickets are separate issues linked to the spec.

## When a skill says "fetch the relevant ticket"

Read the GitHub issue, including labels, comments, assignees, and state. Resolve bare numbers against this repository. Issues and PRs share a number space; verify the object type when ambiguous.

## Pull requests as a triage surface

**PRs as a request surface: no.**

## Parent and child issues

Supporting clients can create a child with `gh issue create --repo LLM-Cube/SqurveBridge --parent <parent> --title "..." --body-file <file>`, or attach an existing child with `gh issue edit <parent> --repo LLM-Cube/SqurveBridge --add-sub-issue <child>`.

For older clients, get the child's numeric database ID with `gh api repos/LLM-Cube/SqurveBridge/issues/<child> --jq .id`, then use `gh api --method POST repos/LLM-Cube/SqurveBridge/issues/<parent>/sub_issues -F sub_issue_id=<child-db-id>`.

If native sub-issues are unavailable, keep an ordered task list in the parent body and a `Part of #<parent>` line in each child. Permission or authentication failures require resolution rather than silent fallback.

## Wayfinding operations

Used by `/wayfinder`. A map is one issue with separate child issues as tickets.

- **Map:** an issue labelled `wayfinder:map`, with Notes / Decisions-so-far / Fog in its body.
- **Child:** link it to the map as above; label it `wayfinder:research`, `wayfinder:prototype`, `wayfinder:grilling`, or `wayfinder:task`.
- **Blocking:** use native dependencies. Supporting clients can run `gh issue edit <child> --repo LLM-Cube/SqurveBridge --add-blocked-by <blocker>`. For older clients, obtain the blocker's database ID and run `gh api --method POST repos/LLM-Cube/SqurveBridge/issues/<child>/dependencies/blocked_by -F issue_id=<blocker-db-id>`. API payloads use database IDs, not issue numbers or node IDs.
- **Fallback blocking:** if native dependencies are unavailable, put `Blocked by: #<n>, #<n>` near the top of the child body. A ticket is unblocked only when every blocker is closed.
- **Frontier:** read the map's ordered children or task list; select an open, unassigned child with no open blockers. Check `issue_dependencies_summary.blocked_by` through `gh api`, or resolve every fallback blocker. First in map order wins.
- **Claim:** re-read the ticket, then run `gh issue edit <number> --repo LLM-Cube/SqurveBridge --add-assignee @me` before starting work. Re-check assignment before proceeding.
- **Resolve:** comment with the answer and validation, close the child, then append a short result and issue link to the map's Decisions-so-far, preserving its remaining body.

Before first wayfinding use, create any missing `wayfinder:*` labels listed above. The five canonical triage roles remain separate from ticket-type labels.
