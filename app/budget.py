import time


class Budget:
    """Global (all users) cap on LLM generations per window. It protects the GPU bill however many clients
    there are. limit <= 0 disables the cap."""

    def __init__(self, limit, window_s=24 * 60 * 60, now=time.monotonic):
        self.limit = limit
        self.window_s = window_s
        self._now = now
        self._used = 0
        self._reset_at = now() + window_s

    def try_consume(self) -> bool:
        if self.limit <= 0:
            return True
        t = self._now()
        if t >= self._reset_at:
            self._used = 0
            self._reset_at = t + self.window_s
        if self._used >= self.limit:
            return False
        self._used += 1
        return True
