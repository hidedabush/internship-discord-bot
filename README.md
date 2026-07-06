# Discord Internship Bot

A beginner-friendly Discord bot that runs locally, scans public GitHub internship README sources, stores opportunities in SQLite, and posts new internship alerts into a Discord channel.

LinkedIn and Jobright are supported through safe manual link ingestion instead of direct scraping.

## What this bot does

- Automatically scans enabled GitHub README sources every 4 hours while the bot is running.
- Runs one scan on startup by default.
- Posts only new internships that have not already been posted to Discord.
- Stores jobs in local SQLite: `internships.db`.
- Avoids duplicate Discord posts using company + role + application link.
- Includes upload/source age info in Discord posts when the source provides it.
- Adds a FAANG or Non-FAANG tag to each opportunity.
- Adds emoji-powered tags to make Discord posts easier to scan.
- Lets you manage GitHub sources with slash commands.
- Includes an optional local dashboard at `http://localhost:5000`.
- Includes a future hourly email digest template in `utils/email_digest_template.py`.

## Important LinkedIn and Jobright note

This project does **not** directly scrape LinkedIn or Jobright.

LinkedIn commonly blocks bots and many job boards restrict automated extraction. Because of that, this bot uses safer alternatives:

- Paste a job URL manually with `/add_manual_job`.
- Use saved job links you personally found.
- Later, import a CSV export if you build that upgrade.
- Use email/RSS alerts only when the source officially supports it.

## Project structure

```text
discord-internship-bot/
|-- bot.py
|-- scanner.py
|-- scraper/
|   |-- __init__.py
|   |-- github_scraper.py
|   |-- linkedin_manual.py
|   `-- jobright_manual.py
|-- database/
|   |-- __init__.py
|   `-- db.py
|-- dashboard/
|   |-- app.py
|   `-- templates/
|       |-- index.html
|       `-- internships.html
|-- utils/
|   |-- config_loader.py
|   |-- email_digest_template.py
|   |-- filters.py
|   |-- formatting.py
|   |-- source_store.py
|   `-- tags.py
|-- config.json
|-- sources.json
|-- requirements.txt
|-- README.md
|-- .env.example
`-- .gitignore
```

## Setup guide for Windows

### 1. Install Python

1. Go to the official Python website.
2. Download Python 3.11 or newer.
3. During installation, check **Add python.exe to PATH**.
4. Open PowerShell and test:

```powershell
python --version
```

You should see something like `Python 3.11.x` or newer.

### 2. Open the project folder

Put this folder somewhere simple, like:

```text
C:\Users\YourName\Desktop\discord-internship-bot
```

Then open PowerShell:

```powershell
cd C:\Users\YourName\Desktop\discord-internship-bot
```

### 3. Create a virtual environment

```powershell
python -m venv .venv
```

Activate it:

```powershell
.\.venv\Scripts\activate
```

You should see `(.venv)` at the beginning of your PowerShell line.

### 4. Install dependencies

