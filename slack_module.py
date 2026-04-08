import os
import logging
import requests
from pathlib import Path
from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler
from dotenv import load_dotenv

import state_manager
from ai_module import generate_post, get_tone_options

load_dotenv()
log = logging.getLogger(__name__)

app = App(token=os.getenv("SLACK_BOT_TOKEN"))

DRAFTS_DIR  = Path(__file__).parent / "drafts"
DRAFTS_DIR.mkdir(exist_ok=True)

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".gif"}
VIDEO_EXTS = {".mp4", ".mov", ".avi"}
MAX_FILES  = 5


# ─────────────────────────────────────────────
# Inbound: Message + file listener
# ─────────────────────────────────────────────

@app.event("message")
def handle_message(event, say, client):
    if event.get("subtype") in ("bot_message", "message_changed", "message_deleted"):
        return
    if event.get("bot_id"):
        return

    if state_manager.is_any_active():
        approval_chan = os.getenv("SLACK_APPROVAL_CHANNEL", "linkedin-approvals")
        say(f"⚠️ A draft is already in progress. Check `#{approval_chan}` to approve, discard, or regenerate it first.")
        return

    text    = event.get("text", "").strip()
    files   = event.get("files", [])
    user    = event.get("user")
    channel = event.get("channel")

    media_filepaths = []
    media_type      = None
    skipped         = []

    if files:
        for file_obj in files[:MAX_FILES]:
            downloaded = _download_slack_file(file_obj, client)
            if not downloaded:
                continue

            path, m_type = downloaded

            # Set dominant type from first file
            if not media_type:
                media_type = m_type

            # Block mixed media
            elif media_type != m_type:
                skipped.append(file_obj.get("name", "unknown"))
                continue

            # LinkedIn only supports 1 video per post
            if m_type == "VIDEO" and len(media_filepaths) >= 1:
                skipped.append(file_obj.get("name", "unknown"))
                say("⚠️ LinkedIn only supports 1 video per post. Extra videos were skipped.")
                continue

            media_filepaths.append(path)

        if skipped:
            say(f"⚠️ Skipped {len(skipped)} file(s) due to mixed media types: `{'`, `'.join(skipped)}`\n"
                f"LinkedIn does not allow mixing images and videos in the same post.")

    # File-only upload — ask for caption
    if not text and media_filepaths:
        state_manager.save_pre_generation(
            raw_notes="",
            slack_channel=channel,
            slack_user_id=user,
            media_filepaths=media_filepaths,
            media_type=media_type,
        )
        count = len(media_filepaths)
        say(f"📎 Got {count} file{'s' if count > 1 else ''}. Reply with your notes/caption and I'll ask for a tone.")
        return

    if not text:
        return

    # Reply-as-caption for file-only uploads
    pre = state_manager.load_pre_generation()
    if pre and pre.get("raw_notes") == "" and pre.get("media_filepaths"):
        media_filepaths = pre["media_filepaths"]
        media_type      = pre["media_type"]
        state_manager.clear_pre_generation()

    state_manager.save_pre_generation(
        raw_notes=text,
        slack_channel=channel,
        slack_user_id=user,
        media_filepaths=media_filepaths,
        media_type=media_type,
    )

    _send_tone_selector(say, media_filepaths=media_filepaths)


@app.event("app_mention")
def handle_mention(event, say):
    say(
        "👋 Drop your notes (or attach up to 5 images / 1 video with a caption) in this channel. "
        "I'll ask you to pick a tone, then send the draft for your approval."
    )


# ─────────────────────────────────────────────
# Tone selector UI
# ─────────────────────────────────────────────

def _send_tone_selector(say, media_filepaths: list = None):
    count      = len(media_filepaths) if media_filepaths else 0
    media_note = f" 📎 {count} file{'s' if count > 1 else ''} attached." if count else ""
    say(
        blocks=[
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"✅ Draft received.{media_note}\n*What tone should I use for this post?*",
                },
            },
            {
                "type": "actions",
                "block_id": "tone_selector_block",
                "elements": [
                    {
                        "type": "static_select",
                        "placeholder": {"type": "plain_text", "text": "Select a tone..."},
                        "action_id": "select_tone",
                        "options": get_tone_options(),
                    }
                ],
            },
        ],
        text="Pick a tone for your LinkedIn post.",
    )


# ─────────────────────────────────────────────
# Tone selection handler
# ─────────────────────────────────────────────

