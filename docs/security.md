# Security model

Rosie can turn on lights, change the thermostat, post to a git server and spend
money on an AI API. The people using it are family, but the content it handles
is not trusted: web pages, pasted text, screenshots and issue comments all come
from outside. This page lists what could go wrong and what stops it.

## Assets and threats

| Asset | Threat | Mitigation | Where |
|---|---|---|---|
| Accounts | Password guessing | No sign-up page. Passwords hashed with scrypt. 5 failures lock the username for 15 minutes. | `auth.py` |
| Sessions | Theft, CSRF | Random tokens stored hashed, in an HttpOnly + Secure + SameSite=Strict cookie. State-changing calls need `X-Requested-With: fetch` and a same-origin `Origin`. Changing a password ends every session. | `auth.py`, `app.py` |
| Admin features | A member escalating privileges | Roles checked on the server on every route. Admin-only tiles and the whole Developer section return 403 to members. | `auth.py`, `sections/*` |
| The house | Unintended device control | The `home.toml` allowlist: entity, permitted actions, and thermostat min/max are all checked on the server. Risky tiles ask for confirmation. Garage doors are status-only. Every action is logged. | `sections/home.py` |
| Home network | SSRF through recipe import | Only http(s) on ports 80/443, with no credentials in the URL. Names under `.local`, `.lan`, `.internal`, `.home.arpa` and `ROSIE_PRIVATE_DOMAINS` are rejected. Every resolved address must be public (not private, loopback, link-local, CGNAT/VPN or IPv4-mapped). The check runs again on every redirect and against the socket's real peer address. | `netfetch.py` |
| AI behaviour | Prompt injection from pages or issues | Untrusted text is sent inside `<page>` / `<issue>` tags with an instruction to treat it as data. Output must match a JSON schema. Markdown sent to the kitchen display is built by code, not the model. The model can only *draft* issues and comments, and a person confirms each one. | `recipe_ai.py`, `dev_chat.py` |
| Money | Runaway API spend | Every call's tokens and cost are logged in `api_usage`. Imports stop when the month reaches `ROSIE_MONTHLY_CAP_USD`. The Kitchen screen shows the month's spend. | `usage.py` |
| Uploads | Malformed or oversized files | Image and PDF types are checked by magic bytes, not file extension, and each upload has a size limit. | `sections/kitchen.py` |
| Browser | XSS, clickjacking | A strict CSP (no inline scripts or styles), `frame-ancestors 'none'`. The front end builds the DOM with text nodes, not `innerHTML`, for user content. | `app.py`, `static/app.js` |
| Secrets | Leaking into git or the browser | Secrets are only in `.env` (gitignored, `chmod 600`). Settings fields holding secrets are excluded from `repr`. No secret is ever sent to the client. | `config.py` |
| Exposure | Internet attackers | The container binds to `127.0.0.1`. The reverse proxy's hostname resolves to a private address, so the app is reachable only at home or over the VPN. | `compose.yaml`, [deploy.md](deploy.md) |

## Tests that guard these

The pytest suite covers:
- `test_auth.py`: lockout, logout, a password change ending sessions, the CSRF
  header requirement, security headers, and one broken section not taking the app down.
- `test_developer.py` and `test_home.py`: member vs. admin access, allowlist
  enforcement, refused actions, and the action log.
- `test_netfetch.py`: blocked URLs, including redirects to private addresses and
  DNS answers that mix public and private addresses.

## Known limits

- One household, one SQLite file. There is no multi-tenant isolation.
- Lockout is per username, not per IP address. Someone on the LAN could lock an account on purpose.
- The JSON schema prevents format attacks, but not a page that lies about its
  ingredients. Imports are previewed and edited before saving for that reason.