```powershell
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

### 5. Create your `.env` file

Copy `.env.example` to `.env`:

```powershell
copy .env.example .env
```

Open `.env` in VS Code or Notepad and paste your Discord bot token:

```env
DISCORD_TOKEN=your_real_token_here
```

Do not share this token. Do not commit `.env` to GitHub.

### 6. Create your `config.json` and `sources.json`

These hold your live channel ID and saved sources, and are gitignored so your personal
settings never get committed. Copy the templates:

```powershell
copy config.example.json config.json
copy sources.example.json sources.json
```

You do not need to edit them by hand — `/set_channel` and `/add_source` (or the
dashboard) fill these in for you.

## Docker setup guide

Use Docker if you want the bot to keep running in a container instead of directly in your Windows PowerShell session.

### 1. Install Docker Desktop

1. Install Docker Desktop for Windows.
2. Start Docker Desktop.
3. Open PowerShell in this project folder.
4. Confirm Docker works:

```powershell
docker --version
```

### 2. Create your `.env` file

If you have not already created it, copy the example file:

```powershell
copy .env.example .env
```

Edit `.env` and set at least:

```env
DISCORD_TOKEN=your_real_token_here
```

Optional but recommended while testing:

```env
DISCORD_GUILD_ID=your_server_id_here
```

You can also set the posting channel in `.env` if you already know it:

```env
DISCORD_CHANNEL_ID=your_channel_id_here
```

Otherwise, run `/set_channel` in Discord after the bot starts.

### 3. Create your `config.json` and `sources.json`

```powershell
copy config.example.json config.json
copy sources.example.json sources.json
```

Both are gitignored and mounted into the container so they persist across rebuilds.

### 5. Check the `Dockerfile`

The project includes a multi-stage `Dockerfile` (dependencies build in one stage, the
runtime image only copies the installed virtual environment and the app code, keeping
the final image smaller) that ends with:

```dockerfile
CMD ["python", "bot.py"]
```

### 6. Check `.dockerignore`

The project includes a `.dockerignore` that keeps secrets, your local virtual
environment, caches, and your local database out of the Docker build context.

### 7. Build the Docker image

```powershell
docker build -t discord-internship-bot .
```

### 8. Run the bot container

This command starts the bot and mounts the project folder into the container so `config.json`, `sources.json`, and `internships.db` persist on your computer:

```powershell
docker run --name discord-internship-bot --env-file .env -v ${PWD}:/app discord-internship-bot
```

If the bot starts correctly, you should see logs saying it logged into Discord and synced slash commands.

### 9. Stop and restart the container

Stop it:

```powershell
docker stop discord-internship-bot
```

Start it again:

```powershell
docker start -a discord-internship-bot
```

If you changed code or dependencies, rebuild and recreate the container:

```powershell
docker stop discord-internship-bot
docker rm discord-internship-bot
docker build -t discord-internship-bot .
docker run --name discord-internship-bot --env-file .env -v ${PWD}:/app discord-internship-bot
```

### Optional: run the dashboard in Docker

The dashboard binds to `127.0.0.1` and runs with Flask debug mode off by default, and
it refuses to start on a non-loopback host unless `DASHBOARD_USERNAME` and
`DASHBOARD_PASSWORD` are both set. To access it from your browser through Docker, set
those plus `DASHBOARD_HOST=0.0.0.0` when running the container:

```powershell
docker run --name internship-dashboard --env-file .env -e DASHBOARD_HOST=0.0.0.0 -e DASHBOARD_USERNAME=youruser -e DASHBOARD_PASSWORD=yourpassword -p 5000:5000 -v ${PWD}:/app discord-internship-bot python dashboard/app.py
```

Open:

```text
http://localhost:5000
```

You'll be prompted for the username/password (HTTP Basic Auth) once `DASHBOARD_HOST`
is widened. Only do this on a network you trust — never expose it to the public
internet. Never set `DASHBOARD_DEBUG=true` unless you're debugging locally with
`DASHBOARD_HOST` left at `127.0.0.1`: Flask's debug mode exposes an unauthenticated
interactive Python console over HTTP whenever a route raises, which is a remote
code execution risk the moment the dashboard is reachable from anywhere else.

## Discord bot creation guide

### 1. Create a Discord application

1. Go to the Discord Developer Portal.
2. Click **New Application**.
3. Name it something like `Internship Alerts Bot`.
4. Click **Create**.

### 2. Create the bot user

1. Open your application.
2. Go to the **Bot** tab.
3. Click **Add Bot** or **Reset Token** if needed.
4. Copy the bot token.
5. Paste the token into your local `.env` file as `DISCORD_TOKEN`.

### 3. Enable intents

This bot uses slash commands and does not need Message Content Intent — leave that off.

It **does** require the **Server Members Intent** (a privileged intent), because the
premium-tier personalized DM digest needs to resolve which members hold the premium
role. Turn this on in the **Bot** tab, under **Privileged Gateway Intents**, even if
you're not using the premium tier yet — the bot requests it unconditionally, and
Discord will refuse the connection with `PrivilegedIntentsRequired` if it's off.

### 4. Create the invite URL

1. Go to **OAuth2**.
2. Open **URL Generator**.
3. Under **Scopes**, select:
   - `bot`
   - `applications.commands`
4. Under **Bot Permissions**, select:
   - View Channels
   - Send Messages
   - Read Message History
   - Use Slash Commands
   - Embed Links
5. Copy the generated invite URL.
6. Open it in your browser.
7. Invite the bot to your Discord server.

### 5. Create a private channel

1. In Discord, create a private text channel like `#internship-alerts`.
2. Give yourself access.
3. Give the bot access.
4. Make sure the bot can:
   - View Channel
   - Send Messages
   - Read Message History
   - Embed Links
   - Use Application Commands