@app.action("select_tone")
def handle_tone_selection(ack, body, client):
    ack()

    selected_tone = body["actions"][0]["selected_option"]["value"]
    tone_label    = body["actions"][0]["selected_option"]["text"]["text"]
    channel       = body["channel"]["id"]

    client.chat_update(
        channel=channel,
        ts=body["message"]["ts"],
        text=f"🎨 Tone set to *{tone_label}*. Generating your post...",
        blocks=[{
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"🎨 Tone set to *{tone_label}*. Generating your post..."},
        }],
    )

    pre = state_manager.load_pre_generation()
    if not pre:
        client.chat_postMessage(channel=channel, text="⚠️ Could not find your draft. Please re-submit.")
        return

    state_manager.clear_pre_generation()

    try:
        generated_post = generate_post(pre["raw_notes"], tone=selected_tone)
    except Exception as e:
        client.chat_postMessage(channel=channel, text=f"❌ Generation failed: `{e}`")
        return

    import time
    draft_path = DRAFTS_DIR / f"slack_draft_{int(time.time())}.txt"
    draft_path.write_text(pre["raw_notes"], encoding="utf-8")

    approval_channel = os.getenv("SLACK_APPROVAL_CHANNEL", "linkedin-approvals")
    slack_ts = send_draft_for_approval(
        post_text=generated_post,
        source_filename=draft_path.name,
        tone_label=tone_label,
        media_filepaths=pre.get("media_filepaths", []),
        media_type=pre.get("media_type"),
        channel=approval_channel,
    )

    state_manager.save_pending(
        draft_filepath=str(draft_path),
        raw_notes=pre["raw_notes"],
        generated_post=generated_post,
        slack_ts=slack_ts,
        slack_channel=approval_channel,
        media_filepaths=pre.get("media_filepaths", []),
        media_type=pre.get("media_type"),
        tone=selected_tone,
    )

    client.chat_postMessage(
        channel=channel,
        text=f"✅ Post generated with *{tone_label}* tone. Check `#{approval_channel}` to review it.",
    )


# ─────────────────────────────────────────────
# Outbound: Approval message
# ─────────────────────────────────────────────

def send_draft_for_approval(
    post_text: str,
    source_filename: str,
    tone_label: str = "",
    media_filepaths: list = None,
    media_type: str = None,
    channel: str = None,
) -> str:
    if not channel:
        channel = os.getenv("SLACK_APPROVAL_CHANNEL", "linkedin-approvals")

    media_filepaths = media_filepaths or []
    count = len(media_filepaths)

    # Header icon
    if media_type == "IMAGE" and count > 0:
        media_icon = f" 🖼️ ×{count}" if count > 1 else " 🖼️"
    elif media_type == "VIDEO":
        media_icon = " 🎬"
    else:
        media_icon = ""

    blocks = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": f"LinkedIn Post — Awaiting Approval{media_icon}"},
        },
        {
            "type": "section",
            "fields": [
                {"type": "mrkdwn", "text": f"*Source:* `{source_filename}`"},
                {"type": "mrkdwn", "text": f"*Tone:* {tone_label or '—'}"},
            ],
        },
        {"type": "divider"},
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": post_text},
        },
    ]

    # List all attached filenames — max 10 elements per context block (Slack limit)
    if media_filepaths:
        elements = [{"type": "mrkdwn", "text": f"📎 *Media ({count} file{'s' if count > 1 else ''}):*"}]
        for path in media_filepaths:
            elements.append({"type": "mrkdwn", "text": f"`{Path(path).name}`"})
        blocks.append({"type": "context", "elements": elements})

    blocks += [
        {"type": "divider"},
        {
            "type": "actions",
            "block_id": "post_actions",
            "elements": [
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "✅ Approve & Publish"},
                    "style": "primary",
                    "action_id": "approve_post",
                    "confirm": {
                        "title": {"type": "plain_text", "text": "Publish to LinkedIn?"},
                        "text": {"type": "mrkdwn", "text": "This will post immediately to your LinkedIn feed."},
                        "confirm": {"type": "plain_text", "text": "Yes, publish it"},
                        "deny": {"type": "plain_text", "text": "Wait, go back"},
                    },
                },
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "✏️ Manual Edit"},
                    "action_id": "edit_post",
                },
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "🔄 Regenerate"},
                    "action_id": "regenerate_post",
                },
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "🗑️ Discard"},
                    "style": "danger",
                    "action_id": "discard_post",
                },
            ],
        },
    ]

    response = app.client.chat_postMessage(
        channel=channel,
        blocks=blocks,
        text="LinkedIn draft ready for review",
    )
    return response["ts"]


# ─────────────────────────────────────────────
# Button handlers
# ─────────────────────────────────────────────

