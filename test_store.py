import unittest
from datetime import date

from hearth.store import Hearth, add_months, match_score

TODAY = date(2026, 10, 9)


class ItemTests(unittest.TestCase):
    def setUp(self):
        self.h = Hearth()

    def test_remember_and_find(self):
        self.h.remember_item("passport", "blue folder, bedroom shelf")
        m = self.h.find_item("where is my passport")
        self.assertEqual(m[0].location, "blue folder, bedroom shelf")

    def test_moving_item_records_previous_location(self):
        self.h.remember_item("scissors", "kitchen drawer")
        r = self.h.remember_item("Scissors", "study desk")
        self.assertEqual(r["moved_from"], "kitchen drawer")
        self.assertEqual(self.h.find_item("scissors")[0].location, "study desk")

    def test_same_location_is_not_a_move(self):
        self.h.remember_item("keys", "hook")
        self.assertIsNone(self.h.remember_item("keys", "hook")["moved_from"])

    def test_ambiguous_query_detected(self):
        self.h.remember_item("laptop charger", "study drawer")
        self.h.remember_item("phone charger", "bedside table")
        matches = self.h.find_item("charger")
        self.assertEqual(len(matches), 2)
        self.assertTrue(self.h.is_ambiguous(matches))

    def test_specific_query_not_ambiguous(self):
        self.h.remember_item("laptop charger", "study drawer")
        self.h.remember_item("phone charger", "bedside table")
        matches = self.h.find_item("laptop charger")
        self.assertFalse(self.h.is_ambiguous(matches))
        self.assertEqual(matches[0].name, "laptop charger")

    def test_unrelated_query_returns_nothing(self):
        self.h.remember_item("passport", "folder")
        self.assertEqual(self.h.find_item("bicycle pump"), [])

    def test_forget(self):
        self.h.remember_item("keys", "hook")
        self.assertTrue(self.h.forget_item("keys"))
        self.assertFalse(self.h.forget_item("keys"))

    def test_empty_name_rejected(self):
        with self.assertRaises(ValueError):
            self.h.remember_item("  ", "x")


class FactTests(unittest.TestCase):
    def test_save_overwrites_and_recalls(self):
        h = Hearth()
        h.save_fact("wifi password", "old")
        h.save_fact("Wifi Password", "sunflower42")
        r = h.recall_fact("what's the wifi password")
        self.assertEqual(r[0]["value"], "sunflower42")
        self.assertEqual(len(r), 1)


class DocumentTests(unittest.TestCase):
    def setUp(self):
        self.h = Hearth()

    def test_urgency_buckets(self):
        self.assertEqual(Hearth.urgency(-1), "overdue")
        self.assertEqual(Hearth.urgency(0), "this_week")
        self.assertEqual(Hearth.urgency(7), "this_week")
        self.assertEqual(Hearth.urgency(8), "this_month")
        self.assertEqual(Hearth.urgency(30), "this_month")
        self.assertEqual(Hearth.urgency(31), "later")

    def test_upcoming_orders_and_filters(self):
        self.h.track_document("warranty", "Fridge warranty", "2026-10-12")
        self.h.track_document("bill", "Electricity", "2026-10-05")
        self.h.track_document("renewal", "Car insurance", "2027-03-01")
        up = self.h.upcoming(30, TODAY)
        self.assertEqual([d["name"] for d in up], ["Electricity", "Fridge warranty"])
        self.assertEqual(up[0]["urgency"], "overdue")
        self.assertEqual(up[1]["days_left"], 3)

    def test_recurring_bill_rolls_forward(self):
        d = self.h.track_document("bill", "Internet", "2026-01-31", recurs="monthly")
        r = self.h.mark_done(d["id"])
        self.assertEqual(r["next_due_on"], "2026-02-28")

    def test_one_off_closes(self):
        d = self.h.track_document("renewal", "Passport renewal", "2026-10-10")
        self.assertIsNone(self.h.mark_done(d["id"])["next_due_on"])
        self.assertEqual(self.h.upcoming(30, TODAY), [])

    def test_bad_kind_and_recurrence_rejected(self):
        with self.assertRaises(ValueError):
            self.h.track_document("pet", "x", "2026-10-10")
        with self.assertRaises(ValueError):
            self.h.track_document("bill", "x", "2026-10-10", recurs="weekly")

    def test_unknown_id(self):
        with self.assertRaises(KeyError):
            self.h.mark_done(999)

    def test_brief_and_speech(self):
        self.h.track_document("bill", "Electricity", "2026-10-05")
        self.h.track_document("warranty", "Fridge warranty", "2026-10-12")
        self.h.track_document("renewal", "Gym", "2026-10-30")
        self.h.remember_item("passport", "folder")
        b = self.h.brief(TODAY)
        self.assertEqual(b["counts"], {"overdue": 1, "this_week": 1, "this_month": 1})
        text = Hearth.speak_brief(b)
        self.assertIn("Overdue: Electricity", text)
        self.assertIn("Fridge warranty in 3 days", text)

    def test_empty_brief_speech(self):
        self.assertEqual(
            Hearth.speak_brief(self.h.brief(TODAY)), "Nothing needs your attention right now."
        )


class HelperTests(unittest.TestCase):
    def test_add_months_clamps(self):
        self.assertEqual(add_months(date(2026, 1, 31), 1), date(2026, 2, 28))
        self.assertEqual(add_months(date(2028, 1, 31), 1), date(2028, 2, 29))
        self.assertEqual(add_months(date(2026, 11, 15), 3), date(2027, 2, 15))

    def test_match_score_basics(self):
        self.assertEqual(match_score("keys", "Keys"), 1.0)
        self.assertGreater(match_score("charger", "laptop charger"), 0.9)
        self.assertLess(match_score("bicycle pump", "passport"), 0.55)


if __name__ == "__main__":
    unittest.main()
