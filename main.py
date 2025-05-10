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

# Configure logging
logger = logging.getLogger(__name__)
logging.basicConfig(filename='updater.log', level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger.setLevel(logging.DEBUG)  # Debug for this script only
# Suppress verbose logs from external libraries
logging.getLogger('requests').setLevel(logging.WARNING)
logging.getLogger('urllib3').setLevel(logging.WARNING)
logging.getLogger('spotipy.client').setLevel(logging.WARNING)
logger.info("Starting Spotify playlist updater")

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
        logger.info(f"No {RECORD_FILE} found. Initializing empty record.")
        return []

# Save playlist record
def save_record(record):
    try:
        with open(RECORD_FILE, 'w') as f:
            json.dump(record, f)
        logger.debug(f"Saved playlist record to {RECORD_FILE}")
    except Exception as e:
        logger.error(f"Failed to save playlist record: {e}")

# Load last update date
def load_last_update():
    try:
        with open(LAST_UPDATE_FILE, 'r') as f:
            data = json.load(f)
            date = datetime.datetime.fromisoformat(data['last_update']).date()
            logger.debug(f"Loaded last update date: {date}")
            return date
    except (FileNotFoundError, json.JSONDecodeError, KeyError):
        logger.info(f"No {LAST_UPDATE_FILE} found. Assuming first update.")
        return None

# Save last update date
def save_last_update(update_date):
    try:
        with open(LAST_UPDATE_FILE, 'w') as f:
            json.dump({'last_update': update_date.isoformat()}, f)
        logger.debug(f"Saved last update date to {LAST_UPDATE_FILE}")
    except Exception as e:
        logger.error(f"Failed to save last update date: {e}")

# Load priority songs
def load_priority_songs():
    try:
        with open(PRIORITY_SONGS_FILE, 'r') as f:
            data = json.load(f)
            songs = data.get('priority_songs', [])
            for song in songs:
                if not all(key in song for key in ['track_id', 'song_name', 'artist_name']):
                    logger.warning(f"Invalid priority song entry: {song}. Skipping.")
                    continue
                logger.debug(f"Priority song: {song['song_name']} by {song['artist_name']} ({song['track_id']})")
            valid_songs = [song['track_id'] for song in songs if all(key in song for key in ['track_id', 'song_name', 'artist_name'])]
            logger.info(f"Loaded {len(valid_songs)} valid priority songs")
            return valid_songs
    except (FileNotFoundError, json.JSONDecodeError, KeyError):
        logger.info(f"No {PRIORITY_SONGS_FILE} found. No priority songs.")
        return []

# Check internet connection
def is_connected():
    for attempt in range(MAX_RETRIES):
        try:
            requests.get('https://www.google.com', timeout=TIMEOUT)
            logger.debug("Internet connection verified")
            return True
        except requests.ConnectionError:
            logger.warning(f"Internet connection attempt {attempt + 1} failed")
            sleep(2)
    logger.error("No internet connection after retries")
    return False

# Get Spotify client
def get_spotify_client():
    client_id = os.getenv('SPOTIFY_CLIENT_ID')
    client_secret = os.getenv('SPOTIFY_CLIENT_SECRET')
    username = os.getenv('SPOTIFY_USERNAME')
    logger.debug(f"Loaded environment: client_id={bool(client_id)}, client_secret={bool(client_secret)}, username={bool(username)}")
    if not all([client_id, client_secret, username]):
        logger.error("Missing required variables: SPOTIFY_CLIENT_ID, SPOTIFY_CLIENT_SECRET, or SPOTIFY_USERNAME")
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
                logger.info("Token refreshed")
            sp = spotipy.Spotify(auth_manager=auth_manager)
            # Verify token scopes
            current_user = sp.current_user()
            token_scopes = auth_manager.scope.split()
            required_scopes = SCOPE.split()
            if not all(scope in token_scopes for scope in required_scopes):
                logger.error(f"Token missing required scopes. Has: {token_scopes}, Needs: {required_scopes}")
                return None
            logger.debug(f"Token scopes verified: {token_scopes}")
            return sp
        except Exception as e:
            logger.error(f"Token refresh or validation failed: {e}")
            return None
    else:
        logger.error("No token_info.json found. Run locally with --initial_setup first.")
        return None

# Update playlist metadata
def update_playlist_metadata(sp, target_playlist, all_tracks):
    description_template = os.getenv('PLAYLIST_DESCRIPTION')
    if not description_template or '{}' not in description_template:
        logger.error("Invalid or missing PLAYLIST_DESCRIPTION")
        return

    # Update description using the actual top track post-update
    artist_name = "Unknown Artist"
    track_name = "Unknown Track"
    for attempt in range(MAX_RETRIES):
        try:
            # Fetch the actual top track
            for offset in range(3):  # Try first 3 tracks
                results = sp.playlist_tracks(target_playlist, limit=1, offset=offset)
                logger.debug(f"Playlist tracks API response (offset {offset}): {len(results['items'])} items")
                if not results['items'] or not results['items'][0].get('track'):
                    logger.warning(f"No valid track at offset {offset}")
                    continue
                first_track = results['items'][0]['track']
                artist_name = first_track['artists'][0]['name'] if first_track.get('artists') else "Unknown Artist"
                track_name = first_track['name'] if first_track.get('name') else "Unknown Track"
                logger.info(f"Extracted artist name for description: {artist_name} (Track: {track_name})")
                description = description_template.format(artist_name)
                sp.playlist_change_details(target_playlist, description=description)
                logger.info(f"Updated description to: {description}")
                break  # Exit offset loop on success
            else:
                # Fallback to all_tracks[0]
                logger.warning("No valid tracks found in playlist. Falling back to first track in all_tracks.")
                if all_tracks:
                    track = sp.track(all_tracks[0])
                    artist_name = track['artists'][0]['name'] if track.get('artists') else "Unknown Artist"
                    track_name = track['name'] if track.get('name') else "Unknown Track"
                    logger.info(f"Extracted fallback artist name: {artist_name} (Track: {track_name})")
                    description = description_template.format(artist_name)
                    sp.playlist_change_details(target_playlist, description=description)
                    logger.info(f"Updated description with fallback: {description}")
                else:
                    logger.warning("No tracks available. Using default artist name.")
                    description = description_template.format(artist_name)
                    sp.playlist_change_details(target_playlist, description=description)
                    logger.info(f"Updated description with default: {description}")
                break
            break  # Exit retry loop on success
        except Exception as e:
            logger.error(f"Description update attempt {attempt + 1} failed: {e}")
            if attempt < MAX_RETRIES - 1:
                sleep(2)
            else:
                logger.warning("Max retries reached for description update. Using default artist name.")
                description = description_template.format(artist_name)
                sp.playlist_change_details(target_playlist, description=description)
                logger.info(f"Updated description with default: {description}")

    # Update cover image using the actual top track
    try:
        for offset in range(3):  # Try first 3 tracks
            results = sp.playlist_tracks(target_playlist, limit=1, offset=offset)
            if not results['items'] or not results['items'][0].get('track'):
                logger.warning(f"No valid track for cover image at offset {offset}")
                continue
            first_track = results['items'][0]['track']
            track_name = first_track['name'] if first_track.get('name') else "Unknown Track"
            album_images = first_track['album']['images']
            if album_images:
                image_url = album_images[0]['url']
                response = requests.get(image_url, timeout=TIMEOUT)
                img = Image.open(io.BytesIO(response.content)).resize((640, 640), Image.Resampling.LANCZOS)
                img_byte_arr = io.BytesIO()
                img.convert('RGB').save(img_byte_arr, format='JPEG', quality=85)
                base64_image = base64.b64encode(img_byte_arr.getvalue()).decode('utf-8')
                sp.playlist_upload_cover_image(target_playlist, base64_image)
                logger.info(f"Cover image updated for track: {track_name}")
                break
        else:
            # Fallback to all_tracks[0]
            logger.warning("No valid tracks found for cover image. Falling back to first track in all_tracks.")
            if all_tracks:
                track = sp.track(all_tracks[0])
                track_name = track['name'] if track.get('name') else "Unknown Track"
                album_images = track['album']['images']
                if album_images:
                    image_url = album_images[0]['url']
                    response = requests.get(image_url, timeout=TIMEOUT)
                    img = Image.open(io.BytesIO(response.content)).resize((640, 640), Image.Resampling.LANCZOS)
                    img_byte_arr = io.BytesIO()
                    img.convert('RGB').save(img_byte_arr, format='JPEG', quality=85)
                    base64_image = base64.b64encode(img_byte_arr.getvalue()).decode('utf-8')
                    sp.playlist_upload_cover_image(target_playlist, base64_image)
                    logger.info(f"Cover image updated with fallback for track: {track_name}")
    except Exception as e:
        logger.error(f"Cover image update failed: {e}")

# Main playlist update logic
def update_playlist():
    now = datetime.datetime.now(datetime.timezone.utc)
    logger.debug(f"Current time: {now} UTC")
    if now.weekday() != 5:
        logger.info(f"Not Saturday. Expected: Any time on Saturday")
        return

    # Check if already updated this Saturday
    current_date = now.date()
    last_update_date = load_last_update()
    if last_update_date and last_update_date == current_date:
        logger.info(f"Playlist already updated on {current_date}. Skipping.")
        return

    if not is_connected():
        logger.error("No internet connection")
        return
    
    sp = get_spotify_client()
    if not sp:
        logger.error("Spotify client initialization failed")
        return

    source_playlist = os.getenv('SOURCE_PLAYLIST')
    target_playlist = os.getenv('TARGET_PLAYLIST')
    username = os.getenv('SPOTIFY_USERNAME')
    logger.debug(f"Environment: source_playlist={source_playlist}, target_playlist={target_playlist}, username={username}")
    if not all([source_playlist, target_playlist, username]):
        logger.error("Missing SOURCE_PLAYLIST, TARGET_PLAYLIST, or SPOTIFY_USERNAME")
        return

    try:
        # Load priority songs (track IDs only)
        priority_songs = load_priority_songs()
        logger.info(f"Priority songs to include: {len(priority_songs)}")

        # Fetch current tracks in the target playlist
        current_tracks = []
        results = sp.playlist_tracks(target_playlist)
        while results:
            current_tracks.extend([item['track']['id'] for item in results['items'] if item['track']])
            results = sp.next(results) if results['next'] else None
        logger.info(f"Current tracks in target playlist: {len(current_tracks)}")

        # Fetch tracks from source playlist
        source_results = sp.playlist_tracks(source_playlist)
        source_songs = [item['track']['id'] for item in source_results['items'] if item['track']]
        logger.info(f"Source playlist tracks: {len(source_songs)}")
        new_songs = [song for song in source_songs if song not in current_tracks]
        logger.info(f"Found {len(new_songs)} new tracks from source playlist")

        # Combine all tracks (priority + current + new) and remove duplicates
        all_tracks = priority_songs + current_tracks + new_songs
        original_count = len(all_tracks)
        all_tracks = list(dict.fromkeys(all_tracks))  # Remove duplicates while preserving order
        duplicates_removed = original_count - len(all_tracks)
        logger.info(f"Combined {original_count} tracks, removed {duplicates_removed} duplicates, resulting in {len(all_tracks)} unique tracks")
        random.shuffle(all_tracks)
        logger.info(f"Shuffled all {len(all_tracks)} tracks (including {len(priority_songs)} priority songs)")

        # Trim to MAX_SONGS if necessary, preserving shuffle
        if len(all_tracks) > MAX_SONGS:
            logger.info(f"Trimming {len(all_tracks)} tracks to {MAX_SONGS} while preserving shuffle")
            all_tracks = all_tracks[:MAX_SONGS]
            num_priority = len([track for track in all_tracks if track in priority_songs])
            logger.info(f"Trimmed to {MAX_SONGS} tracks with {num_priority} priority songs")

        # Clear the target playlist
        if current_tracks:
            sp.user_playlist_remove_all_occurrences_of_tracks(username, target_playlist, current_tracks)
            logger.info("Cleared all existing tracks from target playlist")

        # Add tracks to the playlist
        sp.user_playlist_add_tracks(username, target_playlist, all_tracks)
        logger.info(f"Added {len(all_tracks)} tracks to target playlist")

        # Update metadata using the actual top track
        update_playlist_metadata(sp, target_playlist, all_tracks)

        # Save the current track IDs and update date
        save_record(all_tracks)
        save_last_update(current_date)
        logger.info("Playlist update completed")
    except Exception as e:
        logger.error(f"Update failed: {e}")

if __name__ == "__main__":
    update_playlist()
