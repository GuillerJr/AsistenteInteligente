"""Budget policy with an explicit clock; real stdio behavior is tested separately."""

from types import SimpleNamespace

import pytest

from jarvis_core import codex


class ManualDeadline:
    def __init__(self, clock, end):
        self.clock, self.end = clock, end

    def when(self):
        return self.end

    def expired(self):
        return self.end is not None and self.clock[0] >= self.end

    def reschedule(self, end):
        self.end = end


def policy(monkeypatch):
    clock = [0.0]
    loop = SimpleNamespace(time=lambda: clock[0])
    monkeypatch.setattr(codex, "asyncio", SimpleNamespace(get_running_loop=lambda: loop))
    deadline = ManualDeadline(clock, 10.0)
    return clock, deadline, codex._InferenceBudget(deadline)


def test_inference_time_accumulates_while_human_waits_do_not_renew_it(monkeypatch):
    clock, deadline, budget = policy(monkeypatch)
    clock[0] = 2.0
    budget.pause()
    clock[0] = 1002.0
    budget.resume()
    assert deadline.when() == 1010.0
    clock[0] = 1005.0
    budget.pause()
    clock[0] = 2005.0
    budget.resume()
    assert deadline.when() == 2010.0  # 2 + 3 seconds spent; only 5 remain.
    clock[0] = 2010.0
    with pytest.raises(codex.CodexError, match="codex_turn_expired"):
        budget.pause()


def test_overlapping_callbacks_resume_only_after_the_last_wait_finishes(monkeypatch):
    clock, deadline, budget = policy(monkeypatch)
    clock[0] = 2.0
    budget.pause()
    clock[0] = 1002.0
    budget.pause()
    budget.resume()
    assert deadline.when() is None
    clock[0] = 3002.0
    budget.resume()
    assert deadline.when() == 3010.0


def test_closed_turn_cannot_restart_its_deadline_after_callback_cleanup(monkeypatch):
    clock, deadline, budget = policy(monkeypatch)
    clock[0] = 2.0
    budget.pause()
    budget.closed = True
    clock[0] = 1002.0
    budget.resume()
    assert deadline.when() is None
    with pytest.raises(codex.CodexError, match="codex_turn_expired"):
        budget.pause()
