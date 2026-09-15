# Setup Guide — Quick Start (no virtual environment)

An alternative to [SETUP.md](SETUP.md) for running this pipeline directly against
your system/user Python, with no `venv` to create or activate. Use this if you don't
want a project-local virtual environment and are fine installing this project's
dependencies into your regular Python install.

Everything else — Reddit/Anthropic credentials, Windows Credential Manager, the
`data/` output layout — works exactly the same as SETUP.md. See that file for
background on any step below.

## Prerequisites

- Python 3.11 or newer
- Git
- A Reddit account
- An Anthropic account with API access (console.anthropic.com)

```powershell
python --version
```

**Expected result:** `Python 3.11.x` or higher. If PowerShell says `python` isn't
recognized, use `py -3 --version` instead.

## Step 1: Get the code

```powershell
git clone https://github.com/AndyMelnykov/feedback-reddit-parser.git
cd feedback-reddit-parser
```

## Step 2: Install dependencies

```powershell
pip install -r requirements.txt
```

**Expected result:** output ending with something like:

```
Successfully installed praw-... anthropic-... pyyaml-... keyring-...
```

You can skip this step entirely if you'd rather let `run.ps1` (Step 7) install
dependencies automatically on first run.

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

```powershell
python set_credentials.py
```

It will prompt for each credential in turn, one per line, with the input hidden as you
type:

```
reddit_client_id:
reddit_client_secret:
reddit_user_agent:
anthropic_api_key:
```

Paste the corresponding value at each prompt and press Enter. There is no confirmation
message after the last prompt — reaching a fresh PowerShell prompt with no error means
all four were saved.

## Step 7: Run the pipeline with `run.ps1`

`run.ps1` finds your Python install, installs dependencies if they're missing, runs
the pipeline, and exits — nothing stays activated or running in your shell afterward.
Results land in `data/`, same as SETUP.md describes.

```powershell
.\run.ps1
```

**Run a single stage:**

```powershell
.\run.ps1 -Stage match
```

Valid values for `-Stage` are `fetch`, `extract`, `match`, `report`, `materiality`, or
`all` (the default).

**Use a different config file:**

```powershell
.\run.ps1 -Config other-config.yaml
```

If PowerShell refuses to run the script with a message about execution policies, run
this once (affects only your user account):

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```

## Troubleshooting

See [SETUP.md](SETUP.md#troubleshooting) — all the same error messages and fixes
apply here, except venv-activation issues (there's no venv in this flow).

## Uninstalling

```powershell
python -c "import keyring; [keyring.delete_password('reddit-signal-pipeline', k) for k in ['reddit_client_id','reddit_client_secret','reddit_user_agent','anthropic_api_key']]"
pip uninstall -y praw prawcore anthropic pyyaml keyring
```

Then delete the `data/` folder and `config.yaml` to remove everything else the
pipeline created locally.
