# San Andrejsala — IRL travel game webapp

Realtime web app for a Jet-Lag-style IRL strategy game across Latvia's regions.
Teams have **Influence Points (IP)** that decay continuously based on how much IP
they hold and which region they're in. Teams unlock regions, bet deposits to
capture them, and complete challenge cards for payouts.

## Stack

- **FastAPI** + Uvicorn (single worker) — REST + WebSockets
- **PostgreSQL** + SQLAlchemy 2.0 (async) — game state
- **Jinja2** server-rendered pages + a little vanilla JS for the live balance
- **Caddy** — automatic HTTPS reverse proxy (production)
- **Docker Compose** — same setup locally and on the Hetzner VM

No Redis (one worker is plenty for ~20 users). No PostGIS (regions are labels,
not GPS coordinates — location is self-reported and senate-adjudicated).

## The money model (from the rules)

Continuous IP decay per minute:

```
final_rate = BASE_TAX * (1 + influence_tax) * (1 + region_tax)
```

- `BASE_TAX` = 0.35 IP/min
- **influence tax** by IP held: 0 → 0%, >500 → 10%, >1000 → 50%, >2000 → 100%, >5000 → 500%
- **region tax**: Rīga 70%, Kurzeme 20%, Latgale 15%, Vidzeme 25%, Zemgale 30%

A background loop ticks every few seconds, applies decay, and pushes each team's
live balance over a WebSocket. The browser also smoothly counts down between
server updates using the rate.

## Quick start (local, on your Mac)

Prereqs: Docker Desktop.

```bash
cp .env.example .env          # edit SECRET_KEY for anything non-local
docker compose up --build
```

Then open http://localhost:8000

The DB is auto-created and **seeded** on first boot with:

- admin login — `admin` / `admin` (change it!)
- 5 teams — `team1`..`team5`, password `changeme`
- 5 regions with their tax rates + a few sample challenges each

## Everyday dev loop

- Edit code → Uvicorn auto-reloads (compose runs it with `--reload`).
- Change a model? For v1 the schema is created with `create_all`. To reset:
  `docker compose down -v && docker compose up --build` (drops the DB volume).
  When you want proper migrations, Alembic is wired up (see `alembic.ini`) —
  run `docker compose exec app alembic revision --autogenerate -m "msg"`.

## Deploy to the Hetzner VM

One-time on the VM: install Docker, point a domain's DNS A-record at the VM IP,
put the domain in `Caddyfile` (replace `:80`), open ports 80/443.

Then from your Mac:

```bash
./deploy.sh user@your-vm-ip
```

It rsyncs the repo up and runs `docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build`.
Caddy fetches HTTPS certs automatically.

## What's deferred to v2 (do manually via admin for now)

- Region-capture passive income (holder earns a share of region tax per team present)
- "Steal" challenges (% of capital swing between teams)
- Automatic daily challenge draws + key-task first-blood 50 IP bonus