### 6. Optional: set your guild ID for faster slash commands

Without a guild ID, Discord global slash commands can take a while to appear.

To make commands appear faster while testing:

1. In Discord, go to **User Settings > Advanced**.
2. Turn on **Developer Mode**.
3. Right-click your server icon.
4. Click **Copy Server ID**.
5. Paste it into `.env`:

```env
DISCORD_GUILD_ID=your_server_id_here
```

## Run guide

### Start the bot

Make sure your virtual environment is activated:

```powershell
.\.venv\Scripts\activate
```

Run:

```powershell
python bot.py
```

You should see console logs showing that the bot logged in and synced commands.

### Set the posting channel

In your private Discord channel, run:

```text
/set_channel
```

This saves the current channel ID into `config.json`.

### Automatic scans

By default, `config.json` is set to:

```json
{
  "scan_interval_minutes": 240,
  "auto_scan_enabled": true,
  "auto_scan_on_start": true
}
```

That means:

- The bot runs one scan when it starts.
- The bot scans again every 4 hours.
- Only new jobs are posted to Discord.
- Jobs already posted stay in SQLite and are not posted again.

The bot still only runs while your laptop is on and `python bot.py` is running.

### Manual scan

You can still run a scan any time:

```text
/scan
```

The bot scans enabled GitHub sources, stores jobs in SQLite, and posts new jobs into the configured channel.

### Stop the bot

In PowerShell, press:

```text
Ctrl + C
```

## Discord post format

Each new internship embed includes:

- Company
- Role
- Location
- Uploaded time or source age, when available
- Apply link
- Source link
- Emoji tags
- FAANG or Non-FAANG classification

Example tags:

```text
💻 Software, 🎓 Internship, ⭐ FAANG
```

```text
📊 Data, 🌱 Non-FAANG
```

FAANG detection currently covers common aliases for Meta/Facebook, Apple, Amazon/AWS, Netflix, and Google/Alphabet.

## Discord commands

- `/scan` - manually scan all enabled GitHub sources.
- `/add_source <url>` - add a new GitHub internship repository or README URL.
- `/list_sources` - show all saved sources.
- `/remove_source <url_or_id>` - remove a source by ID or exact URL.
- `/set_channel` - set the current channel as the posting channel.
- `/status` - show bot status, schedule, last scan time, and job counts.
- `/add_manual_job <source> <url> [company] [title] [location]` - manually save a LinkedIn or Jobright link.
- `/set_premium_role <role>` - admin only: set which role gets personalized DM digests.
- `/set_profile <blurb>` - premium members: set your interests for personalized matching.
- `/my_profile` - show your saved profile and premium status.
- `/help` - show available commands.

## Add more GitHub internship links

Option 1: Discord command:

```text
/add_source https://github.com/example/example-internships
```

Option 2: Edit `sources.json` manually:

