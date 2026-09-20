# MESAS paper 1 experiments

Harness for *Simulation-Derived Attestation Envelopes for Time-Critical
Autonomous Access*. Plan of record is `../PLAN_v2.md`.

## Status: the rig scripts have never been run end to end

**`carla_host.py`, `rsu_engine.py` and `load_gen.py` have not been executed
against the rig, in whole or in part.** They have been read, reviewed and
corrected, and they parse, but no line of any of them has been observed running
against CARLA, the physical ESP32, the Keylime verifier or the Mosquitto broker.
Nothing in `results/` came out of them. Every number this harness has produced so
far comes from the rig-free scripts operating on the *thesis pilot data*, and is
labelled provisional wherever it appears.

Treat the corrections below as reviewed-but-unexecuted code. The first run on the
rig is a bring-up, not a data collection.

## What runs where

**Rig-free, runs anywhere, standard library only.**

| script | what it does |
|---|---|
| `contract.py` | the assurance contract, four outcomes, adaptive refresh policy |
| `reanalyse_pilot.py` | re-analysis of the thesis latency data, ECDF and bootstrap intervals |
| `envelope.py` | assurance envelopes and the idealised-against-measured comparison |
| `interval_sweep.py` | envelope against the attestation interval |
| `contention_sensitivity.py` | modelled contention: scale the measured distribution |
| `loaded_arm_test.py` | exact rank test on the seven-trial loaded arm |
| `analyse.py` | turns sweep logs into the paper's tables and figure data |

**Needs the rig.** CARLA on the Windows simulation host, the physical ESP32
on-board unit, the Fedora RSU with TPM 2.0, Linux IMA and Keylime, and the
Mosquitto broker. These cannot run on a development machine. Dependencies and
pins are in `requirements.txt`.

| script | host | needs |
|---|---|---|
| `carla_host.py` | Windows simulation host | CARLA, paho-mqtt |
| `rsu_engine.py` | Fedora RSU | Keylime verifier over REST, mTLS certs, paho-mqtt |
| `load_gen.py` | any host on the segment | paho-mqtt |

## The topology has been restored, so a rerun measures something different

An intermediate version of `carla_host.py` published straight onto
`v2x/rsu/attestation_request` carrying the ESP32's MAC and firmware hash. That
impersonates the on-board unit and removes two of the four hops and the physical
device from the measured path, so its end-to-end latency was not the quantity the
paper names and was not comparable with the pilot's.

The original topology is back:

```
CARLA --v2x/rsu/beacon--> ESP32 --v2x/rsu/attestation_request--> RSU
                                     --v2x/traffic_light/control--> CARLA
```

exactly as in `../Thesis/carla_v2i_mqtt_master.py` and
`../Thesis/esp32_v2x_obu.c`. **A rerun therefore measures a longer path than the
intermediate harness did, and its numbers are not comparable with anything
produced by that version.** The beacon payload carries the trial context (rid,
geometry, budget) that the ESP32 firmware cannot carry; the RSU subscribes to
both topics and pairs them, which is sound only because the sweep driver keeps
exactly one request outstanding. `--no-obu` restores the short path for bring-up
and marks every row it writes `topology=no-obu-BRING-UP-ONLY`.

## What was fixed

Rig scripts, none of it verified by execution:

**`carla_host.py`**
1. Restored the beacon topology above; the ESP32 is back in the measured path.
2. `load_level` and `policy` are set inside `Trial.run()`, before the row is
   serialised. They used to be attached by the caller *after* the write, so they
   never reached the file and `analyse.py` raised `KeyError` on the first row.
   The dead `for k in (...): pass` loop that stood next to it is gone.
3. Phase control is locked to the attestation cycle. It used to be a fixed sleep
   from the end of the previous trial with no reference to the cycle, which left
   evidence age uniform on `[0, T)` at every nominal phase: the factor did not
   exist. The driver now reads `last_successful_attestation` from the RSU's
   telemetry before each trial, sleeps until `t_last + phase*T`, and **aborts the
   trial if the cycle rolls over mid-wait**. Aborted trials are recorded with a
   reason, not silently dropped. This also removes a confound: under the fixed
   sleep, phase 0 fired back to back while phase 1.0 waited a whole interval
   between requests, so offered load varied by a factor of `T` across the levels
   of the very factor whose effect was being read off.

   Where the target point has already gone by, the driver aims at the same point
   of the next cycle, `t_last + T + phase*T`. That is a **prediction** and it
   assumes a regular attestation period. It is needed because the driver only
   learns of a rollover when the next telemetry message arrives, so without it
   every phase below `telemetry_period / T` — including phase 0.0 — would abort
   every time and a whole level of the factor would be empty. Slipping a further
   cycle is detected and aborts. Phase 1.0 is evaluated at `1 - phase_guard`
   (default 0.95) because the cycle rolls over at exactly 1.0; the effective
   value is in every row. The age the driver achieves is recorded as an
   *estimate*, coarse to one telemetry period — the authoritative evidence age is
   the one `rsu_engine.py` reads from the verifier per request, and that is what
   the analysis should bin on.
