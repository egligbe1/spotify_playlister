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

# Load environment variables
load_dotenv()

# Configure logging
logger = logging.getLogger(__name__)
logging.basicConfig(filename='spotify_playlister.log', level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger.setLevel(logging.DEBUG)

# Suppress verbose logs from external libraries
logging.getLogger('requests').setLevel(logging.WARNING)
logging.getLogger('urllib3').setLevel(logging.WARNING)
logging.getLogger('spotipy.client').setLevel(logging.WARNING)

# Constants
SCOPE = 'playlist-modify-public playlist-modify-private playlist-read-private ugc-image-upload'
REDIRECT_URI = 'http://127.0.0.1:8888/callback'
TOKEN_FILE = 'token_info.json'
CONFIG_FILE = 'playlist_config.json'
RECORDS_DIR = 'playlist_records'
LAST_UPDATES_DIR = 'last_updates'

class MultiPlaylistManager:
    def __init__(self):
        self.sp = None
        self.config = self.load_config()
        self.ensure_directories()
        
    def ensure_directories(self):
        """Ensure necessary directories exist"""
        for directory in [RECORDS_DIR, LAST_UPDATES_DIR]:
            Path(directory).mkdir(exist_ok=True)
    
    def load_config(self):
        """Load playlist configuration"""
        try:
            with open(CONFIG_FILE, 'r') as f:
                config = json.load(f)
                logger.info(f"Loaded configuration for {len(config['playlists'])} playlists")
                return config
        except (FileNotFoundError, json.JSONDecodeError) as e:
            logger.error(f"Failed to load config: {e}")
            return None
    
    def get_spotify_client(self):
        """Get Spotify client with authentication"""
        client_id = os.getenv('SPOTIFY_CLIENT_ID')
        client_secret = os.getenv('SPOTIFY_CLIENT_SECRET')
        username = os.getenv('SPOTIFY_USERNAME')
        
        if not all([client_id, client_secret, username]):
            logger.error("Missing required environment variables: SPOTIFY_CLIENT_ID, SPOTIFY_CLIENT_SECRET, or SPOTIFY_USERNAME")
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
                return sp
            except Exception as e:
                logger.error(f"Token refresh failed: {e}")
                if os.path.exists(TOKEN_FILE):
                    os.remove(TOKEN_FILE)
                    logger.info("Cleared invalid token. Re-authentication required.")
                return None
        else:
            logger.error("No token_info.json found. Run the script locally to authenticate via Spotify OAuth and generate token_info.json, then add it to GitHub Secrets as TOKEN_INFO_JSON.")
            return None
    
    def is_connected(self):
        """Check internet connection to Spotify API"""
        try:
            requests.get('https://api.spotify.com/v1', timeout=10)
            return True
        except requests.ConnectionError:
            logger.error("No connection to Spotify API")
            return False
    
    def load_playlist_record(self, playlist_name):
        """Load record for a specific playlist"""
        record_file = Path(RECORDS_DIR) / f"{playlist_name}_record.json"
        try:
            with open(record_file, 'r') as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            logger.info(f"No record found for {playlist_name}")
            return []
    
    def save_playlist_record(self, playlist_name, track_data):
        """Save record for a specific playlist"""
        record_file = Path(RECORDS_DIR) / f"{playlist_name}_record.json"
        try:
            with open(record_file, 'w') as f:
                json.dump(track_data, f, indent=2)
            logger.debug(f"Saved record for {playlist_name}")
        except Exception as e:
            logger.error(f"Failed to save record for {playlist_name}: {e}")
    
    def load_last_update(self, playlist_name):
        """Load last update date for a specific playlist"""
        update_file = Path(LAST_UPDATES_DIR) / f"{playlist_name}_last_update.json"
        try:
            with open(update_file, 'r') as f:
                data = json.load(f)
                date = datetime.datetime.fromisoformat(data['last_update']).date()
                return date
        except (FileNotFoundError, json.JSONDecodeError, KeyError):
            logger.info(f"No last update found for {playlist_name}")
            return None
    
    def save_last_update(self, playlist_name, update_date):
        """Save last update date for a specific playlist"""
        update_file = Path(LAST_UPDATES_DIR) / f"{playlist_name}_last_update.json"
        try:
            with open(update_file, 'w') as f:
                json.dump({'last_update': update_date.isoformat()}, f)
            logger.debug(f"Saved last update for {playlist_name}")
        except Exception as e:
            logger.error(f"Failed to save last update for {playlist_name}: {e}")
    
    def fetch_tracks_from_sources(self, source_playlists):
        """Fetch tracks from multiple source playlists with rate limit handling"""
        all_source_tracks = []
        max_retries = self.config['global_settings']['max_retries']
        market = os.getenv('SPOTIFY_MARKET', None)
        for source_playlist in source_playlists:
            for attempt in range(max_retries):
                try:
                    results = self.sp.playlist_tracks(source_playlist, market=market)
                    source_tracks = [item['track']['id'] for item in results['items'] if item['track']]
                    all_source_tracks.extend(source_tracks)
                    logger.info(f"Fetched {len(source_tracks)} tracks from source {source_playlist}")
                    break
                except spotipy.exceptions.SpotifyException as e:
                    if e.http_status == 429:
                        sleep_time = 2 ** attempt
                        logger.warning(f"Rate limit hit for source {source_playlist}. Retrying in {sleep_time} seconds...")
                        sleep(sleep_time)
                    else:
                        logger.error(f"Failed to fetch tracks from {source_playlist}: {e}")
                        break
                except Exception as e:
                    logger.error(f"Unexpected error fetching tracks from {source_playlist}: {e}")
                    break
        
        # Remove duplicates while preserving order
        unique_tracks = list(dict.fromkeys(all_source_tracks))
        logger.info(f"Total unique tracks from all sources: {len(unique_tracks)}")
        return unique_tracks
    
    def fetch_current_playlist_tracks(self, target_playlist):
        """Fetch current tracks in target playlist with rate limit handling"""
        current_tracks = []
        max_retries = self.config['global_settings']['max_retries']
        market = os.getenv('SPOTIFY_MARKET', None)
        try:
            results = self.sp.playlist_tracks(target_playlist, market=market)
            while results:
                for attempt in range(max_retries):
                    try:
                        current_tracks.extend([item['track']['id'] for item in results['items'] if item['track']])
                        results = self.sp.next(results) if results['next'] else None
                        break
                    except spotipy.exceptions.SpotifyException as e:
                        if e.http_status == 429:
                            sleep_time = 2 ** attempt
                            logger.warning(f"Rate limit hit for target {target_playlist}. Retrying in {sleep_time} seconds...")
                            sleep(sleep_time)
                        else:
                            logger.error(f"Failed to fetch current tracks: {e}")
                            return current_tracks
                    except Exception as e:
                        logger.error(f"Unexpected error fetching current tracks: {e}")
                        return current_tracks
            logger.info(f"Current tracks in target playlist: {len(current_tracks)}")
            return current_tracks
        except Exception as e:
            logger.error(f"Failed to fetch current tracks: {e}")
            return []
    
    def fetch_track_metadata(self, track_ids):
        """Fetch metadata for track IDs with rate limit handling"""
        track_data = []
        max_retries = self.config['global_settings']['max_retries']
        for i in range(0, len(track_ids), 50):
            batch = track_ids[i:i + 50]
            for attempt in range(max_retries):
                try:
                    tracks_info = self.sp.tracks(batch, market=os.getenv('SPOTIFY_MARKET', None))
                    for track in tracks_info['tracks']:
                        if track:
                            track_data.append({
                                "track": {
                                    "id": track['id'],
                                    "name": track['name'],
                                    "artists": [{"name": artist['name']} for artist in track['artists']]
                                }
                            })
                    break
                except spotipy.exceptions.SpotifyException as e:
                    if e.http_status == 429:
                        sleep_time = 2 ** attempt
                        logger.warning(f"Rate limit hit for track metadata batch. Retrying in {sleep_time} seconds...")
                        sleep(sleep_time)
                    else:
                        logger.error(f"Failed to fetch metadata for batch: {e}")
                        break
                except Exception as e:
                    logger.error(f"Unexpected error fetching metadata for batch: {e}")
                    break
        
        return track_data
    
    def update_playlist_metadata(self, target_playlist, all_tracks, description_template):
        """Update playlist description and cover image with rate limit handling"""
        max_retries = self.config['global_settings']['max_retries']
        contact_email = self.config['global_settings'].get('contact_email', 'default@example.com')
        market = os.getenv('SPOTIFY_MARKET', None)
        try:
            # Get the top track for metadata updates
            top_track = None
            artist_name = "Unknown Artist"
            track_name = "Unknown Track"
            
            for attempt in range(max_retries):
                try:
                    results = self.sp.playlist_tracks(target_playlist, limit=1, offset=0, market=market)
                    if results['items'] and results['items'][0].get('track'):
                        top_track = results['items'][0]['track']
                        artist_name = top_track['artists'][0]['name'] if top_track.get('artists') else "Unknown Artist"
                        track_name = top_track['name'] if top_track.get('name') else "Unknown Track"
                        logger.debug(f"Top track for {target_playlist}: {track_name} by {artist_name}")
                        break
                    else:
                        logger.warning(f"No tracks found in playlist {target_playlist}")
                    break
                except spotipy.exceptions.SpotifyException as e:
                    if e.http_status == 429:
                        sleep_time = 2 ** attempt
                        logger.warning(f"Rate limit hit for fetching top track. Retrying in {sleep_time} seconds...")
                        sleep(sleep_time)
                    else:
                        logger.error(f"Failed to fetch top track for {target_playlist}: {e}")
                        break
                except Exception as e:
                    logger.error(f"Unexpected error fetching top track for {target_playlist}: {e}")
                    break
            
            # Update description with mandatory contact info and top artist
            formatted_description = description_template.format(artist_name) if description_template else f"Updated playlist featuring {artist_name}"
            full_description = f"{formatted_description} For submissions, contact: {contact_email}. Cover: {artist_name}"
            
            for attempt in range(max_retries):
                try:
                    self.sp.playlist_change_details(target_playlist, description=full_description)
                    logger.info(f"Updated description: {full_description}")
                    break
                except spotipy.exceptions.SpotifyException as e:
                    if e.http_status == 429:
                        sleep_time = 2 ** attempt
                        logger.warning(f"Rate limit hit for updating description. Retrying in {sleep_time} seconds...")
                        sleep(sleep_time)
                    else:
                        logger.error(f"Failed to update description for {target_playlist}: {e}")
                        break
                except Exception as e:
                    logger.error(f"Unexpected error updating description for {target_playlist}: {e}")
                    break
            
            # Update cover image to the top track's album cover
            if top_track and top_track.get('album', {}).get('images'):
                album_images = top_track['album']['images']
                if album_images:
                    image_url = album_images[0]['url']
                    for attempt in range(max_retries):
                        try:
                            response = requests.get(image_url, timeout=60)  # Increased timeout
                            img = Image.open(io.BytesIO(response.content)).resize((640, 640), Image.Resampling.LANCZOS)
                            img_byte_arr = io.BytesIO()
                            img.convert('RGB').save(img_byte_arr, format='JPEG', quality=85)
                            base64_image = base64.b64encode(img_byte_arr.getvalue()).decode('utf-8')
                            self.sp.playlist_upload_cover_image(target_playlist, base64_image)
                            logger.info(f"Updated cover image for track: {track_name} by {artist_name}")
                            break
                        except spotipy.exceptions.SpotifyException as e:
                            if e.http_status == 429:
                                sleep_time = 2 ** attempt
                                logger.warning(f"Rate limit hit for updating cover image. Retrying in {sleep_time} seconds...")
                                sleep(sleep_time)
                            else:
                                logger.error(f"Failed to update cover image for {target_playlist}: {e}")
                                break
                        except Exception as e:
                            logger.error(f"Unexpected error updating cover image for {target_playlist}: {e}")
                            break
                else:
                    logger.warning(f"No album images found for top track in {target_playlist}")
            else:
                logger.warning(f"No top track found for cover image update in {target_playlist}")
                
        except Exception as e:
            logger.error(f"Failed to update metadata for {target_playlist}: {e}")
    
    def update_single_playlist(self, playlist_config):
        """Update a single playlist based on its configuration"""
        playlist_name = playlist_config['name']
        target_playlist = playlist_config['target_playlist_id']
        source_playlists = playlist_config['source_playlists']
        priority_songs = playlist_config['priority_songs']
        description_template = playlist_config['description_template']
        max_songs = playlist_config['max_songs']
        
        logger.info(f"Starting update for playlist: {playlist_name}")
        
        # Check if already updated today
        current_date = datetime.datetime.now(datetime.timezone.utc).date()
        last_update = self.load_last_update(playlist_name)
        if last_update and last_update == current_date:
            logger.info(f"{playlist_name} already updated today. Skipping.")
            return
        
        try:
            # Fetch tracks from all source playlists
            source_tracks = self.fetch_tracks_from_sources(source_playlists)
            
            # Fetch current tracks in target playlist
            current_tracks = self.fetch_current_playlist_tracks(target_playlist)
            
            # Find new tracks
            new_tracks = [track for track in source_tracks if track not in current_tracks]
            logger.info(f"Found {len(new_tracks)} new tracks for {playlist_name}")
            
            # Combine all tracks (priority + current + new)
            all_tracks = priority_songs + current_tracks + new_tracks
            original_count = len(all_tracks)
            all_tracks = list(dict.fromkeys(all_tracks))  # Remove duplicates
            duplicates_removed = original_count - len(all_tracks)
            logger.info(f"Combined {original_count} tracks, removed {duplicates_removed} duplicates")
            
            # Fetch metadata for all tracks
            track_data = self.fetch_track_metadata(all_tracks)
            
            # Shuffle and trim to max_songs
            random.shuffle(all_tracks)
            if len(all_tracks) > max_songs:
                all_tracks = all_tracks[:max_songs]
                track_data = [item for item in track_data if item['track']['id'] in all_tracks]
                logger.info(f"Trimmed to {max_songs} tracks")
            
            # Clear existing tracks in batches to avoid "Too many ids requested"
            max_retries = self.config['global_settings']['max_retries']
            if current_tracks:
                username = os.getenv('SPOTIFY_USERNAME')
                for i in range(0, len(current_tracks), 100):  # Batch removal
                    batch = current_tracks[i:i + 100]
                    for attempt in range(max_retries):
                        try:
                            self.sp.user_playlist_remove_all_occurrences_of_tracks(username, target_playlist, batch)
                            logger.info(f"Cleared {len(batch)} existing tracks from {playlist_name} (batch {i//100 + 1})")
                            break
                        except spotipy.exceptions.SpotifyException as e:
                            if e.http_status == 429:
                                sleep_time = 2 ** attempt
                                logger.warning(f"Rate limit hit for removing tracks (batch {i//100 + 1}). Retrying in {sleep_time} seconds...")
                                sleep(sleep_time)
                            else:
                                logger.error(f"Failed to remove tracks from {playlist_name} (batch {i//100 + 1}): {e}")
                                break
                        except Exception as e:
                            logger.error(f"Unexpected error removing tracks from {playlist_name} (batch {i//100 + 1}): {e}")
                            break
            
            # Add new tracks in batches
            username = os.getenv('SPOTIFY_USERNAME')
            for i in range(0, len(all_tracks), 100):
                batch = all_tracks[i:i + 100]
                for attempt in range(max_retries):
                    try:
                        self.sp.user_playlist_add_tracks(username, target_playlist, batch)
                        logger.info(f"Added {len(batch)} tracks to {playlist_name} (batch {i//100 + 1})")
                        break
                    except spotipy.exceptions.SpotifyException as e:
                        if e.http_status == 429:
                            sleep_time = 2 ** attempt
                            logger.warning(f"Rate limit hit for adding tracks (batch {i//100 + 1}). Retrying in {sleep_time} seconds...")
                            sleep(sleep_time)
                        else:
                            logger.error(f"Failed to add tracks to {playlist_name} (batch {i//100 + 1}): {e}")
                            break
                    except Exception as e:
                        logger.error(f"Unexpected error adding tracks to {playlist_name} (batch {i//100 + 1}): {e}")
                        break
            
            # Confirm tracks are added before updating metadata
            logger.info(f"Playlist {playlist_name} populated with {len(all_tracks)} tracks")
            # Wait for tracks to register before updating metadata
            sleep(5)  # Increased delay
            # Update metadata
            self.update_playlist_metadata(target_playlist, all_tracks, description_template)
            
            # Save records
            self.save_playlist_record(playlist_name, track_data)
            self.save_last_update(playlist_name, current_date)
            
            logger.info(f"Successfully updated {playlist_name}")
            
        except Exception as e:
            logger.error(f"Failed to update {playlist_name}: {e}")
    
    def update_all_playlists(self):
        """Update all configured playlists"""
        if not self.config:
            logger.error("No configuration loaded")
            return
        
        if not self.is_connected():
            logger.error("No internet connection to Spotify API")
            return
        
        self.sp = self.get_spotify_client()
        if not self.sp:
            logger.error("Failed to initialize Spotify client. Ensure token_info.json is set in GitHub Secrets as TOKEN_INFO_JSON.")
            return
        
        logger.info(f"Starting updates for {len(self.config['playlists'])} playlists")
        
        for playlist_config in self.config['playlists']:
            try:
                self.update_single_playlist(playlist_config)
                sleep(2)
            except Exception as e:
                logger.error(f"Error updating {playlist_config['name']}: {e}")
        
        logger.info("Completed all playlist updates")

def main():
    """Main function to run the multi-playlist updater"""
    manager = MultiPlaylistManager()
    manager.update_all_playlists()

if __name__ == "__main__":
    main()