# v1.5.1

## Fix: new drop campaigns were never found

Around October 4–6, 2026, Twitch stopped sending the drop-campaign list to the device-code (TV) login this app uses. The list now comes back empty, and the web login is rejected with `failed integrity check`. The miner only saw campaigns you had already started. Once those finished it went idle, and **Games → With drops** stayed empty.

The miner now finds campaigns on Twitch itself when the list is empty:

- It looks for live **Drops Enabled** streams for every game in your mining order, plus the most-watched Drops Enabled streams across Twitch, and asks those channels which campaigns they offer. No third-party service is involved.
- In a live check this found 16 campaigns, including Marvel Rivals, VALORANT, ARC Raiders, Escape from Tarkov and Dead by Daylight. Mining resumed on Marvel Rivals, and Twitch credited the progress.
- Only campaigns with someone streaming them right now are found, and those are the only ones that can be mined.

### Account links

Twitch also no longer says whether your account is linked to a game's publisher for this login.

- Campaigns found this way show a **Link unknown** badge.
- They are mined **only while their game is in your mining order** on the Games page. Choosing a game means you've linked its account. Others are listed but not mined.
- Campaigns already in progress keep Twitch's full data, including link status and progress.
- Adding a game to your mining order refreshes campaigns right away, so its live campaigns are found.
- When Twitch reports a campaign as **Unlinked**, its card now has an **Unlinked · Link account** button that opens the publisher's link page. For example, Marvel Rivals campaigns need a linked NetEase account and aren't mined until you link it.

## Fix: links did nothing in the Mac app

Links that open a new window did nothing inside the native app window, including the Twitch activation page, the twitch.tv link in the token instructions, and the new **Link account** buttons. They now open in your default browser (http/https only).

## Tests

- 7 new tests for campaign discovery:
  - The empty-list and integrity-failure fallbacks; any other Twitch error still fails.
  - In-progress data wins over discovered data.
  - A campaign is resolved under its own game (for example *Special Events* on a VALORANT stream).
  - Ended and event-only campaigns are skipped, and upcoming ones are marked.
  - Stream and channel caps.
  - Unknown-link campaigns are eligible only for chosen games, case-insensitively.
- 1 API test: adding a game refreshes campaigns, while reordering or removing does not.

## Downloads

- `TwitchDropsMinerEnhanced-v1.5.1-macOS-arm64.dmg`: drag-and-drop macOS installer.
- `TwitchDropsMinerEnhanced-v1.5.1-macOS-arm64.zip`: macOS application archive.
