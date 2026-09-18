---
name: rio-setup
description: Set up a new empty second brain with Rio's templates and validated bootstrap.
disable-model-invocation: true
---

# Rio setup

Invoke this skill only when the user explicitly asks to initialize a new empty root. It is
not a migration, repair, reset, or ongoing-ingestion skill.

Resolve paths from this skill's directory inside the installed whole package. Ask the S01
and S02 questions progressively using `../../references/operating-contract.md` and
`../../references/source-selection.md`. Reuse volunteered answers and consult
`../../references/answers-format.md` for the supported input values instead of
guessing or reverse-engineering the parser. Require an operator-confirmed answers JSON file;
never treat `../../fixtures/sample-answers.json` as consent. Run
`../../scripts/bootstrap.py`, then the read-only `../../scripts/validate.py`, and stop after
S03. Do not access sources, accounts, or connectors during bootstrap. Do not enable external
actions or automation. Install or copy the whole kit; this skill directory is not standalone.
