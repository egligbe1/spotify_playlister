
# Spotify Playlist Updater

A Python script that automates updating a Spotify playlist with new tracks from a source playlist. It also updates the playlist cover with the first track’s album art and applies a configurable description featuring the first artist’s name.

## Features
- **Automated Updates:** Syncs new tracks from a source playlist to your target playlist.
- **Dynamic Metadata:**
  - Sets the playlist cover to the first track’s album art.
  - Applies a customizable description from `.env` (e.g., `"Immerse yourself... Cover: {artist_name}"`).
- **Scheduling:** Runs hourly via Windows Task Scheduler, executing updates only on Saturday at midnight.
- **Reliability:** Includes timeout retries (10s, 3 attempts) and internet connectivity checks.
- **Logging:** Records execution details in `updater.log`.

## Prerequisites
- Python 3.8+
- Spotify Developer Account (for API credentials)
- Windows (for Task Scheduler; adaptable to other OS)
- Spotify Account (with playlists and user ID)

## Installation
1. **Clone the Repository:**
   ```bash
   git clone https://github.com/[your-username]/spotify-playlist-updater.git
   cd spotify-playlist-updater
   ```

2. **Set Up Virtual Environment:**
   ```bash
   python -m venv venv
   .\venv\Scripts\activate  # Windows
   ```

3. **Install Dependencies:**
   ```bash
   pip install spotipy python-dotenv Pillow requests
   ```

4. **Configure `.env`:**
   - Create a `.env` file:
     ```
     SPOTIFY_CLIENT_ID=your_client_id
     SPOTIFY_CLIENT_SECRET=your_client_secret
     SPOTIFY_USERNAME=your_spotify_username
     SOURCE_PLAYLIST=your_source_playlist_id  # e.g., 6xwBhWuFwOySNZtxDZjzcS
     TARGET_PLAYLIST=your_target_playlist_id
     PLAYLIST_DESCRIPTION=Playlist decription goes here. Cover: {}
     ```
   - Get credentials from [Spotify Developer Dashboard](https://developer.spotify.com/dashboard/).
   - Use `{}` in `PLAYLIST_DESCRIPTION` for the artist name.

5. **Authenticate:**
   ```bash
   python main.py --initial_setup
   ```
   - Authorize via the URL and paste the redirect URL to create `token_info.json`.

## Usage
- **Manual Test:**
  - Comment out the time check in `main.py`:
    ```python
    # if now.weekday() != 5 or now.hour != 0 or now.minute != 0:
    ```
  - Run:
    ```bash
    python main.py
    ```
  - Verify updates in `updater.log` and Spotify.

- **Scheduled Run:**
  - In Windows Task Scheduler:
    - **Name:** "Spotify Playlist Updater"
    - **Trigger:** Daily, 00:00, repeat every 1 hour, indefinitely
    - **Action:**
      - Program: `D:\path\to\spotify-playlist-updater\venv\Scripts\python.exe`
      - Arguments: `main.py`
      - Start in: `D:\path\to\spotify-playlist-updater`
    - **Settings:** Enable "Run if missed" and retries (5 min, 3 attempts).
  - Updates occur Saturday at midnight.

## Files
- `main.py`: Main script
- `.env`: Config (excluded from git)
- `token_info.json`, `playlist_record.json`, `updater.log`: Generated files

## Notes
- **Description:** Customize in `.env` with `{}` for the artist name.
- **Region Issues:** Some curated playlists (e.g., `37i9dQZF1DWX0o6sD1a6P5`) may fail with 404 due to regional API restrictions. Use a working ID or mirror the playlist.
- **Tweaks:** Adjust `TIMEOUT`/`MAX_RETRIES` in `main.py` for network issues.

## Troubleshooting
- **404 Error:** Check `SOURCE_PLAYLIST` accessibility.
- **Timeout:** Test network (`ping api.spotify.com`) or increase `TIMEOUT`.
- **Logs:** See `updater.log` for details.

## Contributing
Fork, report issues, or submit PRs to enhance features (e.g., multi-OS support).

## License
[MIT License](LICENSE)