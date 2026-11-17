"""Cash forecast arithmetic (Phase 11). Pure: no database."""
from datetime import date as D

from loan_tracker.cashflow import Flow, forecast

T = D(2026, 3, 1)


def test_no_events_stays_at_opening():
    f = forecast(50_000, [], T)
    assert (f.low_paise, f.low_date, f.short_date, f.short_paise) == (50_000, T, None, 0)
    assert f.days == []


def test_running_balance_and_first_shortfall():
    flows = [Flow(D(2026, 3, 5), -80_000), Flow(D(2026, 3, 10), 40_000), Flow(D(2026, 3, 12), -30_000)]
    f = forecast(50_000, flows, T)
    assert [(d.day, d.running_paise) for d in f.days] == [
        (D(2026, 3, 5), -30_000), (D(2026, 3, 10), 10_000), (D(2026, 3, 12), -20_000)]
    assert (f.short_date, f.short_paise) == (D(2026, 3, 5), 30_000)   # first day below zero, by how much
    assert (f.low_date, f.low_paise) == (D(2026, 3, 5), -30_000)


def test_same_day_in_and_out_net_first():
    # money in and out on one day: only the day's net matters
    f = forecast(10_000, [Flow(D(2026, 3, 5), -30_000), Flow(D(2026, 3, 5), 25_000)], T)
    assert f.days[0].running_paise == 5_000 and f.short_date is None


def test_buffer_counts_as_short():
    f = forecast(50_000, [Flow(D(2026, 3, 5), -45_000)], T, buffer_paise=10_000)
    assert (f.short_date, f.short_paise) == (D(2026, 3, 5), 5_000)


def test_short_amount_is_the_worst_dip_after_first_shortfall():
    flows = [Flow(D(2026, 3, 5), -60_000), Flow(D(2026, 3, 9), -40_000), Flow(D(2026, 3, 20), 200_000)]
    f = forecast(50_000, flows, T)
    assert (f.short_date, f.short_paise) == (D(2026, 3, 5), 50_000)   # needs 50,000 to stay at 0 throughout


def test_cautious_drops_risky_inflows_only():
    flows = [Flow(D(2026, 3, 5), 40_000, risky=True), Flow(D(2026, 3, 6), -60_000)]
    exp = forecast(30_000, flows, T)
    cau = forecast(30_000, flows, T, cautious=True)
    assert exp.short_date is None and exp.low_paise == 10_000
    assert (cau.short_date, cau.short_paise) == (D(2026, 3, 6), 30_000)


def test_opening_below_buffer_is_short_today():
    f = forecast(-5_000, [], T)
    assert (f.short_date, f.short_paise, f.low_date) == (T, 5_000, T)
