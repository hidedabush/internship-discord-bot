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

This bot uses slash commands and does not need Message Content Intent.

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

If you add a new source later, create a new module in `scraper/`, return the same internship dictionary shape, and call it from `scanner.py`.
