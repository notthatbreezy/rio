# Source and connector selection contract

Choose a source because it supports the confirmed purpose, then choose the least-powerful
access method that can perform the bounded read. A user-supplied file or approved export is
often sufficient. A built-in connector or a Model Context Protocol (MCP) server is optional.
Discovery or configuration is not authorization, and a configured capability is not proof
that the intended account and read operation work.

For every source, confirm: purpose; exact account or location; in-scope topics, dates, and
volume; explicit exclusions; access method; permitted reads and local retention; whether
write capabilities exist but remain unauthorized; capability status and evidence; limits;
and unavailable/restricted-item handling. Mail, calendar, transcript, repository, and
original-file retrieval are separate capabilities. Never store credentials in the vault.

Bootstrap records plans only. It does not read sources, connect accounts, install software,
or test capabilities. Those actions begin at S04 and require separate bounded authorization.
