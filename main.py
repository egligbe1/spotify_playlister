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

load_dotenv()

# Configure logging
logger = logging.getLogger(__name__)
logging.basicConfig(
    filename='updater.log',
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
)
logger.setLevel(logging.DEBUG)
logging.getLogger('requests').setLevel(logging.WARNING)
logging.getLogger('urllib3').setLevel(logging.WARNING)
logging.getLogger('spotipy.client').setLevel(logging.WARNING)
logger.info("Starting Spotify playlist updater")

# Constants
SCOPE = 'playlist-modify-public playlist-modify-private playlist-read-private ugc-image-upload'
REDIRECT_URI = 'http://127.0.0.1:8888/callback'
TOKEN_FILE = 'token_info.json'
LAST_UPDATE_FILE = 'last_update.json'
PLAYLISTS_CONFIG_FILE = 'playlists_config.json'
MAX_RETRIES = 3
TIMEOUT = 30


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------

def get_env_bool(name, default=False):
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in ('1', 'true', 'yes', 'on')


def chunk_list(items, chunk_size=100):
    for i in range(0, len(items), chunk_size):
        yield items[i:i + chunk_size]


def is_connected():
    for attempt in range(MAX_RETRIES):
        try:
            requests.get('https://www.google.com', timeout=TIMEOUT)
            return True
        except requests.ConnectionError:
            logger.warning(f"Internet check attempt {attempt + 1} failed")
            sleep(2)
    logger.error("No internet connection after retries")
    return False


# ---------------------------------------------------------------------------
# Spotify helpers
# ---------------------------------------------------------------------------

def fetch_playlist_track_ids(sp, playlist_id):
    """Fetch all track IDs from a playlist, following pagination."""
    track_ids = []
    results = sp.playlist_tracks(playlist_id, fields='items.track.id,next')
    while results:
        track_ids.extend(
            item['track']['id']
            for item in results['items']
            if item.get('track') and item['track'].get('id')
        )
        results = sp.next(results) if results.get('next') else None
    logger.debug(f"Fetched {len(track_ids)} tracks from playlist {playlist_id}")
    return track_ids


def fetch_trending_songs(sp, search_queries, explicit_playlist_ids=None, limit=40):
    """Auto-discover trending songs via search queries and optional explicit playlist IDs.

    Sources (in order):
      1. explicit_playlist_ids — highest priority, fetched verbatim.
      2. search_queries — Spotify playlist search, market-aware, top 3 results per query.

    Note: The Spotify Recommendations API was deprecated in November 2024 and is not used.
    """
    trending_ids = []
    seen_tracks = set()
    seen_playlists = set()

    # 1. Explicit playlist IDs
    for pid in (explicit_playlist_ids or []):
        if pid in seen_playlists:
            continue
        seen_playlists.add(pid)
        try:
            tracks = fetch_playlist_track_ids(sp, pid)
            added = 0
            for tid in tracks:
                if tid not in seen_tracks:
                    seen_tracks.add(tid)
                    trending_ids.append(tid)
                    added += 1
            logger.info(f"Explicit playlist {pid}: added {added} tracks")
        except Exception as e:
            logger.warning(f"Could not fetch explicit playlist {pid}: {e}")

    # 2. Search-based discovery
    for query in search_queries:
        try:
            results = sp.search(q=query, type='playlist', limit=3)
            playlists = results.get('playlists', {}).get('items', [])
            for playlist in playlists:
                if not playlist:
                    continue
                pid = playlist['id']
                if pid in seen_playlists:
                    continue
                seen_playlists.add(pid)
                tracks = fetch_playlist_track_ids(sp, pid)
                added = 0
                for tid in tracks[:25]:  # cap per playlist to maintain variety
                    if tid not in seen_tracks:
                        seen_tracks.add(tid)
                        trending_ids.append(tid)
                        added += 1
                logger.info(f"Search '{query}' → '{playlist.get('name')}': added {added} tracks")
        except Exception as e:
            logger.warning(f"Search trending discovery failed for '{query}': {e}")

    logger.info(f"Trending songs discovered: {len(trending_ids)}")
    return trending_ids[:limit]


