#!/usr/bin/env python3
"""Re-analysis of the pilot latency data. Runs anywhere; needs no rig.

Three jobs:
  1. Recompute from the raw rows and check the file's own summary line.
  2. Report the distribution properly: median, IQR, p90, p95, maximum observed,
     bootstrap intervals. Never a mean alone, never "worst case".
  3. Emit the ECDF that replaces the mean/min/max bar charts.
"""
import argparse, json, math, os, random, re, statistics as st

HERE = os.path.dirname(os.path.abspath(__file__))
THESIS = os.path.join(HERE, "..", "Thesis")
PILOT = os.path.join(THESIS, "0_overhead-delay_stats_rest_api_without_channel_commotion",
                     "50_rest_api_green_light_trials.txt")
LOADED = os.path.join(THESIS, "100percent_overhead-delay_stats_rest_api",
                      "delay_stats_100_network_load.txt")


def read_pilot(path):
    ete, att = [], []
    for line in open(path):
        if not re.match(r"^\d+\t", line):
            continue
        _, e, a = line.rstrip("\n").split("\t")
        ete.append(float(e.replace("ms", "")))
        att.append(float(a.replace("s", "")) * 1000.0)
    return ete, att


def loaded_summary(path):
    """The loaded arm survives only as this summary block. Parsed rather than
    retyped, and the file handle is closed: the previous version opened it,
    bound the whole text to a name it never read, and left it to the garbage
    collector."""
    out, key, stat = {}, None, None
    with open(path) as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            m = re.match(r"(EtE|Attestation) delay", line, re.I)
            if m:
                key = {"ete": "end-to-end",
                       "attestation": "attestation component"}[m.group(1).lower()]
                continue
            if line.lower() in ("mean", "min", "max"):
                stat = line.lower()
                continue
            m = re.match(r"([\d.]+)\s*ms$", line)
            if m and key and stat:
                out.setdefault(key, {})[stat] = float(m.group(1))
                stat = None
    return out


def claimed(path):
    """The summary line the file carries, so we can check it."""
    out = {}
    for line in open(path):
        m = re.match(r"(EtE|Attestation) Delay.*Mean:\s*([\d.]+) ms", line)
        if m:
            out[m.group(1)] = float(m.group(2))
    return out


def boot_ci(xs, stat=st.median, reps=10000, seed=1234, alpha=0.05):
    rng = random.Random(seed)
    n = len(xs)
    vals = sorted(stat([xs[rng.randrange(n)] for _ in range(n)]) for _ in range(reps))
    return vals[int(alpha / 2 * reps)], vals[int((1 - alpha / 2) * reps)]


def describe(name, xs):
    v = sorted(xs)

    def q(p):
        """Nearest-rank quantile: the smallest observation at or above the
        p-th fraction, index ceil(p*n) - 1. The previous form, int(p*n), is
        the same thing only when p*n is NOT an integer; when it is -- which at
        n = 50 is every quartile and every decile -- it returns the next
        observation up, so p25, p75, p90 and p95 were all one rank high and the
        IQR was wrong at both ends."""
        return v[min(len(v) - 1, max(0, math.ceil(p * len(v)) - 1))]
    lo_m, hi_m = boot_ci(xs, st.median)
    lo_u, hi_u = boot_ci(xs, st.mean)
    return {
        "name": name, "n": len(v),
        "min": v[0], "p25": q(.25), "median": st.median(v), "p75": q(.75),
        "p90": q(.90), "p95": q(.95), "max_observed": v[-1],
        "mean": st.mean(v), "sd": st.pstdev(v),
        "median_ci95": [lo_m, hi_m], "mean_ci95": [lo_u, hi_u],
        "iqr": q(.75) - q(.25),
        "skew_ratio": st.mean(v) / st.median(v),
    }


def ecdf(xs):
    v = sorted(xs)
    return [(x, (i + 1) / len(v)) for i, x in enumerate(v)]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join(HERE, "results"))
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)

    ete, att = read_pilot(PILOT)
    cl = claimed(PILOT)

    print("=" * 74)
    print("PILOT RE-ANALYSIS  (unloaded, REST verifier interface)")
    print("=" * 74)

    report = {}
    for label, xs, key in [("end-to-end", ete, "EtE"), ("attestation component", att, "Attestation")]:
        d = describe(label, xs)
        report[label] = d
        print(f"\n{label}  (n = {d['n']})")
        print(f"  median {d['median']:7.2f} ms   95% CI [{d['median_ci95'][0]:.2f}, {d['median_ci95'][1]:.2f}]")
        print(f"  IQR    {d['iqr']:7.2f} ms   p90 {d['p90']:.2f}   p95 {d['p95']:.2f}")
        print(f"  mean   {d['mean']:7.2f} ms   95% CI [{d['mean_ci95'][0]:.2f}, {d['mean_ci95'][1]:.2f}]")
        print(f"  range  {d['min']:.2f} to {d['max_observed']:.2f} ms (maximum observed, not a bound)")
        print(f"  mean/median = {d['skew_ratio']:.2f}")
        c = cl.get(key)
        if c is not None:
            delta = d["mean"] - c
            flag = "MISMATCH" if abs(delta) > 0.05 else "ok"
            print(f"  file's own summary says mean {c:.2f} ms -> recomputed {d['mean']:.2f} "
                  f"({delta:+.2f}, {100*delta/c:+.1f}%)  [{flag}]")
            report[label]["file_summary_mean"] = c
            report[label]["file_summary_delta"] = delta

    print("\n" + "-" * 74)
    print("INTEGRITY OF THE LOADED CONDITION")
    print("-" * 74)
    ld = loaded_summary(LOADED)
    for label, d in ld.items():
        print(f"  {label}: mean {d.get('mean')} ms, min {d.get('min')} ms, "
              f"max {d.get('max')} ms   (n = 7, no raw samples)")
    print("  The loaded condition is reported as summary statistics only; the")
    print("  comparison charts state it was collected over SEVEN trials against")
    print("  fifty unloaded. It is not comparable and must be rerun at the same")
    print("  scale before any of it is quoted.")
    report["loaded_condition"] = {"n_trials_stated_in_chart_legend": 7,
                                  "raw_samples_available": False,
                                  "reported_summary": ld,
                                  "status": "RERUN REQUIRED"}

    for label, xs in [("ete", ete), ("attest", att)]:
        p = os.path.join(a.out, f"ecdf_{label}_unloaded.dat")
        with open(p, "w") as f:
            f.write("# latency_ms  F\n")
            for x, F in ecdf(xs):
                f.write(f"{x:.3f} {F:.4f}\n")
        print(f"\n  wrote {os.path.relpath(p, HERE)}")

    with open(os.path.join(a.out, "pilot_reanalysis.json"), "w") as f:
        json.dump(report, f, indent=2)
    print(f"  wrote {os.path.relpath(os.path.join(a.out,'pilot_reanalysis.json'), HERE)}")


if __name__ == "__main__":
    main()
