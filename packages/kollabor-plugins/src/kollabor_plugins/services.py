"""Background plugin process supervision primitives."""


def restart_decision(
    policy: str, exit_code: int, crashes: list[float], now: float
) -> tuple[int | None, bool]:
    """Return the retry delay and whether the five-crash limit was reached.

    Crash timestamps and ``now`` share a monotonic time base. The caller records
    the current failure before calling this function.
    """
    if policy not in {"never", "on-failure", "always"}:
        raise ValueError("Invalid service restart policy")
    recent = [stamp for stamp in crashes if 0 <= now - stamp <= 600]
    if len(recent) >= 5:
        return None, True
    if policy == "never" or (policy == "on-failure" and exit_code == 0):
        return None, False
    return min(300, 2 ** max(1, len(recent))), False
