# AI LinkedIn Manager

An AI-powered LinkedIn post pipeline built on Slack, Google Gemini, and the LinkedIn API.

Drop raw notes or images into a Slack channel, pick a tone from a dropdown, and publish a polished post — including image carousels — directly to your LinkedIn feed from your phone or desktop.

---

## How It Works

```
#linkedin-drafts (Slack)
  │
  ├── Type raw notes  /  attach up to 5 images or 1 video
  │
  └── Bot replies with a tone dropdown:
        Post As-Is · Warm & Human · Highly Technical · Empathetic · Professional · Aggressive
              │
              └── Gemini 2.5 Flash Lite generates the post (or bypasses if As-Is)
                    │
                    └── #linkedin-approvals (Slack)
                          │
                          ├── ✅ Approve & Publish  →  LinkedIn feed
                          ├── ✏️ Manual Edit        →  Slack Modal → Updates draft in-place
                          ├── 🔄 Regenerate         →  Feedback Modal → Updates draft in-place
                          └── 🗑️ Discard            →  Archived
```

---

## File Structure

```
linkedin_ai_manager/
│
├── main.py                    # Entry point — starts the Slack Socket Mode listener
├── ai_module.py               # Gemini 2.5 integration with tone directives & AI bypass
├── slack_module.py            # All Slack logic: input, modals, in-place UI updates
├── linkedin_module.py         # LinkedIn OAuth + text / image / video publishing
├── state_manager.py           # Two-phase state persistence (pre-gen + pending)
│
├── prompt_template.txt        # Base writing rules — edit this to change AI style
├── slack_manifest.json        # Paste this into Slack to configure the app instantly
│
├── .env                       # Your secrets — never commit this file
├── .env.template              # Copy this to .env and fill in your values
├── .gitignore                 # Protects API keys and local state files
├── requirements.txt           # Python dependencies
│
├── drafts/                    # Temporary storage for downloaded Slack media
├── logs/
│   ├── published.log          # Record of every published post
│   └── archived_drafts/       # Processed drafts moved here after action
└── state/
    ├── pre_generation_state.json  # Phase 1: waiting for tone selection
    └── pending.json               # Phase 2: waiting for approval button
```

---

## Prerequisites

- Python 3.10 or higher
- A Google account (for Gemini API)
- A Slack account and workspace
- A LinkedIn account with a Company Page

---

## Setup

### 1. Clone the Repository

```bash
git clone https://github.com/Satyansh-Raj/Linkedin-Manager-with-Slack-Integration.git
cd Linkedin-Manager-with-Slack-Integration
```

### 2. Create a Virtual Environment

```bash
python3 -m venv venv
source venv/bin/activate        # Linux / macOS
venv\Scripts\activate           # Windows
```

### 3. Install Dependencies

```bash
pip install -r requirements.txt
```

---

## Getting Your API Keys

### Gemini API Key

| Field | Value |
|---|---|
| URL | https://aistudio.google.com/app/apikey |
| Action | Click **"Create API Key"** |
| Copy | The key starting with `AIza...` |
| Paste into `.env` | `GEMINI_API_KEY` |

---

### Slack Setup

Slack has a manifest system that configures your entire app in one paste — no clicking through 10 screens to add scopes one by one.

#### Step 1 — Create the App from the Manifest

1. Go to **https://api.slack.com/apps**
2. Click **"Create an App"**
3. Choose **"From an app manifest"** ← important, not "From scratch"
4. Select your workspace → click **Next**
5. Select the **JSON** tab in the editor
6. Delete everything in the editor
7. Open `slack_manifest.json` from this repo and paste the entire contents
8. Click **Next** → **Create**

#### Step 2 — Get the Bot Token (`xoxb-`)

1. In the left sidebar click **"OAuth & Permissions"**
2. Click **"Install to Workspace"** → click **"Allow"**
3. Copy the **Bot User OAuth Token** (starts with `xoxb-`) and paste it into `.env` under `SLACK_BOT_TOKEN`

#### Step 3 — Get the App Token (`xapp-`)

1. In the left sidebar click **"Basic Information"**
2. Scroll down to **"App-Level Tokens"**
3. Click **"Generate Token and Scopes"**
4. Token Name: `socket-token`
5. Click **"Add Scope"** → select `connections:write`
6. Click **"Generate"** → copy the token (starts with `xapp-`) and paste it into `.env` under `SLACK_APP_TOKEN`

