#!/usr/bin/env python3
"""Instrumented RSU attestation engine. RUNS ON THE FEDORA RSU.

NOT YET EXECUTED END TO END. This file has been reviewed and corrected but the
rig it drives (TPM 2.0, Keylime, the physical ESP32, the Mosquitto broker) is
not available here, so nothing below has been observed running. See README.md.

Supersedes fedora_final_journal.py. Three changes that the paper needs:

  1. Five-term latency decomposition instead of one end-to-end number, so the
     claim about where the load sensitivity lives is supported rather than
     asserted.
  2. Evidence age is recorded, not just the integrity verdict, by reading the
     verifier's last-successful-attestation timestamp.
  3. The three refresh policies are selectable, so periodic, always and adaptive
     can be compared on the same rig.

TOPOLOGY (restored to the thesis rig, see ../Thesis/esp32_v2x_obu.c):

    CARLA --beacon--> ESP32 --attestation_request--> RSU --control--> CARLA

The engine subscribes to BOTH the beacon and the attestation request. The
beacon carries the trial context (rid, geometry, budget) that the ESP32
firmware cannot carry, and the attestation request carries the identity payload
the ESP32 produces. They are paired on arrival order, which is sound only
because the sweep driver keeps one request outstanding at a time.

CLOCKS. Two are kept, deliberately:

  * time.monotonic() for every DURATION (the four latency terms). Monotonic is
    immune to NTP steps but its zero is arbitrary and process-local.
  * time.time() for every AGE, because the verifier's last_successful_attestation
    is a Unix epoch timestamp and cannot be differenced against a monotonic
    reading. Subtracting an epoch stamp from time.monotonic() -- which an
    earlier version of this file did -- yields an age of order 1e9 s or a large
    negative number, not an evidence age.

Every duration is still a difference of two readings taken at THIS observation
point; nothing is differenced across the ESP32, the RSU and the Windows host.
The decision time in the wall-clock frame is derived as
t_request_seen_wall + (t_decision_mono - t_request_seen_mono), so the freshness
margin gets an epoch-compatible t_d without inheriting a clock step.

Requires: Keylime verifier reachable over its REST interface, mTLS certs,
paho-mqtt (see requirements.txt). Will not run off the rig.
"""
import argparse, json, os, sys, threading, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import contract      # the single implementation of the contract; see item 10

HERE = os.path.dirname(os.path.abspath(__file__))

TOPIC_BEACON  = "v2x/rsu/beacon"
TOPIC_REQUEST = "v2x/rsu/attestation_request"
TOPIC_CONTROL = "v2x/traffic_light/control"
TOPIC_TELEM   = "v2x/rsu/telemetry"

# Effective MQTT delivery is min(publish QoS, subscribe QoS). Every subscription
# in this rig is registered at 0, so every DELIVERY is QoS 0. The one publication
# that is not is the OBU uplink: esp32_thesis.c:98 publishes the attestation
# request at QoS 1 (fifth argument of esp_mqtt_client_publish). That fact is
# recorded here so the asymmetry is visible in the code and not only in prose.
QOS_SUBSCRIBE   = 0
QOS_CONTROL     = 0
QOS_TELEMETRY   = 0
QOS_OBU_UPLINK  = 1   # set by the ESP32 firmware, not by this file

TRUSTED_NODES = {
    "EC:E3:34:65:7F:6C":
        "d4bca3daddb953843162e1df7f89ddbd52e7c96e80f36d0815f393b9fd0bac1d",
}

# Keylime operational states. The failure set is excluded explicitly rather than
# inferred from the absence of a success state, so an unrecognised state fails
# closed instead of being read as a pass.
FAILED_STATES = {"Failed", "Invalid Quote", "Tenant Quote Failed",
                 "Terminated", "Unknown"}
# States in which the platform is under active, passing attestation. Used ONLY
# where the verifier does not expose attestation_status at all.
TRUSTED_STATES = {"Trusted", "Get Quote", "Get Quote Retry", "Saved", "Attested"}