@app.action("approve_post")
def handle_approve(ack, body, client):
    ack()
    state = state_manager.load_pending()
    if not state:
        _update_message(client, body, "⚠️ No pending post found.")
        return

    from linkedin_module import publish_post
    _update_message(client, body, "⏳ Publishing to LinkedIn...")

    try:
        post_url = publish_post(
            post_text=state["generated_post"],
            media_filepaths=state.get("media_filepaths", []),
            media_type=state.get("media_type"),
        )
        _update_message(client, body, f"✅ *Published successfully!*\n{post_url}")
        _archive_draft(state["draft_filepath"])
        _cleanup_media(state.get("media_filepaths", []))
        state_manager.clear_pending()
        _log_published(state["draft_filepath"], state["generated_post"], post_url, state.get("tone"))
    except Exception as e:
        log.error(f"LinkedIn publish failed: {e}")
        _update_message(client, body, f"❌ *Publish failed:* `{e}`")


@app.action("edit_post")
def handle_edit(ack, body, client):
    ack()
    state = state_manager.load_pending()
    if not state:
        _update_message(client, body, "⚠️ No pending post found.")
        return

    # Open a pop-up window in Slack pre-filled with the generated text
    client.views_open(
        trigger_id=body["trigger_id"],
        view={
            "type": "modal",
            "callback_id": "edit_modal",
            "private_metadata": body["channel"]["id"], # <-- We stash the True Channel ID here
            "title": {"type": "plain_text", "text": "Edit Post"},
            "submit": {"type": "plain_text", "text": "Save Changes"},
            "close": {"type": "plain_text", "text": "Cancel"},
            "blocks": [
                {
                    "type": "input",
                    "block_id": "edit_block",
                    "label": {"type": "plain_text", "text": "Tweak your draft before publishing:"},
                    "element": {
                        "type": "plain_text_input",
                        "action_id": "edit_input",
                        "multiline": True,
                        "initial_value": state["generated_post"],
                    },
                }
            ],
        },
    )


@app.view("edit_modal")
def handle_edit_submit(ack, body, client, view):
    ack()
    # Grab the text the user just typed into the modal
    new_text = view["state"]["values"]["edit_block"]["edit_input"]["value"]
    
    # Retrieve the true Channel ID from the metadata
    real_channel_id = view["private_metadata"]
    
    state = state_manager.load_pending()
    if not state:
        return

    # Overwrite the AI's text in your local memory file
    state_manager.update_generated_post(new_text)

    # --- Rebuild the Slack UI so the user can see their edits ---
    from ai_module import TONE_DIRECTIVES
    from pathlib import Path
    
    tone_label = TONE_DIRECTIVES.get(state.get("tone", "professional"), {}).get("label", "")
    media_filepaths = state.get("media_filepaths", [])
    media_type = state.get("media_type")
    count = len(media_filepaths)

    # 1. Determine Header Icon
    if media_type == "IMAGE" and count > 0:
        media_icon = f" 🖼️ ×{count}" if count > 1 else " 🖼️"
    elif media_type == "VIDEO":
        media_icon = " 🎬"
    else:
        media_icon = ""

    # 2. Build the core blocks
    blocks = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": f"LinkedIn Post — Awaiting Approval{media_icon}"},
        },
        {
            "type": "section",
            "fields": [
                {"type": "mrkdwn", "text": f"*Source:* `{Path(state['draft_filepath']).name}`"},
                {"type": "mrkdwn", "text": f"*Tone:* {tone_label or '—'}"},
            ],
        },
        {"type": "divider"},
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": new_text}, # <--- THIS INJECTS THE NEW TEXT
        },
    ]

    # 3. Add Media Context if files exist
    if media_filepaths:
        elements = [{"type": "mrkdwn", "text": f"📎 *Media ({count} file{'s' if count > 1 else ''}):*"}]
        for path in media_filepaths:
            elements.append({"type": "mrkdwn", "text": f"`{Path(path).name}`"})
        blocks.append({"type": "context", "elements": elements})

    # 4. Add Buttons back
    blocks += [
        {"type": "divider"},
        {
            "type": "actions",
            "block_id": "post_actions",
            "elements": [
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "✅ Approve & Publish"},
                    "style": "primary",
                    "action_id": "approve_post",
                    "confirm": {
                        "title": {"type": "plain_text", "text": "Publish to LinkedIn?"},
                        "text": {"type": "mrkdwn", "text": "This will post immediately to your LinkedIn feed."},
                        "confirm": {"type": "plain_text", "text": "Yes, publish it"},
                        "deny": {"type": "plain_text", "text": "Wait, go back"},
                    },
                },
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "✏️ Manual Edit"},
                    "action_id": "edit_post",
                },
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "🔄 Regenerate"},
                    "action_id": "regenerate_post",
                },
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "🗑️ Discard"},
                    "style": "danger",
                    "action_id": "discard_post",
                },
            ],
        },
    ]

    # 5. Force Slack to update the original message using the REAL ID
    client.chat_update(
        channel=real_channel_id,
        ts=state["slack_ts"],
        blocks=blocks,
        text="LinkedIn draft updated"
    )
    
          
