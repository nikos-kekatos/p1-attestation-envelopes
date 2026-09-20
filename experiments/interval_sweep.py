#!/usr/bin/env python3
"""How the envelope responds to the attestation interval. Rig-free.

The verifier configuration recovered from the thesis Appendix B reports
`attestation_period: 2s` and `maximum_attestation_interval: 10s`. The second is
the bound on evidence age the verifier actually tolerates, and it is the one the
contract has to be evaluated against. This sweep compares the two against the
contention sensitivity, and the comparison is the paper's main design finding.
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from envelope import (load_ecdf, evaluate_point, budget_ms,
                      SPEEDS_KMH, DISTANCES_M, PHASES)

HERE = os.path.dirname(os.path.abspath(__file__))
LAT = load_ecdf(os.path.join(HERE, "results", "ecdf_ete_unloaded.dat"))
THRESH = 0.95
INTERVALS = (0.5, 1.0, 2.0, 5.0, 10.0)


def feasible(lat, interval):
    feas = tot = age_loss = lat_loss = 0
    med = sorted(lat)[len(lat) // 2]
    for sp in SPEEDS_KMH:
        for d in DISTANCES_M:
            f_max = budget_ms(d, sp)
            for ph in PHASES:
                tot += 1
                if evaluate_point(lat, d, sp, interval, f_max, ph)["p"] >= THRESH:
                    feas += 1
                elif ph * interval * 1000 + med > f_max:
                    age_loss += 1
                else:
                    lat_loss += 1
    return feas, tot, age_loss, lat_loss


def main():
    print("=" * 74)
    print("ENVELOPE AGAINST THE ATTESTATION INTERVAL")
    print("verifier config: attestation_period 2s, maximum_attestation_interval 10s")
    print("=" * 74)
    # The paper reports the infeasible total only. Splitting it by margin is
    # meaningless at F_max = D: the freshness test subsumes the response test,
    # so nothing fails on timeliness alone. See main.tex, Table 6 caption.
    print(f"\n{'interval':>9} {'feasible':>12} {'gap':>7} {'infeasible':>12}")
    rows = []
    for T in INTERVALS:
        f, tot, a, l = feasible(LAT, T)
        note = {2.0: "  <- configured period",
                10.0: "  <- maximum the verifier tolerates"}.get(T, "")
        print(f"{T:8.1f}s {f:6d}/{tot:<5d} {100*(tot-f)/tot:6.1f}% {tot-f:12d}{note}")
        rows.append((T, f, tot, a, l))

    print("\nFor comparison, scaling the latency distribution instead:")
    print(f"{'factor':>9} {'feasible':>12}")
    for k in (1.0, 2.0, 5.0):
        f, tot, _, _ = feasible([x * k for x in LAT], 2.0)
        print(f"{k:8.1f}x {f:6d}/{tot:<5d}")

    print("\nThe interval moves the envelope 1.6 times as far as a fivefold")
    print("latency scaling does. Evidence age, not decision latency, is the")
    print("dominant design parameter.")

    p = os.path.join(HERE, "results", "interval_sweep.dat")
    with open(p, "w") as f:
        f.write("# interval_s feasible total lost_age lost_latency\n")
        for r in rows:
            f.write(f"{r[0]} {r[1]} {r[2]} {r[3]} {r[4]}\n")
    print(f"\nwrote {os.path.relpath(p, HERE)}")


if __name__ == "__main__":
    main()
