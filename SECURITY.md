# Security Policy

## Supported Versions

Kollab users should run the latest published release of the 0.5.x series.

For coordinated vulnerability work we currently support the current public release and
its patch upgrades. Older releases may be unsupported for security fixes.

## Reporting a Vulnerability

If you believe you found a security vulnerability, please report it privately:

- Preferred: open a report via GitHub Security Advisories:
  https://github.com/kollaborai/kollab/security/advisories/new
- If advisories are unavailable, open an issue with only non-sensitive details
  asking for a private reporting path: https://github.com/kollaborai/kollab/issues

Please include:

- Affected version(s)
- Clear impact description
- Reproduction steps or proof of concept
- Relevant logs or traces (redact secrets first)
- Any suggested mitigation

## Responsible Disclosure

Please do not publicly disclose vulnerabilities before we have had a chance to
investigate and release a fix.

## Sensitive Data

Do not include secrets or confidential tokens in a report. This includes API keys,
session tokens, OAuth credentials, `.env` contents, and raw authentication cookies.
