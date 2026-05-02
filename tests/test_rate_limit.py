from rate_limit import RateLimiter


def test_allows_under_budget():
    rl = RateLimiter(max_requests=3, window_sec=60)
    for _ in range(3):
        assert rl.is_allowed(user_id=1, now=0.0)


def test_blocks_at_budget():
    rl = RateLimiter(max_requests=3, window_sec=60)
    for _ in range(3):
        rl.is_allowed(user_id=1, now=0.0)
    assert not rl.is_allowed(user_id=1, now=0.0)


def test_independent_users():
    rl = RateLimiter(max_requests=2, window_sec=60)
    rl.is_allowed(user_id=1, now=0.0)
    rl.is_allowed(user_id=1, now=0.0)
    assert not rl.is_allowed(user_id=1, now=0.0)
    # Different user has fresh budget
    assert rl.is_allowed(user_id=2, now=0.0)
    assert rl.is_allowed(user_id=2, now=0.0)
    assert not rl.is_allowed(user_id=2, now=0.0)


def test_window_slides():
    rl = RateLimiter(max_requests=2, window_sec=10)
    assert rl.is_allowed(user_id=1, now=0.0)
    assert rl.is_allowed(user_id=1, now=5.0)
    # Still within window — blocked
    assert not rl.is_allowed(user_id=1, now=8.0)
    # First request expired (now=11 > 0+10), so 1 slot frees up
    assert rl.is_allowed(user_id=1, now=11.0)


def test_reset_clears_budget():
    rl = RateLimiter(max_requests=1, window_sec=60)
    assert rl.is_allowed(user_id=1, now=0.0)
    assert not rl.is_allowed(user_id=1, now=0.0)
    rl.reset()
    assert rl.is_allowed(user_id=1, now=0.0)
