# Support

This document explains where to ask questions, report problems, and request
changes for Kollab.

## Where To Get Help

- **Bugs:** open a GitHub issue with a minimal reproduction, environment details,
  expected behavior, and actual behavior.
- **Feature requests:** open a GitHub issue describing the workflow you want to
  improve and what would make the change complete.
- **Security vulnerabilities:** do not open a public issue. Follow
  [SECURITY.md](SECURITY.md).
- **Release and contribution process:** see [CONTRIBUTING.md](CONTRIBUTING.md)
  and [docs/release-process.md](docs/release-process.md).

## What To Include

For bugs, include:

- `kollab --version`
- operating system, shell, terminal emulator, and Python version
- provider/profile involved, if relevant
- the smallest command sequence that reproduces the problem
- redacted logs or screenshots, if useful

## What Not To Include

Do not post:

- API keys or provider tokens
- OAuth credentials, cookies, or session files
- raw private conversation logs or transcripts
- contents of `.env` files
- local runtime data from `~/.kollab`
- customer, employer, or private repository data

## Support Expectations

Kollab is maintained as beta software. The project prioritizes:

1. security reports
2. install, packaging, and startup regressions
3. data-loss or conversation-persistence bugs
4. provider/profile compatibility issues
5. clear, reproducible CLI/TUI defects
6. documented feature requests with acceptance criteria

Maintainers may close issues that cannot be reproduced, contain sensitive data,
or do not include enough information to investigate.