#### Step 4 — Create the Slack Channels

In your Slack workspace, create two private channels: `linkedin-drafts` and `linkedin-approvals`. In each channel, type:

```
/invite @LinkedIn Manager
```

---

### LinkedIn Setup

#### Step 1 — Create a Company Page

LinkedIn requires every developer app to be linked to a Company Page.

Go to **https://www.linkedin.com/company/setup/new** and create a basic page.

#### Step 2 — Create the Developer App

Go to **https://www.linkedin.com/developers/apps** and click **"Create App"**.

Name it `LinkedIn AI Manager` and link it to the page you just created.

#### Step 3 — Copy Your Credentials

Go to the **"Auth"** tab on your app dashboard.

| Field | Location | Paste into `.env` |
|---|---|---|
| Client ID | Auth tab → Client ID field | `LINKEDIN_CLIENT_ID` |
| Client Secret | Auth tab → click the 👁️ eye icon | `LINKEDIN_CLIENT_SECRET` |
| Redirect URI | Add `http://localhost:8080/callback` under OAuth 2.0 settings | `LINKEDIN_REDIRECT_URI` |

Under **OAuth 2.0 settings**, add exactly `http://localhost:8080/callback` as your redirect URL and save.

#### Step 4 — Request API Products

1. Click the **"Products"** tab
2. Request access for **"Share on LinkedIn"**
3. Request access for **"Sign In with LinkedIn using OpenID Connect"**
4. Wait ~10 minutes. Go back to the **Auth** tab and confirm all four scopes are visible:

| Scope | Source |
|---|---|
| `openid` | Sign In with LinkedIn product |
| `profile` | Sign In with LinkedIn product |
| `email` | Sign In with LinkedIn product |
| `w_member_social` | Share on LinkedIn product |

#### Step 5 — Run One-Time OAuth

```bash
python linkedin_module.py --auth
```

This opens your browser to log in to LinkedIn. It captures your token and saves it directly to your `.env` file.

> **Token expiry:** LinkedIn tokens last 60 days. Re-run this command when they expire.

---

## Running the Bot

```bash
source venv/bin/activate
python main.py
```

The bot connects to Slack via Socket Mode and waits for your input. No server required.

---

## Usage

### Creating a Post (Text Only)

1. Go to `#linkedin-drafts` in Slack and type your raw notes
2. The bot replies with a tone dropdown — select one:

| Tone | Description |
|---|---|
| ✍️ **Post As-Is (No AI)** | Bypasses the AI entirely. Publishes your exact raw text |
| **Warm & Human** | Casual, relatable, first-person |
| **Highly Technical** | Architecture, tooling, engineering depth |
| **Empathetic / Human Journey** | Vulnerable, struggle-focused |
| **Professional / Corporate** | Crisp, ROI-focused, business language |
| **Aggressive / Sales** | Bold, urgent, CTA-driven |

3. Check `#linkedin-approvals`. The generated post appears with four buttons:

| Button | Action |
|---|---|
| ✅ **Approve & Publish** | Posts immediately to LinkedIn |
| ✏️ **Manual Edit** | Opens a Slack modal to manually rewrite the draft. Updates in-place when saved |
| 🔄 **Regenerate** | Opens a feedback modal, re-runs Gemini, updates the message in-place |
| 🗑️ **Discard** | Clears the draft and archives it |

### Creating a Post with Images / Video

Attach up to **5 images** (`.jpg`, `.png`, `.gif`) or **1 video** (`.mp4`, `.mov`) to your Slack message along with a caption. The bot downloads the files and handles LinkedIn's multi-step media upload pipeline automatically.

### Emergency Override

If a draft gets stuck in memory, type `/clear` in any channel to wipe the bot's state files and reset it instantly.

---

## Modifying the Prompt Engine

You can completely change the bot's base writing style without touching any Python code. Edit `prompt_template.txt` directly:

- Want shorter posts? Add: `"Never exceed 3 paragraphs."`
- Hate emojis? Add: `"Strictly forbid the use of emojis."`
- Want a specific structure? Add: `"Always start with a one-sentence hook, followed by a bulleted list."`

The core rules live in this text file. Tone-specific instructions (technical depth, emotional register, hashtag usage) are injected dynamically from the Slack dropdown and do not need to be in the file.
