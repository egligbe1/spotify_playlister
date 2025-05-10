import spotipy
from spotipy.oauth2 import SpotifyOAuth
import json
import requests
import datetime
import os
import logging
from pathlib import Path
from dotenv import load_dotenv
from PIL import Image
import io
import base64
from time import sleep
import random

# Load environment variables (from .env locally, GitHub Secrets/Vars in cloud)
load_dotenv()

# Set up logging
logging.basicConfig(filename='updater.log', level=logging.DEBUG, format='%(asctime)s - %(levelname)s - %(message)s')
logging.info("Starting Spotify playlist updater")

# Constants
SCOPE = 'playlist-modify-public playlist-modify-private playlist-read-private ugc-image-upload'
REDIRECT_URI = 'http://localhost:8888/callback'
TOKEN_FILE = 'token_info.json'
RECORD_FILE = 'playlist_record.json'
LAST_UPDATE_FILE = 'last_update.json'
PRIORITY_SONGS_FILE = 'priority_songs.json'
MAX_RETRIES = 3
TIMEOUT = 30
MAX_SONGS = 70

# Load or initialize playlist record
def load_record():
    try:
        with open(RECORD_FILE, 'r') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        logging.info(f"No {RECORD_FILE} found. Initializing empty record.")
        return []

# Save playlist record
def save_record(record):
    try:
        with open(RECORD_FILE, 'w') as f:
            json.dump(record, f)
        logging.debug(f"Saved playlist record to {RECORD_FILE}")
    except Exception as e:
        logging.error(f"Failed to save playlist record: {e}")

# Load last update date
def load_last_update():
    try:
        with open(LAST_UPDATE_FILE, 'r') as f:
            data = json.load(f)
            date = datetime.datetime.fromisoformat(data['last_update']).date()
            logging.debug(f"Loaded last update date: {date}")
            return date
    except (FileNotFoundError, json.JSONDecodeError, KeyError):
        logging.info(f"No {LAST_UPDATE_FILE} found. Assuming first update.")
        return None

# Save last update date
def save_last_update(update_date):
    try:
        with open(LAST_UPDATE_FILE, 'w') as f:
            json.dump({'last_update': update_date.isoformat()}, f)
        logging.debug(f"Saved last update date to {LAST_UPDATE_FILE}")
    except Exception as e:
        logging.error(f"Failed to save last update date: {e}")

# Load priority songs
def load_priority_songs():
    try:
        with open(PRIORITY_SONGS_FILE, 'r') as f:
            data = json.load(f)
            songs = data.get('priority_songs', [])
            for song in songs:
                if not all(key in song for key in ['track_id', 'song_name', 'artist_name']):
                    logging.warning(f"Invalid priority song entry: {song}. Skipping.")
                    continue
                logging.debug(f"Priority song: {song['song_name']} by {song['artist_name']} ({song['track_id']})")
            valid_songs = [song['track_id'] for song in songs if all(key in song for key in ['track_id', 'song_name', 'artist_name'])]
            logging.info(f"Loaded {len(valid_songs)} valid priority songs")
            return valid_songs
    except (FileNotFoundError, json.JSONDecodeError, KeyError):
        logging.info(f"No {PRIORITY_SONGS_FILE} found. No priority songs.")
        return []

# Check internet connection
def is_connected():
    for attempt in range(MAX_RETRIES):
        try:
            requests.get('https://www.google.com', timeout=TIMEOUT)
            logging.debug("Internet connection verified")
            return True
        except requests.ConnectionError:
            logging.warning(f"Internet connection attempt {attempt + 1} failed")
            sleep(2)
    logging.error("No internet connection after retries")
    return False

# Get Spotify client
def get_spotify_client():
    client_id = os.getenv('SPOTIFY_CLIENT_ID')
    client_secret = os.getenv('SPOTIFY_CLIENT_SECRET')
    username = os.getenv('SPOTIFY_USERNAME')
    logging.debug(f"Loaded environment: client_id={bool(client_id)}, client_secret={bool(client_secret)}, username={bool(username)}")
    if not all([client_id, client_secret, username]):
        logging.error("Missing required variables: SPOTIFY_CLIENT_ID, SPOTIFY_CLIENT_SECRET, or SPOTIFY_USERNAME")
        return None

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
                logging.info("Token refreshed")
            sp = spotipy.Spotify(auth_manager=auth_manager)
            # Verify token scopes
            current_user = sp.current_user()
            token_scopes = auth_manager.scope.split()
            required_scopes = SCOPE.split()
            if not all(scope in token_scopes for scope in required_scopes):
                logging.error(f"Token missing required scopes. Has: {token_scopes}, Needs: {required_scopes}")
                return None
            logging.debug(f"Token scopes verified: {token_scopes}")
            return sp
        except Exception as e:
            logging.error(f"Token refresh or validation failed: {e}")
            return None
    else:
        logging.error("No token_info.json found. Run locally with --initial_setup first.")
        return None

