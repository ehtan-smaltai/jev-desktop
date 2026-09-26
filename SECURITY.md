# Security

## Your API keys

- `jevd setup` stores keys in `%APPDATA%\jev-desktop\.env`, outside any project folder. Nothing in this repo
  needs a key.
- `.env` and run traces (`runs/`) are git-ignored. Traces are written to `%APPDATA%\jev-desktop\runs\` and never
  contain keys.
- Screen text is redacted (API keys, tokens, `password=`-style values) before it is sent to a model or printed.
- Every push and pull request is scanned for secrets (gitleaks in CI, plus GitHub push protection).

**Never paste keys, `.env` contents, or unredacted traces into issues or pull requests.** If you did, revoke
the key at the provider immediately; deleting the comment does not un-leak it.

## Reporting a vulnerability

Please use GitHub's private vulnerability reporting (Security tab -> "Report a vulnerability") rather than a
public issue.
