# v1.8.9

## Features & Improvements

- **Archive Past Ignored Campaigns**:
  - Added "Archive past campaigns" in the Ignored Campaigns section and a dedicated backend route (`POST /api/campaigns/archive`).
  - Cleans up expired and finished campaigns from `ignored_campaigns` so settings don't accumulate stale IDs.
  - Automatic maintenance cleanup in `twitch._prune_ignored_past()` prunes past campaigns without user intervention.
  - Distinguishes past vs active ignored campaigns in the UI with a "Past" badge.

- **Clear Indicator When Active Campaign Has No Streamers**:
  - Displays a warning banner in the Cockpit card when an active campaign is selected but no live channels with drops are broadcasting.
  - Shows clear streamer status: *"No streamers online · Waiting for live channel"*.
  - Provides a 1-click button to defer the campaign and mine another game in the queue for now, plus quick link to review all campaigns that can be automatically started.
  - Channels tab shows an informative message explaining the miner is waiting for streams to come online.

- **Direct Priority / Auto-Drop Button on Campaign Cards**:
  - Added a state-aware `+ Prioritize / Auto-drop` button on every campaign card.
  - Displays `✓ Prioritized` (with green status indicator) if the game is already in your mining order. Clicking it removes the game from priority; clicking when unselected adds it to the mining order.
  - Dynamically updates across all campaign cards whenever the mining order changes.
