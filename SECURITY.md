# Security policy

tracecost reads trace export files you point it at and the price table bundled in the package
(or one you pass with `--prices`). It writes only to stdout, or to a file you name. It makes no
network requests, starts no processes, sends no telemetry, and never modifies the input files.

Trace exports can contain prompts, completions and user identifiers. tracecost only reads token
counts, model names, ids and cost attributes, and its JSON output contains only those fields, but
the input files themselves are yours to protect: treat them like logs.

## Reporting a vulnerability

Please report vulnerabilities privately through GitHub: open the **Security** tab of this
repository and choose **Report a vulnerability**. Do not open a public issue.

You can expect an acknowledgement within a week. Fixes are released as a new patch version
and noted in [CHANGELOG.md](CHANGELOG.md).

## Supported versions

Only the latest release receives fixes.
