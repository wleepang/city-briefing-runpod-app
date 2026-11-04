from app.budget import Budget


def test_calls_are_refused_past_the_limit_until_the_window_resets():
    t = {"now": 0}
    b = Budget(limit=2, window_s=1000, now=lambda: t["now"])
    assert [b.try_consume() for _ in range(4)] == [True, True, False, False]
    t["now"] = 999
    assert b.try_consume() is False
    t["now"] = 1000
    assert [b.try_consume() for _ in range(3)] == [True, True, False]


def test_a_limit_of_zero_or_less_disables_the_cap():
    for limit in (0, -1):
        b = Budget(limit=limit)
        assert all(b.try_consume() for _ in range(1000))
