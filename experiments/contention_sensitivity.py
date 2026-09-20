#!/usr/bin/env python3
"""How much would contention have to cost before the envelope moves?

The loaded pilot arm is seven trials with no raw samples, so it cannot support a
measured contention result. It can support a SENSITIVITY analysis: shift the
measured unloaded distribution by a multiplicative factor and report where the
envelope breaks. That is a modelled arm, labelled as one, and it answers the
question the missing experiment would have answered without pretending to have
run it.
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from envelope import load_ecdf, evaluate_point, budget_ms, SPEEDS_KMH, DISTANCES_M, PHASES

HERE = os.path.dirname(os.path.abspath(__file__))
lat = load_ecdf(os.path.join(HERE, "results", "ecdf_ete_unloaded.dat"))
INTERVAL, THRESH = 2.0, 0.95

# the pilot's own loaded/unloaded mean ratio, for reference only (n=7)
PILOT_RATIO = 538.74 / 422.83

print("=" * 74)
print("CONTENTION SENSITIVITY  (modelled: measured unloaded distribution scaled)")
print(f"pilot loaded/unloaded mean ratio = {PILOT_RATIO:.3f}  (n=7, reference only)")
print("Attribution: a point is age-alone if it stays infeasible with latency")
print("set to zero, and jointly attributable otherwise.")
print("=" * 74)
print(f"\n{'factor':>7}{'feasible':>11}{'age alone':>11}{'jointly':>9}")

for k in (1.0, PILOT_RATIO, 1.5, 2.0, 3.0, 5.0):
    scaled = [x * k for x in lat]
    feas = age_alone = joint = 0
    for sp in SPEEDS_KMH:
        for d in DISTANCES_M:
            f_max = budget_ms(d, sp)
            for ph in PHASES:
                age0 = ph * INTERVAL * 1000
                r = evaluate_point(scaled, d, sp, INTERVAL, f_max, ph)
                if r["p"] >= THRESH:
                    feas += 1
                elif age0 > f_max:       # infeasible even with zero latency
                    age_alone += 1
                else:
                    joint += 1
    print(f"{k:7.2f}{feas:>8}/60{age_alone:>11}{joint:>9}")

print("\nThe age-alone count is constant by construction: it does not depend on")
print("the latency scaling. What grows with contention is the jointly")
print("attributable set, from five points to twenty-four.")
