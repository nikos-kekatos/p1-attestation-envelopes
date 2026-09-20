#!/usr/bin/env python3
"""The feasibility boundary in closed form. Rig-free.

With F_max = D(x_r) the contract reduces to a single test. Evidence age at the
decision is phi*T + L and decision latency is L, and both are compared against
D, so the freshness test binds and the contract holds for a sample iff

    phi*T + L <= D.

A point is feasible under the empirical criterion when at least a fraction q of
samples satisfy that, i.e. when D - phi*T >= Q(q), the q-quantile of the measured
latency. Substituting D = d/v gives a boundary that is a straight line through
the origin in the (speed, distance) plane:

    d_min(v) = v * (Q(q) + phi*T).

The slope has units of seconds and is the total time budget the geometry must
supply. This is exact for the measured distribution and replaces a grid
percentage, which depends on where the grid is sampled.
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from envelope import load_ecdf, budget_ms, SPEEDS_KMH, DISTANCES_M, PHASES

HERE = os.path.dirname(os.path.abspath(__file__))
LAT = sorted(load_ecdf(os.path.join(HERE, "results", "ecdf_ete_unloaded.dat")))
Q = 0.95
QLAT = LAT[min(len(LAT) - 1, int(Q * len(LAT)))]          # nearest-rank q-quantile

CONFIGS = [("phi=0.00, T=2 s",  0.00, 2.0),
           ("phi=0.25, T=2 s",  0.25, 2.0),
           ("phi=0.50, T=2 s",  0.50, 2.0),
           ("phi=1.00, T=2 s",  1.00, 2.0),
           ("phi=0.50, T=10 s", 0.50, 10.0),
           ("phi=1.00, T=10 s", 1.00, 10.0)]


def slope_s(phase, interval_s):
    """Total time the geometry must supply, in seconds."""
    return QLAT / 1000.0 + phase * interval_s


def d_min(v_kmh, phase, interval_s):
    return slope_s(phase, interval_s) * (v_kmh / 3.6)


def check_against_grid():
    """The boundary must agree with the point-by-point evaluation."""
    n = len(LAT)
    bad = 0
    for sp in SPEEDS_KMH:
        for d in DISTANCES_M:
            D = budget_ms(d, sp)
            for ph in PHASES:
                emp = sum(1 for L in LAT if ph * 2.0 * 1000 + L <= D) / n >= Q
                cls = d >= d_min(sp, ph, 2.0)
                if emp != cls:
                    bad += 1
                    print(f"    MISMATCH d={d} v={sp} phi={ph}: grid={emp} closed={cls}")
    return bad


def main():
    print("=" * 74)
    print(f"FEASIBILITY BOUNDARY   d_min(v) = v * (Q + phi*T),  Q(0.95) = {QLAT:.2f} ms")
    print("=" * 74)
    print(f"\n{'configuration':<20}{'slope (s)':>10}" + "".join(f"{v:>10} km/h" for v in SPEEDS_KMH))
    for lbl, ph, T in CONFIGS:
        print(f"{lbl:<20}{slope_s(ph,T):>10.3f}" +
              "".join(f"{d_min(v,ph,T):>12.1f} m" for v in SPEEDS_KMH))

    print(f"\nagreement with the 60-point grid evaluation at T=2 s: ", end="")
    bad = check_against_grid()
    print("exact, 60/60" if bad == 0 else f"{bad} mismatches")

    p = os.path.join(HERE, "results", "boundary.dat")
    with open(p, "w") as f:
        f.write("# v_kmh  " + "  ".join(l.replace(" ", "") for l, _, _ in CONFIGS) + "\n")
        for v in range(45, 116):
            f.write(f"{v} " + " ".join(f"{d_min(v,ph,T):.2f}" for _, ph, T in CONFIGS) + "\n")
    print(f"wrote {os.path.relpath(p, HERE)}")


if __name__ == "__main__":
    main()
