#!/usr/bin/env python3
"""Turn sweep logs into the paper's tables and figure data. Rig-free.

Reads results/*.jsonl from carla_host.py and rsu_engine.py and writes:
  envelope_grid_measured.dat   RQ3, MEASURED deadline-miss probability
  (envelope.py writes envelope_grid_model.dat, the MODELLED grid, from the
   pilot ECDF. The two used to share the filename envelope_grid.dat and
   whichever ran second destroyed the other's output, silently, because the
   schemas differ and nothing downstream checked which one it was reading.)

Two counting rules, both of which were wrong before:

  * The denominator is EVERY trial in the cell, including the ones that timed
    out. Dropping rows with no latency dropped them from the numerator AND the
    denominator, which reports the miss rate among the trials that did not
    miss. Trials aborted before the request left the host (no phase lock, or a
    failed publish) are a different thing and are excluded and counted
    separately: they are not members of the cell, because the factor they were
    supposed to sit at was never established.
  * The success test is the CONTRACT, not the response margin. met_budget in
    the sweep row only tests M_R >= 0. The contract is integrity AND M_R >= 0
    AND M_F >= 0, so the freshness term is joined in from the RSU row by rid.
    Both columns are written; p_resp is the old, over-optimistic quantity and
    is kept only so the difference between them can be shown.
"""
import argparse, glob, json, math, os, statistics as st

HERE = os.path.dirname(os.path.abspath(__file__))


def wilson(k, n, z=1.96):
    if n == 0:
        return (0.0, 1.0)
    p, d = k / n, 1 + z * z / n
    c = p + z * z / (2 * n)
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return ((c - h) / d, (c + h) / d)


def load(pattern):
    rows = []
    for p in glob.glob(pattern):
        with open(p) as fh:
            for line in fh:
                line = line.strip()
                if line:
                    rows.append(json.loads(line))
    return rows


def contract_ok(row, rsu_row):
    """Full contract on one trial: integrity, response margin, freshness margin.

    Returns (ok, evidence_seen). evidence_seen is False when no RSU row could be
    joined, in which case the freshness term is unknown and only the response
    margin is tested -- reported separately so the shortfall is visible rather
    than assumed away.
    """
    if row.get("latency_ms") is None:
        return False, rsu_row is not None          # timeout: a miss, and counted
    within = row["latency_ms"] / 1000.0 <= row["budget_s"]
    if rsu_row is None:
        return bool(within), False
    if rsu_row.get("outcome") != "PERMIT":
        return False, True
    age = rsu_row.get("evidence_age_at_decision")
    f_max = rsu_row.get("f_max")
    if age is None or f_max is None:
        # the engine could not evaluate freshness; do not pretend it passed
        return False, True
    return bool(within and age <= f_max), True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--results", default=os.path.join(HERE, "results"))
    a = ap.parse_args()

    sweep = load(os.path.join(a.results, "*_L*.jsonl"))
    rsu = load(os.path.join(a.results, "*_rsu.jsonl"))
    if not sweep:
        print("No sweep data yet. This is the critical path: run carla_host.py "
              "and rsu_engine.py on the rig, then re-run this.")
        return

    rsu_by_rid = {r["rid"]: r for r in rsu if r.get("rid")}

    by, aborted = {}, {}
    missing_keys = 0
    for r in sweep:
        try:
            k = (r["load_level"], r["speed_kmh"], r["distance_m"], r["phase"])
        except KeyError:
            # rows written by a build that attached load_level and policy after
            # serialisation; they cannot be placed in the design
            missing_keys += 1
            continue
        if r.get("aborted"):
            aborted[k] = aborted.get(k, 0) + 1
            continue
        by.setdefault(k, []).append(r)

    if missing_keys:
        print(f"WARNING: {missing_keys} rows carry no load_level/speed/distance/"
              f"phase and were skipped. They predate the carla_host.py fix.")

    p = os.path.join(a.results, "envelope_grid_measured.dat")
    n_no_ev = 0
    with open(p, "w") as f:
        f.write("# MEASURED grid from the rig. envelope.py writes the modelled "
                "one to envelope_grid_model.dat.\n")
        f.write("# load speed_kmh distance_m phase n k_resp p_resp lo_resp "
                "hi_resp k_contract p_contract lo_c hi_c n_aborted n_no_evidence\n")
        for k in sorted(by):
            rs = by[k]
            n = len(rs)
            met = sum(1 for r in rs if r.get("met_budget"))
            ok_c = ev_seen = 0
            for r in rs:
                o, seen = contract_ok(r, rsu_by_rid.get(r.get("rid")))
                ok_c += 1 if o else 0
                ev_seen += 1 if seen else 0
            no_ev = n - ev_seen
            n_no_ev += no_ev
            lo, hi = wilson(met, n)
            clo, chi = wilson(ok_c, n)
            f.write(f"{k[0]} {k[1]} {k[2]} {k[3]} {n} {met} {met/n:.4f} "
                    f"{lo:.4f} {hi:.4f} {ok_c} {ok_c/n:.4f} {clo:.4f} {chi:.4f} "
                    f"{aborted.get(k, 0)} {no_ev}\n")
    print(f"wrote {p}  ({len(by)} cells, {sum(len(v) for v in by.values())} trials, "
          f"{sum(aborted.values())} aborted and excluded)")
    if n_no_ev:
        print(f"WARNING: {n_no_ev} trials had no joinable RSU row, so their "
              f"freshness margin is unknown and only the response margin was "
              f"tested. p_contract is optimistic for those cells.")

    if rsu:
        terms = ["l_obu", "l_identity", "l_verifier", "l_refresh", "l_policy"]
        print("\nLatency decomposition (ms, median):")
        for t in terms:
            v = [r[t] * 1000 for r in rsu if r.get(t) is not None]
            if v:
                print(f"  {t:12s} n={len(v):5d}  median {st.median(v):7.2f}")
        outs = {}
        for r in rsu:
            outs[r["outcome"]] = outs.get(r["outcome"], 0) + 1
        print("\nOutcomes:", outs)
        ages = [r["evidence_age_at_decision"] for r in rsu
                if r.get("evidence_age_at_decision") is not None]
        if ages:
            print(f"Evidence age at decision (s): n={len(ages)} "
                  f"median {st.median(ages):.3f} max {max(ages):.3f}")
        else:
            print("Evidence age at decision: NOT RECORDED on any row. The "
                  "freshness result cannot be quoted from this run.")


if __name__ == "__main__":
    main()
