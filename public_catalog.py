"""
Public community drops feed (https://twitch-drops-api.sunkwi.com/v2/drops).

Used when Twitch's account-bound GQL queries (Campaigns / ViewerDropsDashboard)
fail or return null due to Twitch's client-integrity challenge or device-code
client restrictions.

The public catalog aggregates all active and upcoming campaigns across Twitch.
Campaign details (timeBasedDrops, allowed channels, dates, games) are verified
and merged into inventory so that all games with drops appear in the app.
"""
from __future__ import annotations
import json
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlparse

import aiohttp
from constants import JsonType

logger = logging.getLogger("TwitchDrops")

PUBLIC_CATALOG_URL = "https://twitch-drops-api.sunkwi.com/v2/drops"
MAX_CAMPAIGNS = 2000
MAX_FEED_AGE = timedelta(hours=6)
MAX_BODY_BYTES = 10 * 1024 * 1024

_ID_RE = re.compile(r"^[A-Za-z0-9_.:\-]{1,200}$")
_LOGIN_RE = re.compile(r"^[A-Za-z0-9_]{1,50}$")
_DISTRIBUTION_TYPES = ("BADGE", "EMOTE", "DIRECT_ENTITLEMENT", "UNKNOWN")


def _parse_iso(string: str) -> datetime:
    s = string.replace("Z", "+00:00")
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _clean_reward(record: dict[str, Any], default_game_info: dict[str, Any]) -> JsonType | None:
    try:
        status = record.get("status")
        if status not in ("ACTIVE", "UPCOMING"):
            return None
        reward_id = record.get("id")
        if not reward_id or not _ID_RE.match(str(reward_id)):
            return None
        name = str(record.get("name") or "Campaign").strip()
        
        # Game details
        raw_game = record.get("game") or {}
        game_id = str(raw_game.get("id") or default_game_info.get("id") or "0")
        game_name = str(raw_game.get("displayName") or raw_game.get("name") or default_game_info.get("displayName") or "Unknown")
        box_art = str(raw_game.get("boxArtURL") or default_game_info.get("boxArtURL") or "")

        game = {
            "id": game_id,
            "name": game_name,
            "displayName": game_name,
            "slug": (game_name).lower().replace(" ", "-"),
            "boxArtURL": box_art,
        }

        start_at = str(record.get("startAt") or default_game_info.get("startAt") or "")
        end_at = str(record.get("endAt") or default_game_info.get("endAt") or "")
        if not start_at or not end_at:
            return None

        allow = record.get("allow") or {}
        channels = [
            {
                "id": str(ch.get("id") or ""),
                "name": str(ch.get("name") or ch.get("login") or "streamer"),
                "displayName": str(ch.get("displayName") or ch.get("name") or "Streamer")
            }
            for ch in (allow.get("channels") or [])
            if ch.get("id")
        ]

        time_based_drops = []
        for drop in record.get("timeBasedDrops") or []:
            drop_id = drop.get("id")
            if not drop_id or not _ID_RE.match(str(drop_id)):
                continue
            benefits = []
            for edge in (drop.get("benefitEdges") or []):
                b = edge.get("benefit") or {}
                benefits.append({
                    "benefit": {
                        "id": str(b.get("id") or ""),
                        "name": str(b.get("name") or "").strip(),
                        "distributionType": b.get("distributionType", "DIRECT_ENTITLEMENT"),
                        "imageAssetURL": str(b.get("imageAssetURL") or "")
                    }
                })
            time_based_drops.append({
                "id": str(drop_id),
                "name": str(drop.get("name") or "").strip(),
                "startAt": str(drop.get("startAt") or start_at),
                "endAt": str(drop.get("endAt") or end_at),
                "requiredMinutesWatched": int(drop.get("requiredMinutesWatched") or 0),
                "preconditionDrops": [{"id": p["id"]} for p in (drop.get("preconditionDrops") or []) if "id" in p] or None,
                "benefitEdges": benefits
            })

        return {
            "id": str(reward_id),
            "name": name,
            "game": game,
            "self": {"isAccountConnected": False},
            "linkUnknown": True,
            "accountLinkURL": str(record.get("accountLinkURL") or ""),
            "detailsURL": str(record.get("detailsURL") or ""),
            "startAt": start_at,
            "endAt": end_at,
            "status": status,
            "allow": {
                "isEnabled": bool(allow.get("isEnabled", False)),
                "channels": channels
            },
            "timeBasedDrops": time_based_drops
        }
    except Exception as e:
        logger.debug(f"Error cleaning public catalog reward: {e}")
        return None


def parse_public_catalog(payload: dict[str, Any], now: datetime | None = None) -> list[JsonType]:
    now = now or datetime.now(timezone.utc)
    try:
        updated_raw = payload.get("lastUpdatedAt")
        if updated_raw:
            try:
                updated = _parse_iso(updated_raw)
                if now - updated > MAX_FEED_AGE:
                    logger.warning(f"Public drops catalog feed is stale ({updated_raw})")
            except Exception:
                pass

        groups = payload.get("data")
        if not isinstance(groups, list):
            return []

        campaigns: list[JsonType] = []
        for g in groups:
            default_game = {
                "id": str(g.get("gameId") or "0"),
                "displayName": str(g.get("gameDisplayName") or "Unknown"),
                "boxArtURL": str(g.get("gameBoxArtURL") or ""),
                "startAt": str(g.get("startAt") or ""),
                "endAt": str(g.get("endAt") or "")
            }
            for reward in g.get("rewards") or []:
                cleaned = _clean_reward(reward, default_game)
                if cleaned:
                    campaigns.append(cleaned)
                    if len(campaigns) >= MAX_CAMPAIGNS:
                        return campaigns
        return campaigns
    except Exception as exc:
        logger.warning(f"Error parsing public drops catalog: {exc}")
        return []


async def fetch_public_catalog(session: aiohttp.ClientSession, proxy: str | None = None) -> list[JsonType]:
    try:
        async with session.get(
            PUBLIC_CATALOG_URL,
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"},
            timeout=aiohttp.ClientTimeout(total=15),
            proxy=proxy or None
        ) as resp:
            if resp.status != 200:
                logger.warning(f"Public drops catalog returned HTTP {resp.status}")
                return []
            body = bytearray()
            async for chunk in resp.content.iter_chunked(64 * 1024):
                body += chunk
                if len(body) > MAX_BODY_BYTES:
                    logger.warning("Public drops catalog response too large")
                    return []
            data = json.loads(body)
            return parse_public_catalog(data)
    except Exception as exc:
        logger.warning(f"Public drops catalog unavailable: {exc}")
        return []
