"""
Run this script locally once to generate a fresh token_info.json.

Steps:
  1. python setup_token.py
  2. A browser window opens — log in and authorise the app.
  3. The script captures the callback automatically (no pasting needed).
  4. token_info.json is saved and the refresh_token is printed.
     Copy that value into GitHub → Settings → Secrets → SPOTIFY_REFRESH_TOKEN.
"""

import json
import os
import time as _time
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse, parse_qs

import requests
from dotenv import load_dotenv

load_dotenv()

CLIENT_ID     = os.getenv('SPOTIFY_CLIENT_ID')
CLIENT_SECRET = os.getenv('SPOTIFY_CLIENT_SECRET')
REDIRECT_URI  = 'http://127.0.0.1:8888/callback'
SCOPE         = 'playlist-modify-public playlist-modify-private playlist-read-private ugc-image-upload'
TOKEN_FILE    = 'token_info.json'

if not all([CLIENT_ID, CLIENT_SECRET]):
    raise SystemExit("Missing SPOTIFY_CLIENT_ID or SPOTIFY_CLIENT_SECRET in .env")

auth_code = None


class CallbackHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        global auth_code
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)
        if 'code' in params:
            auth_code = params['code'][0]
            self.send_response(200)
            self.send_header('Content-type', 'text/html')
            self.end_headers()
            self.wfile.write(b'<h2>Authorised! You can close this tab.</h2>')
        else:
            error = params.get('error', ['unknown'])[0]
            self.send_response(400)
            self.send_header('Content-type', 'text/html')
            self.end_headers()
            self.wfile.write(f'<h2>Error: {error}</h2>'.encode())

    def log_message(self, *args):
        pass  # suppress server logs


def exchange_code(code):
    resp = requests.post(
        'https://accounts.spotify.com/api/token',
        data={
            'grant_type':   'authorization_code',
            'code':          code,
            'redirect_uri':  REDIRECT_URI,
        },
        auth=(CLIENT_ID, CLIENT_SECRET),
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()


# Build the auth URL
import urllib.parse as _up
auth_url = (
    'https://accounts.spotify.com/authorize?'
    + _up.urlencode({
        'client_id':     CLIENT_ID,
        'response_type': 'code',
        'redirect_uri':  REDIRECT_URI,
        'scope':         SCOPE,
    })
)

# Start local callback server in a background thread
server = HTTPServer(('127.0.0.1', 8888), CallbackHandler)
thread = threading.Thread(target=server.handle_request)  # handles exactly one request
thread.daemon = True
thread.start()

print(f"\nOpening Spotify login in your browser...")
print(f"If the browser doesn't open, visit:\n  {auth_url}\n")
webbrowser.open(auth_url)

thread.join(timeout=300)
server.server_close()

if not auth_code:
    raise SystemExit("Timed out waiting for authorisation. Run the script again.")

print("Authorisation received. Exchanging code for tokens...")
token_info = exchange_code(auth_code)
# Spotipy requires expires_at (Unix timestamp); the API only returns expires_in (seconds)
token_info.setdefault('expires_at', int(_time.time()) + token_info.get('expires_in', 3600))

with open(TOKEN_FILE, 'w') as f:
    json.dump(token_info, f, indent=2)

refresh_token = token_info.get('refresh_token', '')
print(f"\ntokens saved to {TOKEN_FILE}")
print(f"\n{'='*60}")
print("SPOTIFY_REFRESH_TOKEN (add this to GitHub Secrets):")
print(f"\n  {refresh_token}\n")
print(f"{'='*60}")
print("\nDone. You can now run: FORCE_UPDATE=true python main.py")
