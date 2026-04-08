import json
import time
from pathlib import Path

STATE_DIR    = Path(__file__).parent / "state"
STATE_DIR.mkdir(exist_ok=True)
PENDING_FILE = STATE_DIR / "pending.json"
PRE_GEN_FILE = STATE_DIR / "pre_generation_state.json"


# ─────────────────────────────────────────────
# Phase 1: Pre-generation state
# ─────────────────────────────────────────────

def save_pre_generation(
    raw_notes: str,
    slack_channel: str,
    slack_user_id: str,
    media_filepaths: list = None,
    media_type: str = None,
) -> None:
    state = {
        "raw_notes": raw_notes,
        "slack_channel": slack_channel,
        "slack_user_id": slack_user_id,
        "media_filepaths": media_filepaths or [],
        "media_type": media_type,
        "submitted_at": time.time(),
    }
    PRE_GEN_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")


def load_pre_generation() -> dict | None:
    if not PRE_GEN_FILE.exists():
        return None
    try:
        return json.loads(PRE_GEN_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, KeyError):
        return None


def clear_pre_generation() -> None:
    if PRE_GEN_FILE.exists():
        PRE_GEN_FILE.unlink()


def is_pre_generation_pending() -> bool:
    return PRE_GEN_FILE.exists()


# ─────────────────────────────────────────────
# Phase 2: Pending approval state
# ─────────────────────────────────────────────

def save_pending(
    draft_filepath: str,
    raw_notes: str,
    generated_post: str,
    slack_ts: str,
    slack_channel: str,
    media_filepaths: list = None,
    media_type: str = None,
    tone: str = None,
) -> None:
    state = {
        "draft_filepath": draft_filepath,
        "raw_notes": raw_notes,
        "generated_post": generated_post,
        "slack_ts": slack_ts,
        "slack_channel": slack_channel,
        "media_filepaths": media_filepaths or [],
        "media_type": media_type,
        "tone": tone,
        "submitted_at": time.time(),
    }
    PENDING_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")


def load_pending() -> dict | None:
    if not PENDING_FILE.exists():
        return None
    try:
        return json.loads(PENDING_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, KeyError):
        return None


def clear_pending() -> None:
    if PENDING_FILE.exists():
        PENDING_FILE.unlink()


def is_pending() -> bool:
    return PENDING_FILE.exists()


def is_any_active() -> bool:
    return is_pre_generation_pending() or is_pending()


def update_generated_post(new_post: str) -> None:
    state = load_pending()
    if state:
        state["generated_post"] = new_post
        PENDING_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")


def update_media(media_filepaths: list, media_type: str) -> None:
    """Update media fields in pending state."""
    state = load_pending()
    if state:
        state["media_filepaths"] = media_filepaths or []
        state["media_type"] = media_type
        PENDING_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")
