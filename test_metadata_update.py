#!/usr/bin/env python3
"""
Test script to verify metadata update functionality
"""

import sys
import os
from spotify_playlister import MultiPlaylistManager

def test_metadata_update():
    """Test metadata update for a single playlist"""
    print("Testing metadata update functionality...")
    
    manager = MultiPlaylistManager()
    
    # Test with the first playlist in config
    if manager.config and manager.config['playlists']:
        test_playlist = manager.config['playlists'][0]
        playlist_name = test_playlist['name']
        target_playlist = test_playlist['target_playlist_id']
        description_template = test_playlist['description_template']
        
        print(f"Testing metadata update for: {playlist_name}")
        print(f"Target playlist ID: {target_playlist}")
        
        # Initialize Spotify client
        manager.sp = manager.get_spotify_client()
        if not manager.sp:
            print("Failed to initialize Spotify client")
            return False
        
        # Test metadata update
        try:
            manager.update_playlist_metadata(target_playlist, [], description_template)
            print("✅ Metadata update test completed successfully")
            return True
        except Exception as e:
            print(f"❌ Metadata update test failed: {e}")
            return False
    else:
        print("No playlists found in configuration")
        return False

if __name__ == "__main__":
    success = test_metadata_update()
    sys.exit(0 if success else 1)
