# Contributing

Rosie is a personal household project, but issues and pull requests are welcome.

## Getting set up

```sh
python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'
.venv/bin/pytest -q
```

No external services are needed for the tests. Home Assistant, Prometheus, Forgejo,
ntfy and the Anthropic API are all mocked.

## Workflow

1. Open an issue first for anything bigger than a small fix, so we can agree on the
   approach. Use the bug or feature template.
2. Make one branch and one pull request per change, kept small enough to review in one sitting.
3. Before pushing:
   ```sh
   .venv/bin/ruff check src tests
   .venv/bin/ruff format src tests
   .venv/bin/pytest -q
   ```
   CI runs the same checks and builds the Docker image.
4. Add or update tests for any behavior change. Anything that touches auth, `netfetch.py`,
   Home Assistant actions or AI prompts needs a test that exercises the refusal path, not
   just the success path.

## Ground rules

- **No secrets in git.** Configuration goes in `.env` (see `.env.example`).
- **New device controls are allowlist entries** in `home.toml`, not new code paths.
- **AI output never acts on its own.** A model may draft; a person confirms.
- **Keep the front end build-free:** plain JavaScript, no framework, no bundler.

## Issue numbers in the code

Comments such as `(#26)` refer to the private tracker where Rosie started, not to
this repository's issues.
