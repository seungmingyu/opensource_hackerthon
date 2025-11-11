import os, time, base64, hashlib, secrets
from urllib.parse import urlencode
import requests
from dotenv import load_dotenv

load_dotenv()

SPOTIFY_CLIENT_ID = os.getenv("SPOTIFY_CLIENT_ID")
REDIRECT_URI = os.getenv("SPOTIFY_REDIRECT_URI", "http://127.0.0.1:8000/callback")

AUTH_URL = "https://accounts.spotify.com/authorize"
TOKEN_URL = "https://accounts.spotify.com/api/token"
API = "https://api.spotify.com/v1"

SCOPES = " ".join([
    "user-read-recently-played",
    "user-top-read",
    "user-read-playback-state",
    "playlist-modify-private",
    "playlist-modify-public",
    "user-library-read",  
])

STATE_PKCE = {}

def _pkce():
    v = base64.urlsafe_b64encode(secrets.token_bytes(32)).decode().rstrip("=")
    ch = base64.urlsafe_b64encode(hashlib.sha256(v.encode()).digest()).decode().rstrip("=")
    return v, ch

def build_login_redirect():
    state = secrets.token_urlsafe(16)
    ver, ch = _pkce()
    STATE_PKCE[state] = {"ver": ver, "t": time.time()}
    q = {
        "client_id": SPOTIFY_CLIENT_ID,
        "response_type": "code",
        "redirect_uri": REDIRECT_URI,
        "state": state,
        "scope": SCOPES,
        "code_challenge_method": "S256",
        "code_challenge": ch,
        "show_dialog": "true",  
    }
    return f"{AUTH_URL}?{urlencode(q)}"

def exchange_token(code: str, state: str):
    ver = STATE_PKCE.pop(state)["ver"]
    data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": REDIRECT_URI,
        "client_id": SPOTIFY_CLIENT_ID,
        "code_verifier": ver,
    }
    r = requests.post(TOKEN_URL, data=data, timeout=10)
    r.raise_for_status()
    token_data = r.json()
    return {
        "access_token": token_data["access_token"],
        "refresh_token": token_data.get("refresh_token"),
        "expires_in": token_data.get("expires_in", 3600)
    }


def refresh_access_token(refresh_token: str):
    data = {"grant_type": "refresh_token", "refresh_token": refresh_token, "client_id": SPOTIFY_CLIENT_ID}
    r = requests.post(TOKEN_URL, data=data, timeout=10)
    if r.status_code >= 400:
        print("[spotify] refresh error:", r.status_code, r.text[:500])
        r.raise_for_status()
    td = r.json()
    return {"access_token": td["access_token"], "refresh_token": td.get("refresh_token")}


def get_me(access_token: str):
    r = requests.get(f"{API}/me", headers={"Authorization": f"Bearer {access_token}"}, timeout=10)
    r.raise_for_status()
    return r.json()