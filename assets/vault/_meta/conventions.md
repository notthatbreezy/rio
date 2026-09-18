# Conventions

## Names and routing

Knowledge-page filenames use lowercase ASCII kebab-case. Route unresolved material to
`00-Inbox/`, entities and concepts to `10-Notes/`, finite outcomes to `20-Projects/`,
ongoing responsibilities to `30-Areas/`, references to `40-Resources/`, and archived
pages to `50-Archive/<original-relative-path>`.

## Knowledge-page schema

Live pages use one-line, JSON-compatible YAML values: quoted strings, JSON arrays, or `null`.
Required fields are `id`, `title`, `kind`, `status`, `created`, `updated`, and `tags`.
IDs remain stable and use `<kind>-<YYYYMMDD>-<slug>`. Kinds are `inbox`, `entity`,
`concept`, `project`, `area`, `resource`, or `moc`; statuses are `draft`, `active`,
or `archived`. Dates are quoted UTC `YYYY-MM-DD` values. The first heading matches `title`.

Entities add `entity_type`: `person`, `team`, `product`, or `organization`. Projects add
`outcome`, `target_state`, and `target_date`. Target state is `unknown`, `proposed`,
`confirmed`, or `disputed`; unknown requires `null`. Archived pages retain kind and ID and
add `archived_on` and `archive_reason`.

## Evidence and processing

Source IDs use `src-<YYYYMMDDTHHMMSSZ>-<slug>`. Captures use
`raw/<channel>/<YYYY-MM-DDTHHMMSSZ>-<slug>.<extension>` and exclusive creation. Preserve
original bytes, describe representations and limitations, and use SHA-256 for retained
bytes. Deduplicate by upstream identity plus version or content hash, not title.

Processing states are `pending`, `processed`, `blocked`, `skipped`, and `superseded`.
Mutable processing state stays separate from immutable evidence and the append-only log.
The procedure is: check scope, identify source/version, acquire permitted evidence,
register, synthesize, connect, validate, record outcome.

## Links, validation, and archive

Every live page must be reachable from the index or knowledge map. Use relative Markdown
links. Report validation defects without silently repairing evidence. Archive only on user
direction or supported completion/dormancy evidence; repair links and active indexes.
