from datetime import datetime, timezone
import unittest
from state_run_contracts import require_current_closed_session


class ForwardObservationTests(unittest.TestCase):
    def calendar(self):return [('2026-10-28',True),('2026-10-29',True),('2026-10-30',True),('2026-10-31',False)]

    def test_next_morning_uses_previous_close(self):
        require_current_closed_session('2026-10-29',self.calendar(),datetime(2026,10,30,0,tzinfo=timezone.utc))

    def test_old_day_cannot_be_created_as_forward(self):
        with self.assertRaises(RuntimeError):
            require_current_closed_session('2026-10-12',self.calendar(),datetime(2026,10,30,0,tzinfo=timezone.utc))

    def test_after_close_uses_today(self):
        require_current_closed_session('2026-10-30',self.calendar(),datetime(2026,10,30,8,tzinfo=timezone.utc))

    def test_weekend_and_incomplete_calendar(self):
        require_current_closed_session('2026-10-30',self.calendar(),datetime(2026,10,31,0,tzinfo=timezone.utc))
        with self.assertRaises(RuntimeError):
            require_current_closed_session('2026-10-30',self.calendar()[:-1],datetime(2026,10,31,0,tzinfo=timezone.utc))


if __name__ == '__main__':unittest.main()
