"""Risk row 1: drop eligibility, progress and claim-window rules in inventory.py."""
import logging
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from channel import Channel
from constants import MAX_EXTRA_MINUTES, PriorityMode, State
from exceptions import GQLException
from inventory import DropsCampaign
from twitch import Twitch


NOW = datetime(2026, 10, 11, 12, tzinfo=timezone.utc)
GAME = {"id": "1001", "name": "Game", "boxArtURL": ""}
OTHER_GAME = {"id": "2002", "name": "Other Game", "boxArtURL": ""}
SPECIAL_GAME = {"id": "509663", "name": "Special Events", "boxArtURL": ""}


class Clock(datetime):
    current = NOW

    @classmethod
    def now(cls, tz=None):
        return cls.current.astimezone(tz) if tz else cls.current.replace(tzinfo=None)


def ts(moment):
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def benefit(benefit_id, distribution="DIRECT_ENTITLEMENT"):
    return {"benefit": {"id": benefit_id, "name": f"Reward {benefit_id}",
                        "distributionType": distribution, "imageAssetURL": ""}}


def drop(drop_id, *, required=60, watched=0, claimed=False, claim_id=None, preconditions=(),
         start=NOW - timedelta(days=1), end=NOW + timedelta(days=1), benefits=None,
         self_edge=True):
    data = {
        "id": drop_id, "name": drop_id, "startAt": ts(start), "endAt": ts(end),
        "benefitEdges": [benefit(f"{drop_id}-benefit")] if benefits is None else benefits,
        "preconditionDrops": [{"id": pid} for pid in preconditions] or None,
        "requiredMinutesWatched": required,
    }
    if self_edge:
        data["self"] = {"dropInstanceID": claim_id, "isClaimed": claimed,
                        "currentMinutesWatched": watched}
    return data


def campaign_data(drops, *, game=GAME, linked=True, start=NOW - timedelta(days=1),
                  end=NOW + timedelta(days=1), status=None, acl=(), acl_enabled=True):
    return {
        "id": "campaign", "name": "Campaign", "game": dict(game),
        "self": {"isAccountConnected": linked}, "accountLinkURL": "",
        "startAt": ts(start), "endAt": ts(end),
        "status": status or ("ACTIVE" if end > NOW else "EXPIRED"),
        "allow": {"channels": [{"id": str(cid), "name": f"acl{cid}", "displayName": f"ACL{cid}"}
                               for cid in acl] or None,
                  "isEnabled": acl_enabled},
        "timeBasedDrops": list(drops),
    }


class InventoryTestCase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        Clock.current = NOW
        self.clock = patch("inventory.datetime", Clock)
        self.clock.start()
        self.settings = SimpleNamespace(
            priority=[], exclude=set(), priority_mode=PriorityMode.PRIORITY_ONLY, auth_token="",
            auto_claim=False, enable_badges_emotes=False, available_drops_check=False, dump=False,
        )
        self.twitch = Twitch(self.settings)
        # Claims raise a desktop notification (osascript on macOS); keep tests off the OS.
        self.twitch.gui.send_notification = Mock()

    async def asyncTearDown(self):
        self.twitch.gui.progress.stop_timer()
        logging.getLogger("TwitchDrops").removeHandler(self.twitch.gui._log_handler)
        self.clock.stop()

    def campaign(self, *drops, claimed_benefits=None, **kwargs):
        return DropsCampaign(self.twitch, campaign_data(drops, **kwargs), claimed_benefits or {})

    def live_channel(self, channel_id, game=GAME):
        """A channel as returned by Twitch's game directory (drops-enabled filter)."""
        return Channel.from_directory(self.twitch, {
            "id": str(9000 + channel_id), "game": dict(game), "viewersCount": 10, "title": "live",
            "broadcaster": {"id": str(channel_id), "login": f"streamer{channel_id}",
                            "displayName": f"Streamer{channel_id}"},
        }, drops_enabled=True)


