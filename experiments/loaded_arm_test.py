#!/usr/bin/env python3
"""Does the loaded pilot arm support a contention effect? Rig-free.

The loaded arm survives only as summary statistics: mean, min, max over seven
trials, with no raw samples. That rules out an ECDF or an interval, but not a
test. Under exchangeability of the two arms, the probability that all n loaded
observations rank above the k smallest of the m unloaded ones is

    C(m + n - k, n) / C(m + n, n)

which needs nothing from the loaded arm except its minimum and its count. This
is exact. An earlier version used (1 - Fhat(min))^n, which treats the empirical
CDF of fifty samples as the true one and is anticonservative by about a factor
of two here.
"""
import math, os, re, statistics as st

HERE = os.path.dirname(os.path.abspath(__file__))
PILOT = os.path.join(HERE, "..", "Thesis",
                     "0_overhead-delay_stats_rest_api_without_channel_commotion",
                     "50_rest_api_green_light_trials.txt")

# Reported for the loaded arm. n = 7 is stated in both comparison-chart legends.
LOADED = {"end-to-end":            {"n": 7, "mean": 538.74, "min": 438.67, "max": 656.15},
          "attestation component": {"n": 7, "mean": 26.24,  "min": 14.40,  "max": 59.20}}


def unloaded():
    ete, att = [], []
    for line in open(PILOT):
        if re.match(r"^\d+\t", line):
            _, e, a = line.rstrip("\n").split("\t")
            ete.append(float(e.replace("ms", "")))
            att.append(float(a.replace("s", "")) * 1000.0)
    return {"end-to-end": ete, "attestation component": att}


def main():
    print("=" * 74)
    print("LOADED ARM: exact distribution-free test on the reported minimum")
    print("H0: the loaded samples are drawn from the unloaded distribution")
    print("=" * 74)
    for label, xs in unloaded().items():
        L = LOADED[label]
        m, n = L["min"], L["n"]
        # STRICT inequality: the hypothesis is that the loaded observations
        # exceed the unloaded ones, and a tie is not an exceedance. There is one
        # tie at the attestation minimum (14.40 ms); counting it would inflate
        # the apparent effect and give p = 0.77 instead of 0.88.
        k = sum(1 for x in xs if x < m)       # unloaded observations it exceeds
        mm = len(xs)
        # exact rank test under exchangeability: the probability that all n
        # loaded observations rank above the k smallest of the m unloaded ones
        p = math.comb(mm + n - k, n) / math.comb(mm + n, n)
        print(f"\n{label}")
        print(f"  unloaded n={len(xs)}  mean {st.mean(xs):7.2f}  median {st.median(xs):7.2f}")
        print(f"  loaded   n={n}   mean {L['mean']:7.2f}  min {m:7.2f}")
        print(f"  loaded min exceeds {k}/{mm} unloaded observations")
        print(f"  P(all {n} exceed it | H0) = {p:.2e}"
              f"   -> {'REJECT: a contention effect is supported' if p < 0.05 else 'no evidence of an effect'}")
    print("\nThe magnitude does not follow from this. A seven-sample mean is a")
    print("fragile point estimate and the 27.7% figure should not be quoted.")


if __name__ == "__main__":
    main()