def integrity_ok(results):
    """Platform integrity from one verifier response.

    A CONJUNCTION. The previous test was a disjunction,

        attestation_status == "PASS" or operational_state == "Get Quote"

    which admitted the platform whenever it happened to be mid-cycle -- and
    "Get Quote" is re-entered on the retry path after a quote has FAILED, so a
    failing platform was admitted on every second poll. The failure set is
    excluded explicitly, so an unrecognised state fails closed rather than being
    read as a pass.

    Verifier builds that expose no attestation_status at all fall back to the
    operational state alone, which is what fedora_final_journal.py did; that is
    weaker and is the reason the field is preferred where it exists.
    """
    status = results.get("attestation_status")
    state = results.get("operational_state")
    if state in FAILED_STATES:
        return False
    if status is not None:
        return status == "PASS"
    return state in TRUSTED_STATES


def now():
    """Monotonic clock. Every DURATION in this file is a difference of two
    readings of THIS function, never a difference across two hosts and never a
    difference against an epoch timestamp."""
    return time.monotonic()


def now_wall():
    """Wall clock, seconds since the Unix epoch. Companion stamp to now(). Used
    ONLY for evidence AGE, because the verifier's timestamps are epoch-based."""
    return time.time()


class Verifier:
    """Keylime verifier over REST. Returns (pass, evidence_epoch, cost, raw)."""

    def __init__(self, ip, port, uuid, certs, timeout=2.0, api="v2.1"):
        self.url = f"https://{ip}:{port}/{api}/agents/{uuid}"
        self.certs, self.timeout = certs, timeout

    #: The REST agent endpoint reports the cycle; it does not drive it. Set by
    #: any subclass that implements force_refresh() against a cycle API.
    can_refresh = False

    def query(self):
        import requests
        t0 = now()
        r = requests.get(self.url, cert=(self.certs["cert"], self.certs["key"]),
                         verify=self.certs["ca"], timeout=self.timeout)
        cost = now() - t0
        d = r.json().get("results", {})

        ok = integrity_ok(d)

        # Evidence age comes from the verifier's own timestamp where it exposes
        # one. Falling back to "now" would silently report age zero and destroy
        # the freshness result, so we mark it missing instead. The test is an
        # explicit `is not None`: a legitimate timestamp of 0.0 is falsy, and so
        # is the empty string some verifier builds emit for "never attested",
        # and those two must not be conflated with each other or with absence.
        ts = d.get("last_successful_attestation")
        t_evidence = None
        if ts is not None:
            try:
                t_evidence = float(ts)
            except (TypeError, ValueError):
                t_evidence = None
        return ok, t_evidence, cost, d

    def force_refresh(self):
        """Request a new attestation cycle rather than waiting for the poll.

        NOT IMPLEMENTED. The previous body simply repeated the GET above and
        returned its result, which looks like a refresh, costs one round trip,
        and returns exactly the same held evidence with exactly the same
        last_successful_attestation. Any 'always' or 'adaptive' run built on it
        would have reported a refresh cost and a freshness benefit that did not
        happen. It raises rather than degrading silently.

        To implement: POST to the verifier's agent endpoint to re-add or
        reactivate the agent (v2.x has no 'attest now' verb), then poll
        query() until last_successful_attestation advances past the epoch stamp
        taken before the POST, and return the elapsed monotonic cost. Set
        can_refresh = True on the subclass that does it.
        """
        raise NotImplementedError(
            "Verifier.force_refresh() is a stub: the Keylime REST agent "
            "endpoint reports the attestation cycle but does not drive it. "
            "Implement it against the verifier's cycle API before running "
            "--policy always or --policy adaptive; --policy periodic does not "
            "use it.")


def _request_from(payload, t_r_wall, fallback_distance_m, fallback_speed_kmh):
    """Build the contract's Request from whatever geometry reached the RSU.

    Returns (Request, source). The ESP32 payload carries only mac and hash, so
    the geometry arrives on the beacon and is merged in by the caller. If the
    beacon context is missing the configured fallback geometry is used and the
    row is marked, so a topology fault shows up in the logs as `fallback`
    rather than as an unexplained shift in the budget.
    """
    rid = payload.get("rid") or "unknown"
    d, v, b = payload.get("distance_m"), payload.get("speed_kmh"), payload.get("budget_s")
    if d is not None and v:
        return contract.Request(rid, t_r_wall, float(d), float(v) / 3.6), "geometry"
    if b is not None:
        # contract.budget() is distance/speed; a unit-speed request whose
        # distance is the budget reproduces it exactly.
        return contract.Request(rid, t_r_wall, float(b), 1.0), "budget"
    return (contract.Request(rid, t_r_wall, float(fallback_distance_m),
                             float(fallback_speed_kmh) / 3.6), "fallback")


