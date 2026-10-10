from datetime import datetime, timezone

import pytest

from hdmirror import months


def test_shift_and_between():
    assert months.shift("202612", 1) == "202701"
    assert months.shift("202601", -1) == "202512"
    assert months.between("202511", "202602") == ["202511", "202512", "202601", "202602"]
    assert months.between("202602", "202601") == []


def test_current_uses_utc_minus_5():
    # 03:00 UTC on Nov 1st is still October on HistData's EST clock.
    assert months.current(datetime(2026, 11, 1, 3, tzinfo=timezone.utc)) == "202610"
    assert months.current(datetime(2026, 11, 1, 5, tzinfo=timezone.utc)) == "202611"


def test_check_rejects_bad_months():
    with pytest.raises(ValueError):
        months.check("202613")
