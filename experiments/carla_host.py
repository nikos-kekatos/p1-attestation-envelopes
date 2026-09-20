#!/usr/bin/env python3
"""Sweep driver on the simulation host. RUNS ON THE WINDOWS CARLA HOST.

NOT YET EXECUTED END TO END. Reviewed and corrected, but the rig (CARLA, the
physical ESP32, the Fedora RSU, the Mosquitto broker) is not available here, so
nothing below has been observed running. See README.md.

Supersedes carla_v2i_mqtt_master.py. What it adds:

  1. The factor sweep: trigger distance x approach speed x evidence-age phase,
     with a configurable repetition count per cell.
  2. Phase control. A request is held until the chosen fraction of the
     attestation interval has elapsed SINCE THE LAST SUCCESSFUL ATTESTATION,
     which is what turns evidence age from an observation into a controlled
     variable.
  3. The budget travels with the request, so the RSU evaluates the contract
     against the geometry that produced it rather than a constant.

TOPOLOGY. This driver publishes a BEACON and waits; the physical ESP32 answers
it with the attestation request, exactly as in ../Thesis/carla_v2i_mqtt_master.py
and ../Thesis/esp32_v2x_obu.c:

    CARLA --v2x/rsu/beacon--> ESP32 --v2x/rsu/attestation_request--> RSU
                                                --v2x/traffic_light/control--> CARLA

An earlier version of this file published straight onto the attestation-request
topic with the ESP32's MAC and firmware hash in the payload. That impersonates
the on-board unit, removes two of the four hops and takes the physical device
out of the measured path entirely, so the resulting latency is not the quantity
the paper names. --no-obu still does that, for bring-up only; it is labelled in
every row it writes and must not be used for a reported result.

The beacon payload carries the trial context (rid, geometry, budget) that the
ESP32 firmware cannot carry. The RSU subscribes to both topics and pairs them,
which is sound because this driver keeps exactly one request outstanding.

All timing is taken from ONE monotonic clock on this host: the beacon is stamped
here and the decision is stamped here on arrival, so no duration is ever a
difference across two machines.
"""
import argparse, json, os, sys, threading, time, uuid

HERE = os.path.dirname(os.path.abspath(__file__))

TOPIC_BEACON  = "v2x/rsu/beacon"
TOPIC_REQUEST = "v2x/rsu/attestation_request"
TOPIC_CONTROL = "v2x/traffic_light/control"
TOPIC_TELEM   = "v2x/rsu/telemetry"

# Effective delivery is min(publish QoS, subscribe QoS). SUBSCRIPTIONS STAY AT
# QoS 0 throughout this rig, matching the thesis configuration and
# esp32_thesis.c:110, so every delivery is at-most-once. The one asymmetry is
# the OBU uplink: esp32_thesis.c:98 publishes the attestation request at QoS 1
# (fifth argument of esp_mqtt_client_publish), which puts one PUBACK round trip
# inside every end-to-end measurement, on the hop most exposed to contention.
# This host cannot set that -- the firmware does -- but the --no-obu bring-up
# path publishes at the same QoS so the two are at least comparable.
QOS_SUBSCRIBE  = 0
QOS_BEACON     = 0
QOS_OBU_UPLINK = 1

SPEEDS_KMH = (50, 80, 110)
DISTANCES_M = (20, 50, 100, 150)
PHASES = (0.0, 0.25, 0.5, 0.75, 1.0)

MAC = "EC:E3:34:65:7F:6C"
FW  = "d4bca3daddb953843162e1df7f89ddbd52e7c96e80f36d0815f393b9fd0bac1d"

PHASE_TOL = 0.005          # s, slack on "the phase point has not passed yet"

# paho's MQTT_ERR_SUCCESS is 0 in both 1.x and 2.x. Naming it here keeps
# Trial.run() importable and testable without paho installed.
MQTT_ERR_SUCCESS = 0


def now():
    return time.monotonic()


def make_client(client_id):
    """paho-mqtt 2.0 removed the client_id-only constructor and requires a
    CallbackAPIVersion; passing client_id= raises TypeError there. Same guard
    the thesis scripts use, which also pins the v1 callback signatures used
    below."""
    import paho.mqtt.client as mqtt
    try:
        return mqtt.Client(mqtt.CallbackAPIVersion.VERSION1, client_id)
    except AttributeError:
        return mqtt.Client(client_id)


