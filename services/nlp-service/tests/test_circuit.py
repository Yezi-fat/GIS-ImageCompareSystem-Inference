"""P-024 熔断器验收：60s 窗口连续 5 次失败断流 30s 后半开试探，参数可配。"""
from nlp_service.nlp.circuit import CircuitBreaker


class FakeClock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now

    def advance(self, seconds: float):
        self.now += seconds


def _breaker() -> tuple[CircuitBreaker, FakeClock]:
    clock = FakeClock()
    return CircuitBreaker(failure_threshold=5, window_s=60, open_duration_s=30, clock=clock), clock


def test_closed_until_threshold():
    cb, _ = _breaker()
    for _ in range(4):
        cb.on_failure()
    assert cb.state() == "closed" and cb.allow() is True
    cb.on_failure()  # 第 5 次
    assert cb.state() == "open"


def test_open_blocks_and_half_open_after_duration():
    cb, clock = _breaker()
    for _ in range(5):
        cb.on_failure()
    assert cb.allow() is False           # 断流中
    clock.advance(31)                    # 超过 open_duration 30s
    assert cb.state() == "half_open"
    assert cb.allow() is True            # 半开放行一次试探
    assert cb.allow() is False           # 半开仅允许一个试探在途


def test_half_open_success_closes():
    cb, clock = _breaker()
    for _ in range(5):
        cb.on_failure()
    clock.advance(31)
    cb.allow()
    cb.on_success()                      # 试探成功 → 闭合
    assert cb.state() == "closed" and cb.allow() is True


def test_half_open_failure_reopens():
    cb, clock = _breaker()
    for _ in range(5):
        cb.on_failure()
    clock.advance(31)
    cb.allow()
    cb.on_failure()                      # 试探失败 → 重新断流
    assert cb.state() == "open" and cb.allow() is False


def test_window_slides():
    """窗口外旧失败不计数（60s 窗口滑动）。"""
    cb, clock = _breaker()
    for _ in range(4):
        cb.on_failure()
    clock.advance(61)                    # 旧失败滑出窗口
    cb.on_failure()
    assert cb.state() == "closed"        # 窗口内仅 1 次失败
