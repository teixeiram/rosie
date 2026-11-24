# Deploying Rosie

This is how Rosie runs in my home lab: a Raspberry Pi 5 running Docker, with Caddy
as the HTTPS reverse proxy. Any Linux host with Docker works the same way.

A checkout without a `.env` runs as `homeapp-dev` on port 8199. Running
`docker compose up` in a development checkout therefore can't replace the
deployed app, which sets `APP_INSTANCE` and `APP_PORT` in its `.env`.

## First time

```sh
git clone https://github.com/teixeiram/rosie.git ~/docker/rosie
cd ~/docker/rosie
cp .env.example .env && chmod 600 .env
mkdir -p data   # create it yourself, or Docker creates it owned by root
# Fill in .env (each setting is explained there). Leave a section's settings
# empty to keep that section off.
docker compose up -d --build
docker compose exec app python -m rosie.manage add-user <name> "<Name>" admin
docker compose exec app python -m rosie.manage add-user <name> "<Name>" member
```

The container listens only on `127.0.0.1`. Put a reverse proxy with TLS in front
of it. With Caddy:

```
@rosie host rosie.example.com
handle @rosie {
	reverse_proxy 127.0.0.1:8098
}
```

In my setup the hostname is in public DNS but resolves to a private address, and
the certificate comes from a DNS-01 challenge. That way phones get real HTTPS (which the
PWA install and Secure cookies need), but the app is unreachable from the internet.
Add that domain to `ROSIE_PRIVATE_DOMAINS` so recipe import refuses links to it.

## Home (Home Assistant)

1. In Home Assistant, go to your profile → Security → Long-lived access tokens → Create token,
   and name it after this app.
2. Put it in `.env` as `HA_TOKEN=...` (never in git), then run `docker compose up -d`.
3. `home.toml` lists what the app may show and control. Edit it and rebuild to
   change the tiles. Every action is logged in the `home_actions` table.

To turn Home off again, delete the token in Home Assistant, or empty `HA_TOKEN`.

## Update

```sh
cd ~/docker/rosie && git pull && docker compose up -d --build
```

## Phones

Open the app's URL and sign in, then:
- iPhone: Share → Add to Home Screen.
- Android: menu → Install app. After that, sharing a recipe page or text to the app opens it in Kitchen.

## Monitoring

What I run alongside it, in the separate monitoring stack:
- A Prometheus blackbox probe of `https://<host>/healthz`.
- An alert when the container disappears: `absent(docker_container_info{name="homeapp"})`.

## Data

All app data is in `./data/app.db` (SQLite: users, sessions, recipes, lists, API usage).
Back up the `data/` directory.
