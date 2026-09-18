# Confirmed-answer format

Use [sample-answers.json](../fixtures/sample-answers.json) as a shape reference,
not as a pre-approved configuration. All listed fields are required; unknown
fields and duplicate keys are rejected. Set `confirmed` to `true` only after
the user has explicitly approved the completed record.

## Top-level fields

| Field | Value |
| --- | --- |
| `schema_version` | Integer `1`, not a string, boolean, or floating-point value. |
| `primary_purpose` | Nonempty single-line user-confirmed purpose. |
| `useful_first_result` | Nonempty single-line description of useful output. |
| `review_preference` | Nonempty single-line preference, such as reviewing Markdown files. |
| `workspace` | Object described below. |
| `sources` | Nonempty array of source-plan objects described below. |
| `exclusions` | Nonempty array of nonempty single-line category or scope strings. |
| `external_actions` | Exactly `"unauthorized"` for this bootstrap kit. |
| `recovery` | Object containing only `decision`. |
| `confirmed` | Boolean consent flag; bootstrap requires `true`. |
| `confirmed_on` | User-confirmation date as `"YYYY-MM-DD"`. Generated files use the actual creation date separately. |

Strings cannot contain newlines or control characters. Use ordinary descriptions,
not credentials or tokens. Answers are encoded as inert text in generated records.

## Workspace

- `destination`: exact intended vault path. An absolute path is clearest; a
  relative path is resolved relative to the answers file, not the shell.
- `authorize_local_setup`: boolean; must be `true` after approval.
- `authorize_create_if_missing`: boolean. Use `false` if only an already-existing
  empty root is authorized. Use `true` only if creating a missing root is also
  approved. Its immediate parent must already exist.

The CLI's `--root` must resolve to the same destination. Network, link/reparse,
nonempty, package-internal, and other unsafe roots are rejected.

## Each source plan

All these fields are required:

| Field | Value |
| --- | --- |
| `source_and_purpose` | Source name and why it is useful. |
| `account_or_location` | Exact approved account/location or supplied file path; recorded only, never accessed by bootstrap. |
| `in_scope` | Topics, projects, dates/current-state exceptions, and volume boundaries. |
| `out_of_scope` | Explicit excluded locations, relationships, topics, or content. |
| `access_method` | Description such as `supplied_file`, an existing connector, or a planned MCP. A description does not install or verify anything. |
| `allowed_operations` | Nonempty array using the exact supported strings below. |
| `capability_status` | `planned` or `available_unverified` during bootstrap. |
| `limits_and_retention` | Volume/pagination cap, retention decision, and handling of inaccessible or restricted sources. |

Supported operations:

- `read supplied file`
- `read approved source`
- `search approved source`
- `retain a local evidence copy`
- `retain references only`

Every source plan needs at least one read/search operation. Select the appropriate
retention operation to describe the approved handling; do not add mutating
operations. Bootstrap records the plan without performing any of these operations.
`verified` and `blocked` are later capability outcomes, not accepted proof states
for an unexecuted S04.

## Recovery

`recovery.decision` is one of:

- `deferred`: postpone verification; S13 is deferred and recovery remains unverified.
- `verify_later`: leave S13 unstarted for a later separately authorized test.
- `not_required`: explicitly choose the branch that does not require verification;
  S13 is not applicable, not falsely complete.

None of these options configures a backup, inspects a device, or performs a restore.