class DropEligibilityTests(InventoryTestCase):
    async def test_drop_with_unclaimed_prerequisite_cannot_be_earned(self):
        locked = self.campaign(drop("first"), drop("second", preconditions=["first"]))
        self.assertTrue(locked.timed_drops["first"].can_earn())
        self.assertFalse(locked.timed_drops["second"].can_earn())

        unlocked = self.campaign(drop("first", claimed=True), drop("second", preconditions=["first"]))
        self.assertTrue(unlocked.timed_drops["second"].can_earn())

    async def test_rewardless_drop_is_earnable_only_while_it_unlocks_a_later_drop(self):
        chained = self.campaign(drop("step", benefits=[]), drop("reward", preconditions=["step"]))
        self.assertTrue(chained.timed_drops["step"].can_earn())

        standalone = self.campaign(drop("step", benefits=[]), drop("reward"))
        self.assertFalse(standalone.timed_drops["step"].can_earn())

    async def test_drop_earning_window_includes_start_and_excludes_end(self):
        start, end = NOW + timedelta(hours=1), NOW + timedelta(hours=3)
        timed = self.campaign(drop("timed", start=start, end=end),
                              end=NOW + timedelta(days=1)).timed_drops["timed"]
        for moment, expected in ((start - timedelta(seconds=1), False), (start, True),
                                 (end - timedelta(seconds=1), True), (end, False)):
            with self.subTest(moment=moment):
                Clock.current = moment
                self.assertIs(timed.can_earn(), expected)

    async def test_drop_requiring_no_minutes_is_never_earnable(self):
        campaign = self.campaign(drop("instant", required=0))
        self.assertFalse(campaign.timed_drops["instant"].can_earn())
        self.assertFalse(campaign.can_earn())

    async def test_unlinked_account_cannot_earn_direct_entitlement_campaign(self):
        campaign = self.campaign(drop("reward"), linked=False)
        self.assertFalse(campaign.eligible)
        self.assertFalse(campaign.can_earn())
        self.assertFalse(campaign.can_earn_within(NOW + timedelta(hours=1)))
        self.assertFalse(campaign.timed_drops["reward"].can_earn())

    async def test_badge_and_emote_campaigns_follow_badges_setting_instead_of_account_link(self):
        for linked in (True, False):
            badge = drop("badge", benefits=[benefit("badge", "BADGE")])
            campaign = self.campaign(badge, linked=linked)
            with self.subTest(linked=linked):
                self.settings.enable_badges_emotes = False
                self.assertFalse(campaign.can_earn())
                self.settings.enable_badges_emotes = True
                self.assertTrue(campaign.can_earn())

    async def test_campaign_marked_expired_cannot_earn_before_its_end_time(self):
        campaign = self.campaign(drop("reward"), status="EXPIRED")
        self.assertFalse(campaign.active)
        self.assertTrue(campaign.expired)
        self.assertFalse(campaign.can_earn())
        self.assertFalse(campaign.can_earn_within(NOW + timedelta(hours=1)))

    async def test_upcoming_campaign_is_earnable_within_the_hour_it_starts_but_not_now(self):
        soon = NOW + timedelta(minutes=30)
        campaign = self.campaign(drop("reward", start=soon), start=soon)
        self.assertTrue(campaign.upcoming)
        self.assertFalse(campaign.can_earn())
        self.assertTrue(campaign.can_earn_within(NOW + timedelta(hours=1)))

        later = NOW + timedelta(hours=2)
        distant = self.campaign(drop("reward", start=later), start=later)
        self.assertFalse(distant.can_earn_within(NOW + timedelta(hours=1)))

    async def test_campaign_acl_restricts_earning_to_listed_channels(self):
        campaign = self.campaign(drop("reward"), acl=[1])
        self.assertTrue(campaign.can_earn(self.live_channel(1)))
        self.assertFalse(campaign.can_earn(self.live_channel(2)))
        self.assertFalse(campaign.timed_drops["reward"].can_earn(self.live_channel(2)))

    async def test_disabled_acl_lets_any_channel_earn(self):
        campaign = self.campaign(drop("reward"), acl=[1], acl_enabled=False)
        self.assertTrue(campaign.can_earn(self.live_channel(2)))

    async def test_channel_must_stream_campaign_game_unless_campaign_is_special(self):
        campaign = self.campaign(drop("reward"))
        offline = Channel(self.twitch, id=3, login="offline")
        self.assertFalse(campaign.can_earn(self.live_channel(1, OTHER_GAME)))
        self.assertFalse(campaign.can_earn(offline))
        self.assertTrue(campaign.can_earn(offline, ignore_channel_status=True))

        special = self.campaign(drop("reward"), game=SPECIAL_GAME)
        self.assertTrue(special.can_earn(self.live_channel(1, OTHER_GAME)))

    async def test_drop_without_self_edge_is_claimed_only_if_benefit_was_awarded_during_its_window(self):
        reward = drop("reward", self_edge=False)
        during = {"reward-benefit": NOW - timedelta(hours=1)}
        before = {"reward-benefit": NOW - timedelta(days=30)}  # same benefit from an older campaign
        self.assertTrue(self.campaign(reward, claimed_benefits=during).timed_drops["reward"].is_claimed)
        self.assertFalse(self.campaign(reward, claimed_benefits=before).timed_drops["reward"].is_claimed)