# Update playlist metadata
def update_playlist_metadata(sp, target_playlist):
    # Update description
    for attempt in range(MAX_RETRIES):
        try:
            results = sp.playlist_tracks(target_playlist, limit=1)
            logging.debug(f"Playlist tracks API response: {len(results['items'])} items")
            if not results['items']:
                logging.warning("No tracks in target playlist")
                return
            
            first_track = results['items'][0]['track']
            artist_name = first_track['artists'][0]['name'] if first_track['artists'] else "Unknown Artist"
            logging.info(f"Extracted artist name: {artist_name}")

            description_template = os.getenv('PLAYLIST_DESCRIPTION')
            if not description_template or '{}' not in description_template:
                logging.error("Invalid or missing PLAYLIST_DESCRIPTION")
                return
            logging.debug(f"Description template: {description_template}")

            description = description_template.format(artist_name)
            sp.playlist_change_details(target_playlist, description=description)
            logging.info(f"Updated description to: {description}")
            break
        except Exception as e:
            logging.error(f"Description update attempt {attempt + 1} failed: {e}")
            if attempt < MAX_RETRIES - 1:
                sleep(2)
            else:
                logging.error("Max retries reached for description update")
                return

    # Update cover image
    try:
        results = sp.playlist_tracks(target_playlist, limit=1)
        if not results['items']:
            return
        
        album_images = results['items'][0]['track']['album']['images']
        if album_images:
            image_url = album_images[0]['url']
            response = requests.get(image_url, timeout=TIMEOUT)
            img = Image.open(io.BytesIO(response.content)).resize((640, 640), Image.Resampling.LANCZOS)
            img_byte_arr = io.BytesIO()
            img.convert('RGB').save(img_byte_arr, format='JPEG', quality=85)
            base64_image = base64.b64encode(img_byte_arr.getvalue()).decode('utf-8')
            sp.playlist_upload_cover_image(target_playlist, base64_image)
            logging.info("Cover image updated")
    except Exception as e:
        logging.error(f"Cover image update failed: {e}")

# Main playlist update logic
def update_playlist():
    now = datetime.datetime.now(datetime.timezone.utc)
    logging.debug(f"Current time: {now} UTC")
    if now.weekday() != 5:
        logging.info(f"Not Saturday. Expected: Any time on Saturday")
        return

    # Check if already updated this Saturday
    current_date = now.date()
    last_update_date = load_last_update()
    if last_update_date and last_update_date == current_date:
        logging.info(f"Playlist already updated on {current_date}. Skipping.")
        return

    if not is_connected():
        logging.error("No internet connection")
        return
    
    sp = get_spotify_client()
    if not sp:
        logging.error("Spotify client initialization failed")
        return

    source_playlist = os.getenv('SOURCE_PLAYLIST')
    target_playlist = os.getenv('TARGET_PLAYLIST')
    username = os.getenv('SPOTIFY_USERNAME')
    logging.debug(f"Environment: source_playlist={source_playlist}, target_playlist={target_playlist}, username={username}")
    if not all([source_playlist, target_playlist, username]):
        logging.error("Missing SOURCE_PLAYLIST, TARGET_PLAYLIST, or SPOTIFY_USERNAME")
        return

    try:
        # Load priority songs (track IDs only)
        priority_songs = load_priority_songs()
        logging.info(f"Priority songs to include: {len(priority_songs)}")

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
        logging.info(f"Found {len(new_songs)} new tracks from source playlist")

        # Combine all tracks (priority + current + new) and remove duplicates
        all_tracks = priority_songs + current_tracks + new_songs
        original_count = len(all_tracks)
        all_tracks = list(dict.fromkeys(all_tracks))  # Remove duplicates while preserving order
        duplicates_removed = original_count - len(all_tracks)
        logging.info(f"Combined {original_count} tracks, removed {duplicates_removed} duplicates, resulting in {len(all_tracks)} unique tracks")
        random.shuffle(all_tracks)
        logging.info(f"Shuffled all {len(all_tracks)} tracks (including {len(priority_songs)} priority songs)")

        # Clear the target playlist
        if current_tracks:
            sp.user_playlist_remove_all_occurrences_of_tracks(username, target_playlist, current_tracks)
            logging.info("Cleared all existing tracks from target playlist")

        # Trim to MAX_SONGS if necessary, preserving priority songs
        if len(all_tracks) > MAX_SONGS:
            num_priority = len([track for track in all_tracks if track in priority_songs])
            if num_priority > MAX_SONGS:
                logging.warning(f"Priority songs ({num_priority}) exceed MAX_SONGS ({MAX_SONGS}). Trimming all tracks.")
                all_tracks = all_tracks[:MAX_SONGS]
            else:
                num_to_keep = MAX_SONGS - num_priority
                non_priority_tracks = [track for track in all_tracks if track not in priority_songs]
                all_tracks = [track for track in all_tracks if track in priority_songs] + non_priority_tracks[:num_to_keep]
                logging.info(f"Trimmed to {MAX_SONGS} tracks: {num_priority} priority + {num_to_keep} others")
        sp.user_playlist_add_tracks(username, target_playlist, all_tracks)
        logging.info(f"Added {len(all_tracks)} tracks to target playlist")

        # Update metadata
        update_playlist_metadata(sp, target_playlist)

        # Save the current track IDs and update date
        save_record(all_tracks)
        save_last_update(current_date)
        logging.info("Playlist update completed")
    except Exception as e:
        logging.error(f"Update failed: {e}")

if __name__ == "__main__":
    update_playlist()
