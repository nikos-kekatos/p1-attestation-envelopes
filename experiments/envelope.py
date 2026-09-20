#!/usr/bin/env python3
"""Assurance envelopes: where a security verdict is still actionable.

Rig-free. Takes an empirical latency distribution (the pilot ECDF, or any rerun
that replaces it) and a physical operating space, and reports for each operating
point the probability that the contract is satisfied.

Two envelopes are produced and compared, which is the simulation-reality gap
stated as a result:

  E_ideal : assurance is instantaneous and evidence is always fresh, which is
            what a simulation that omits the security mechanism assumes
  E_HIL   : assurance latency is drawn from the measured distribution and
            evidence carries the age the attestation interval implies
"""
import argparse, json, math, os, statistics as st

HERE = os.path.dirname(os.path.abspath(__file__))

SPEEDS_KMH = (50, 80, 110)
DISTANCES_M = (20, 50, 100, 150)
PHASES = (0.0, 0.25, 0.5, 0.75, 1.0)


def wilson(k, n, z=1.96):
    """Wilson score interval. Correct at the 0 and 1 ends, where the normal
    approximation is not, and those ends are exactly where the envelope sits."""
    if n == 0:
        return (0.0, 1.0)
    p = k / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return ((c - h) / d, (c + h) / d)


def load_ecdf(path):
    xs = []
    for line in open(path):
        if line.startswith("#"):
            continue
        xs.append(float(line.split()[0]))
    return sorted(xs)


def budget_ms(distance_m, speed_kmh):
    return distance_m / (speed_kmh / 3.6) * 1000.0


def evaluate_point(latencies_ms, distance_m, speed_kmh, interval_s, f_max_ms,
                   phase, ideal=False):
    """Fraction of samples that satisfy the contract at one operating point."""
    d = budget_ms(distance_m, speed_kmh)
    age_at_request = phase * interval_s * 1000.0
    ok = 0
    n = len(latencies_ms)
    for L in latencies_ms:
        lat = 0.0 if ideal else L
        age_at_decision = 0.0 if ideal else age_at_request + lat
        if lat <= d and age_at_decision <= f_max_ms:
            ok += 1
    lo, hi = wilson(ok, n)
    return {"n": n, "k": ok, "p": ok / n, "ci95": [lo, hi], "budget_ms": d,
            "evidence_age_at_request_ms": age_at_request}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ecdf", default=os.path.join(HERE, "results", "ecdf_ete_unloaded.dat"))
    ap.add_argument("--interval", type=float, default=2.0,
                    help="evidence age bound, s. The verifier config in the "
                         "thesis Appendix B reports attestation_period 2s and "
                         "maximum_attestation_interval 10s, so 2.0 is the "
                         "nominal case and 10.0 the tolerated worst case.")
    ap.add_argument("--fmax", type=float, default=None,
                    help="freshness bound, ms; default is the budget itself, "
                         "which is the smallest value that leaves DENY-LATE observable")
    ap.add_argument("--out", default=os.path.join(HERE, "results"))
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)

    lat = load_ecdf(a.ecdf)
    print("=" * 74)
    print(f"ASSURANCE ENVELOPE   latency n={len(lat)}  interval={a.interval}s")
    print("PROVISIONAL: the latency distribution is the unloaded pilot set.")
    print("Rerun under graded contention before quoting any of this.")
    print("=" * 74)

    rows, grid = [], {}
    for sp in SPEEDS_KMH:
        for d in DISTANCES_M:
            f_max = a.fmax if a.fmax is not None else budget_ms(d, sp)
            for ph in PHASES:
                hil = evaluate_point(lat, d, sp, a.interval, f_max, ph)
                idl = evaluate_point(lat, d, sp, a.interval, f_max, ph, ideal=True)
                rows.append({"speed_kmh": sp, "distance_m": d, "phase": ph,
                             "f_max_ms": f_max, "hil": hil, "ideal": idl})
                grid[(sp, d, ph)] = (hil["p"], idl["p"])

    # feasible = the contract holds on at least 95% of samples at that point
    THRESH = 0.95
    feas_h = sum(1 for r in rows if r["hil"]["p"] >= THRESH)
    feas_i = sum(1 for r in rows if r["ideal"]["p"] >= THRESH)
    print(f"\nOperating points: {len(rows)}")
    print(f"  feasible under an idealised simulation : {feas_i:4d}  ({100*feas_i/len(rows):.1f}%)")
    print(f"  feasible under measured behaviour      : {feas_h:4d}  ({100*feas_h/len(rows):.1f}%)")
    print(f"  points the simulation admits and the measured system does not: "
          f"{feas_i-feas_h} ({100*(feas_i-feas_h)/len(rows):.1f}% of the space)")

    print(f"\nContract-satisfaction probability, phase by geometry "
          f"(F_max = budget, {a.interval}s interval)")
    for sp in SPEEDS_KMH:
        print(f"\n  {sp} km/h")
        print("    dist |" + "".join(f"  phi={p:<5.2f}" for p in PHASES))
        for d in DISTANCES_M:
            cells = "".join(f"  {grid[(sp,d,p)][0]:9.2f}" for p in PHASES)
            print(f"    {d:4d} |{cells}")

    with open(os.path.join(a.out, "envelope.json"), "w") as f:
        json.dump({"provisional": True, "interval_s": a.interval,
                   "latency_n": len(lat), "threshold": THRESH,
                   "feasible_ideal": feas_i, "feasible_hil": feas_h,
                   "rows": rows}, f, indent=2)

    # MODELLED grid, from the pilot ECDF. analyse.py writes the MEASURED one
    # to envelope_grid_measured.dat. The two carried the same filename until
    # now and different schemas, so whichever ran second silently destroyed the
    # other's output and any plot built from it was of whichever had run last.
    p = os.path.join(a.out, "envelope_grid_model.dat")
    with open(p, "w") as f:
        f.write("# MODELLED grid from the pilot ECDF; the measured grid is "
                "envelope_grid_measured.dat (analyse.py)\n")
        f.write("# speed_kmh distance_m phase p_hil p_ideal\n")
        for r in rows:
            f.write(f"{r['speed_kmh']} {r['distance_m']} {r['phase']} "
                    f"{r['hil']['p']:.4f} {r['ideal']['p']:.4f}\n")
    print(f"\n  wrote {os.path.relpath(p, HERE)} and envelope.json")


if __name__ == "__main__":
    main()