def handle_request(payload, verifier, policy, f_max_arg, l_refresh_est,
                   l_decide_est, fallback_distance_m=20.0,
                   fallback_speed_kmh=110.0, allow_missing_evidence_ts=False):
    """One authorisation. Emits the five-term decomposition and the two margins.

    The outcome is NOT computed here: it is delegated to contract.evaluate(),
    and the refresh choice to contract.policy_action(), so there is exactly one
    implementation of the contract and the offline replay in analyse.py cannot
    drift from the online decision. An earlier version of this function carried
    its own copy which had already drifted three ways: it tested evidence age at
    the REQUEST rather than projected to the DECISION, it used a strict `>`
    where the contract uses a non-strict comparison and therefore denied a zero
    margin the contract admits, and it mapped the fail-closed refresh action
    straight to DENY-LATE even when the held evidence was still fresh.

    DIVERGENCE, deliberate and recorded: the response margin here is
    t_decision - t_request_seen, measured at the RSU. carla_host.py measures the
    full round trip, beacon out through the ESP32 and decision back. The RSU
    figure therefore excludes the beacon hop, the OBU turnaround and the return
    transport, and is always the smaller of the two. Both are logged; the paper
    must say which one it quotes. The contract as fielded should be evaluated
    against the round trip, because that is when the actuator can act.
    """
    t_req_mono, t_req_wall = now(), now_wall()
    stamps = {"t_request_seen": t_req_mono, "t_request_seen_wall": t_req_wall}
    t_beacon_mono = payload.get("_t_beacon_seen")
    if t_beacon_mono is not None:
        # both stamps taken on this host, so this is a valid duration: it is the
        # beacon hop plus the ESP32 turnaround plus the QoS-1 uplink.
        stamps["l_obu"] = t_req_mono - t_beacon_mono

    def finish(outcome, extra):
        t_dec = now()
        stamps["t_decision"] = t_dec
        stamps["t_decision_wall"] = t_req_wall + (t_dec - t_req_mono)
        stamps.update(extra)
        return outcome

    # ---- term 1: identity check, no verifier involved -----------------------
    t0 = now()
    mac = payload.get("mac")
    # Every ESP32 variant on the rig emits "hash" (esp32_v2x_obu.c:34,
    # esp32_thesis.c:96); only fedora_final_journal.py ever looked for a
    # different key. "hash" wins, "fw_hash" is accepted so a rerun of the
    # bring-up harness still matches.
    fw = payload.get("hash")
    if fw is None:
        fw = payload.get("fw_hash")
    identity_ok = mac is not None and fw is not None and TRUSTED_NODES.get(mac) == fw
    stamps["l_identity"] = now() - t0

    if not identity_ok:
        stamps["l_verifier"] = stamps["l_refresh"] = stamps["l_policy"] = 0.0
        return (finish(contract.Outcome.DENY_INTEGRITY.value,
                       {"refresh_action": "none",
                        "evidence_age_at_request": None,
                        "evidence_age_at_decision": None,
                        "reason": "identity: unknown MAC or firmware hash"}),
                stamps, {"reason": "identity"})

    # ---- term 2: platform integrity, and the refresh decision ---------------
    ok, t_evidence, cost, raw = verifier.query()
    stamps["l_verifier"] = cost

    req, geometry_source = _request_from(payload, t_req_wall,
                                         fallback_distance_m, fallback_speed_kmh)
    stamps["geometry_source"] = geometry_source
    stamps["budget_s"] = contract.budget(req)

    # F_max. The paper's configuration is F_max = D(x_r) per request, not a
    # constant: the freshness bound that matters is the one the geometry that
    # produced the request implies. --fmax overrides it for the sensitivity runs.
    f_max = f_max_arg if f_max_arg is not None else contract.budget(req)
    stamps["f_max"] = f_max

    # Evidence age is an EPOCH difference, not a monotonic one.
    age_at_request = None if t_evidence is None else (t_req_wall - t_evidence)
    stamps["evidence_age_at_request"] = age_at_request
    stamps["evidence_timestamp"] = t_evidence

    freshness_evaluated = t_evidence is not None
    if not freshness_evaluated:
        if not allow_missing_evidence_ts:
            stamps["l_refresh"] = stamps["l_policy"] = 0.0
            return (finish(contract.Outcome.DENY_STALE.value,
                           {"refresh_action": "none",
                            "evidence_age_at_decision": None,
                            "freshness_evaluated": False,
                            "reason": "verifier exposes no evidence timestamp; "
                                      "failing closed on freshness"}),
                    stamps, raw)
        # Explicitly opted in: run the rest of the contract with the freshness
        # term disabled and SAY SO in the row, rather than substituting an age.
        ev_t_a, f_max_eval = t_req_wall, float("inf")
    else:
        ev_t_a, f_max_eval = t_evidence, f_max
    stamps["freshness_evaluated"] = freshness_evaluated

    ev = contract.Evidence(integrity_pass=ok, t_a=ev_t_a, source="periodic")

    action = contract.policy_action(policy, req, ev, f_max_eval,
                                    l_refresh_est, l_decide_est)
    stamps["l_refresh"] = 0.0
    if action == contract.REFRESH:
        ok, t_evidence, stamps["l_refresh"], raw = verifier.force_refresh()
        ev = contract.Evidence(integrity_pass=ok,
                               t_a=t_evidence if t_evidence is not None else now_wall(),
                               source="refreshed")
        stamps["evidence_timestamp"] = t_evidence
    # FAIL_CLOSED is recorded as an action, not mapped to an outcome. The
    # contract decides: under `adaptive` fail-closed is only reachable when the
    # projected age already exceeds F_max, so evaluate() returns DENY-STALE of
    # its own accord. Under `always` it means the refresh did not fit the
    # budget, and if the held evidence is still fresh the contract permits --
    # correctly, because declining to spend a refresh does not invalidate
    # evidence that is inside F_max.
    stamps["refresh_action"] = action

    # ---- term 3: policy evaluation ------------------------------------------
    # t_d handed to the contract is the instant the policy step BEGINS; the
    # recorded t_decision is taken after the outcome exists. l_policy is the gap
    # between them, so it is now the real cost of evaluating the contract rather
    # than the ~0 that two back-to-back stamps produced.
    t_policy_start_mono = now()
    t_d_wall = t_req_wall + (t_policy_start_mono - t_req_mono)
    verdict = contract.evaluate(req, ev, t_d_wall, f_max_eval)
    t_dec_mono = now()
    stamps["l_policy"] = t_dec_mono - t_policy_start_mono
    stamps["t_decision"] = t_dec_mono
    stamps["t_decision_wall"] = t_req_wall + (t_dec_mono - t_req_mono)
    stamps["t_contract_eval_wall"] = t_d_wall
    stamps["m_r"] = verdict.m_r
    stamps["m_f"] = None if not freshness_evaluated else verdict.m_f
    stamps["evidence_age_at_decision"] = None if not freshness_evaluated else verdict.evidence_age
    stamps["reason"] = verdict.reason
    return verdict.outcome.value, stamps, raw