class Trial:
    def __init__(self, client, log, load_level=0, policy="periodic",
                 use_obu=True, phase_guard=0.05):
        self.client, self.log = client, log
        self.load_level, self.policy = load_level, policy
        self.use_obu, self.phase_guard = use_obu, phase_guard
        self.pending = {}
        self.telemetry = None        # last attestation-cycle report from the RSU
        self.connected = threading.Event()

    # ---- MQTT plumbing ---------------------------------------------------
    def on_connect(self, client, _ud, _flags, rc):
        """Subscribe HERE, not once after connect(). paho resubscribes nothing
        of its own accord, so a subscription registered outside this callback is
        lost for the remainder of the run the first time the broker drops the
        session -- and under graded contention that loss is load-correlated,
        which would manufacture exactly the effect the sweep is measuring."""
        if rc != 0:
            print(f"[mqtt] connect failed rc={rc}")
            return
        client.subscribe([(TOPIC_CONTROL, QOS_SUBSCRIBE),
                          (TOPIC_TELEM, QOS_SUBSCRIBE)])
        self.connected.set()
        print(f"[mqtt] connected, subscribed to {TOPIC_CONTROL} and "
              f"{TOPIC_TELEM} at QoS {QOS_SUBSCRIBE}")

    def on_disconnect(self, _client, _ud, rc):
        self.connected.clear()
        if rc != 0:
            print(f"[mqtt] unexpected disconnect rc={rc}; reconnecting")

    def on_message(self, _c, _u, msg):
        try:
            if msg.topic == TOPIC_TELEM:
                d = json.loads(msg.payload.decode())
                ts = d.get("last_successful_attestation")
                if ts is not None:
                    # t_now and t_last are BOTH RSU wall-clock stamps, so their
                    # difference is an evidence age that needs no clock
                    # alignment; t_seen is local monotonic.
                    self.telemetry = {"t_last": float(ts),
                                      "t_now": float(d.get("t_now", ts)),
                                      "t_seen": now(),
                                      "interval_s": d.get("interval_s")}
                return
            d = json.loads(msg.payload.decode())
            rid = d.get("rid")
            rec = self.pending.get(rid)
            if rec is not None and "t_decision" not in rec:
                rec["t_decision"] = now()
                rec["outcome"] = d.get("outcome")
        except Exception as e:
            print(f"[ERROR] decision handling failed: {e!r}")

    # ---- phase control ---------------------------------------------------
    def effective_phase(self, phase):
        """Phase 1.0 is the limit point at which the next attestation lands, so
        it is evaluated at (1 - phase_guard). Recorded in every row."""
        return min(phase, 1.0 - self.phase_guard)

    def wait_for_phase(self, phase, interval_s, max_wait):
        """Hold until `phase` of the attestation interval has elapsed since the
        last successful attestation.

        The previous implementation slept `phase * interval` from the END OF THE
        PREVIOUS TRIAL, with no reference to the attestation cycle at all. The
        cycle is free-running, so that made the evidence age at the request
        uniform on [0, T) at every nominal phase and the factor did not exist.

        It also confounded phase with offered load: at phase 0 the driver fired
        back to back, at phase 1.0 it waited a whole interval between requests,
        so the request rate differed by a factor of T across the levels of the
        very factor whose effect on latency was being read off. Phase-locking
        removes that: every trial now waits for a point in the cycle regardless
        of its nominal phase, so inter-request spacing is set by the cycle.

        Two cases:

        A. the target point is still ahead in the current cycle. Sleep to
           t_last + phase*T and ABORT if the cycle rolls over mid-wait, because
           the evidence being phased against no longer exists.
        B. the point has already gone by. The cycle is periodic with period T,
           so the same point of the NEXT cycle is at t_last + T + phase*T; aim
           there. This is a PREDICTION and it assumes a regular attestation
           period. Without it every phase below telemetry_period/T would be
           unreachable -- the driver only learns of a rollover when the next
           telemetry message arrives -- and phase 0.0 would abort every time,
           emptying a whole level of the factor. Slipping a further whole cycle
           is detected (more than one rollover observed) and aborts.

        The age this achieves is an ESTIMATE, coarse to one telemetry period.
        The authoritative evidence age is the one rsu_engine.py reads from the
        verifier and records per request; analyse.py should bin on that.

        Returns (ok, reason, effective_phase, age_estimate, rollover_observed).
        """
        eff = self.effective_phase(phase)
        target = eff * interval_s
        stale_limit = max(2.0, 0.5 * interval_s)
        t_start = now()

        while now() - t_start < max_wait:
            tel = self.telemetry
            if tel is None:
                time.sleep(0.05)
                continue
            if now() - tel["t_seen"] > stale_limit:
                # the RSU stopped reporting: the cycle lock is gone and any
                # phase claimed from here on would be fiction
                time.sleep(0.05)
                if now() - t_start >= max_wait:
                    return False, "telemetry-stale", eff, None, False
                continue

            t_last0 = tel["t_last"]
            age = (tel["t_now"] - t_last0) + (now() - tel["t_seen"])
            if age <= target + PHASE_TOL:
                deadline, expect_rollover = now() + (target - age), False
            else:
                deadline, expect_rollover = now() + (interval_s - age) + target, True
                if deadline - now() > max_wait - (now() - t_start):
                    return False, "phase-timeout", eff, None, False

            rollovers, t_seen_last = 0, t_last0
            while now() < deadline:
                cur = self.telemetry
                if cur is not None and cur["t_last"] != t_seen_last:
                    rollovers += 1
                    t_seen_last = cur["t_last"]
                    if not expect_rollover:
                        # case A: the evidence we were phasing against is gone
                        return False, "cycle-rollover", eff, None, True
                    if rollovers > 1:
                        # case B: slipped a whole cycle, the aim is meaningless
                        return False, "cycle-slip", eff, None, True
                time.sleep(0.001)

            cur = self.telemetry
            if cur is not None and cur["t_last"] != t_seen_last:
                rollovers += 1
                t_seen_last = cur["t_last"]
                if not expect_rollover:
                    return False, "cycle-rollover", eff, None, True

            age_est = None
            if cur is not None:
                age_est = (cur["t_now"] - cur["t_last"]) + (now() - cur["t_seen"])
                if expect_rollover and rollovers == 0:
                    # fired on the prediction before the telemetry caught up, so
                    # the stamp in hand is one cycle old; correct for it rather
                    # than reporting an age of about T at a phase of about 0.
                    age_est = max(0.0, age_est - interval_s)
            return True, "ok", eff, age_est, rollovers > 0
        return False, "phase-timeout", eff, None, False

    # ---- one trial -------------------------------------------------------
    def run(self, distance_m, speed_kmh, phase, interval_s, rep):
        rid = uuid.uuid4().hex[:12]
        budget_s = distance_m / (speed_kmh / 3.6)
        # The largest budget in the sweep is 150 m at 50 km/h = 10.8 s, so a
        # fixed 5 s timeout truncated the very cells the envelope is about and
        # scored them as misses regardless of what the rig did.
        timeout = max(2.0, 3.0 * budget_s)
        rec = {"rid": rid, "distance_m": distance_m, "speed_kmh": speed_kmh,
               "phase": phase, "interval_s": interval_s, "rep": rep,
               "budget_s": budget_s, "timeout_s": timeout,
               # load_level and policy are set HERE, before the row is written.
               # They used to be attached to the returned dict by the caller,
               # after the row had already been serialised, so they never
               # reached the file and analyse.py raised KeyError on row one.
               "load_level": self.load_level, "policy": self.policy,
               "topology": "obu" if self.use_obu else "no-obu-BRING-UP-ONLY"}

        ok, reason, eff_phase, age_est, rolled = self.wait_for_phase(
            phase, interval_s, max_wait=max(4.0 * interval_s, 5.0))
        rec["effective_phase"] = eff_phase
        rec["phase_status"] = reason
        rec["rollover_observed"] = rolled
        # An ESTIMATE, coarse to one telemetry period. The authoritative
        # evidence age is the one rsu_engine.py reads from the verifier.
        rec["evidence_age_at_request_est_s"] = age_est
        if not ok:
            # No phase lock means no controlled evidence age, so the trial is
            # not a member of its cell. Recorded, not silently dropped.
            rec["aborted"] = reason
            rec["met_budget"] = False
            self.log.write(json.dumps(rec) + "\n")
            return rec

        self.pending[rid] = rec
        body = json.dumps({"rid": rid, "budget_s": budget_s,
                           "distance_m": distance_m, "speed_kmh": speed_kmh,
                           "interval_s": interval_s, "phase": eff_phase})
        rec["t_request"] = now()
        if self.use_obu:
            # The ESP32 ignores the beacon payload and answers the topic
            # (esp32_thesis.c:113), so the context rides along for the RSU.
            info = self.client.publish(TOPIC_BEACON, body, qos=QOS_BEACON)
        else:
            info = self.client.publish(TOPIC_REQUEST, json.dumps(
                {"rid": rid, "mac": MAC, "hash": FW, "budget_s": budget_s,
                 "distance_m": distance_m, "speed_kmh": speed_kmh}),
                qos=QOS_OBU_UPLINK)
        rec["publish_rc"] = info.rc
        if info.rc != MQTT_ERR_SUCCESS:
            # A publish that never left the client is not a deadline miss; it is
            # a lost trial, and conflating the two would attribute a broker
            # failure to the assurance path.
            rec["aborted"] = f"publish-rc-{info.rc}"
            rec["met_budget"] = False
            self.pending.pop(rid, None)
            self.log.write(json.dumps(rec) + "\n")
            return rec

        t0 = now()
        while "t_decision" not in rec and now() - t0 < timeout:
            time.sleep(0.001)
        if "t_decision" in rec:
            rec["latency_ms"] = (rec["t_decision"] - rec["t_request"]) * 1000.0
            rec["met_budget"] = rec["latency_ms"] / 1000.0 <= budget_s
        else:
            rec["timeout"] = True
            # A trial that never returned a decision missed its budget by
            # definition. Leaving met_budget absent made analyse.py drop the row
            # from the denominator as well as the numerator, which reports the
            # miss rate among the trials that did not miss.
            rec["met_budget"] = False
        # Prune: the entry is done with, and self.pending otherwise grows for
        # the whole run (hours, 12k rows) and keeps every timed-out trial's
        # record alive so a late decision could still mutate it after logging.
        self.pending.pop(rid, None)
        self.log.write(json.dumps(rec) + "\n")
        return rec


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--broker", default="127.0.0.1")
    ap.add_argument("--reps", type=int, default=200,
                    help="repetitions per cell; the pilot's 50 is too few and "
                         "its loaded arm had 7")
    ap.add_argument("--interval", type=float, default=2.0,
                    help="verifier attestation_period, s; must match the value "
                         "rsu_engine.py publishes in its telemetry")
    ap.add_argument("--speeds", type=int, nargs="+", default=list(SPEEDS_KMH))
    ap.add_argument("--distances", type=int, nargs="+", default=list(DISTANCES_M))
    ap.add_argument("--phases", type=float, nargs="+", default=list(PHASES))
    ap.add_argument("--phase-guard", type=float, default=0.05,
                    help="phase 1.0 is evaluated at (1 - guard) of the interval, "
                         "because the cycle rolls over at exactly 1.0; the "
                         "effective phase is recorded in every row")
    ap.add_argument("--load-level", type=int, default=0,
                    help="recorded in every row so runs can be pooled")
    ap.add_argument("--policy", default="periodic", help="recorded, set on the RSU")
    ap.add_argument("--no-obu", action="store_true",
                    help="BRING-UP ONLY: publish the attestation request "
                         "directly, impersonating the ESP32. Cuts the physical "
                         "device and two hops out of the measured path. Rows "
                         "are marked topology=no-obu-BRING-UP-ONLY.")
    ap.add_argument("--connect-timeout", type=float, default=15.0)
    ap.add_argument("--run-id", default="sweep")
    ap.add_argument("--out", default=os.path.join(HERE, "results"))
    a = ap.parse_args()

    os.makedirs(a.out, exist_ok=True)
    path = os.path.join(a.out, f"{a.run_id}_L{a.load_level}_{a.policy}.jsonl")
    log = open(path, "a", buffering=1)

    c = make_client("CarlaSweepDriver")
    t = Trial(c, log, load_level=a.load_level, policy=a.policy,
              use_obu=not a.no_obu, phase_guard=a.phase_guard)
    c.on_connect = t.on_connect
    c.on_disconnect = t.on_disconnect
    c.on_message = t.on_message
    c.connect(a.broker, 1883, 60)
    c.loop_start()

    # Gate the first trial on the subscription actually being in place. Firing
    # into a broker we are not yet subscribed to loses the first decisions and
    # scores them as timeouts.
    if not t.connected.wait(a.connect_timeout):
        c.loop_stop()
        raise SystemExit(f"no MQTT connection to {a.broker} after "
                         f"{a.connect_timeout}s; nothing was run")
    if t.telemetry is None:
        print("waiting for the RSU attestation-cycle telemetry "
              f"({TOPIC_TELEM}); phase control needs it...")
        t0 = now()
        while t.telemetry is None and now() - t0 < a.connect_timeout:
            time.sleep(0.05)
        if t.telemetry is None:
            c.loop_stop()
            raise SystemExit(
                "no telemetry from rsu_engine.py: start it first, with "
                "--telemetry-period > 0. Without the attestation cycle the "
                "phase factor is not controlled and the run is worthless.")

    cells = [(d, s, p) for s in a.speeds for d in a.distances for p in a.phases]
    total = len(cells) * a.reps
    print(f"{len(cells)} cells x {a.reps} reps = {total} trials -> {path}")
    if a.no_obu:
        print("*** --no-obu: the ESP32 is NOT in the measured path. Bring-up only. ***")
    n = 0
    try:
        for (d, s, p) in cells:
            misses = aborts = 0
            for rep in range(a.reps):
                r = t.run(d, s, p, a.interval, rep)
                if r.get("aborted"):
                    aborts += 1
                if not r.get("met_budget", False):
                    misses += 1
                n += 1
            print(f"  d={d:3d}m v={s:3d}km/h phi={p:.2f}: "
                  f"{misses}/{a.reps} missed the budget, {aborts} aborted "
                  f"[{n}/{total}]")
    finally:
        c.loop_stop()
        c.disconnect()
        log.close()
    print(f"done -> {path}")


if __name__ == "__main__":
    main()
