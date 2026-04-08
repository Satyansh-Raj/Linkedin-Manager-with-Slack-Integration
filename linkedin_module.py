import os
import sys
import json
import time
import webbrowser
import requests
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs, urlencode
from pathlib import Path
from dotenv import load_dotenv, set_key

load_dotenv()

ENV_FILE            = Path(__file__).parent / ".env"
LINKEDIN_AUTH_URL   = "https://www.linkedin.com/oauth/v2/authorization"
LINKEDIN_TOKEN_URL  = "https://www.linkedin.com/oauth/v2/accessToken"
LINKEDIN_UGC_URL    = "https://api.linkedin.com/v2/ugcPosts"
LINKEDIN_ASSETS_URL = "https://api.linkedin.com/v2/assets?action=registerUpload"
LINKEDIN_ME_URL     = "https://api.linkedin.com/v2/userinfo"
SCOPES = "openid profile email w_member_social"


# ─────────────────────────────────────────────
# OAuth
# ─────────────────────────────────────────────

class _CallbackHandler(BaseHTTPRequestHandler):
    auth_code = None

    def do_GET(self):
        params = parse_qs(urlparse(self.path).query)
        if "code" in params:
            _CallbackHandler.auth_code = params["code"][0]
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"<h2>Auth successful! You can close this tab.</h2>")
        else:
            self.send_response(400)
            self.end_headers()
            self.wfile.write(b"<h2>Auth failed - no code received.</h2>")

    def log_message(self, *args):
        pass


