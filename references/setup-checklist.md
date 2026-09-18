# Canonical setup checklist

Generated from `manifests/tasks.json`; do not edit this reference by hand.

| ID | Setup task | Depends on | Completion evidence required |
| --- | --- | --- | --- |
| S01 | Establish purpose and review preferences | None | User confirms a primary use case and what a useful result looks like; review preference recorded. |
| S02 | Agree on workspace, source scope, and permissions | S01 | Exact root is verified empty or absent; its creation/use, a per-source access plan with explicit in/out boundaries, local writes/retention, exclusions, action gates, and recovery responsibility are confirmed. |
| S03 | Establish the fixed starter structure | S02 | Empty-root precondition rechecked immediately before bootstrap; required directories/files and seven templates created without overwriting; contracts recorded; any user-requested deviations approved. |
| S04 | Verify the selected source path | S02 | Selected connector/MCP's account, relevant callable read tools, and one bounded authorized read verified, or a supplied local file is readable. Record unavailable capabilities and read/write exposure. Configuration alone is not proof; connector changes require separate approval. |
| S05 | Implement and test the ingest procedure | S03 | Duplicate-input, changed-version, and interrupted-operation tests pass with safe fixtures; processing states and provenance rules documented. |
| S06 | Implement session-start sweep and triage | S05 | Fixture cases for processed, pending, superseded, skipped, and blocked work are classified correctly without unintended writes. |
| S07 | Establish evidence-backed queries and briefs | S05 | A known-answer query cites evidence; an unknown/conflicting-answer case preserves uncertainty; a meeting brief distinguishes invitees from attendance. |
| S08 | Establish report-only validation | S03, S05, S06 | Metadata, duplicate identity, links, provenance, integrity, and backlog checks report fixture defects without silently repairing them. |
| S09 | Seed the primary work context | S03, S04 | User confirms the focus; actual terminology and authoritative sources are recorded; unknown or disputed deadlines remain explicit. |
| S10 | Approve a bounded pilot | S04, S05, S09 | Source selection and expected output are confirmed, or selection is explicitly delegated within scope. |
| S11 | Execute and review the pilot | S05, S10 | Source handled under the completed procedure; synthesis linked and validated; user accepts or requests specific changes. |
| S12 | Exercise the integrated manual workflow | S06, S07, S08, S11 | Re-run causes no duplicates; resume works; queries and validation behave as designed; fresh startup finds rules and next state. |
| S13 | Verify recovery, if selected | S02, S03 | Restore into a separately approved test location preserves source hashes and useful notes. No backup destination is assumed. |
| S14 | Agree on ongoing operating mode | S12 | User selects manual-only or a defined automation proposal. Limits and recovery status are explicit; proposals are not enabled jobs. |
| S15 | Authorize and test automation, if selected | S04, S12, S14; S13 if recovery verification was made a requirement | Exact configuration approved; dry run, duplicate suppression, overlap guard, checkpointing, and failure reporting tested; first enabled run verified. |
| S16 | Handoff and readiness statement | S12, S14; S15 for automated mode; S13 if required | User sees actual completed scope, unresolved/deferred items, source limitations, and how to resume, pause, or revoke authorization. |