```json
{
  "id": "myrepo01",
  "url": "https://github.com/example/example-internships",
  "type": "github_readme",
  "enabled": true,
  "date_added": "2026-07-02T00:00:00+00:00"
}
```

Restart the bot or run `/scan` after editing.

## Config file

Edit `config.json`:

```json
{
  "discord_channel_id": "",
  "scan_interval_minutes": 240,
  "auto_scan_enabled": true,
  "auto_scan_on_start": true,
  "max_posts_per_scan": 20,
  "include_keywords": ["software", "swe", "intern", "data", "ai", "quant", "gpu", "cuda"],
  "exclude_keywords": ["senior", "staff", "principal", "full-time", "new grad"]
}
```

Optional `.env` overrides:

```env
SCAN_INTERVAL_MINUTES=240
AUTO_SCAN_ENABLED=true
AUTO_SCAN_ON_START=true
MAX_POSTS_PER_SCAN=20
```

## Optional local-LLM relevance and quality scoring

The keyword include/exclude filter above is cheap but coarse: it can't tell a strong
posting from a weak one, and it lets through the occasional parser mistake as long as
a keyword happens to match. If you have a local Ollama server, the bot can run a
second pass that judges each new posting for relevance and gives it a 1-5 quality
score, so when a scan finds more postings than `max_posts_per_scan` can send at once,
the strongest matches win the scarce slots instead of whichever happened to appear
first in a README.

Enable it in `config.json`:

```json
{
  "llm_filter_enabled": true,
  "ollama_host": "http://192.168.1.84:11434",
  "ollama_model": "llama3.2:3b",
  "llm_timeout_seconds": 15,
  "llm_min_quality_score": 1
}
```

Or via `.env`: `LLM_FILTER_ENABLED=true`, `OLLAMA_HOST=...`, `OLLAMA_MODEL=...`.

Make sure the model is pulled first:

```bash
docker exec ollama ollama pull llama3.2:3b
```

Notes:

- **Off by default.** Nothing changes until `llm_filter_enabled` is `true`.
- **Fails open.** If Ollama is unreachable, slow, or returns something unusable, the
  posting is kept with a neutral score rather than dropped — a flaky LLM call should
  never cause a real internship to go unposted.
- **Only runs on new postings**, after the keyword filter, so it's not spending a
  model call on every row of every README on every scan.
- **`llm_min_quality_score`** (1-5) additionally drops postings scored below the
  threshold, on top of the model's own relevant/not-relevant judgement. Leave at `1`
  to only filter, not additionally threshold.
- Scored postings show a star rating ("Match") and the model's one-line reason (as
  the embed footer) in Discord; unscored postings (feature off, or the fallback path)
  show neither.

## Optional premium tier: personalized DM digests

Everything above ranks/filters postings the same way for the whole server. The
premium tier adds a second, *personalized* layer on top for members your organization
has marked as paid/premium — it doesn't change or gate anything the rest of the
server sees.

There's no billing integration here at all. "Premium" is just a Discord role your
officers assign the same way you'd assign any other role (e.g. when dues are paid) —
the bot only checks whether a member holds that role.

**Setup:**

1. Create a role in your server for premium members (any name).
2. Make sure **Server Members Intent** is enabled (see the intents step above) —
   required for the bot to know who holds the role.
3. Run `/set_premium_role @YourPremiumRole` (admin/Manage Server permission required).
4. Premium members run `/set_profile` with 1-3 sentences describing what they're
   looking for, e.g. `"Backend/Go internships, remote OK, sophomore, open to startups"`.
   `/my_profile` shows what's currently saved.

That's it — after every scan, each premium member with a saved profile gets DMed
their top matches from that scan (ranked and explained against *their* blurb, not
just the server-wide ranking), in addition to the shared channel post everyone gets.

Config (`config.json`):

```json
{
  "premium_role_id": "",
  "personal_digest_top_n": 5,
  "personal_digest_min_score": 4
}
```