4. Trial timeout is `max(2.0, 3*budget_s)`. The fixed 5 s was shorter than the
   largest budget in the sweep (150 m at 50 km/h is 10.8 s), so it truncated the
   cells the envelope is about.
5. Timeouts set `met_budget=False` instead of leaving the key absent.
6. Subscriptions moved into `on_connect`, the first trial is gated on a
   connection event, and the publish return code is checked.
7. `self.pending` is pruned after every trial.
8. OBU uplink expectation set to QoS 1; subscriptions stay at QoS 0. See below.

**`rsu_engine.py`**
1. Evidence age was `time.monotonic()` minus Keylime's
   `last_successful_attestation`, which is a Unix epoch stamp: the difference is
   of order `-1e9 s`, not an age. A wall-clock companion stamp is taken at every
   observation point and ages are computed against it; the four latency terms
   stay on the monotonic clock. Both are recorded. The decision time in the
   wall-clock frame is derived as `t_request_wall + (t_decision_mono -
   t_request_mono)`, so the freshness margin gets an epoch-compatible `t_d`
   without inheriting an NTP step.
2. `float(ts) if ts else None` is now an explicit `is not None` test: a
   legitimate `0.0` and the empty string some builds emit for "never attested"
   are both falsy and must not be conflated with absence.
3. Accepts `hash` (what every ESP32 variant emits) in preference to `fw_hash`,
   which no firmware on this rig has ever sent.
4. Integrity is a conjunction. `attestation_status == "PASS" or
   operational_state == "Get Quote"` admitted the platform whenever it was
   mid-cycle, including on the retry path after a failed quote. It is now
   "not in the failure set, **and** status PASS", with a documented fallback for
   verifier builds that do not expose `attestation_status`.
5. The duplicated contract is gone: the engine imports `contract.py` and calls
   `contract.evaluate` and `contract.policy_action`. The copy had already drifted
   three ways — it tested evidence age at the request rather than projected to the
   decision, used a strict `>` where the contract uses a non-strict comparison,
   and mapped the fail-closed refresh action to `DENY-LATE` even when the held
   evidence was still fresh.
6. `Verifier.force_refresh()` raises `NotImplementedError` with an explanation.
   It was a stub that repeated the same GET and returned the same held evidence
   with the same timestamp, so an `always` or `adaptive` run would have reported a
   refresh cost and a freshness benefit that did not happen. `main()` refuses to
   start those two policies until it is implemented against the verifier's cycle
   API. **`--policy periodic` is the only policy that can be run today.**
7. `--fmax` is optional. The paper's configuration is `F_max = D(x_r)` per
   request, so the default is now the budget carried by the request; a scalar
   `--fmax` pins it for a sensitivity run.
8. Subscriptions moved into `on_connect` (both of them, request and beacon),
   publish return code checked, `on_message` wrapped in `try/except` that logs and
   fails closed instead of killing the handler for the rest of the run.
9. `t_decision` is stamped *after* the outcome exists, so `l_policy` is the real
   cost of evaluating the contract rather than the `~0` two back-to-back stamps
   produced.
10. Unused `log` parameter removed; `--cert-dir` defaults to `$KEYLIME_CV_CA`
    before the distribution path.
11. Publishes the attestation cycle on `v2x/rsu/telemetry`, which is what makes
    phase control on the Windows host possible at all.

**`load_gen.py`**
1. `achieved_*` counted `publish()` calls, which is offered throughput under
   another name and cannot show saturation. The fields are now `offered_*` (the
   target), `published_*` (the send-loop rate, still offered), and `received_*`
   measured by a second subscribed client, plus `delivery_ratio`.
2. `--calibrate` exists now. The help text used to point at a flag that did not.
3. `--payload` defaults to 1024, as the original flooder used; the topic default
   matches it too (`v2x/network/noise`).
4. The pacing loop no longer busy-waits the whole inter-packet gap, which burned
   a core hardest at the *lowest* load levels and put a host-CPU confound in the
   control arm, pointing the wrong way.