class TelemetryPublisher(threading.Thread):
    """Publish the verifier's attestation cycle so the sweep driver can phase
    against it.

    The sweep driver runs on the Windows host and has no route to the verifier's
    mTLS endpoint, so the cycle has to reach it some other way. Each message
    carries BOTH the last-successful-attestation stamp and the RSU wall clock at
    the moment of publication, so the driver can compute an evidence AGE from a
    difference of two same-host stamps and never has to align its clock with
    this one.

    This adds one verifier GET per period for the duration of a run. It is a
    load on the same path being measured; keep the period well above the
    request rate and report it.
    """

    def __init__(self, client, verifier, period, interval_s):
        super().__init__(daemon=True)
        self.client, self.verifier = client, verifier
        self.period, self.interval_s = period, interval_s
        self.stop_flag = threading.Event()

    def run(self):
        while not self.stop_flag.wait(self.period):
            try:
                ok, ts, cost, d = self.verifier.query()
            except Exception as e:                      # verifier down, keep going
                print(f"[telemetry] verifier query failed: {e}")
                continue
            msg = {"t_now": now_wall(), "last_successful_attestation": ts,
                   "operational_state": d.get("operational_state"),
                   "attestation_status": d.get("attestation_status"),
                   "integrity_pass": ok, "interval_s": self.interval_s,
                   "l_verifier": cost}
            self.client.publish(TOPIC_TELEM, json.dumps(msg), qos=QOS_TELEMETRY)