class DropProgressTests(InventoryTestCase):
    async def test_progress_completes_exactly_at_required_minutes(self):
        reward = self.campaign(drop("reward", required=60, watched=59)).timed_drops["reward"]
        self.assertLess(reward.progress, 1.0)
        self.assertEqual(reward.remaining_minutes, 1)

        reward.update_minutes(60)
        self.assertEqual(reward.progress, 1.0)
        self.assertEqual(reward.remaining_minutes, 0)

    async def test_reported_minutes_are_clamped_between_zero_and_required(self):
        reward = self.campaign(drop("reward", required=60, watched=30)).timed_drops["reward"]
        reward.update_minutes(95)
        self.assertEqual((reward.current_minutes, reward.remaining_minutes), (60, 0))
        reward.update_minutes(-5)
        self.assertEqual((reward.current_minutes, reward.progress), (0, 0.0))

    async def test_claimed_drop_reports_full_progress_despite_stale_minutes(self):
        reward = self.campaign(drop("reward", required=60, watched=5, claimed=True)).timed_drops["reward"]
        self.assertEqual(reward.progress, 1.0)
        self.assertEqual(reward.remaining_minutes, 0)

    async def test_chained_drop_remaining_time_includes_unfinished_prerequisite(self):
        campaign = self.campaign(drop("first", required=30, watched=10),
                                 drop("second", required=60, preconditions=["first"]))
        self.assertEqual(campaign.timed_drops["second"].total_remaining_minutes, 80)
        self.assertEqual(campaign.remaining_minutes, 80)
        self.assertEqual(campaign.required_minutes, 90)

    async def test_first_drop_is_the_earnable_drop_closest_to_completion(self):
        campaign = self.campaign(
            drop("done", watched=60, claimed=True),
            drop("early", watched=10),
            drop("close", watched=40),
            drop("locked", watched=55, preconditions=["early"]),
        )
        self.assertEqual(campaign.first_drop.id, "close")

    async def test_estimated_minutes_stop_earning_at_cap_until_real_progress_arrives(self):
        campaign = self.campaign(drop("reward", required=60, watched=10))
        reward, channel = campaign.timed_drops["reward"], self.live_channel(1)
        for _ in range(MAX_EXTRA_MINUTES - 1):
            campaign.bump_minutes(channel)
        self.assertTrue(reward.can_earn(channel))
        self.assertIsNot(self.twitch._state, State.CHANNEL_SWITCH)

        campaign.bump_minutes(channel)
        self.assertEqual(reward.current_minutes, 10 + MAX_EXTRA_MINUTES)
        self.assertFalse(reward.can_earn(channel))
        self.assertIs(self.twitch._state, State.CHANNEL_SWITCH)

        reward.update_minutes(30)
        self.assertEqual(reward.current_minutes, 30)
        self.assertTrue(reward.can_earn(channel))


class ClaimWindowTests(InventoryTestCase):
    def ended_campaign(self, *, claimed=False, claim_id="instance"):
        return self.campaign(
            drop("reward", watched=60, claimed=claimed, claim_id=claim_id,
                 end=NOW - timedelta(hours=1)),
            end=NOW - timedelta(hours=1),
        )

    async def test_reward_stays_claimable_until_24_hours_after_campaign_end(self):
        campaign = self.ended_campaign()
        reward = campaign.timed_drops["reward"]
        deadline = campaign.ends_at + timedelta(hours=24)
        for moment, expected in ((deadline - timedelta(seconds=1), True), (deadline, False)):
            with self.subTest(moment=moment):
                Clock.current = moment
                self.assertIs(reward.can_claim, expected)

    async def test_reward_without_claim_instance_or_already_claimed_is_not_claimable(self):
        self.assertFalse(self.ended_campaign(claim_id=None).timed_drops["reward"].can_claim)
        self.assertFalse(self.ended_campaign(claimed=True).timed_drops["reward"].can_claim)

    async def test_claim_after_window_is_refused_without_contacting_twitch(self):
        reward = self.ended_campaign().timed_drops["reward"]
        Clock.current = reward.campaign.ends_at + timedelta(hours=24)
        with patch.object(self.twitch, "gql_request", AsyncMock()) as gql:
            self.assertFalse(await reward.claim())
        gql.assert_not_awaited()
        self.assertFalse(reward.is_claimed)

    async def test_successful_claim_marks_reward_claimed_and_campaign_finished(self):
        for status in ("ELIGIBLE_FOR_ALL", "DROP_INSTANCE_ALREADY_CLAIMED"):
            campaign = self.ended_campaign()
            reward = campaign.timed_drops["reward"]
            response = {"data": {"claimDropRewards": {"status": status}}}
            with self.subTest(status=status), \
                    patch.object(self.twitch, "gql_request", AsyncMock(return_value=response)):
                self.assertTrue(await reward.claim())
                self.assertTrue(reward.is_claimed)
                self.assertFalse(reward.can_claim)
                self.assertTrue(campaign.finished)
                self.assertEqual(campaign.claimed_drops, 1)

    async def test_failed_claim_leaves_reward_claimable(self):
        failures = {
            "graphql error": AsyncMock(return_value={"data": {"errors": [{"message": "x"}]}}),
            "empty result": AsyncMock(return_value={"data": {"claimDropRewards": None}}),
            "rejected status": AsyncMock(
                return_value={"data": {"claimDropRewards": {"status": "DROP_INSTANCE_NOT_FOUND"}}}),
            "transport error": AsyncMock(side_effect=GQLException("boom")),
        }
        for label, gql in failures.items():
            campaign = self.ended_campaign()
            reward = campaign.timed_drops["reward"]
            with self.subTest(label), patch.object(self.twitch, "gql_request", gql), \
                    self.assertLogs("TwitchDrops", logging.ERROR):
                self.assertFalse(await reward.claim())
                self.assertFalse(reward.is_claimed)
                self.assertTrue(reward.can_claim)
                self.assertFalse(campaign.finished)


if __name__ == "__main__":
    unittest.main()
