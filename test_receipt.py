import unittest
from datetime import date

from hearth.receipt import extract_with_bedrock, find_dates, parse_receipt


class ReceiptTests(unittest.TestCase):
    def test_warranty_receipt(self):
        text = (
            "Samsung 253L Refrigerator\n"
            "Sold by: Vijay Sales, Chennai\n"
            "Invoice date: 12 Jan 2026\n"
            "Total: ₹28,490\n"
            "Comes with 2 year warranty on the unit.\n"
        )
        r = parse_receipt(text)
        self.assertEqual(r["kind"], "warranty")
        self.assertEqual(r["name"], "Samsung 253L Refrigerator")
        self.assertEqual(r["vendor"], "Vijay Sales")
        self.assertEqual(r["due_on"], "2028-01-12")
        self.assertEqual(r["warranty_months"], 24)
        self.assertEqual(r["confidence"], "high")

    def test_month_warranty_iso_date(self):
        r = parse_receipt("Mixer grinder\nPurchased 2026-03-31\n6 months warranty")
        self.assertEqual(r["due_on"], "2026-09-30")

    def test_bill_uses_due_date(self):
        r = parse_receipt("Electricity bill\nBill date 01/10/2026\nAmount due Rs. 1,240\nDue date 15/10/2026")
        self.assertEqual(r["kind"], "bill")
        self.assertEqual(r["due_on"], "2026-10-15")

    def test_day_first_numeric_dates(self):
        self.assertEqual(find_dates("paid on 05/10/2026"), [date(2026, 10, 5)])

    def test_invalid_dates_ignored(self):
        self.assertEqual(find_dates("on 31/02/2026"), [])

    def test_unparseable_text_is_low_confidence(self):
        r = parse_receipt("just some words")
        self.assertEqual(r["confidence"], "low")
        self.assertIn("due_on", r["missing"])

    def test_bedrock_falls_back_without_credentials(self):
        # boto3 is not installed here / no credentials: must not raise
        r = extract_with_bedrock("Kettle\n2026-01-01\n1 year warranty")
        self.assertEqual(r["due_on"], "2027-01-01")
        self.assertTrue(r["source"].startswith("offline-parser"))


if __name__ == "__main__":
    unittest.main()
