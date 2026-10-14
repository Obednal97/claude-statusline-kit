import unittest
from datetime import datetime

from claude_rotate.config import Tier
from claude_rotate.limits import (
    available, best_available, blocked_until, classify_failure, compute_limited_until,
    limit_kind, parse_reset, pick, stop_decision,
)

A = Tier("a", "/p", "login")
B = Tier("b", "/p", "token")
W = Tier("work", None, "login")
TIERS = (A, B, W)


def ts(*args):
    return datetime(*args).timestamp()  # naive = local time, same as the code under test


NOW = ts(2026, 10, 11, 14, 0)  # a Sunday


class ClassifyTest(unittest.TestCase):
    def test_plan_limits(self):
        for msg in [
            "You've hit your session limit · resets 3:45pm",
            "You've hit your weekly limit · resets Mon 12:00am",
            "You've hit your Opus limit · resets 3pm",
            "you've hit your sonnet limit",
        ]:
            self.assertEqual(classify_failure("rate_limit", msg), "limit", msg)

    def test_throttle_is_not_a_limit(self):
        self.assertEqual(classify_failure("rate_limit", "Server is temporarily limiting requests"), "other")
        self.assertEqual(classify_failure("rate_limit", "API Error: Rate limit reached"), "other")

    def test_auth_errors(self):
        for error in ("authentication_failed", "account_on_hold", "oauth_org_not_allowed", "billing_error"):
            self.assertEqual(classify_failure(error, "x"), "auth", error)

    def test_real_2_1_296_limit_messages(self):
        # Claude Code 2.1.296 builds `You've hit your ${name}${suffix}` and has generic fallbacks.
        for msg in [
            "You've hit your limit · resets 3pm (Europe/London)",
            "You've hit your usage limit · resets 3:45pm",
            "You've hit your Fable limit · resets Mon 12:00am",
            "You've hit your session limit · resets 3:45pm · progress saved",
        ]:
            self.assertEqual(classify_failure("rate_limit", msg), "limit", msg)

    def test_real_weekly_limit_with_spend_cap_message(self):
        # Captured live on 2026-10-11 (error "rate_limit", apiError usage_limit_reached, rateLimitType seven_day).
        msg = ("You've hit your monthly spend limit · raise it at "
               "https://claude.ai/settings/usage?from=cc_cli_limit_message · "
               "your weekly limit resets Oct 16 at 5am (Europe/London)")
        now = ts(2026, 10, 11, 16, 8)
        self.assertEqual(classify_failure("rate_limit", msg), "limit")
        self.assertEqual(compute_limited_until(msg, {}, now), ts(2026, 10, 16, 5, 0))

    def test_other_2_1_296_limit_wordings(self):
        for msg in [
            "You've reached your Fable limit. /model to switch models.",
            "You're out of extra usage · resets Mon 12:00am",
            "You're out of usage credits. /model to switch models.",
            "You've hit your fast limit · resets 3pm",
            "You've hit your team's shared budget. /model to switch models.",
        ]:
            self.assertEqual(classify_failure("rate_limit", msg), "limit", msg)
        self.assertEqual(limit_kind("You're out of extra usage"), "usage")
        info = "You've used your included Fable usage for this week. Continuing on Opus uses usage credits."
        self.assertEqual(classify_failure("rate_limit", info), "other")

    def test_throttle_mentioning_usage_limit_is_not_a_limit(self):
        msg = "Server is temporarily limiting requests (not your usage limit)"
        self.assertEqual(classify_failure("rate_limit", msg), "other")

    def test_other_errors(self):
        self.assertEqual(classify_failure("server_error", "You've hit your session limit"), "other")

    def test_limit_kind(self):
        self.assertEqual(limit_kind("You've hit your Weekly limit"), "weekly")
        self.assertIsNone(limit_kind("nothing"))


class ParseResetTest(unittest.TestCase):
    def test_time_later_today(self):
        self.assertEqual(parse_reset("You've hit your session limit · resets 3:45pm", NOW), ts(2026, 10, 11, 15, 45))

    def test_time_without_minutes_and_timezone(self):
        self.assertEqual(parse_reset("You've hit your session limit · resets 3pm (Europe/London)", NOW), ts(2026, 10, 11, 15, 0))

    def test_time_already_passed_rolls_to_tomorrow(self):
        self.assertEqual(parse_reset("resets 9am", NOW), ts(2026, 10, 12, 9, 0))

    def test_exactly_now_rolls_to_tomorrow(self):
        self.assertEqual(parse_reset("resets 2pm", NOW), ts(2026, 10, 12, 14, 0))

    def test_midnight_and_noon(self):
        self.assertEqual(parse_reset("resets 12am", NOW), ts(2026, 10, 12, 0, 0))
        self.assertEqual(parse_reset("resets 12pm", NOW), ts(2026, 10, 12, 12, 0))

    def test_weekday(self):
        self.assertEqual(parse_reset("You've hit your weekly limit · resets Mon 12:00am", NOW), ts(2026, 10, 12, 0, 0))

    def test_same_weekday_earlier_time_is_next_week(self):
        self.assertEqual(parse_reset("resets Sun 9am", NOW), ts(2026, 10, 18, 9, 0))

    def test_month_day(self):
        self.assertEqual(parse_reset("resets Oct 14, 3pm", NOW), ts(2026, 10, 14, 15, 0))
        self.assertEqual(parse_reset("resets Oct 14 at 3:45pm", NOW), ts(2026, 10, 14, 15, 45))

    def test_month_day_in_the_past_is_next_year(self):
        self.assertEqual(parse_reset("resets Jan 2, 1am", NOW), ts(2027, 1, 2, 1, 0))

    def test_trailing_text_is_ignored(self):
        msg = "You've hit your session limit · resets 3:45pm · /upgrade to keep using Claude"
        self.assertEqual(parse_reset(msg, NOW), ts(2026, 10, 11, 15, 45))

    def test_unparseable(self):
        for msg in ["no reset here", "resets soon", "resets 13pm", "resets Feb 30, 1am", ""]:
            self.assertIsNone(parse_reset(msg, NOW), msg)


