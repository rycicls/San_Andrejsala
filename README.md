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

## Deploy to Hetzner

The prod stack is `app` + `db` + **Caddy** (auto-HTTPS reverse proxy). Only Caddy
is exposed (80/443); the app and Postgres are not reachable from the internet.

### 1. Provision the VM
- Create a Hetzner Cloud VM (Ubuntu 22.04/24.04). Note its public IP.
- **(Recommended)** Point a domain's DNS **A record** at that IP. HTTPS needs a
  domain — and team passwords are sent at login, so you want HTTPS.
- Open the firewall for **22, 80, 443** (Hetzner Cloud Firewall or `ufw`).

Everything below runs **on the VM itself** — SSH in once, do all of this there.
There's no rsync-from-Mac step; the VM pulls its own code from GitHub.

### 2. Install Docker + clone the repo (once)
```bash
ssh user@VM_IP

curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker "$USER"   # log out/in so `docker` works without sudo

git clone https://github.com/rycicls/San_Andrejsala.git andrejsala
cd andrejsala
```

### 3. Create `.env` (once, on the VM)
Secrets live only here — `git pull` / `deploy.sh` never touch this file.
```bash
cp .env.example .env
# edit .env — CHANGE the marked values:
#   POSTGRES_PASSWORD  -> a strong password
#   DATABASE_URL       -> same user/password/db
#   SECRET_KEY         -> openssl rand -hex 32
#   ADMIN_PASSWORD     -> your admin password
#   SITE_ADDRESS       -> your domain (e.g. game.example.com), or :80 for IP-only
nano .env
```

### 4. Deploy (first time and every update, from the VM)
```bash
./deploy.sh
```
It `git pull`s the latest code, then runs
`docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build`.
On first boot the DB is created + seeded automatically; with a real `SITE_ADDRESS`
domain, Caddy fetches HTTPS certs on its own.

If you've made local edits on the VM you don't want overwritten by `git pull`,
run `./deploy.sh --no-pull` instead — it deploys the working tree as-is.

Open `https://your-domain` (or `http://VM_IP` if you left `SITE_ADDRESS=:80`).

### Operating it (also on the VM, from the `andrejsala` directory)
```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml logs -f app
docker compose -f docker-compose.yml -f docker-compose.prod.yml ps
# reset everything (wipes the DB + re-seeds):
docker compose -f docker-compose.yml -f docker-compose.prod.yml down -v
```

> **Note:** the admin password is only seeded on a *fresh* DB. To change it later
> see the "admin password" note — it's a `docker exec` one-liner or a DB reset.

### Backups
`scripts/backup.sh` dumps the DB (gzipped) to `./backups` and keeps the newest 48
files. Losing IP balances / captured regions mid-game is unrecoverable, so run it
on a schedule during the event. Add a cron entry on the VM:
```bash
crontab -e
# every 30 min (48 files = last 24h):
*/30 * * * * /home/USER/andrejsala/scripts/backup.sh >> /home/USER/andrejsala/backups/backup.log 2>&1
```
Restore a dump:
```bash
gunzip -c backups/andrejsala-YYYYMMDD-HHMMSS.sql.gz \
  | docker compose -f docker-compose.yml -f docker-compose.prod.yml exec -T db psql -U game -d andrejsala
```

## V2 mechanics (implemented)

- **Region-capture passive income** — a region's holder earns `base_tax * region_tax`
  per team present in that region, per minute (rule 3.10). Folded into the live
  balance as a net rate: if income beats decay, your balance *grows*. Shown on the
  dashboard and pushed over the WebSocket.
- **"Steal" challenges** — bet is a fixed % of your own capital; pick a target team;
  on success you steal that same % of *their* current capital (rule 4.2). Seeded in
  Rīga (20%) and Zemgale (15%).
- **Admin-set challenges + key-task unlock** — admins **pre-assign** which
  challenges are active for each day and region (author them in the admin panel,
  then assign to a day). There is **no random draw**. Each region has a key task;
  a team must complete it to (a) **see** that region's challenges, (b) bet on them,
  and (c) place region-capture deposits there (rules 2.1, 3.1). The first team to
  do a region's key task each day also gets +50 IP (`KEY_TASK_BONUS`, rule 2.3).
- **30-minute region timer (rule 3.2)** — a team must have spent at least
  `REGION_MIN_MINUTES` (30) of **continuous** presence in its current region
  before it can place capture deposits there. The game loop advances the timer
  **only while the game is running**, and **leaving the region resets it to 0**.
  Shown on the dashboard as `X/30 min` with a countdown.
- **Cross-day challenge carry-over (rule 2.8)** — because a team's key-task
  unlock (`TeamKeyUnlock`) persists across days, a region unlocked on an earlier
  day stays visible/bettable when the team re-enters it on later days.

> **Schema note:** these features added tables (`region_presence`, and earlier
> V2 tables). `create_all` creates *new* tables automatically on startup, so a
> restart is enough for the presence table — no data wipe needed. (Only *column*
> additions to existing tables would require `docker compose down -v`.)