def fetch_artist_songs(sp, artist_name, include_features=False, limit=200):
    """Fetch tracks from an artist's full discography (albums + singles).

    Args:
        include_features: if False, only tracks where the artist is a primary
                          artist are returned (no guest appearances by others).
    """
    # Resolve artist name → Spotify artist ID
    results = sp.search(q=f'artist:{artist_name}', type='artist', limit=5)
    artists = results.get('artists', {}).get('items', [])
    if not artists:
        logger.warning(f"Artist '{artist_name}' not found on Spotify")
        return []

    # Prefer an exact name match
    artist = next(
        (a for a in artists if a['name'].lower() == artist_name.lower()),
        artists[0]
    )
    artist_id = artist['id']
    logger.info(f"Resolved artist '{artist_name}' → '{artist['name']}' ({artist_id})")

    track_ids = []
    seen = set()

    for album_type in ('album', 'single'):
        try:
            page = sp.artist_albums(artist_id, album_type=album_type, limit=50)
            while page:
                for album in page['items']:
                    try:
                        tracks_page = sp.album_tracks(album['id'])
                        while tracks_page:
                            for track in tracks_page['items']:
                                tid = track.get('id')
                                if not tid:
                                    continue
                                primary_ids = [a['id'] for a in track.get('artists', [])]
                                if not include_features and artist_id not in primary_ids:
                                    continue
                                if tid not in seen:
                                    seen.add(tid)
                                    track_ids.append(tid)
                            tracks_page = sp.next(tracks_page) if tracks_page.get('next') else None
                    except Exception as e:
                        logger.warning(f"Could not fetch tracks for album {album['id']}: {e}")
                page = sp.next(page) if page.get('next') else None
        except Exception as e:
            logger.warning(f"Could not fetch {album_type}s for '{artist_name}': {e}")

    logger.info(f"Total tracks in discography for '{artist_name}': {len(track_ids)}")
    return track_ids[:limit]


# ---------------------------------------------------------------------------
# State persistence
# ---------------------------------------------------------------------------

def load_last_update():
    try:
        with open(LAST_UPDATE_FILE, 'r') as f:
            data = json.load(f)
        date_str = data.get('last_update') or data.get('date')
        if date_str:
            return datetime.datetime.fromisoformat(date_str).date()
    except (FileNotFoundError, json.JSONDecodeError, KeyError):
        pass
    logger.info(f"No {LAST_UPDATE_FILE} found. Assuming first run.")
    return None


def save_last_update(update_date):
    try:
        with open(LAST_UPDATE_FILE, 'w') as f:
            json.dump({'last_update': update_date.isoformat()}, f)
        logger.debug(f"Saved last update date: {update_date}")
    except Exception as e:
        logger.error(f"Failed to save last update date: {e}")


def load_playlist_configs():
    import re as _re
    try:
        with open(PLAYLISTS_CONFIG_FILE, 'r', encoding='utf-8') as f:
            raw = f.read()
        # Strip JS-style // comments so the config file stays human-readable
        raw = _re.sub(r'//.*', '', raw)
        data = json.loads(raw)
        configs = data.get('playlists', [])
        logger.info(f"Loaded {len(configs)} playlist configs")
        return configs
    except (FileNotFoundError, json.JSONDecodeError) as e:
        logger.error(f"Failed to load {PLAYLISTS_CONFIG_FILE}: {e}")
        return []


# ---------------------------------------------------------------------------
# Spotify client
# ---------------------------------------------------------------------------

def get_spotify_client():
    client_id = os.getenv('SPOTIFY_CLIENT_ID')
    client_secret = os.getenv('SPOTIFY_CLIENT_SECRET')
    username = os.getenv('SPOTIFY_USERNAME')
    if not all([client_id, client_secret, username]):
        logger.error("Missing SPOTIFY_CLIENT_ID, SPOTIFY_CLIENT_SECRET, or SPOTIFY_USERNAME")
        return None

    auth_manager = SpotifyOAuth(
        client_id=client_id,
        client_secret=client_secret,
        redirect_uri=REDIRECT_URI,
        scope=SCOPE,
        username=username,
        cache_path=TOKEN_FILE,
    )

    token_info = auth_manager.get_cached_token()
    if token_info is None:
        refresh_token = os.getenv('SPOTIFY_REFRESH_TOKEN')
        if refresh_token:
            try:
                token_info = auth_manager.refresh_access_token(refresh_token)
                logger.info("Token obtained via SPOTIFY_REFRESH_TOKEN env var")
            except Exception as e:
                logger.error(f"Failed to refresh token from SPOTIFY_REFRESH_TOKEN: {e}")

    if not token_info:
        logger.error("No token available. Run setup_token.py or set SPOTIFY_REFRESH_TOKEN.")
        return None

    try:
        if auth_manager.is_token_expired(token_info):
            token_info = auth_manager.refresh_access_token(token_info['refresh_token'])
            logger.info("Token refreshed")
        sp = spotipy.Spotify(auth_manager=auth_manager)
        sp.current_user()
        return sp
    except Exception as e:
        logger.error(f"Spotify client setup failed: {e}")
        return None


