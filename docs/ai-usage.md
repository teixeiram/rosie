# How AI was used

AI shows up in two separate places in this project, and both are disclosed here.

## 1. AI inside the app

Rosie calls Anthropic's Claude API for three features:

- **Recipe import:** Claude Opus by default (`ROSIE_MODEL`). It reads a page, pasted text,
  screenshots or a PDF and returns a recipe in a fixed JSON schema.
- **Recipe chat:** Claude Sonnet by default (`ROSIE_CHAT_MODEL`). It answers questions over the
  saved collection, using prompt caching because the whole collection is sent each time.
- **Developer chat:** questions about Forgejo issues. It can return *draft*
  issues and comments, which a person must confirm before anything is posted.

[security.md](security.md) explains how these calls are constrained: input treated
as untrusted, schema-forced output, a spending cap, and human confirmation.

## 2. AI as a development tool

I built Rosie with **Claude Code** (Anthropic's coding agent) as a pair programmer.
The division of work:

- **Me:** gathering requirements from my household, writing the issues, deciding the
  architecture and security model (allowlists, no sign-up page, LAN-only exposure,
  the spending cap), reviewing every change, testing on real phones, and deploying.
- **Claude Code:** drafting implementation code, tests and documentation from those
  issues, and running the test suite and linter. Its output was reviewed and often
  corrected before merging.

Preparing this public copy was also done with Claude Code. That covered removing
home-specific details (addresses, hostnames, device names, family calendars),
making the home domain a setting, and writing this README, the architecture and
security docs, the CI workflow and the contribution guidelines. I reviewed all of it.

I am responsible for everything in this repository. AI wrote some of the code,
but none of it was accepted unreviewed.
