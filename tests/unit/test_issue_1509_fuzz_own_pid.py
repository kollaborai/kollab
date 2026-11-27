"""#1509: the process enumeration fuzz test passes when the worker's own pid is in its sample."""

import os
import random

import tests.unit.test_process_enumeration_fuzz as fuzz


def test_fuzz_passes_when_own_pid_is_sampled(monkeypatch) -> None:
    rng = random.Random(71101)
    pids = rng.sample(range(10000, 90000), rng.randint(2, 20))
    monkeypatch.setattr(os, "getpid", lambda: pids[0])
    fuzz.test_process_iter_generated_pid_reuse_and_owner_filtering(monkeypatch)