@app.action("regenerate_post")
def handle_regenerate(ack, body, client):
    ack()
    client.views_open(
        trigger_id=body["trigger_id"],
        view={
            "type": "modal",
            "callback_id": "regenerate_modal",
            "private_metadata": body["channel"]["id"], # <-- FIX 1: Pass the true Channel ID
            "title": {"type": "plain_text", "text": "Regenerate Post"},
            "submit": {"type": "plain_text", "text": "Regenerate"},
            "close": {"type": "plain_text", "text": "Cancel"},
            "blocks": [
                {
                    "type": "input",
                    "block_id": "feedback_block",
                    "label": {"type": "plain_text", "text": "What should be changed?"},
                    "element": {
                        "type": "plain_text_input",
                        "action_id": "feedback_input",
                        "multiline": True,
                        "placeholder": {
                            "type": "plain_text",
                            "text": 'e.g. "Make it shorter" or "Focus more on the technical challenge"',
                        },
                    },
                }
            ],
        },
    )


@app.view("regenerate_modal")
def handle_regenerate_submit(ack, body, client, view):
    ack()
    feedback = view["state"]["values"]["feedback_block"]["feedback_input"]["value"]
    real_channel_id = view["private_metadata"] # <-- Retrieve the Channel ID
    
    state = state_manager.load_pending()
    if not state:
        return

    # 1. Instantly update the Slack message to a "Loading" state so the user knows it's working
    client.chat_update(
        channel=real_channel_id,
        ts=state["slack_ts"],
        text="⏳ Regenerating...",
        blocks=[{"type": "section", "text": {"type": "mrkdwn", "text": f"⏳ *Regenrating with Gemini...*\nApplying feedback: _{feedback}_"}}]
    )

    try:
        # 2. Call the AI for the new text
        new_post = generate_post(
            state["raw_notes"],
            tone=state.get("tone", "professional"),
            feedback=feedback,
        )
        
        # 3. Save the new text to local memory
        state_manager.update_generated_post(new_post)

        # 4. Rebuild the Slack UI (just like the edit button)
        from ai_module import TONE_DIRECTIVES
        from pathlib import Path
        
        tone_label = TONE_DIRECTIVES.get(state.get("tone", "professional"), {}).get("label", "")
        media_filepaths = state.get("media_filepaths", [])
        media_type = state.get("media_type")
        count = len(media_filepaths)

        if media_type == "IMAGE" and count > 0:
            media_icon = f" 🖼️ ×{count}" if count > 1 else " 🖼️"
        elif media_type == "VIDEO":
            media_icon = " 🎬"
        else:
            media_icon = ""

        blocks = [
            {
                "type": "header",
                "text": {"type": "plain_text", "text": f"LinkedIn Post — Awaiting Approval{media_icon}"},
            },
            {
                "type": "section",
                "fields": [
                    {"type": "mrkdwn", "text": f"*Source:* `{Path(state['draft_filepath']).name}`"},
                    {"type": "mrkdwn", "text": f"*Tone:* {tone_label or '—'}"},
                ],
            },
            {"type": "divider"},
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": new_post}, # <--- Inject the newly regenerated text
            },
        ]

        if media_filepaths:
            elements = [{"type": "mrkdwn", "text": f"📎 *Media ({count} file{'s' if count > 1 else ''}):*"}]
            for path in media_filepaths:
                elements.append({"type": "mrkdwn", "text": f"`{Path(path).name}`"})
            blocks.append({"type": "context", "elements": elements})

        blocks += [
            {"type": "divider"},
            {
                "type": "actions",
                "block_id": "post_actions",
                "elements": [
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "✅ Approve & Publish"},
                        "style": "primary",
                        "action_id": "approve_post",
                        "confirm": {
                            "title": {"type": "plain_text", "text": "Publish to LinkedIn?"},
                            "text": {"type": "mrkdwn", "text": "This will post immediately to your LinkedIn feed."},
                            "confirm": {"type": "plain_text", "text": "Yes, publish it"},
                            "deny": {"type": "plain_text", "text": "Wait, go back"},
                        },
                    },
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "✏️ Manual Edit"},
                        "action_id": "edit_post",
                    },
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "🔄 Regenerate"},
                        "action_id": "regenerate_post",
                    },
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "🗑️ Discard"},
                        "style": "danger",
                        "action_id": "discard_post",
                    },
                ],
            },
        ]

        # 5. Force Slack to overwrite the Loading State with the final UI
        client.chat_update(
            channel=real_channel_id,
            ts=state["slack_ts"],
            blocks=blocks,
            text="LinkedIn draft regenerated"
        )
        
    except Exception as e:
        # If the API fails, show the error right inside the message block
        client.chat_update(
            channel=real_channel_id,
            ts=state["slack_ts"],
            text=f"❌ Regeneration failed: `{e}`",
            blocks=[{"type": "section", "text": {"type": "mrkdwn", "text": f"❌ *Regeneration failed:* `{e}`"}}]
        )