**`analyse.py`**
1. Writes `envelope_grid_measured.dat`. It and `envelope.py` both wrote
   `envelope_grid.dat` with different schemas, so whichever ran second destroyed
   the other's output silently. `envelope.py` now writes
   `envelope_grid_model.dat`.
2. Every trial counts in the denominator, including timeouts. Skipping rows with
   no latency dropped them from numerator *and* denominator, which reports the
   miss rate among the trials that did not miss. Trials aborted before the
   request left the host are excluded and counted separately: the factor they
   were meant to sit at was never established.
3. Success is the contract, not the response margin: the freshness term is joined
   in from the RSU row by rid. Both columns are written so the difference is
   visible.

**All three rig scripts** build their MQTT client through a `make_client()`
guard, because `mqtt.Client(client_id=...)` is a `TypeError` on paho-mqtt 2.0+.
`requirements.txt` pins `paho-mqtt` and `requests`.

**Small ones.** `reanalyse_pilot.py`'s quantile was off by one whenever `p*n` was
an integer, which at `n = 50` is every quartile and every decile — `p90` was
500.14 ms and is 498.62 ms; it also opened the loaded-arm file, never read it and
never closed it, and now parses and reports it. `contention_sensitivity.py`
already scales by `PILOT_RATIO` (1.2742) rather than a retyped 1.28. Absolute
paths are gone or environment-overridable, and every script defaults its output
to `results/` next to the source rather than the current directory.

## Critical path

**The timing numbers must be rerun before anything is quoted.** Two problems in
the pilot data, both found by `reanalyse_pilot.py`:

1. **The loaded arm is seven trials, not fifty.** Both comparison charts state it
   in their legends. The unloaded arm is fifty. The +27.7% end-to-end figure
   therefore compares fifty trials against seven and cannot stand. The
   seven-trial attestation mean of 26.24 ms falls *inside* the bootstrap 95%
   interval of the unloaded **mean**, [26.01, 41.70] ms — and inside the interval
   of the unloaded median, [17.80, 27.35] ms — so there is no evidence of any
   difference in that component either way. (An earlier revision of this file
   attached [26.01, 41.70] to the median. It is the mean's interval.)
2. **The 50-trial file disagrees with itself.** Its summary line reports an
   end-to-end mean of 421.77 ms and an attestation mean of 29.17 ms. Recomputing
   from the fifty rows above that line gives 422.83 and 33.30, a 14% discrepancy
   on the attestation component, while minima and maxima match exactly. Rows were
   edited after the summary was generated, or the summary covers a different set.

`loaded_arm_test.py` is what can still be said about the loaded arm: an exact
rank test on the reported minimum rejects for end-to-end (p = 2.49e-3) and finds
nothing for the attestation component (p = 0.768). The magnitude does not follow.

Neither problem is fatal to the paper, and both are fatal to the numbers.

## Order of work on the rig

Bring-up first, and expect the first attempt to fail somewhere: none of this has
been executed.

```sh
pip install -r requirements.txt

# 0. calibrate the channel: find the packet rate at which delivery stops
#    tracking the offered rate, so "load" is a measured utilisation and not a
#    knob position. Use the rung BELOW the reported knee as --saturation-pps.
python3 load_gen.py --calibrate
python3 load_gen.py --saturation-pps <measured> --level 0 --duration 60

# 1. baseline, no contention, periodic policy. F_max defaults to D(x_r) per
#    request; start the RSU first, the driver waits for its cycle telemetry.
python3 rsu_engine.py --policy periodic --run-id base &
python3 carla_host.py --reps 200 --load-level 0 --policy periodic --run-id base

# 2. graded contention, one run per level
for L in 25 50 75 100; do
  python3 load_gen.py --saturation-pps <measured> --level $L --duration 900 &
  python3 carla_host.py --reps 200 --load-level $L --policy periodic --run-id load
done

# 3. the three refresh policies at fixed contention
#    BLOCKED: rsu_engine.py refuses --policy always and --policy adaptive until
#    Verifier.force_refresh() is implemented against the verifier's cycle API.
#    Do not work around the refusal; the stub returns held evidence.

# 4. the two attacks, through the contract this time
#    rogue OBU: flash a unit whose MAC and firmware hash are not in the table
#    tampered RSU: modify a file covered by the strict IMA policy
python3 carla_host.py --reps 20 --run-id attack_rogue
python3 carla_host.py --reps 20 --run-id attack_tamper

# 5. analysis
python3 analyse.py                       # -> envelope_grid_measured.dat
python3 envelope.py --ecdf results/ecdf_ete_unloaded.dat   # -> envelope_grid_model.dat
```

`--reps 200` rather than the pilot's 50, because the envelope is a probability
and the interesting cells are near 0 and 1 where intervals are widest.

## Two things the harness fixes by construction

**Clocks.** Every *duration* is a difference between two readings of the same
monotonic clock at one observation point; no duration is ever a difference across
the ESP32, the Fedora RSU and the Windows host, so no result depends on their
synchronisation. Every *age* is a difference of two wall-clock stamps, because
the verifier's `last_successful_attestation` is a Unix epoch value and cannot be
differenced against a monotonic reading. The two are kept apart deliberately and
both are recorded. Where the driver needs the RSU's evidence age it is handed a
difference of two RSU-side wall stamps plus a local monotonic elapsed, so it
never has to align its clock with the RSU's.

**Evidence age is measured, not assumed.** `rsu_engine.py` reads the verifier's
last-successful-attestation timestamp and records it. Where the verifier does not
expose one it records `None` rather than substituting the current time, because
substituting would silently report age zero and destroy the freshness result,
which is the paper's headline. The default in that case is to *fail closed* on
freshness; `--allow-missing-evidence-ts` runs the rest of the contract with the
freshness term disabled and marks every affected row, and must not be used for
the freshness result.

## Quality of service is not uniform in the original rig

The thesis states QoS 0 throughout. It is not: `esp32_thesis.c:98` calls
`esp_mqtt_client_publish(client, TOPIC_REQUEST, payload, 0, 1, 0)`, whose fifth
argument is QoS, so the on-board unit publishes its identity payload at **QoS 1**.
The broker log confirms it (`Received PUBLISH from ESP32_657F6C (d0, q1, ...)`).

**Every subscription in this rig stays at QoS 0** — `esp32_thesis.c:110`, and
both Python clients here — and effective delivery is the lower of publish and
subscribe QoS, so every *delivery* is at-most-once regardless. What QoS 1 on the
uplink does buy is one PUBACK round trip between the ESP32 and the broker, inside
every end-to-end measurement, on the hop most exposed to contention.

The harness now records that asymmetry rather than flattening it: the constant
`QOS_OBU_UPLINK = 1` appears in both `carla_host.py` and `rsu_engine.py` with the
firmware line cited, the bring-up `--no-obu` path publishes at the same QoS so it
is at least comparable, and the subscriptions are explicitly at 0. Neither Python
host can *set* the uplink QoS — the firmware does — so this is a documented
expectation, not a control.

## A structural result that fell out of the contract

Evidence age at the decision is `t_d - t_a`, and `t_a <= t_r`, so
`age >= t_d - t_r`. If a decision is late, `t_d - t_r > D`, and the freshness
bound satisfies `F_max <= D`, then the evidence is also stale. Every late
decision is then reported as stale and **DENY-LATE can never be observed**.
DENY-LATE is only reachable when `F_max > D(x_r)`, so the freshness bound is a
design parameter that has to be chosen against the budget rather than in
isolation. `contract.masking_threshold()` reports the crossover.

This is why `rsu_engine.py` now defaults `F_max` to `D(x_r)`: it is the boundary
case, and a run at that setting should produce no DENY-LATE at all under periodic
held evidence. If one appears, the topology or the clock handling is wrong.

Note one deliberate divergence between the two measurement points. The RSU's
response margin is `t_decision - t_request_seen`, measured after the ESP32 has
already answered; `carla_host.py` measures the full round trip from beacon to
decision. The RSU figure is always the smaller. Both are logged, and the paper has
to say which one it quotes — the fielded contract should be evaluated against the
round trip, because that is when the actuator can act.

## Provisional results, on pilot data

`envelope.py`, `interval_sweep.py` and `contention_sensitivity.py` run today,
using the unloaded pilot distribution as a stand-in. Over 60 operating points, an
idealised simulation that omits assurance latency and evidence age admits **100%**
as feasible; the measured distribution admits **76.7%** (46/60). The simulation
therefore admits 23.3% of the operating space that the real system refuses, and
every lost point is lost to evidence age rather than to latency. Against the
attestation interval the feasible count runs 54, 52, 46, 35, 24 at 0.5, 1, 2, 5
and 10 s.

**These are placeholders.** They are computed from a 50-sample unloaded pilot
distribution collected on the *original* topology, by scripts that have never
seen this harness's own output. Step 2 above replaces the latency distribution;
until it does, no number in this section is a measurement of the system the paper
describes.
