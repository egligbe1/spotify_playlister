import spotipy
from spotipy.oauth2 import SpotifyOAuth
import json
import requests
import datetime
import os
import sys
import logging
from pathlib import Path
from dotenv import load_dotenv
from PIL import Image
import io
import base64
from time import sleep

# Load .env file
env_path = Path('.') / '.env'
load_dotenv(dotenv_path=env_path)

# Set up logging (no sensitive data)
logging.basicConfig(filename='updater.log', level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logging.info(f"Loading .env file from: {env_path.resolve()}")

# Constants
SCOPE = 'playlist-modify-public playlist-modify-private playlist-read-private ugc-image-upload'
REDIRECT_URI = 'http://localhost:8888/callback'
TOKEN_FILE = 'token_info.json'
RECORD_FILE = 'playlist_record.json'
MAX_RETRIES = 3
TIMEOUT = 30
MAX_SONGS = 50  # Maximum songs in target playlist

# Load or initialize playlist record
def load_record():
    try:
        with open(RECORD_FILE, 'r') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return []

# Save playlist record
def save_record(record):
    with open(RECORD_FILE, 'w') as f:
        json.dump(record, f)

# Check internet connection
def is_connected():
    for attempt in range(MAX_RETRIES):
        try:
            requests.get('https://www.google.com', timeout=TIMEOUT)
            return True
        except requests.ConnectionError:
            sleep(2)
    logging.error("No internet connection.")
    return False

# Get Spotify client
def get_spotify_client():
    client_id = os.getenv('SPOTIFY_CLIENT_ID')
    client_secret = os.getenv('SPOTIFY_CLIENT_SECRET')
    username = os.getenv('SPOTIFY_USERNAME')
    logging.info("Spotify credentials loaded from .env")
    if not all([client_id, client_secret, username]):
        logging.error("Missing required variables in .env.")
        sys.exit("Error: Set SPOTIFY_CLIENT_ID, SPOTIFY_CLIENT_SECRET, and SPOTIFY_USERNAME in .env.")

    auth_manager = SpotifyOAuth(
        client_id=client_id,
        client_secret=client_secret,
        redirect_uri=REDIRECT_URI,
        scope=SCOPE,
        username=username,
        cache_path=TOKEN_FILE
    )

    if Path(TOKEN_FILE).exists():
        try:
            token_info = auth_manager.get_cached_token()
            if auth_manager.is_token_expired(token_info):
                auth_manager.refresh_access_token(token_info['refresh_token'])
                logging.info("Token refreshed.")
            return spotipy.Spotify(auth_manager=auth_manager)
        except Exception as e:
            logging.error(f"Token refresh failed: {e}")
            sys.exit("Token refresh failed. Run with --initial_setup.")
    else:
        sys.exit("Run with --initial_setup to authenticate.")

# Initial authentication setup
def initial_setup():
    client_id = os.getenv('SPOTIFY_CLIENT_ID')
    client_secret = os.getenv('SPOTIFY_CLIENT_SECRET')
    username = os.getenv('SPOTIFY_USERNAME')
    logging.info("Spotify credentials loaded from .env")
    if not all([client_id, client_secret, username]):
        logging.error("Missing required variables in .env.")
        sys.exit("Error: Set SPOTIFY_CLIENT_ID, SPOTIFY_CLIENT_SECRET, and SPOTIFY_USERNAME in .env.")

    auth_manager = SpotifyOAuth(
        client_id=client_id,
        client_secret=client_secret,
        redirect_uri=REDIRECT_URI,
        scope=SCOPE,
        username=username,
        cache_path=TOKEN_FILE
    )
    
    auth_url = auth_manager.get_authorize_url()
    print(f"Please visit this URL to authorize: {auth_url}")
    response = input("Enter the URL you were redirected to: ")
    logging.info(f"User entered redirect URL: {response}")
    
    try:
        code = auth_manager.parse_response_code(response)
        token_info = auth_manager.get_access_token(code, as_dict=False)
        auth_manager.cache_handler.save_token_to_cache(auth_manager.get_access_token(code, as_dict=True))
        logging.info("Initial setup complete.")
        print("Setup complete. Token saved in token_info.json.")
    except Exception as e:
        logging.error(f"Authentication failed: {e}")
        sys.exit(f"Authentication error: {e}")

# Update playlist metadata
def update_playlist_metadata(sp, target_playlist):
    try:
        results = sp.playlist_tracks(target_playlist, limit=1)
        if not results['items']:
            logging.warning("No tracks in target playlist.")
            return
        
        first_track = results['items'][0]['track']
        artist_name = first_track['artists'][0]['name']
        album_images = first_track['album']['images']

        description_template = os.getenv('PLAYLIST_DESCRIPTION')
        description = description_template.format(artist_name)
        sp.playlist_change_details(target_playlist, description=description)
        logging.info(f"Updated description to: {description}")

        if album_images:
            image_url = album_images[0]['url']
            response = requests.get(image_url, timeout=TIMEOUT)
            img = Image.open(io.BytesIO(response.content)).resize((640, 640), Image.Resampling.LANCZOS)
            img_byte_arr = io.BytesIO()
            img.convert('RGB').save(img_byte_arr, format='JPEG', quality=85)
            base64_image = base64.b64encode(img_byte_arr.getvalue()).decode('utf-8')
            sp.playlist_upload_cover_image(target_playlist, base64_image)
            logging.info("Cover image updated.")
    except Exception as e:
        logging.error(f"Metadata update failed: {e}")

# Main playlist update logic
def update_playlist():
    now = datetime.datetime.now()
    # if now.weekday() != 5 or now.hour != 0 or now.minute > 5:
    #     logging.info(f"Not scheduled time. Current time: {now}. Expected: Saturday 00:00-00:05.")
    #     print(f"Not scheduled time. Current time: {now}. Expected: Saturday 00:00-00:05.")
    #     return

    if not is_connected():
        logging.error("No internet connection.")
        sys.exit("No internet connection.")
    
    sp = get_spotify_client()
    source_playlist = os.getenv('SOURCE_PLAYLIST')
    target_playlist = os.getenv('TARGET_PLAYLIST')
    username = os.getenv('SPOTIFY_USERNAME')
    if not all([source_playlist, target_playlist]):
        logging.error("Missing SOURCE_PLAYLIST or TARGET_PLAYLIST in .env.")
        sys.exit("Error: Set SOURCE_PLAYLIST and TARGET_PLAYLIST in .env.")

    try:
        # Fetch current tracks in the target playlist
        current_tracks = []
        results = sp.playlist_tracks(target_playlist)
        while results:
            current_tracks.extend([item['track']['id'] for item in results['items'] if item['track']])
            results = sp.next(results) if results['next'] else None
        logging.info(f"Current tracks in target playlist: {len(current_tracks)}")

        # Fetch tracks from source playlist
        source_results = sp.playlist_tracks(source_playlist)
        source_songs = [item['track']['id'] for item in source_results['items'] if item['track']]
        logging.info(f"Source playlist tracks: {len(source_songs)}")
        new_songs = [song for song in source_songs if song not in current_tracks]

        # Add new songs if any
        if new_songs:
            sp.user_playlist_add_tracks(username, target_playlist, new_songs)
            logging.info(f"Added {len(new_songs)} new tracks from source playlist.")
            # Refresh current tracks after adding
            current_tracks = []
            results = sp.playlist_tracks(target_playlist)
            while results:
                current_tracks.extend([item['track']['id'] for item in results['items'] if item['track']])
                results = sp.next(results) if results['next'] else None
            logging.info(f"Total tracks after adding: {len(current_tracks)}")

        # Trim to MAX_SONGS if exceeds
        if len(current_tracks) > MAX_SONGS:
            songs_to_remove = current_tracks[:len(current_tracks) - MAX_SONGS]
            sp.user_playlist_remove_all_occurrences_of_tracks(username, target_playlist, songs_to_remove)
            logging.info(f"Removed {len(songs_to_remove)} tracks to trim to {MAX_SONGS}.")
            current_tracks = current_tracks[len(songs_to_remove):]

        # Update metadata
        update_playlist_metadata(sp, target_playlist)

        # Save the current track IDs
        save_record(current_tracks)
        logging.info("Playlist update completed.")
        print("Update completed.")
    except Exception as e:
        logging.error(f"Update failed: {e}")
        sys.exit(f"Error: {e}")

if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--initial_setup":
        initial_setup()
    else:
        update_playlist()