- **`personal_digest_top_n`** — max postings DMed per member per scan.
- **`personal_digest_min_score`** (1-5) — only DM postings scored at or above this
  personal-fit score; keeps digests from including a "meh" match just to fill five
  slots.
- Uses the same `ollama_host`/`ollama_model`/`llm_timeout_seconds` settings as the
  server-wide LLM filter above, and the same fail-open behavior — if Ollama is
  unreachable, that member's digest is silently skipped for this scan rather than
  DMing them something broken or blocking the shared-channel post.
- If a premium member has DMs closed to the bot, they're skipped (logged, not
  retried) — it doesn't affect anyone else's digest or the shared channel post.

## Uptime monitoring (Uptime Kuma)

The bot has no HTTP server, so it can't be health-checked the usual way (something
pinging a `/health` endpoint). Instead it pushes a heartbeat *out* to an Uptime Kuma
[Push monitor](https://github.com/louislam/uptime-kuma) on a timer — if the process
dies, hangs, or loses its connection, the pings stop arriving and Kuma flags it down
using whatever alerting you've already got configured there.

**Setup, in Uptime Kuma:**

1. Add New Monitor → Monitor Type: **Push**.
2. Set the **Heartbeat Interval** to something a bit longer than
   `heartbeat_interval_minutes` below (e.g. 2x it), so a single missed tick doesn't
   immediately page you.
3. Save, then copy the Push URL it gives you
   (`http://<kuma-host>:3001/api/push/<token>?status=up&msg=OK&ping=`).

**Setup, in this bot** (`.env`, since the URL contains an auth token):

```env
UPTIME_KUMA_PUSH_URL=http://192.168.1.84:3001/api/push/your-token-here
```

Optional (`config.json`): `heartbeat_interval_minutes` (default `5`).

Notes:

- **Off by default** — nothing pings anywhere until `UPTIME_KUMA_PUSH_URL` is set.
- This is a liveness check only (bot process alive and connected to Discord), not a
  "is everything working perfectly" check — Ollama being down, for instance, doesn't
  stop the heartbeat, since the LLM features already fail open gracefully instead of
  crashing. If you want that level of detail, `/status` shows it directly.
- A failed push (Kuma unreachable) is logged and dropped, not retried — the next
  scheduled tick tries again on its own.

## Storage maintenance

Nothing in the bot ever prunes on its own by default in most bots — this one does,
so `internships.db` doesn't grow forever on a server that's meant to run for months.
A daily background task (`storage_maintenance_interval_hours`, default `24`):

1. Deletes postings older than `data_retention_days` (default `180`) whose status is
   still `closed`, `unknown`, or `ignored`. Postings marked `active`, `applied`, or
   `saved` (via the dashboard) are **never** auto-deleted, regardless of age — those
   carry personal value that outweighs the storage cost.
2. Runs `PRAGMA wal_checkpoint(TRUNCATE)` and `VACUUM` to flush the WAL file and
   reclaim disk space the deletes freed up.

This always runs (no config flag to enable it — it has no external dependency and is
cheap even when there's nothing to prune). Set `data_retention_days` to `0` or
negative to disable the pruning step specifically while still keeping the
checkpoint/VACUUM housekeeping. Check current database size and retention settings
any time with `/status`.

## Optional dashboard

The dashboard is local only. It is not password protected, so do not expose it to the public internet.

Run it in a second PowerShell window:

```powershell
cd C:\Users\YourName\Desktop\discord-internship-bot
.\.venv\Scripts\activate
python dashboard/app.py
```

Open:

```text
http://localhost:5000
```

Dashboard features:

- View saved sources.
- Add a GitHub source URL.
- Enable/disable sources.
- View found internships.
- See uploaded/source age and first-seen time.
- Mark internships as `saved`, `applied`, `ignored`, `closed`, etc.

## Future hourly email digest

The project includes a template for the next upgrade in:

```text
utils/email_digest_template.py
```

`render_hourly_email_digest(jobs)` returns:

- `subject`
- `text`
- `html`

It is ready to be used later by an hourly job that:

1. Reads new internships from SQLite.
2. Reads subscribed user email addresses from a user database table.
3. Renders the digest template.
4. Sends the email through a provider such as SendGrid, Mailgun, Amazon SES, or SMTP.
5. Marks those jobs as emailed so users do not receive duplicates.

No email is sent yet. This file is only the digest template for the later database-email feature.

## How duplicate detection works

The bot builds a duplicate key from:

```text
company + role/title + application link
```

That key is stored in SQLite as a unique value. If the same job appears again in the same repo or another repo, the bot updates `last_seen` but does not repost it.

## Troubleshooting

### Bot is offline

- Make sure `python bot.py` is running.
- Make sure your `.env` file exists.
- Make sure `DISCORD_TOKEN` is set.
- Make sure your laptop has internet.

### Invalid Discord token

- Go back to the Discord Developer Portal.
- Open your application.
- Go to **Bot**.
- Reset/copy the token again.
- Paste it into `.env`.
- Save the file and restart `python bot.py`.

### Bot does not post in the channel

- Run `/set_channel` inside the private channel.
- Check `config.json` and make sure `discord_channel_id` is saved.
- Make sure the bot has permission to view and send messages in that channel.
- Make sure the bot has **Embed Links** permission.

### Scheduled scans are not running

- Check `/status` and confirm auto scan is enabled.
- Confirm `scan_interval_minutes` is `240`.
- Keep `python bot.py` running. The schedule stops when the process stops.
- If you changed `.env` or `config.json`, restart the bot.

### Slash commands do not appear

- Set `DISCORD_GUILD_ID` in `.env` for faster local testing.
- Restart the bot.
- Wait a minute and refresh Discord with `Ctrl + R`.
- Make sure you invited the bot with the `applications.commands` scope.

### GitHub link does not scrape correctly

- Make sure it is a public GitHub repo or README file.
- The scraper works best with Markdown tables.
- Try using the actual README URL, not just the repo homepage.
- Some repos use unusual formatting. You may need to improve `scraper/github_scraper.py` for that repo.

### Duplicate jobs keep posting

- Confirm that `internships.db` is not being deleted between runs.
- Check if the application links are changing every scan due to tracking parameters.
- Improve `build_dedupe_key()` in `database/db.py` if needed.

### Python package install errors

Try:

```powershell
python -m pip install --upgrade pip setuptools wheel
python -m pip install -r requirements.txt
```

If Python is not found, reinstall Python and check **Add python.exe to PATH**.

## Development notes

The main flow is:

1. `bot.py` starts the Discord bot and scheduled scan loop.
2. `scanner.py` loads enabled sources from `sources.json`.
3. `scraper/github_scraper.py` fetches and parses GitHub READMEs.
4. `utils/filters.py` applies include/exclude keywords.
5. `utils/tags.py` adds FAANG or Non-FAANG classification.
6. `database/db.py` stores and deduplicates jobs.
7. `utils/formatting.py` formats jobs as Discord embeds.
8. `bot.py` posts new jobs and marks them as posted.
9. `bot.py` also DMs each premium member (see the premium tier section above) their
   personal top matches for this scan's new jobs, scored by `utils/personalization.py`
   against their `/set_profile` blurb.

If you add a new source later, create a new module in `scraper/`, return the same internship dictionary shape, and call it from `scanner.py`.

`FULL_PROJECT.md` is a generated snapshot of every tracked file for onboarding/AI-assistant
context — don't hand-edit it. Regenerate it after changing any tracked file:

```bash
python scripts/generate_full_project_doc.py
```

### Running tests

```bash
pip install -r requirements-dev.txt
pytest
```

Tests focus on `scraper/github_scraper.py` (the markdown-table parser is the most
format-fragile part of the project) and `utils/tags.py` (FAANG alias matching).
