# Setup Guide (Windows)

Step-by-step instructions to install and run the Reddit signal pipeline, including
creating your Reddit and Anthropic API credentials and storing them in Windows
Credential Manager. Every command below was verified on Windows 11 with PowerShell.

## Prerequisites

- Python 3.11 or newer
- Git
- A Reddit account
- An Anthropic account with API access (console.anthropic.com)

Check your Python version:

```powershell
python --version
```

**Expected result:** `Python 3.11.x` or higher. If PowerShell says `python` isn't
recognized, use `py -3 --version` instead — and substitute `py -3` for `python` in
every command below.

## Step 1: Get the code

```powershell
git clone https://github.com/AndyMelnykov/feedback-reddit-parser.git
cd feedback-reddit-parser
```

## Step 2: Create a virtual environment and install dependencies

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

**Expected result:** your prompt now starts with `(.venv)`, and the install ends with
something like:

```
Successfully installed praw-... anthropic-... pyyaml-... keyring-...
```

If PowerShell blocks the activation script with a message about execution policies,
run this once (affects only your user account, not the whole machine), then retry:

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```

## Step 3: Create a Reddit API app

1. Log into Reddit, then go to https://www.reddit.com/prefs/apps
2. Click **create app** (or **create another app**) near the bottom.
3. Fill in the form:
   - **name**: anything, e.g. `reddit-signal-pipeline`
   - **type**: select **script** (not "web app" or "installed app")
   - **description**: optional
   - **about url**: leave blank
   - **redirect uri**: `http://localhost:8080` (required by the form, unused by this
     pipeline since it never does an interactive OAuth login)
4. Click **create app**.

**Expected result:** a box appears for your new app showing:
- A string of ~14 characters directly under the app name and "personal use script"
  — this is your **client ID**.
- A field labeled **secret** with a longer string — this is your **client secret**.

Copy both somewhere temporarily (you'll paste them into `set_credentials.py` in Step 6
and never need the raw text again after that).

You'll also need a **user agent** string. Reddit requires this exact format:

```
platform:app-id:version (by /u/your-reddit-username)
```

For example: `windows:reddit-signal-pipeline:v1.0 (by /u/yourusername)`

## Step 4: Get an Anthropic API key

1. Log into https://console.anthropic.com
2. Go to **API Keys** in the left sidebar.
3. Click **Create Key**, give it a name (e.g. `reddit-signal-pipeline`), and create it.

**Expected result:** a key starting with `sk-ant-...` is shown once. Copy it now — the
console will not show the full value again after you navigate away.

## Step 5: Configure which subreddits to track

```powershell
Copy-Item config.yaml.example config.yaml
notepad config.yaml
```

Edit the `subreddits` list to the communities relevant to your product, e.g.:

```yaml
subreddits:
  - yourproductname
  - relatedcommunity
fetch_limit_per_subreddit: 100
trend_window_weeks: 8
```

Save and close. `config.yaml` is gitignored, so this file stays local to your machine.

## Step 6: Store your credentials in Windows Credential Manager

The pipeline never reads secrets from a file — it reads them from the Windows
Credential Manager vault via the `keyring` library, so nothing sensitive is ever
written to disk in plain text.

Run the one-time setup script:

```powershell
python set_credentials.py
```

It will prompt for each credential in turn, one per line, with the input hidden as you
type (this is expected — `getpass` never echoes characters):

```
reddit_client_id:
reddit_client_secret:
reddit_user_agent:
anthropic_api_key:
```

Paste the corresponding value at each prompt and press Enter. There is no confirmation
message after the last prompt — reaching a fresh PowerShell prompt with no error means
all four were saved.

**Verify it worked**, either through the GUI or the command line:

- **GUI**: open **Control Panel → Credential Manager → Windows Credentials**. Look under
  **Generic Credentials** for entries related to `reddit-signal-pipeline` (the exact
  target-name format can vary slightly by Windows/keyring version — you may see one
  combined entry or one entry per credential). You're only confirming something was
  saved; you don't need to open or edit these entries manually.
- **Command line**:
  ```powershell
  cmdkey /list | Select-String "reddit-signal-pipeline"
  ```
  **Expected result:** at least one line containing `reddit-signal-pipeline`.

If you ever need to re-run this (e.g. you rotated your Anthropic key), just run
`python set_credentials.py` again — it overwrites the existing entries.

## Step 7: Run the pipeline

Run all five stages in sequence:

```powershell
python run_weekly.py
```

**Expected result:** no console output on success (each stage only prints if it skips a
bad post). Afterward, a `data/` folder appears next to your code with:

```
data/
  state.json             # last-seen post per subreddit
  pi_agent.db            # SQLite database — evidence, extracted candidates, canonical
                          # topics, weekly mention counts, and material signals
  reports/2026-W33.json  # this week's trend report
  reports/2026-W33.csv   # same report, as CSV
  events.jsonl           # one line per material_signal event (only appended once a
                          # topic crosses the materiality threshold — see README)
```

Example contents of `reports/2026-W33.csv` after a few weeks of runs:

```
id,canonical_name,mentions_this_week,total_mentions,trend
t_a1b2c3d4,Dark mode support,5,12,rising
t_e5f6a7b8,Export to CSV,2,2,new
```

**Run a single stage** — useful if you fixed a bug in matching and don't want to
re-fetch from Reddit:

```powershell
python run_weekly.py --stage match
```

Valid values for `--stage` are `fetch`, `extract`, `match`, `report`, `materiality`, or
`all` (the default).

## Troubleshooting

**`Missing credential 'reddit_client_id' in OS credential store. Run set_credentials.py first.`**
One or more credentials weren't saved. Re-run `python set_credentials.py` and make sure
you pressed Enter after each of the four prompts.

**`config at config.yaml must define a non-empty 'subreddits' list`**
Your `config.yaml` is missing or has an empty `subreddits:` list. Revisit Step 5.

**PowerShell says the activation script is blocked**
Run `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` (Step 2) and retry.

**`python query.py search <keyword>` returns nothing for a subreddit you just fetched**
That subreddit had no new posts since the last run (or all of them were removed/
deleted). This is normal on a fresh subreddit or after a very recent previous run —
check `data/state.json` to confirm the subreddit was reached.

**`sqlite3.OperationalError: table signal_candidate has no column named entity` (or
similar "has no column named" error)**
Your local `data/pi_agent.db` predates a schema change on this branch. `db.py`'s
`SCHEMA_SQL` uses `CREATE TABLE IF NOT EXISTS`, so it never alters an existing table —
there's no migration machinery in this codebase, by design (`data/` is gitignored and
dev-only). Delete `data/pi_agent.db` and re-run `python run_weekly.py` to rebuild the
database from scratch with the current schema.

## Scheduling a weekly run

To automate this, create a Windows Task Scheduler task that runs
`python run_weekly.py` from this folder once a week, under **your own Windows user
account** — Credential Manager entries are keyed to the account that created them, so a
task running as `SYSTEM` or a different user account won't be able to read the
credentials saved in Step 6.

## Uninstalling

To remove the stored credentials:

```powershell
python -c "import keyring; [keyring.delete_password('reddit-signal-pipeline', k) for k in ['reddit_client_id','reddit_client_secret','reddit_user_agent','anthropic_api_key']]"
```

Then delete the `data/` folder, `config.yaml`, and the `.venv` folder to remove
everything the pipeline created locally.
