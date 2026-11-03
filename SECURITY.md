# Security policy

## Reporting a vulnerability

Please report vulnerabilities privately through
[GitHub's private vulnerability reporting](https://github.com/AndreMuraro/warehouse-context-mcp/security/advisories/new),
not in public issues. Include the SQL or steps that reproduce the problem. You can expect
an acknowledgement within a few days.

In scope: anything that lets a client of the server write to a warehouse, read outside
the configured schema, read a column flagged as PII, read files, environment variables or
network resources, exhaust resources past the configured limits, or obtain credentials.

## Supported versions

Only the latest release receives fixes.

## How the server is designed to fail safe

See [docs/threat-model.md](docs/threat-model.md) for the assets, trust boundaries and
controls, and the ADRs in [docs/adr](docs/adr) for the reasoning behind them.
