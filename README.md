# Discord Internship Bot

A beginner-friendly Discord bot that runs locally on your Windows laptop and posts internship opportunities into a private Discord channel.

The MVP supports public GitHub internship README repositories. LinkedIn and Jobright are supported through safe manual link ingestion instead of direct scraping.

## What this bot does

- Runs only when you start it locally.
- Scans enabled GitHub README sources.
- Parses Markdown internship tables.
- Stores jobs in local SQLite: `internships.db`.
- Avoids duplicate Discord posts using company + role + application link.
- Posts clean embeds into your private Discord channel.
- Lets you manage GitHub sources with slash commands.
- Includes an optional local dashboard at `http://localhost:5000`.

## Important LinkedIn and Jobright note

This project does **not** directly scrape LinkedIn or Jobright.

LinkedIn commonly blocks bots and states that crawlers/bots/extensions that scrape or automate LinkedIn are not permitted. Job boards also often change layouts, require login, block automated traffic, or restrict automated extraction in their terms. Because of that, this bot uses safer alternatives:

- Paste a job URL manually with `/add_manual_job`.
- Use saved job links you personally found.
- Later, import a CSV export if you build that upgrade.
- Use email/RSS alerts only when the source officially supports it.

## Project structure

```text
discord-internship-bot/
├── bot.py
├── scanner.py
├── scraper/
│   ├── __init__.py
│   ├── github_scraper.py
│   ├── linkedin_manual.py
│   └── jobright_manual.py
├── database/
│   ├── __init__.py
│   └── db.py
├── dashboard/
│   ├── app.py
│   └── templates/
│       ├── index.html
│       └── internships.html
├── utils/
│   ├── config_loader.py
│   ├── source_store.py
│   ├── formatting.py
│   └── filters.py
├── config.json
├── sources.json
├── requirements.txt
├── README.md
├── .env.example
└── .gitignore
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

This MVP uses slash commands and does not need Message Content Intent.

You can leave privileged intents off unless you later add text-prefix commands or member-reading features.

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

### Scan internships

Run:

```text
/scan
```

The bot will scan enabled GitHub sources, store jobs in SQLite, and post new jobs into the configured channel.

### Stop the bot

In PowerShell, press:

```text
Ctrl + C
```

## Discord commands

- `/scan` — manually scan all enabled GitHub sources.
- `/add_source <url>` — add a new GitHub internship repository or README URL.
- `/list_sources` — show all saved sources.
- `/remove_source <url_or_id>` — remove a source by ID or exact URL.
- `/set_channel` — set the current channel as the posting channel.
- `/status` — show bot status, number of sources, last scan time, and job counts.
- `/add_manual_job <source> <url> [company] [title] [location]` — manually save a LinkedIn or Jobright link.
- `/help` — show available commands.

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
  "scan_interval_minutes": 60,
  "auto_scan_enabled": false,
  "auto_scan_on_start": false,
  "max_posts_per_scan": 20,
  "include_keywords": ["software", "swe", "intern", "data", "ai", "quant", "gpu", "cuda"],
  "exclude_keywords": ["senior", "staff", "principal", "full-time", "new grad"]
}
```

Recommended beginner setting: keep `auto_scan_enabled` as `false` and use `/scan` manually until everything works.

If you want scheduled scanning while the bot is running locally, set:

```json
"auto_scan_enabled": true,
"scan_interval_minutes": 60
```

The bot still only runs while your laptop is on and `python bot.py` is running.

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
- Mark internships as `saved`, `applied`, `ignored`, `closed`, etc.

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
- Check if the application links are changing every scan due tracking parameters.
- Improve `build_dedupe_key()` in `database/db.py` if needed.

### Python package install errors

Try:

```powershell
python -m pip install --upgrade pip setuptools wheel
python -m pip install -r requirements.txt
```

If Python is not found, reinstall Python and check **Add python.exe to PATH**.

### Permission errors in Discord

- Check channel permissions, not just server permissions.
- Private channels need explicit bot access.
- Make sure role order does not block the bot.
- Re-invite the bot if you forgot a permission during invite.

## Future upgrade ideas

- Deploy later to Railway, Render, or a VPS.
- Add email alerts.
- Add AI summarization of job posts.
- Add automatic resume keyword matching.
- Add a stronger applied tracker with notes and deadlines.
- Add CSV export.
- Add CSV import for manual LinkedIn/Jobright saved jobs.
- Add GitHub Actions later if you ever want scheduled cloud scanning.
- Add per-source category filters.
- Add a web dashboard login if you deploy it outside localhost.

## Development notes

This project is intentionally not overengineered. The main flow is:

1. `bot.py` receives `/scan`.
2. `scanner.py` loads enabled sources from `sources.json`.
3. `scraper/github_scraper.py` fetches and parses GitHub READMEs.
4. `utils/filters.py` applies include/exclude keywords.
5. `database/db.py` stores and deduplicates jobs.
6. `utils/formatting.py` formats jobs as Discord embeds.
7. `bot.py` posts new jobs and marks them as posted.

If you want to add a new source later, create a new module in `scraper/`, return the same internship dictionary shape, and call it from `scanner.py`.