def run_oauth_flow():
    client_id     = os.getenv("LINKEDIN_CLIENT_ID")
    client_secret = os.getenv("LINKEDIN_CLIENT_SECRET")
    redirect_uri  = os.getenv("LINKEDIN_REDIRECT_URI", "http://localhost:8080/callback")

    if not client_id or not client_secret:
        print("ERROR: LINKEDIN_CLIENT_ID and LINKEDIN_CLIENT_SECRET must be set in .env")
        sys.exit(1)

    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "scope": SCOPES,
    }
    auth_url = f"{LINKEDIN_AUTH_URL}?{urlencode(params)}"
    print(f"\nOpening LinkedIn authorization page...\nIf browser doesn't open:\n{auth_url}\n")
    webbrowser.open(auth_url)

    server = HTTPServer(("localhost", 8080), _CallbackHandler)
    print("Waiting for LinkedIn callback on http://localhost:8080 ...")
    server.handle_request()

    if not _CallbackHandler.auth_code:
        print("ERROR: Did not receive authorization code.")
        sys.exit(1)

    token_response = requests.post(
        LINKEDIN_TOKEN_URL,
        data={
            "grant_type": "authorization_code",
            "code": _CallbackHandler.auth_code,
            "redirect_uri": redirect_uri,
            "client_id": client_id,
            "client_secret": client_secret,
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    token_response.raise_for_status()
    token_data   = token_response.json()
    access_token = token_data["access_token"]
    expires_in   = token_data.get("expires_in", 5184000)
    expiry_ts    = str(int(time.time()) + expires_in)

    me_response = requests.get(
        LINKEDIN_ME_URL,
        headers={"Authorization": f"Bearer {access_token}"},
    )
    me_response.raise_for_status()
    member_urn = f"urn:li:person:{me_response.json()['sub']}"

    set_key(str(ENV_FILE), "LINKEDIN_ACCESS_TOKEN", access_token)
    set_key(str(ENV_FILE), "LINKEDIN_TOKEN_EXPIRY", expiry_ts)
    set_key(str(ENV_FILE), "LINKEDIN_MEMBER_URN", member_urn)

    print(f"\n✅ Auth successful!")
    print(f"   Member URN : {member_urn}")
    print(f"   Token expires in {expires_in // 86400} days\n")


# ─────────────────────────────────────────────
# Token management
# ─────────────────────────────────────────────

def _check_token_valid() -> bool:
    token  = os.getenv("LINKEDIN_ACCESS_TOKEN")
    expiry = os.getenv("LINKEDIN_TOKEN_EXPIRY")
    if not token or not expiry:
        return False
    return time.time() < (float(expiry) - 86400)


def _ensure_authenticated():
    if not _check_token_valid():
        raise RuntimeError(
            "LinkedIn token is missing or expired.\n"
            "Run: python linkedin_module.py --auth"
        )


def _auth_headers() -> dict:
    load_dotenv(override=True)
    return {
        "Authorization": f"Bearer {os.getenv('LINKEDIN_ACCESS_TOKEN')}",
        "Content-Type": "application/json",
        "X-Restli-Protocol-Version": "2.0.0",
    }


# ─────────────────────────────────────────────
# Media upload pipeline (Steps 1 & 2)
# ─────────────────────────────────────────────

def _register_media_upload(media_type: str) -> tuple:

    member_urn = os.getenv("LINKEDIN_MEMBER_URN")
    recipe_map = {
        "IMAGE": "urn:li:digitalmediaRecipe:feedshare-image",
        "VIDEO": "urn:li:digitalmediaRecipe:feedshare-video",
    }
    recipe = recipe_map.get(media_type)
    if not recipe:
        raise ValueError(f"Unsupported media type: {media_type}")

    payload = {
        "registerUploadRequest": {
            "recipes": [recipe],
            "owner": member_urn,
            "serviceRelationships": [
                {
                    "relationshipType": "OWNER",
                    "identifier": "urn:li:userGeneratedContent",
                }
            ],
        }
    }

    response = requests.post(
        LINKEDIN_ASSETS_URL,
        headers=_auth_headers(),
        data=json.dumps(payload),
    )
    if response.status_code not in (200, 201):
        raise RuntimeError(f"Media registration failed {response.status_code}: {response.text}")

    data       = response.json()
    upload_url = data["value"]["uploadMechanism"][
        "com.linkedin.digitalmedia.uploading.MediaUploadHttpRequest"
    ]["uploadUrl"]
    asset_urn  = data["value"]["asset"]
    return upload_url, asset_urn


def _upload_binary_file(upload_url: str, filepath: str, media_type: str):
    """Step 2: PUT raw file bytes to the upload URL."""
    import mimetypes
    detected, _  = mimetypes.guess_type(filepath)
    mime_fallback = {"IMAGE": "image/jpeg", "VIDEO": "video/mp4"}
    content_type  = detected or mime_fallback.get(media_type, "application/octet-stream")

    with open(filepath, "rb") as f:
        file_bytes = f.read()

    response = requests.put(
        upload_url,
        data=file_bytes,
        headers={
            "Authorization": f"Bearer {os.getenv('LINKEDIN_ACCESS_TOKEN')}",
            "Content-Type": content_type,
        },
    )
    if response.status_code not in (200, 201):
        raise RuntimeError(f"Binary upload failed {response.status_code}: {response.text}")


# ─────────────────────────────────────────────
# Publishing (Step 3)
# ─────────────────────────────────────────────

def publish_post(
    post_text: str,
    media_filepaths: list = None,
    media_type: str = None,
) -> str:

    _ensure_authenticated()
    load_dotenv(override=True)

    member_urn      = os.getenv("LINKEDIN_MEMBER_URN")
    media_filepaths = media_filepaths or []

    if not member_urn:
        raise RuntimeError("LINKEDIN_MEMBER_URN not set. Run --auth first.")

    asset_urns = []

    # ── Upload loop (Steps 1 + 2 per file) ──
    if media_filepaths and media_type:
        for filepath in media_filepaths:
            print(f"Uploading {Path(filepath).name} ({media_type})...")
            upload_url, asset_urn = _register_media_upload(media_type)
            _upload_binary_file(upload_url, filepath, media_type)
            asset_urns.append(asset_urn)
            print(f"  ✓ Uploaded. Asset URN: {asset_urn}")

    # ── Build ugcPosts payload (Step 3) ──
    if asset_urns:
        media_array = [
            {
                "status": "READY",
                "description": {"text": ""},
                "media": urn,
                "title": {"text": ""},
            }
            for urn in asset_urns
        ]
        share_content = {
            "shareCommentary": {"text": post_text},
            "shareMediaCategory": media_type,
            "media": media_array,
        }
    else:
        share_content = {
            "shareCommentary": {"text": post_text},
            "shareMediaCategory": "NONE",
        }

    payload = {
        "author": member_urn,
        "lifecycleState": "PUBLISHED",
        "specificContent": {
            "com.linkedin.ugc.ShareContent": share_content
        },
        "visibility": {
            "com.linkedin.ugc.MemberNetworkVisibility": "PUBLIC"
        },
    }

    response = requests.post(
        LINKEDIN_UGC_URL,
        headers=_auth_headers(),
        data=json.dumps(payload),
    )
    if response.status_code not in (200, 201):
        raise RuntimeError(f"LinkedIn API error {response.status_code}: {response.text}")

    post_id  = response.headers.get("X-RestLi-Id", "unknown")
    post_url = f"https://www.linkedin.com/feed/update/{post_id}/"
    return post_url


# ─────────────────────────────────────────────
# CLI entry point
# ─────────────────────────────────────────────

if __name__ == "__main__":
    if "--auth" in sys.argv:
        run_oauth_flow()
    else:
        print("Usage: python linkedin_module.py --auth")
