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
            # Get the top track for metadata updates with improved retry logic
            top_track = None
            artist_name = "Unknown Artist"
            track_name = "Unknown Track"
            
            # Wait longer for playlist changes to propagate
            sleep(10)
            
            for attempt in range(max_retries):
                try:
                    results = self.sp.playlist_tracks(target_playlist, limit=1, offset=0, market=market)
                    if results['items'] and results['items'][0].get('track'):
                        top_track = results['items'][0]['track']
                        artist_name = top_track['artists'][0]['name'] if top_track.get('artists') else "Unknown Artist"
                        track_name = top_track['name'] if top_track.get('name') else "Unknown Track"
                        logger.info(f"Top track for {target_playlist}: {track_name} by {artist_name}")
                        break
                    else:
                        logger.warning(f"No tracks found in playlist {target_playlist}")
                        # Wait and retry if no tracks found
                        if attempt < max_retries - 1:
                            sleep(5)
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
                    logger.info(f"Attempting to update cover image from: {image_url}")
                    for attempt in range(max_retries):
                        try:
                            response = requests.get(image_url, timeout=60)  # Increased timeout
                            if response.status_code == 200:
                                img = Image.open(io.BytesIO(response.content)).resize((640, 640), Image.Resampling.LANCZOS)
                                img_byte_arr = io.BytesIO()
                                img.convert('RGB').save(img_byte_arr, format='JPEG', quality=85)
                                base64_image = base64.b64encode(img_byte_arr.getvalue()).decode('utf-8')
                                self.sp.playlist_upload_cover_image(target_playlist, base64_image)
                                logger.info(f"Successfully updated cover image for track: {track_name} by {artist_name}")
                                break
                            else:
                                logger.warning(f"Failed to download image: HTTP {response.status_code}")
                                if attempt < max_retries - 1:
                                    sleep(5)
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
                            if attempt < max_retries - 1:
                                sleep(5)
                            break
                else:
                    logger.warning(f"No album images found for top track in {target_playlist}")
            else:
                logger.warning(f"No top track found for cover image update in {target_playlist}")
                
        except Exception as e:
            logger.error(f"Failed to update metadata for {target_playlist}: {e}")
    
    def reorder_playlist_random(self, target_playlist, priority_songs):
        """Randomly reorder entire playlist while preserving 'added_at'.
        Does not pin priority songs to the top; trimming logic elsewhere
        ensures priority songs are not removed when enforcing max size.
        Uses Spotify's reorder API to avoid remove+add, which would reset dates.
        """
        try:
            # Build current order of track IDs
            current_tracks = self.fetch_current_playlist_tracks(target_playlist)
            if not current_tracks:
                return
            
            # Shuffle all tracks uniformly (no priority pinning)
            desired_order = list(current_tracks)
            random.shuffle(desired_order)
            
            if desired_order == current_tracks:
                logger.info("Reorder skipped: already in desired random order")
                return
            
            max_retries = self.config['global_settings']['max_retries']
            # Perform in-place transformation using Spotify reorder API
            working = list(current_tracks)
            for target_index, track_id in enumerate(desired_order):
                if working[target_index] == track_id:
                    continue
                # Find current index of the track that should be at target_index
                try:
                    current_index = working.index(track_id)
                except ValueError:
                    # Should not happen; safety check
                    continue
                # Reorder: move the single item from current_index to target_index
                for attempt in range(max_retries):
                    try:
                        # Spotify API: insert_before is the position the range will be inserted before
                        self.sp.playlist_reorder_items(
                            target_playlist,
                            range_start=current_index,
                            insert_before=target_index,
                            range_length=1
                        )
                        # Reflect change locally
                        item = working.pop(current_index)
                        working.insert(target_index, item)
                        break
                    except spotipy.exceptions.SpotifyException as e:
                        if e.http_status == 429:
                            sleep_time = 2 ** attempt
                            logger.warning(f"Rate limit hit while reordering. Retrying in {sleep_time} seconds...")
                            sleep(sleep_time)
                        else:
                            logger.error(f"Failed to reorder item (HTTP {e.http_status}): {e}")
                            break
                    except Exception as e:
                        logger.error(f"Unexpected error during reorder: {e}")
                        break
            logger.info("Completed random reorder while preserving 'added_at'")
        except Exception as e:
            logger.error(f"Failed to perform random reorder: {e}")
    
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
            logger.info(f"{playlist_name} already updated today. Updating metadata only.")
            # Still update metadata even if tracks are already current
            self.update_playlist_metadata(target_playlist, [], description_template)
            return
        
        try:
            # Fetch tracks from all source playlists
            source_tracks = self.fetch_tracks_from_sources(source_playlists)
            
            # Fetch current tracks in target playlist
            current_tracks = self.fetch_current_playlist_tracks(target_playlist)
            
            # Add priority songs to source tracks (they should always be included)
            all_source_tracks = list(dict.fromkeys(priority_songs + source_tracks))
            
            # Find tracks to remove (in target but not in source)
            tracks_to_remove = [track for track in current_tracks if track not in all_source_tracks]
            
            # Find tracks to add (in source but not in target)
            tracks_to_add = [track for track in all_source_tracks if track not in current_tracks]
            
            # Find tracks to keep (in both source and target)
            tracks_to_keep = [track for track in current_tracks if track in all_source_tracks]
            
            # Log detailed sync summary
            self.log_sync_summary(playlist_config, source_tracks, current_tracks, tracks_to_remove, tracks_to_add, tracks_to_keep)
            
            # Remove tracks that are in target but not in source
            if tracks_to_remove:
                username = os.getenv('SPOTIFY_USERNAME')
                max_retries = self.config['global_settings']['max_retries']
                for i in range(0, len(tracks_to_remove), 100):  # Batch removal
                    batch = tracks_to_remove[i:i + 100]
                    for attempt in range(max_retries):
                        try:
                            self.sp.user_playlist_remove_all_occurrences_of_tracks(username, target_playlist, batch)
                            logger.info(f"Removed {len(batch)} tracks from {playlist_name} (batch {i//100 + 1})")
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
            
            # Add tracks that are in source but not in target
            if tracks_to_add:
                username = os.getenv('SPOTIFY_USERNAME')
                max_retries = self.config['global_settings']['max_retries']
                for i in range(0, len(tracks_to_add), 100):  # Batch addition
                    batch = tracks_to_add[i:i + 100]
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
            
            # Calculate final track count
            final_track_count = len(tracks_to_keep) + len(tracks_to_add)
            logger.info(f"Final track count for {playlist_name}: {final_track_count}")
            
            # If we exceed max_songs, trim without re-adding to preserve "date added"
            if final_track_count > max_songs:
                # Get the current state after additions/removals in existing order
                current_final_tracks = self.fetch_current_playlist_tracks(target_playlist)

                # Determine which tracks to keep, prioritizing priority songs, but do not reorder
                # Keep priority songs first (in their current order), then fill remaining slots with non-priority tracks in current order
                priority_tracks_in_playlist = [track for track in current_final_tracks if track in priority_songs]
                non_priority_tracks_in_playlist = [track for track in current_final_tracks if track not in priority_songs]

                available_slots_after_priority = max_songs - len(priority_tracks_in_playlist)
                if available_slots_after_priority < 0:
                    # More priority tracks than max_songs: keep only the first max_songs priority tracks (existing order)
                    tracks_to_keep_final = priority_tracks_in_playlist[:max_songs]
                else:
                    tracks_to_keep_final = priority_tracks_in_playlist + non_priority_tracks_in_playlist[:available_slots_after_priority]

                # Remove only the surplus tracks; do not re-add or reorder remaining tracks
                tracks_to_remove_final = [track for track in current_final_tracks if track not in tracks_to_keep_final]

                if tracks_to_remove_final:
                    username = os.getenv('SPOTIFY_USERNAME')
                    max_retries = self.config['global_settings']['max_retries']
                    for i in range(0, len(tracks_to_remove_final), 100):
                        batch = tracks_to_remove_final[i:i + 100]
                        for attempt in range(max_retries):
                            try:
                                self.sp.user_playlist_remove_all_occurrences_of_tracks(username, target_playlist, batch)
                                logger.info(f"Trimmed {len(batch)} surplus tracks from {playlist_name} (batch {i//100 + 1})")
                                break
                            except spotipy.exceptions.SpotifyException as e:
                                if e.http_status == 429:
                                    sleep_time = 2 ** attempt
                                    logger.warning(f"Rate limit hit while trimming (batch {i//100 + 1}). Retrying in {sleep_time} seconds...")
                                    sleep(sleep_time)
                                else:
                                    logger.error(f"Failed to trim tracks from {playlist_name} (batch {i//100 + 1}): {e}")
                                    break
                            except Exception as e:
                                logger.error(f"Unexpected error trimming tracks from {playlist_name} (batch {i//100 + 1}): {e}")
                                break

                logger.info(f"Trimmed playlist {playlist_name} to {len(tracks_to_keep_final)} tracks (max: {max_songs}) without re-adding, preserving 'date added' for kept tracks")
            
            # Randomly reorder while preserving 'added_at'
            self.reorder_playlist_random(target_playlist, priority_songs)
            
            # Wait for operations to register before updating metadata
            sleep(5)
            
            # Always update metadata to ensure cover and description are current
            self.update_playlist_metadata(target_playlist, [], description_template)
            
            # Save records
            final_tracks = self.fetch_current_playlist_tracks(target_playlist)
            track_data = self.fetch_track_metadata(final_tracks)
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
    
    def force_metadata_update_all(self):
        """Force metadata update for all playlists without changing tracks"""
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
        
        logger.info(f"Force updating metadata for {len(self.config['playlists'])} playlists")
        
        for playlist_config in self.config['playlists']:
            try:
                playlist_name = playlist_config['name']
                target_playlist = playlist_config['target_playlist_id']
                description_template = playlist_config['description_template']
                
                logger.info(f"Force updating metadata for: {playlist_name}")
                self.update_playlist_metadata(target_playlist, [], description_template)
                sleep(2)
            except Exception as e:
                logger.error(f"Error force updating metadata for {playlist_config['name']}: {e}")
        
        logger.info("Completed force metadata updates for all playlists")

    def get_sync_summary(self, playlist_config, source_tracks, current_tracks, tracks_to_remove, tracks_to_add, tracks_to_keep):
        """Generate a detailed summary of the sync operation"""
        playlist_name = playlist_config['name']
        priority_songs = playlist_config['priority_songs']
        
        summary = f"\n=== SYNC SUMMARY FOR {playlist_name} ===\n"
        summary += f"Source tracks: {len(source_tracks)}\n"
        summary += f"Current tracks in target: {len(current_tracks)}\n"
        summary += f"Priority songs: {len(priority_songs)}\n"
        summary += f"Tracks to remove: {len(tracks_to_remove)}\n"
        summary += f"Tracks to add: {len(tracks_to_add)}\n"
        summary += f"Tracks to keep: {len(tracks_to_keep)}\n"
        summary += f"Final track count: {len(tracks_to_keep) + len(tracks_to_add)}\n"
        
        if tracks_to_remove:
            summary += f"\nTracks being removed:\n"
            for track_id in tracks_to_remove[:5]:  # Show first 5
                summary += f"  - {track_id}\n"
            if len(tracks_to_remove) > 5:
                summary += f"  ... and {len(tracks_to_remove) - 5} more\n"
        
        if tracks_to_add:
            summary += f"\nTracks being added:\n"
            for track_id in tracks_to_add[:5]:  # Show first 5
                summary += f"  + {track_id}\n"
            if len(tracks_to_add) > 5:
                summary += f"  ... and {len(tracks_to_add) - 5} more\n"
        
        summary += "=" * 50 + "\n"
        return summary

    def log_sync_summary(self, playlist_config, source_tracks, current_tracks, tracks_to_remove, tracks_to_add, tracks_to_keep):
        """Log a detailed summary of the sync operation"""
        summary = self.get_sync_summary(playlist_config, source_tracks, current_tracks, tracks_to_remove, tracks_to_add, tracks_to_keep)
        logger.info(summary)

def main():
    """Main function to run the multi-playlist updater"""
    import sys
    
    manager = MultiPlaylistManager()
    
    # Check if force metadata update is requested
    if len(sys.argv) > 1 and sys.argv[1] == "--force-metadata":
        logger.info("Force metadata update mode enabled")
        manager.force_metadata_update_all()
    else:
        manager.update_all_playlists()

if __name__ == "__main__":
    main()