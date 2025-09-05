# Spotify Playlist Sync Logic Update

## Overview

The playlist sync logic has been completely rewritten to implement proper playlist synchronization instead of complete playlist replacement. This ensures that existing tracks in target playlists are preserved when they exist in source playlists.

## New Sync Logic

### Core Principles

1. **Remove songs that exist in target but not in source** - These tracks are no longer available in the source playlists and should be removed from the target.

2. **Add songs that exist in source but not in target** - New tracks from source playlists are added to the target playlist.

3. **Keep songs that exist in both source and target** - Existing tracks in the target playlist are preserved, but their positions may be reordered for variety and to respect the max_songs limit.

4. **Preserve priority songs** - Priority songs are always included in the source tracks and will be added if not present.

### Implementation Details

#### Before (Old Logic)
- Fetched all source tracks
- Fetched current target tracks  
- Combined priority + current + new tracks
- **Completely cleared the target playlist**
- Added all tracks back (losing existing track positions and metadata)

#### After (New Logic)
- Fetched all source tracks (including priority songs)
- Fetched current target tracks
- **Calculated differences:**
  - `tracks_to_remove`: Tracks in target but not in source
  - `tracks_to_add`: Tracks in source but not in target  
  - `tracks_to_keep`: Tracks in both source and target
- **Selective operations:**
  - Remove only tracks that need to be removed
  - Add only tracks that need to be added
  - Preserve all existing tracks that should be kept
  - Reorder tracks when needed (for variety and max_songs compliance)

### Benefits

1. **Preserves Playlist History** - Existing tracks are kept (though positions may change for variety)
2. **Efficient Updates** - Only necessary changes are made
3. **Respects User Customizations** - User-added tracks that exist in sources are preserved
4. **Better Performance** - Fewer API calls when minimal changes are needed
5. **Detailed Logging** - Clear visibility into what's being added, removed, and kept

### Logging Improvements

The new implementation provides detailed logging:

```
=== SYNC SUMMARY FOR Playlist Name ===
Source tracks: 150
Current tracks in target: 80
Priority songs: 2
Tracks to remove: 5
Tracks to add: 25
Tracks to keep: 75
Final track count: 100

Tracks being removed:
  - track_id_1
  - track_id_2
  - track_id_3
  - track_id_4
  - track_id_5

Tracks being added:
  + track_id_6
  + track_id_7
  + track_id_8
  + track_id_9
  + track_id_10
  ... and 15 more
```

### Configuration

The sync logic respects all existing configuration options:

- `priority_songs`: Always included in source tracks
- `max_songs`: Applied after sync operations (preserves priority songs)
- `source_playlists`: Used to determine what tracks should be in target
- `description_template`: Updated only when changes occur

### Key Features

- **Preserves track history** - Tracks that exist in both source and target are kept
- **Allows position flexibility** - Track positions can be reordered for variety
- **Respects max_songs limit** - When exceeding the limit, playlist is reordered and trimmed
- **Priority songs first** - Priority songs are always placed at the top
- **Efficient operations** - Only necessary changes are made

## Migration Notes

- **No configuration changes required** - All existing playlist configurations work with the new logic
- **Backward compatible** - The API and configuration format remain the same
- **Improved reliability** - Less likely to lose tracks entirely
- **Better user experience** - Playlists maintain their character and history while staying fresh

## Example Scenarios

### Scenario 1: New tracks added to source
- **Source**: 100 tracks (including 5 new ones)
- **Target**: 95 tracks (existing)
- **Result**: 5 tracks added, 95 tracks preserved, 100 total

### Scenario 2: Tracks removed from source  
- **Source**: 90 tracks (5 removed)
- **Target**: 95 tracks (existing)
- **Result**: 5 tracks removed, 90 tracks preserved, 90 total

### Scenario 3: Mixed changes
- **Source**: 100 tracks (10 new, 5 removed)
- **Target**: 95 tracks (existing)
- **Result**: 5 tracks removed, 10 tracks added, 90 tracks preserved, 100 total
