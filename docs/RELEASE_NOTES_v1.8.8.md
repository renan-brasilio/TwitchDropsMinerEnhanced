# v1.8.8

## Fixes & Improvements

- **Community Drops Catalog Integration (`public_catalog.py`)**:
  - Integrated public community drops feed (`https://twitch-drops-api.sunkwi.com/v2/drops`) into inventory discovery.
  - Fixes missing active campaigns when Twitch returns an empty campaign list to device-code/TV clients or fails browser-integrity checks.
  - Automatically loads and normalizes all 100+ active and upcoming games with drop campaigns across Twitch (e.g. Overwatch, Marvel Rivals, Once Human, ARC Raiders, Valorant, etc.).
  - Preserves stream-scraping discovery as a secondary fallback if the feed is unreachable.
- **Resilient Claim Endpoint (`api.py`)**:
  - Safe error handling around on-demand claim ID generation so unlinked or partially complete drops don't raise 500 errors during batch claims.
- **Session & Quality Settings Safety (`twitch.py`)**:
  - Guarded `connection_quality` lookup in `get_session` against missing attribute in simple mock environments.
- **Comprehensive Test Coverage**:
  - Added test suite for public catalog feed parsing and inventory population in `test_campaign_discovery.py`.
  - All 311 unit and E2E tests passing.