# ---------------------------------------------------------------------------
# Per-playlist update
# ---------------------------------------------------------------------------

def _get_top_artists(sp, track_ids, n=5):
    """Return names of the top n unique artists across a batch of tracks."""
    if not track_ids:
        return []
    try:
        batch = sp.tracks(track_ids[:min(20, len(track_ids))])
        artists, seen = [], set()
        for track in (batch.get('tracks') or []):
            if not track:
                continue
            for artist in track.get('artists', []):
                name = artist.get('name')
                if name and name not in seen:
                    seen.add(name)
                    artists.append(name)
                    if len(artists) >= n:
                        return artists
        return artists
    except Exception as e:
        logger.warning(f"Could not fetch top artists: {e}")
        return []


def _collect_cover_urls(sp, track_ids, count=4):
    """Return up to `count` unique album art URLs from the given track IDs."""
    urls, seen = [], set()
    try:
        batch = sp.tracks(track_ids[:min(20, len(track_ids))])
        for track in (batch.get('tracks') or []):
            if not track:
                continue
            images = track.get('album', {}).get('images', [])
            if images:
                url = images[0]['url']
                if url not in seen:
                    seen.add(url)
                    urls.append(url)
                    if len(urls) >= count:
                        break
    except Exception as e:
        logger.warning(f"Could not collect cover URLs: {e}")
    return urls


def _update_cover(sp, playlist_id, track_ids):
    """Upload the first available album art (640×640 JPEG) as the playlist cover."""
    if not track_ids:
        return
    try:
        urls = _collect_cover_urls(sp, track_ids, count=1)
        if not urls:
            logger.warning(f"No cover image found for playlist {playlist_id}")
            return
        resp = requests.get(urls[0], timeout=TIMEOUT)
        resp.raise_for_status()
        img = Image.open(io.BytesIO(resp.content)).resize((640, 640), Image.Resampling.LANCZOS).convert('RGB')
        buf = io.BytesIO()
        img.save(buf, format='JPEG', quality=85)
        encoded = base64.b64encode(buf.getvalue()).decode('utf-8')
        if len(encoded) > 256 * 1024:
            buf = io.BytesIO()
            img.save(buf, format='JPEG', quality=55)
            encoded = base64.b64encode(buf.getvalue()).decode('utf-8')
        sp.playlist_upload_cover_image(playlist_id, encoded)
        logger.info(f"Cover updated for {playlist_id}")
    except Exception as e:
        logger.error(f"Cover update failed for {playlist_id}: {e}")


