# v1.8.10

## Bug Fixes & Mining Resiliency

- **Game Directory Slug Alignment (`public_catalog.py`)**:
  - Use the exact GQL category slug provided by the public catalog feed (e.g., `overwatch-2` for Overwatch, `marvel-rivals` for Marvel Rivals) rather than naive string sanitization.
  - Fixes zero live streams returned when fetching drop-enabled directory channels for games with custom Twitch slugs.

- **Non-Priority Mining Mode Eligibility (`inventory.py`)**:
  - When "Mine only selected games" is turned OFF (i.e. *Ending Soonest* or *Lowest Availability First* modes), all active campaigns with unknown account links are now eligible to be mined once priority games finish or are offline.

- **Informative Waiting Status Display (`twitch.py`)**:
  - Displays `"No available channels to watch. Waiting for an ONLINE channel..."` when all selected games have offline streamers, rather than ambiguously reverting to generic `"Idle"`.