def make_client(client_id):
    """paho-mqtt 2.0 removed the positional/keyword client_id constructor and
    requires a CallbackAPIVersion. Same guard the thesis scripts use, so the
    v1-style callback signatures below stay correct on both major versions."""
    import paho.mqtt.client as mqtt
    try:
        return mqtt.Client(mqtt.CallbackAPIVersion.VERSION1, client_id)
    except AttributeError:
        return mqtt.Client(client_id)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--broker", default="127.0.0.1")
    ap.add_argument("--verifier-ip", default="127.0.0.1")
    ap.add_argument("--verifier-port", default="8881")
    ap.add_argument("--verifier-api", default="v2.1")
    ap.add_argument("--uuid", default="d432fbb3-d2f1-4a97-9ef7-75bd81c00000")
    ap.add_argument("--cert-dir", default=os.environ.get(
        "KEYLIME_CV_CA", "/var/lib/keylime/cv_ca"),
        help="Keylime CA directory; defaults to $KEYLIME_CV_CA then the "
             "distribution path")
    ap.add_argument("--policy", choices=contract.POLICIES, default="periodic")
    ap.add_argument("--fmax", type=float, default=None,
                    help="freshness bound, s. DEFAULT: F_max = D(x_r), the "
                         "budget carried by each request, which is the paper's "
                         "configuration. Give a number to pin it for a "
                         "sensitivity run.")
    ap.add_argument("--refresh-est", type=float, default=0.25,
                    help="expected cost of obtaining fresh evidence, s")
    ap.add_argument("--decide-est", type=float, default=0.0,
                    help="expected cost of reaching a decision on held "
                         "evidence, s; the adaptive policy projects the "
                         "evidence age forward by this much")
    ap.add_argument("--interval", type=float, default=2.0,
                    help="verifier attestation_period, s; published in the "
                         "telemetry so the sweep driver can phase against it")
    ap.add_argument("--telemetry-period", type=float, default=0.5,
                    help="seconds between attestation-cycle telemetry "
                         "publications; 0 disables (phase control then has no "
                         "reference and carla_host.py will abort its trials)")
    ap.add_argument("--fallback-distance", type=float, default=20.0,
                    help="geometry used only when a request arrives with no "
                         "beacon context; rows are marked geometry_source=fallback")
    ap.add_argument("--fallback-speed", type=float, default=110.0)
    ap.add_argument("--allow-missing-evidence-ts", action="store_true",
                    help="if the verifier exposes no last_successful_attestation, "
                         "evaluate the rest of the contract with the freshness "
                         "term DISABLED instead of failing closed. Rows are "
                         "marked freshness_evaluated=false. Do not use for the "
                         "freshness result.")
    ap.add_argument("--run-id", default="run")
    ap.add_argument("--out", default=os.path.join(HERE, "results"))
    a = ap.parse_args()

    if a.policy in ("always", "adaptive") and not Verifier.can_refresh:
        ap.error("--policy %s needs Verifier.force_refresh(), which is a "
                 "documented stub on the REST interface. Implement it against "
                 "the verifier's cycle API first; running it now would report a "
                 "refresh that did not happen. --policy periodic works today."
                 % a.policy)

    os.makedirs(a.out, exist_ok=True)
    certs = {"cert": os.path.join(a.cert_dir, "client-cert.crt"),
             "key":  os.path.join(a.cert_dir, "client-private.pem"),
             "ca":   os.path.join(a.cert_dir, "cacert.crt")}
    verifier = Verifier(a.verifier_ip, a.verifier_port, a.uuid, certs,
                        api=a.verifier_api)
    path = os.path.join(a.out, f"{a.run_id}_rsu.jsonl")
    log = open(path, "a", buffering=1)

    import paho.mqtt.client as mqtt

    state = {"beacon": None}     # last trial context seen on the beacon topic

    def on_connect(client, _ud, _flags, rc):
        # Subscribing here and not once after connect() is what keeps a mid-run
        # reconnect from silently dropping the subscription for the rest of the
        # run. paho resubscribes nothing on its own; a broker that drops the
        # session under load would otherwise leave this engine connected, alive
        # and deaf, and the losses would be load-correlated -- exactly the
        # signal the experiment is trying to measure.
        if rc != 0:
            print(f"[mqtt] connect failed rc={rc}")
            return
        client.subscribe([(TOPIC_REQUEST, QOS_SUBSCRIBE),
                          (TOPIC_BEACON, QOS_SUBSCRIBE)])
        print(f"[mqtt] connected, subscribed to {TOPIC_REQUEST} and "
              f"{TOPIC_BEACON} at QoS {QOS_SUBSCRIBE}")

    def on_disconnect(_client, _ud, rc):
        if rc != 0:
            print(f"[mqtt] unexpected disconnect rc={rc}; reconnecting")

    def on_message(client, _ud, msg):
        try:
            if msg.topic == TOPIC_BEACON:
                # trial context; the ESP32 ignores the payload and answers the
                # topic, so this is how rid and geometry reach the RSU at all.
                try:
                    ctx = json.loads(msg.payload.decode())
                except (ValueError, UnicodeDecodeError):
                    ctx = {}
                ctx["_t_beacon_seen"] = now()
                state["beacon"] = ctx
                return

            payload = json.loads(msg.payload.decode())
            ctx = state["beacon"]
            state["beacon"] = None   # consume it; one request outstanding
            if ctx:
                # request fields win where both carry a key
                merged = dict(ctx)
                merged.update(payload)
                merged["_t_beacon_seen"] = ctx.get("_t_beacon_seen")
                payload = merged

            outcome, stamps, raw = handle_request(
                payload, verifier, a.policy, a.fmax, a.refresh_est,
                a.decide_est, a.fallback_distance, a.fallback_speed,
                a.allow_missing_evidence_ts)

            info = client.publish(TOPIC_CONTROL, json.dumps(
                {"rid": payload.get("rid"), "outcome": outcome,
                 "green": outcome == contract.Outcome.PERMIT.value}),
                qos=QOS_CONTROL)
            if info.rc != mqtt.MQTT_ERR_SUCCESS:
                print(f"[mqtt] decision publish failed rc={info.rc}")

            rec = {"rid": payload.get("rid"), "policy": a.policy,
                   "fmax_arg": a.fmax, "outcome": outcome,
                   "publish_rc": info.rc, **stamps}
            log.write(json.dumps(rec) + "\n")
            print(f"[{payload.get('rid')}] {outcome}  "
                  f"verifier {stamps.get('l_verifier', 0)*1000:.1f} ms  "
                  f"refresh {stamps.get('l_refresh', 0)*1000:.1f} ms  "
                  f"policy {stamps.get('l_policy', 0)*1000:.2f} ms  "
                  f"age {stamps.get('evidence_age_at_decision')}  "
                  f"action {stamps.get('refresh_action')}")
        except Exception as e:
            # A handler that dies takes the subscription's usefulness with it
            # for the rest of the run and leaves the sweep driver timing out
            # against a silent broker. Log, fail closed, keep serving.
            print(f"[ERROR] request handling failed: {e!r}")
            try:
                rid = json.loads(msg.payload.decode()).get("rid")
            except Exception:
                rid = None
            try:
                client.publish(TOPIC_CONTROL, json.dumps(
                    {"rid": rid, "outcome": "ERROR", "green": False,
                     "error": repr(e)}), qos=QOS_CONTROL)
                log.write(json.dumps({"rid": rid, "outcome": "ERROR",
                                      "error": repr(e)}) + "\n")
            except Exception:
                pass

    c = make_client("RSU_Assurance_Engine")
    c.on_connect = on_connect
    c.on_disconnect = on_disconnect
    c.on_message = on_message
    c.connect(a.broker, 1883, 60)

    telem = None
    if a.telemetry_period > 0:
        telem = TelemetryPublisher(c, verifier, a.telemetry_period, a.interval)
        telem.start()

    fmax_desc = "D(x_r) per request" if a.fmax is None else f"{a.fmax}s"
    print(f"RSU engine up: policy={a.policy} F_max={fmax_desc} -> {path}")
    try:
        c.loop_forever()
    except KeyboardInterrupt:
        pass
    finally:
        if telem:
            telem.stop_flag.set()
        log.close()


if __name__ == "__main__":
    main()