class AvailabilityTest(unittest.TestCase):
    def test_empty_is_available(self):
        self.assertTrue(available({}, NOW))

    def test_limited_until(self):
        self.assertFalse(available({"limited_until": NOW + 60}, NOW))
        self.assertTrue(available({"limited_until": NOW - 1}, NOW))

    def test_full_window_with_future_reset_blocks(self):
        acct = {"seven_day": {"pct": 100, "resets_at": NOW + 600, "seen_at": NOW}}
        self.assertFalse(available(acct, NOW))
        self.assertEqual(blocked_until(acct, NOW), NOW + 600)

    def test_full_window_after_its_reset_is_available(self):
        self.assertTrue(available({"five_hour": {"pct": 100, "resets_at": NOW - 1, "seen_at": NOW - 9000}}, NOW))

    def test_partial_window_is_available(self):
        self.assertTrue(available({"five_hour": {"pct": 99.4, "resets_at": NOW + 600}}, NOW))


class PickTest(unittest.TestCase):
    def test_first_available_in_priority_order(self):
        self.assertEqual(pick(TIERS, {}, NOW), A)
        self.assertEqual(pick(TIERS, {"a": {"limited_until": NOW + 60}}, NOW), B)

    def test_unusable_tiers_are_skipped(self):
        self.assertEqual(pick(TIERS, {"a": {"limited_until": NOW + 60}}, NOW, {"b"}), W)

    def test_all_limited_picks_soonest_reset(self):
        accounts = {"a": {"limited_until": NOW + 300}, "b": {"limited_until": NOW + 100}, "work": {"limited_until": NOW + 200}}
        self.assertIsNone(best_available(TIERS, accounts, NOW))
        self.assertEqual(pick(TIERS, accounts, NOW), B)

    def test_all_limited_ties_go_to_priority(self):
        accounts = {n: {"limited_until": NOW + 100} for n in ("a", "b", "work")}
        self.assertEqual(pick(TIERS, accounts, NOW), A)

    def test_everything_unusable(self):
        self.assertIsNone(pick(TIERS, {}, NOW, {"a", "b", "work"}))


class ComputeLimitedUntilTest(unittest.TestCase):
    def test_uses_fresh_matching_window(self):
        acct = {"five_hour": {"pct": 100, "resets_at": NOW + 1234, "seen_at": NOW - 60}}
        self.assertEqual(compute_limited_until("You've hit your session limit · resets 9pm", acct, NOW), NOW + 1234)

    def test_weekly_uses_seven_day_window(self):
        acct = {
            "five_hour": {"pct": 10, "resets_at": NOW + 100, "seen_at": NOW},
            "seven_day": {"pct": 100, "resets_at": NOW + 99999, "seen_at": NOW},
        }
        self.assertEqual(compute_limited_until("You've hit your weekly limit", acct, NOW), NOW + 99999)

    def test_stale_window_falls_back_to_message(self):
        acct = {"five_hour": {"pct": 100, "resets_at": NOW + 1234, "seen_at": NOW - 3600}}
        self.assertEqual(
            compute_limited_until("You've hit your session limit · resets 3:45pm", acct, NOW), ts(2026, 10, 11, 15, 45))

    def test_model_limit_uses_message(self):
        acct = {"five_hour": {"pct": 100, "resets_at": NOW + 1234, "seen_at": NOW}}
        self.assertEqual(compute_limited_until("You've hit your Opus limit · resets 3pm", acct, NOW), ts(2026, 10, 11, 15, 0))

    def test_generic_limit_uses_any_full_fresh_window(self):
        acct = {
            "five_hour": {"pct": 100, "resets_at": NOW + 777, "seen_at": NOW - 30},
            "seven_day": {"pct": 60, "resets_at": NOW + 99999, "seen_at": NOW - 30},
        }
        self.assertEqual(compute_limited_until("You've hit your limit · resets 9pm", acct, NOW), NOW + 777)

    def test_unknown_falls_back_to_one_hour(self):
        self.assertEqual(compute_limited_until("You've hit your session limit", {}, NOW), NOW + 3600)


class StopDecisionTest(unittest.TestCase):
    def test_stays_when_on_best(self):
        self.assertIsNone(stop_decision(TIERS, {}, "a", NOW, 0, 120))

    def test_moves_up_after_interval(self):
        self.assertEqual(stop_decision(TIERS, {}, "b", NOW, NOW - 121, 120), A)

    def test_waits_for_interval_before_moving_up(self):
        self.assertIsNone(stop_decision(TIERS, {}, "b", NOW, NOW - 30, 120))

    def test_moves_down_immediately_when_current_is_limited(self):
        # Review focus: another session marked "a" limited; don't wait for the interval.
        self.assertEqual(stop_decision(TIERS, {"a": {"limited_until": NOW + 600}}, "a", NOW, NOW - 1, 120), B)

    def test_no_move_when_nothing_better_is_available(self):
        accounts = {n: {"limited_until": NOW + 600} for n in ("a", "b", "work")}
        self.assertIsNone(stop_decision(TIERS, accounts, "b", NOW, 0, 120))
