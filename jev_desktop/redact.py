"""Remove secrets from screen text before it is sent to any model provider."""

import re

MASK = "[REDACTED]"

TOKENS = re.compile(
    r"sk-(?:ant-|proj-|or-v1-)?[A-Za-z0-9_\-]{16,}"  # Anthropic, OpenAI, OpenRouter
    r"|apikey_[A-Za-z0-9_]{16,}"  # TypeSafe
    r"|apify_api_[A-Za-z0-9]{16,}"
    r"|fc-[a-f0-9]{24,}"  # Firecrawl
    r"|AIza[0-9A-Za-z_\-]{30,}|AQ\.[A-Za-z0-9_\-]{20,}"  # Google
    r"|AKIA[0-9A-Z]{16}"  # AWS
    r"|gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}"
    r"|xox[abprs]-[A-Za-z0-9\-]{10,}"  # Slack
    r"|eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}"  # JWT
    r"|-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?(?:-----END [A-Z ]*PRIVATE KEY-----|$)"
)
# NAME=value / NAME: value where the name says it is secret.
ASSIGNED = re.compile(
    r"(?im)^([ \t]*[\w.\-]*(?:key|token|secret|password|passwd|pwd|credential|auth)[\w.\-]*[ \t]*[:=][ \t]*)(\S.*)$"
)
# Long unbroken high-entropy strings (mixed letters and digits) are treated as secrets.
LONG = re.compile(r"\b(?=[A-Za-z0-9_\-]*\d)(?=[A-Za-z0-9_\-]*[A-Za-z])[A-Za-z0-9_\-]{32,}\b")


def redact(text):
    if not text:
        return text
    text = TOKENS.sub(MASK, text)
    text = ASSIGNED.sub(lambda m: m.group(1) + MASK, text)
    return LONG.sub(MASK, text)