def update_single_playlist(sp, config):
    playlist_id = config['id']
    display_name = config.get('name', playlist_id)
    playlist_type = config.get('type', 'genre')  # 'genre' | 'artist'
    max_songs = config.get('max_songs', 70)
    public = config.get('public', True)
    description = config.get('description', '')
    priority_songs = [
        s['track_id'] for s in config.get('priority_songs', [])
        if all(k in s for k in ('track_id', 'song_name', 'artist_name'))
    ]

    logger.info(f"=== Updating [{playlist_type}]: {display_name} ({playlist_id}) ===")

    # Log current follower count for growth tracking
    try:
        info = sp.playlist(playlist_id, fields='followers')
        followers = info.get('followers', {}).get('total', 0)
        logger.info(f"Current followers: {followers:,}")
    except Exception:
        pass

    # Fetch what's already in the playlist
    current_tracks = fetch_playlist_track_ids(sp, playlist_id)
    logger.info(f"Current tracks in playlist: {len(current_tracks)}")

    # ── ARTIST playlist: pull full discography ─────────────────────────────
    if playlist_type == 'artist':
        artist_name = config.get('artist_name', display_name)
        include_features = config.get('include_features', False)
        all_tracks = fetch_artist_songs(sp, artist_name, include_features=include_features, limit=max_songs * 3)
        if not all_tracks:
            logger.warning(f"No tracks found for artist '{artist_name}'. Skipping.")
            return
        # Deduplicate and trim; priority songs go first
        all_tracks = list(dict.fromkeys(priority_songs + all_tracks))
        random.shuffle(all_tracks)
        all_tracks = all_tracks[:max_songs]
        logger.info(f"Artist discography: {len(all_tracks)} tracks selected")

        # Artist playlists use a static description
        final_description = description

    # ── GENRE playlist: search-based trending discovery ────────────────────
    else:
        search_queries = config.get('search_queries', [])
        source_playlists = config.get('source_playlists', [])

        source_songs = []
        for pid in source_playlists:
            try:
                tracks = fetch_playlist_track_ids(sp, pid)
                source_songs.extend(tracks)
                logger.info(f"Source playlist {pid}: {len(tracks)} tracks")
            except Exception as e:
                logger.warning(f"Could not fetch source playlist {pid}: {e}")

        trending = fetch_trending_songs(sp, search_queries, limit=40)

        all_source = list(dict.fromkeys(source_songs + trending))
        new_songs = [s for s in all_source if s not in current_tracks]
        logger.info(f"New tracks to add: {len(new_songs)}")

        all_tracks = list(dict.fromkeys(priority_songs + current_tracks + new_songs))
        random.shuffle(all_tracks)

        if len(all_tracks) > max_songs:
            priority_in_list = [t for t in all_tracks if t in priority_songs]
            non_priority = [t for t in all_tracks if t not in priority_songs]
            slots = max(0, max_songs - len(priority_in_list))
            all_tracks = priority_in_list + non_priority[:slots]
            logger.info(f"Trimmed to {len(all_tracks)} ({len(priority_in_list)} priority + {slots} others)")

        # Build a keyword-rich description listing the top 5 artists in the playlist.
        # This makes the playlist discoverable when fans search for any of those artists.
        top_artists = _get_top_artists(sp, all_tracks, n=5)
        if top_artists:
            if len(top_artists) == 1:
                artist_str = top_artists[0]
            else:
                artist_str = ', '.join(top_artists[:-1]) + ' & ' + top_artists[-1]
        else:
            artist_str = 'Various Artists'
        final_description = description.format(artist_str) if '{' in description else description

    # Push changes to Spotify
    try:
        sp.playlist_change_details(playlist_id, description=final_description, public=public)
        logger.info(f"Metadata updated")
    except Exception as e:
        logger.error(f"Metadata update failed for {playlist_id}: {e}")

    if all_tracks:
        sp.playlist_replace_items(playlist_id, all_tracks[:100])
        for chunk in chunk_list(all_tracks[100:], 100):
            sp.playlist_add_items(playlist_id, chunk)
        logger.info(f"Populated with {len(all_tracks)} tracks")
        _update_cover(sp, playlist_id, all_tracks)
    else:
        sp.playlist_replace_items(playlist_id, [])
        logger.info("Cleared playlist (no tracks found)")


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

def update_all_playlists():
    now = datetime.datetime.now(datetime.timezone.utc)
    logger.debug(f"Current time: {now} UTC")

    force_update = get_env_bool('FORCE_UPDATE', default=False)

    # Day-of-week gate — bypassed by FORCE_UPDATE=true (e.g. workflow_dispatch)
    update_days_str = os.getenv('UPDATE_DAYS', '5')  # Default: Saturday
    try:
        allowed_days = [int(d.strip()) for d in update_days_str.split(',')]
    except ValueError:
        allowed_days = [5]
        logger.warning("Invalid UPDATE_DAYS; defaulting to Saturday (5)")

    if not force_update and now.weekday() not in allowed_days:
        logger.info(
            f"Not an update day (weekday {now.weekday()}, allowed: {allowed_days}). "
            "Set FORCE_UPDATE=true to override."
        )
        return

    # Duplicate-run guard
    current_date = now.date()
    if not force_update and load_last_update() == current_date:
        logger.info(f"Already updated today ({current_date}). Use FORCE_UPDATE=true to re-run.")
        return

    if not is_connected():
        logger.error("No internet connection")
        return

    sp = get_spotify_client()
    if not sp:
        logger.error("Spotify client initialization failed")
        return

    configs = load_playlist_configs()
    if not configs:
        logger.error(f"No playlist configs in {PLAYLISTS_CONFIG_FILE}")
        return

    succeeded = 0
    for config in configs:
        pid = config.get('id', 'unknown')
        try:
            update_single_playlist(sp, config)
            succeeded += 1
        except Exception as e:
            logger.error(f"Failed to update playlist {pid} ({config.get('name', '')}): {e}")

    save_last_update(current_date)
    logger.info(f"All done. Updated {succeeded}/{len(configs)} playlists.")


if __name__ == "__main__":
    update_all_playlists()
