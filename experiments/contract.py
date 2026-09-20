#!/usr/bin/env python3
"""The assurance-validity contract and its four outcomes.

An authorisation is assurance-valid only when integrity passes, the decision
lands inside the budget the physical state allows, and the evidence it rests on
is fresh enough:

    integrity(t_a) = PASS  and  M_R >= 0  and  M_F >= 0

    M_R = D(x_r) - (t_d - t_r)        response margin
    M_F = F_max  - (t_d - t_a)        freshness margin

Pure and rig-free: it takes timestamps and returns a verdict, so it is the same
object whether it runs live on the RSU or offline over a recorded trace.
"""
from dataclasses import dataclass
from enum import Enum


class Outcome(str, Enum):
    PERMIT = "PERMIT"
    DENY_INTEGRITY = "DENY-INTEGRITY"
    DENY_STALE = "DENY-STALE"
    DENY_LATE = "DENY-LATE"


@dataclass(frozen=True)
class Request:
    rid: str
    t_r: float          # request time, s, monotonic at the observation point
    distance_m: float   # to the actuation point
    speed_mps: float


@dataclass(frozen=True)
class Evidence:
    integrity_pass: bool
    t_a: float          # when the evidence was generated
    source: str = "periodic"   # periodic | refreshed


@dataclass(frozen=True)
class Verdict:
    outcome: Outcome
    m_r: float
    m_f: float
    budget: float
    evidence_age: float
    reason: str


def budget(req, model="kinematic"):
    """Authorisation budget from physical state.

    UPPER BOUND, not the preemption budget: a fielded controller must also
    transmit the command, act on it and run clearance intervals. Kept as a
    separate function so a richer model can be substituted without touching
    the contract.
    """
    if model == "kinematic":
        return req.distance_m / req.speed_mps
    raise ValueError(model)


def evaluate(req, ev, t_d, f_max, model="kinematic"):
    """The contract. Integrity dominates, then freshness, then timeliness:
    a tampered platform is reported as tampered even if the answer was slow.

    Note the two margins are not independent WHEN THE EVIDENCE IS HELD, that
    is when it was generated before the request. Evidence age at the decision
    is t_d - t_a and, for held evidence, t_a <= t_r, so age >= t_d - t_r. If
    the decision is late,
    t_d - t_r > D, and F_max <= D, then age > F_max as well: every late
    decision is also stale and DENY-LATE can never be observed. DENY-LATE is
    therefore only reachable when F_max > D(x_r), which makes the freshness
    bound a design parameter that has to be chosen against the budget rather
    than picked in isolation. `masking_threshold` below reports the crossover.

    The premise fails under on-demand refresh: evidence obtained in response to
    the request satisfies t_r < t_a <= t_d, so its age can be small while the
    decision is late, and DENY-LATE is then observable at any f_max. Masking is
    a property of periodic held evidence, not of the contract.
    """
    d = budget(req, model)
    m_r = d - (t_d - req.t_r)
    m_f = f_max - (t_d - ev.t_a)
    age = t_d - ev.t_a

    if not ev.integrity_pass:
        return Verdict(Outcome.DENY_INTEGRITY, m_r, m_f, d, age,
                       "integrity measurement reported a violation")
    if m_f < 0:
        return Verdict(Outcome.DENY_STALE, m_r, m_f, d, age,
                       f"evidence age {age*1000:.1f} ms exceeds F_max {f_max*1000:.1f} ms")
    if m_r < 0:
        return Verdict(Outcome.DENY_LATE, m_r, m_f, d, age,
                       f"decision took {(t_d-req.t_r)*1000:.1f} ms of a {d*1000:.1f} ms budget")
    return Verdict(Outcome.PERMIT, m_r, m_f, d, age, "assurance-valid")


# ---------------------------------------------------------------------------
# Mission-adaptive refresh
# ---------------------------------------------------------------------------

REUSE, REFRESH, FAIL_CLOSED = "reuse", "refresh", "fail-closed"


def refresh_decision(req, ev, f_max, l_refresh, l_decide, model="kinematic"):
    """Choose at request time whether to reuse held evidence or obtain new
    evidence, using the budget that remains.

    The reuse test must project the age forward to the DECISION, not evaluate
    it at the request. The contract tests t_d - t_a; testing t_r - t_a instead
    reuses evidence whenever f_max - (t_r - t_a) is smaller than the decision
    latency, and the contract then rejects it as stale. `l_decide` is the
    expected cost of reaching a decision on held evidence, and `l_refresh` the
    expected cost of obtaining new evidence; both are estimated by the caller
    from the running distribution rather than assumed. Comparisons are
    non-strict, matching the contract, which admits a zero margin."""
    age_at_decision = (req.t_r - ev.t_a) + l_decide
    if age_at_decision <= f_max:
        return REUSE
    if l_refresh <= budget(req, model):
        return REFRESH
    return FAIL_CLOSED


def masking_threshold(req, model="kinematic"):
    """Infimum of the F_max at which DENY-LATE is observable for held evidence.

    Not attained: observability needs F_max > D strictly. Under on-demand
    refresh the threshold does not apply at all, see evaluate().
    """
    return budget(req, model)


POLICIES = ("periodic", "always", "adaptive")


def policy_action(policy, req, ev, f_max, l_refresh, l_decide, model="kinematic"):
    if policy == "periodic":
        return REUSE
    if policy == "always":
        return REFRESH if l_refresh <= budget(req, model) else FAIL_CLOSED
    if policy == "adaptive":
        return refresh_decision(req, ev, f_max, l_refresh, l_decide, model)
    raise ValueError(policy)


if __name__ == "__main__":
    # Worked example at the geometry the platform uses: trigger 20 m, 110 km/h.
    r = Request("demo", t_r=0.0, distance_m=20.0, speed_mps=110 / 3.6)
    print(f"budget = {budget(r)*1000:.1f} ms")
    print(f"DENY-LATE observable only for F_max > {masking_threshold(r)*1000:.1f} ms")
    f_max = 1.0
    print(f"F_max = {f_max*1000:.0f} ms")
    for name, ev, t_d in [
        ("fresh, fast",   Evidence(True,  -0.05), 0.30),
        ("fresh, late",   Evidence(True,  -0.05), 0.90),
        ("aged, fast",    Evidence(True,  -1.90), 0.30),
        ("tampered",      Evidence(False, -0.05), 0.30),
    ]:
        v = evaluate(r, ev, t_d, f_max=f_max)
        print(f"  {name:12s} -> {v.outcome.value:15s} {v.reason}")
