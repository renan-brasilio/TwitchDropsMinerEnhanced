# v1.9.0

## Account-specific drop completion on game covers

- Show mined reward progress beneath each game's drop-campaign count: `1/3 mined`, `All 3 mined`, and `2/3 mined · 1 to claim`.
- Keep current completed campaigns in **Games → With drops** so you can see which rewards this account has already mined, without putting completed campaigns back into the mining queue or campaign cards.
- Show the same completion information in **All games** and search. Refresh it through live inventory events and status polling while preserving mounted game covers during progress updates.
- Distinguish watch-time completion from claiming. A reward counts as mined when its required watch time is complete or Twitch reports it claimed; completed watch time alone does not mean it has been claimed.
- Exclude ignored campaigns and expired campaigns without claimable rewards from game-cover progress. Clear account-specific progress when disconnecting or changing accounts.

Pending campaign totals and mining priority behavior are unchanged.