@app.action("discard_post")
def handle_discard(ack, body, client):
    ack()
    state = state_manager.load_pending()
    if state:
        _archive_draft(state["draft_filepath"], discarded=True)
        _cleanup_media(state.get("media_filepaths", []))
        state_manager.clear_pending()
    _update_message(client, body, "🗑️ Draft discarded and archived.")


# ─────────────────────────────────────────────
# File downloader
# ─────────────────────────────────────────────

def _download_slack_file(file_obj: dict, client) -> tuple | None:
    url = file_obj.get("url_private_download") or file_obj.get("url_private")
    if not url:
        return None

    filename = file_obj.get("name", "slack_file")
    ext      = Path(filename).suffix.lower()

    if ext in IMAGE_EXTS:
        media_type = "IMAGE"
    elif ext in VIDEO_EXTS:
        media_type = "VIDEO"
    else:
        log.warning(f"Unsupported file type: {ext} ({filename}) — skipped")
        return None

    import time
    local_path = DRAFTS_DIR / f"slack_{int(time.time())}_{filename}"
    headers    = {"Authorization": f"Bearer {os.getenv('SLACK_BOT_TOKEN')}"}
    response   = requests.get(url, headers=headers, stream=True)

    if response.status_code != 200:
        log.error(f"Failed to download {filename}: HTTP {response.status_code}")
        return None

    with open(local_path, "wb") as f:
        for chunk in response.iter_content(chunk_size=8192):
            f.write(chunk)

    return str(local_path), media_type


# ─────────────────────────────────────────────
# Emergency Override Command
# ─────────────────────────────────────────────

@app.command("/clear")
def handle_clear_command(ack, respond, command):
    # Acknowledge the command request immediately
    ack()
    
    # Wipe all active memory states
    import state_manager
    state_manager.clear_pre_generation()
    state_manager.clear_pending()
    
    # Send a private ephemeral message back to the user
    respond("🧹 *System memory cleared!* All stuck drafts have been wiped. You are ready to start a new one.")


# ─────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────

def _update_message(client, body, text: str):
    client.chat_update(
        channel=body["channel"]["id"],
        ts=body["message"]["ts"],
        text=text,
        blocks=[{"type": "section", "text": {"type": "mrkdwn", "text": text}}],
    )


def _archive_draft(filepath: str, discarded: bool = False):
    import shutil, time
    src = Path(filepath)
    if not src.exists():
        return
    suffix      = "discarded" if discarded else "published"
    archive_dir = src.parent.parent / "logs" / "archived_drafts"
    archive_dir.mkdir(parents=True, exist_ok=True)
    dest = archive_dir / f"{src.stem}_{suffix}_{int(time.time())}{src.suffix}"
    shutil.move(str(src), str(dest))


def _cleanup_media(filepaths: list):
    """Delete all downloaded media files after publishing or discarding."""
    for filepath in filepaths or []:
        try:
            Path(filepath).unlink(missing_ok=True)
        except Exception as e:
            log.warning(f"Could not delete media file {filepath}: {e}")


def _log_published(filepath: str, post_text: str, post_url: str, tone: str = None):
    import time
    log_dir  = Path(__file__).parent / "logs"
    log_dir.mkdir(exist_ok=True)
    log_file = log_dir / "published.log"
    entry = (
        f"\n{'='*60}\n"
        f"Timestamp : {time.strftime('%Y-%m-%d %H:%M:%S')}\n"
        f"Tone      : {tone or 'unknown'}\n"
        f"Source    : {filepath}\n"
        f"URL       : {post_url}\n"
        f"Post      :\n{post_text}\n"
    )
    with open(log_file, "a", encoding="utf-8") as f:
        f.write(entry)


def start_socket_listener():
    handler = SocketModeHandler(app, os.getenv("SLACK_APP_TOKEN"))
    handler.start()
