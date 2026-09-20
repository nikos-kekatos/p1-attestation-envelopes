# Mission-Aware Attestation Envelopes — reproduction repository

Code and data for:

> **Mission-Aware Attestation Envelopes for Time-Critical Autonomous Action:
> A Hardware-in-the-Loop V2I Study**
> Dimitrios Nikou, Nikolaos Kekatos, Sofia Petridou, Stylianos Basagiannis.

The paper formulates remote attestation as a runtime-assurance *contract* rather
than a binary gate: an authorisation holds only when integrity is valid, the
evidence is fresh enough, and the decision completes inside a budget derived from
the current physical state. The contract yields four outcomes — `PERMIT`,
`DENY-INTEGRITY`, `DENY-STALE`, `DENY-LATE` — and the *attestation envelope* is
the region of mission states in which a security verdict is still actionable.

---

## Read this first: what actually produced the paper's numbers

**The hardware-in-the-loop rig scripts have never been executed end to end.**
`experiments/carla_host.py`, `experiments/rsu_engine.py` and
`experiments/load_gen.py` have been written, reviewed and corrected, and they
parse, but no line of any of them has been observed running against CARLA, the
physical ESP32 on-board unit, the Keylime verifier or the Mosquitto broker.
Nothing in `experiments/results/` came out of them.

Every number in the paper comes from the **rig-free analysis scripts** operating
on the **thesis pilot latency data** shipped in `Thesis/`. That data is a
50-trial unloaded arm and a 7-trial 100%-network-load arm, measured on the
thesis rig by the first author. The paper labels these results provisional
wherever they appear, and this repository does not claim otherwise.

`experiments/HARNESS_NOTES.md` is the harness author's own status note, kept
verbatim. It records the topology restoration and the specific defects fixed in
the rig scripts, none of it verified by execution. Read it before any rig run.

---

## Requirements

- **Analysis (everything the paper reports): Python 3.10+, standard library only.**
  Nothing to install. Verified on Python 3.14.
- **Rig only:** `experiments/requirements.txt` (`paho-mqtt==1.6.1`,
  `requests==2.32.3`). Do not import these from the analysis scripts; they are
  required to stay standard-library-only.

## Reproduce

```sh
./run_all.sh
```

Writes one file per experiment to `out/`. **Expected runtime: under 10 seconds
in total.** Exits non-zero if any step fails.

Or run any step directly:

```sh
cd experiments
python3 contract.py               # the four contract outcomes + adaptive refresh
python3 reanalyse_pilot.py        # ECDF + bootstrap intervals over the pilot data
python3 loaded_arm_test.py        # exact rank test on the 7-trial loaded arm
python3 envelope.py               # assurance envelope, idealised vs measured
python3 interval_sweep.py         # envelope against the attestation interval
python3 contention_sensitivity.py # modelled contention, scaled distribution
python3 boundary.py               # feasibility boundary d_min(v)
python3 analyse.py                # sweep logs -> paper tables (needs rig data)
```

### Expected outputs

`experiments/results/` already contains a committed copy of every derived
artefact, and **regeneration is byte-identical** (verified: re-running
`reanalyse_pilot.py`, `envelope.py`, `interval_sweep.py` and `boundary.py`
reproduces all nine files exactly).

| File | Produced by | Feeds |
|---|---|---|
| `ecdf_ete_unloaded.dat`, `ecdf_attest_unloaded.dat` | `reanalyse_pilot.py` | latency ECDFs |
| `pilot_reanalysis.json` | `reanalyse_pilot.py` | quantiles, bootstrap CIs |
| `envelope.json`, `envelope_grid_model.dat`, `envelope_contour.dat`, `envelope_boundary.dat` | `envelope.py` | the envelope figures and the grid classification |
| `interval_sweep.dat` | `interval_sweep.py` | attestation-interval sensitivity |
| `boundary.dat` | `boundary.py` | closed-form feasibility boundary |

Representative console output, to check a run went right:

```
contract.py    budget = 654.5 ms; DENY-LATE observable only for F_max > 654.5 ms
boundary.py    Q(0.95) = 559.21 ms; phi=0.00,T=2s -> 7.8 m at 50 km/h
loaded_arm_test.py  unloaded n=50 mean 422.83 ms; loaded n=7 mean 538.74 ms
```

---

## What is reproducible here

| Paper claim | Reproducible? | From what |
|---|---|---|
| Four contract outcomes; `DENY-LATE` unobservable unless `F_max` exceeds the budget | **Yes**, fully | `contract.py`, closed form, no data needed |
| Assurance-latency distribution and the contention effect | **Yes**, from the shipped pilot data | `reanalyse_pilot.py`, `loaded_arm_test.py` |
| Attestation envelope; security-blind model admits the whole space, hardware-informed model admits ~3/4; every refused point fails the freshness margin | **Yes** | `envelope.py` over the pilot ECDF |
| Attestation-interval sensitivity ≈ a fivefold scaling of the latency distribution | **Yes** | `interval_sweep.py`, `contention_sensitivity.py` |
| Closed-form feasibility boundary `d_min(v)` | **Yes** | `boundary.py` |
| Failure-mode discrimination | **Yes**, as a model result | `contract.py` + `envelope.py` |

## What is NOT reproducible here, and why

Stated plainly so nobody wastes time on it.

1. **Anything requiring the HIL rig.** `carla_host.py` (Windows host, CARLA,
   paho-mqtt), `rsu_engine.py` (Fedora RSU with TPM 2.0, Linux IMA, Keylime
   verifier over REST with mTLS) and `load_gen.py` cannot run on a development
   machine. They are shipped as reviewed-but-unexecuted source. `analyse.py`
   prints `No sweep data yet` until a rig sweep exists, which is correct
   behaviour, not a failure.
2. **Fresh latency measurements.** The pilot data is a historical measurement of
   a specific rig. It cannot be regenerated without that hardware: a physical
   ESP32 on-board unit, a TPM-2.0 Fedora roadside unit, and CARLA on a Windows
   host. A rerun on the restored topology measures a *longer* path than an
   intermediate harness version did and would not be numerically comparable.
3. **`rig_reference/esp32_v2x_obu.c` is deliberately absent.** The thesis copy
   of that firmware contains a hardcoded Wi-Fi SSID and password
   (`Thesis/esp32_v2x_obu.c`, lines 7–8 in the source tree). It is excluded
   rather than redacted. An author who wants it in the artefact must strip the
   credentials first. `esp32_attestation_obu.c` is included and is clean.

**No API keys, model providers or paid services are involved anywhere in this
repository.** Nothing here calls a network service.

---

## Layout and provenance

```
experiments/            analysis + rig drivers   <- paper1_dtloop/experiments/
experiments/results/    committed derived data   <- paper1_dtloop/experiments/results/
experiments/HARNESS_NOTES.md                     <- paper1_dtloop/experiments/README.md
Thesis/                 pilot latency data       <- paper1_dtloop/Thesis/
rig_reference/          thesis rig sources       <- paper1_dtloop/Thesis/*.py, *.c
run_all.sh              top-level runner         (new, written for this repo)
```

`Thesis/` keeps the two pilot directories at the exact relative path the scripts
expect (`experiments/../Thesis/...`), so no script was modified in the move.
See `PROVENANCE.md` for the full original-path mapping.

## Related repositories

This repository covers **one** of three MESAS papers. It shares **no code** with
the other two — verified by content hash. See `../README.md`.

## Licence

See `LICENSE`. It is a placeholder: the authors must choose the licence before
release, and must confirm the licence and consent under which the thesis pilot
data in `Thesis/` and the rig sources in `rig_reference/` may be redistributed.
