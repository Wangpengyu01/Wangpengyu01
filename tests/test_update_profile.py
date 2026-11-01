import unittest
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta, timezone
from unittest.mock import patch

from scripts.update_profile import (
    parse_calendar,
    collect,
    render_contributions,
    render_stats,
    summarize_repositories,
)


CALENDAR = '''
<td id="d1" data-date="2026-10-30" data-level="0"></td>
<tool-tip for="d1">No contributions on October 30th.</tool-tip>
<td id="d2" data-date="2026-10-31" data-level="1"></td>
<tool-tip for="d2">1 contribution on October 31st.</tool-tip>
<td id="d3" data-date="2026-11-01" data-level="4"></td>
<tool-tip for="d3">1,234 contributions on November 1st.</tool-tip>
'''


class MetricsTests(unittest.TestCase):
    def test_collection_uses_public_rolling_calendar_not_calendar_year_query(self):
        start = date(2025, 11, 2)
        html = ''.join(
            f'<td id="d{i}" data-date="{start+timedelta(days=i)}" data-level="0"></td>'
            f'<tool-tip for="d{i}">No contributions on this day.</tool-tip>'
            for i in range(365))

        def response(url):
            if url == 'https://api.github.com/users/Wangpengyu01':
                return '{"followers":1}'
            if url == 'https://api.github.com/users/Wangpengyu01/repos?per_page=100&type=owner&page=1':
                return '[]'
            if url == 'https://github.com/users/Wangpengyu01/contributions':
                return html
            self.fail(f'Unexpected data endpoint: {url}')

        with patch('scripts.update_profile.fetch', side_effect=response), \
             patch('scripts.update_profile.datetime') as clock:
            clock.now.return_value = datetime(2026, 11, 1, 10, tzinfo=timezone.utc)
            metrics = collect('Wangpengyu01')
        self.assertEqual(len(metrics['days']), 365)
        self.assertEqual(metrics['days'][0]['date'], '2025-11-02')
        self.assertEqual(metrics['days'][-1]['date'], '2026-11-01')

    def test_repo_totals_exclude_private_and_other_owners_and_fork_stars(self):
        def repo(owner, private, fork, stars):
            return {"owner": {"login": owner}, "private": private,
                    "fork": fork, "stargazers_count": stars}

        result = summarize_repositories([
            repo("Wangpengyu01", False, False, 7),
            repo("wangpengyu01", False, True, 90),
            repo("Wangpengyu01", True, False, 100),
            repo("someone-else", False, False, 200),
        ], "Wangpengyu01")
        self.assertEqual(result, {"repos": 2, "stars": 7})

    def test_calendar_keeps_zero_days_and_parses_comma_separated_counts(self):
        days = parse_calendar(CALENDAR, date(2026, 10, 30), date(2026, 11, 1))
        self.assertEqual([d["date"] for d in days],
                         ["2026-10-30", "2026-10-31", "2026-11-01"])
        self.assertEqual([d["count"] for d in days], [0, 1, 1234])
        self.assertEqual([d["level"] for d in days], [0, 1, 4])

    def test_incomplete_calendar_is_an_error_instead_of_fake_zero_activity(self):
        with self.assertRaises(ValueError):
            parse_calendar(CALENDAR.replace('for="d2"', 'for="unrelated"'),
                           date(2026, 10, 30), date(2026, 11, 1))

    def test_calendar_missing_a_date_is_rejected(self):
        with self.assertRaises(ValueError):
            parse_calendar(CALENDAR, date(2026, 10, 29), date(2026, 11, 1))

    def test_contribution_cells_follow_sunday_first_weeks_across_month_boundary(self):
        days = parse_calendar(CALENDAR, date(2026, 10, 30), date(2026, 11, 1))
        root = ET.fromstring(render_contributions(days, "2026-11-01"))
        cells = [n for n in root.iter() if n.get("data-date")]
        self.assertEqual(len(cells), 3)
        by_date = {n.get("data-date"): n for n in cells}
        friday, saturday, sunday = [by_date[s] for s in
                                    ["2026-10-30", "2026-10-31", "2026-11-01"]]
        self.assertEqual(friday.get("x"), saturday.get("x"))
        self.assertGreater(float(saturday.get("y")), float(friday.get("y")))
        self.assertGreater(float(sunday.get("x")), float(saturday.get("x")))
        self.assertLess(float(sunday.get("y")), float(saturday.get("y")))
        text = "".join(root.itertext())
        self.assertIn("1,235", text)
        self.assertIn("2 active days", text)
        self.assertIn("2 day best streak", text)

    def test_empty_language_data_and_xml_sensitive_names_render_safely(self):
        for languages in [{}, {"A&B <lang>": 100}]:
            metrics = {"repos": 0, "stars": 0, "followers": 0,
                       "contributions": 0, "languages": languages,
                       "updated": "2026-11-01"}
            for mobile in [False, True]:
                root = ET.fromstring(render_stats(metrics, mobile=mobile))
                self.assertIn("PUBLIC REPOS", "".join(root.itertext()))
                if languages:
                    self.assertIn("A&B <lang>", "".join(root.itertext()))


if __name__ == "__main__":
    unittest.main()
