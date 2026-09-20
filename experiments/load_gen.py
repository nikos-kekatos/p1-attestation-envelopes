#!/usr/bin/env python3
"""Graded, calibrated channel load. RUNS ON THE RIG.

NOT YET EXECUTED END TO END. Reviewed and corrected, but the Mosquitto broker
and the rig segment are not available here. See README.md.

Supersedes noise_generator_mqtt_network.py in one respect that matters: it
reports MEASURED delivery rather than a generator setting. "100% load" in the
pilot data is a knob position, not a measurement, and a reviewer will say so.

What the fields mean, because the distinction is the whole point of this file:

  offered_*    the target: what was asked for on the command line.
  published_*  publish() call rate achieved by the sending loop. This is still
               an OFFERED figure. paho's publish() hands the message to a
               client-side queue and returns; it does not mean the message
               reached the broker, let alone a subscriber. An earlier version
               of this file reported exactly this number as "achieved", so its
               achieved throughput was offered throughput under another name
               and could not have shown saturation at all.
  received_*   messages a SEPARATE subscribed client actually got back through
               the broker. This is the measured figure and the one to report.
               It is a LOWER bound: the receiving client's own loop can fall
               behind at high rates and QoS 0 delivery is at-most-once by
               design, so received < published is exactly the saturation signal
               being looked for, but part of it may be receiver-side.
  delivery_ratio = received_pps / published_pps.

--calibrate ramps the offered rate and reports the knee, which is what
--saturation-pps then wants as its argument.
"""
import argparse, json, os, subprocess, threading, time

HERE = os.path.dirname(os.path.abspath(__file__))

LEVELS = (0, 25, 50, 75, 100)   # percent of the calibrated saturation point

# The original flooder used 1 KB messages (noise_generator_mqtt_network.py:12).
# A different payload size is a different offered bit rate at the same packet
# rate, so it is not comparable with the pilot's loaded arm.
DEFAULT_PAYLOAD = 1024
DEFAULT_TOPIC = "v2x/network/noise"


def make_client(client_id):
    """paho-mqtt 2.0 requires a CallbackAPIVersion and rejects the client_id-only
    constructor with a TypeError. Same guard the thesis scripts use; it also
    pins the v1 callback signatures used below."""
    import paho.mqtt.client as mqtt
    try:
        return mqtt.Client(mqtt.CallbackAPIVersion.VERSION1, client_id)
    except AttributeError:
        return mqtt.Client(client_id)


def rtt_probe(host, n, out):
    try:
        r = subprocess.run(["ping", "-c", str(n), "-i", "0.2", host],
                           capture_output=True, text=True, timeout=n * 0.4 + 10)
        for line in r.stdout.splitlines():
            if "min/avg/max" in line:
                out["rtt_ms"] = line.split("=")[1].strip().split("/")[:4]
    except Exception as e:
        out["rtt_error"] = str(e)


class Counter:
    """A second client, subscribed to the noise topic, that counts what the
    broker actually delivers. Runs in paho's own network thread."""

    def __init__(self, broker, topic, client_id="load_counter"):
        self.topic = topic
        self.n = 0
        self.bytes = 0
        self.ready = threading.Event()
        self.c = make_client(client_id)
        self.c.on_connect = self._on_connect
        self.c.on_message = self._on_message
        self.c.connect(broker, 1883, 60)
        self.c.loop_start()

    def _on_connect(self, client, _ud, _flags, rc):
        if rc != 0:
            print(f"[counter] connect failed rc={rc}")
            return
        client.subscribe(self.topic, qos=0)   # subscriptions stay at QoS 0
        self.ready.set()

    def _on_message(self, _c, _u, msg):
        self.n += 1
        self.bytes += len(msg.payload)

    def wait(self, timeout=10.0):
        return self.ready.wait(timeout)

    def stop(self):
        self.c.loop_stop()
        self.c.disconnect()


def flood(broker, topic, payload_bytes, target_pps, duration, counter=None):
    """Publish at target_pps for `duration` seconds and report what happened."""
    import paho.mqtt.client as mqtt
    c = make_client(f"load_{target_pps}")
    c.connect(broker, 1883, 60)
    c.loop_start()
    blob = b"x" * payload_bytes

    n0 = counter.n if counter else 0
    b0 = counter.bytes if counter else 0
    sent = dropped = 0
    t0 = time.monotonic()
    gap = (1.0 / target_pps) if target_pps else 0.0
    while time.monotonic() - t0 < duration and target_pps:
        info = c.publish(topic, blob, qos=0)
        if info.rc != mqtt.MQTT_ERR_SUCCESS:
            dropped += 1
        sent += 1
        nxt = t0 + sent * gap
        # Pace with sleep, spin only the last millisecond. The previous version
        # busy-waited the whole gap, so it burned a core hardest at the LOWEST
        # load levels -- 100% of a CPU at level 25 and much less at level 100 --
        # which puts a host-CPU confound in the control arm, pointing the wrong
        # way, on a measurement whose entire purpose is a load effect.
        while True:
            slack = nxt - time.monotonic()
            if slack <= 0:
                break
            if slack > 0.002:
                time.sleep(slack - 0.001)
            else:
                time.sleep(0)      # yield; sub-millisecond residue
    el = time.monotonic() - t0
    c.loop_stop(); c.disconnect()

    # let the tail drain before reading the counter
    if counter:
        time.sleep(0.5)
    recv = (counter.n - n0) if counter else None
    recv_b = (counter.bytes - b0) if counter else None

    pub_pps = sent / el if el else 0.0
    out = {"offered_pps": target_pps,
           "offered_mbps": target_pps * payload_bytes * 8 / 1e6,
           "published_pps": pub_pps,
           "published_mbps": pub_pps * payload_bytes * 8 / 1e6,
           "published_packets": sent,
           "publish_rc_failures": dropped,
           "payload_bytes": payload_bytes, "duration_s": el}
    if recv is not None:
        r_pps = recv / el if el else 0.0
        out.update({"received_packets": recv, "received_pps": r_pps,
                    "received_mbps": recv_b * 8 / 1e6 / el if el else 0.0,
                    "delivery_ratio": (r_pps / pub_pps) if pub_pps else None})
    else:
        out["received_packets"] = None
        out["note"] = ("no subscriber: every throughput figure here is OFFERED, "
                       "not measured")
    return out


def calibrate(broker, topic, payload, step_s, rates):
    """Ramp the offered rate and report where delivery stops tracking it.

    The saturation point is the first offered rate at which the measured
    delivery ratio falls below 0.95. Report the rate BELOW the knee as
    --saturation-pps, so level 100 sits at the edge rather than past it.
    """
    counter = Counter(broker, topic)
    if not counter.wait():
        counter.stop()
        raise SystemExit("calibration needs a subscriber; none connected")
    rows, knee = [], None
    print(f"{'offered_pps':>12}{'published_pps':>15}{'received_pps':>14}{'ratio':>8}")
    for r in rates:
        s = flood(broker, topic, payload, r, step_s, counter)
        rows.append(s)
        ratio = s.get("delivery_ratio")
        print(f"{r:12d}{s['published_pps']:15.1f}{s['received_pps']:14.1f}"
              f"{(ratio if ratio is not None else float('nan')):8.3f}")
        if knee is None and ratio is not None and ratio < 0.95:
            knee = r
    counter.stop()
    return rows, knee


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--broker", default="127.0.0.1")
    ap.add_argument("--topic", default=DEFAULT_TOPIC)
    ap.add_argument("--saturation-pps", type=int, default=None,
                    help="the measured saturation packet rate. Obtain it with "
                         "--calibrate first; it is required unless --calibrate "
                         "is given.")
    ap.add_argument("--level", type=int, choices=LEVELS, default=None,
                    help="percent of --saturation-pps; required unless --calibrate")
    ap.add_argument("--payload", type=int, default=DEFAULT_PAYLOAD,
                    help="bytes per message; the original flooder used 1024 and "
                         "a different size is a different offered bit rate")
    ap.add_argument("--duration", type=float, default=60.0)
    ap.add_argument("--no-subscriber", action="store_true",
                    help="skip the counting subscriber. Every throughput figure "
                         "is then OFFERED only and must not be reported as "
                         "achieved.")
    ap.add_argument("--calibrate", action="store_true",
                    help="ramp the offered rate and report the saturation knee")
    ap.add_argument("--calibrate-rates", type=int, nargs="+",
                    default=[500, 1000, 2000, 4000, 8000, 16000, 32000])
    ap.add_argument("--calibrate-step", type=float, default=10.0,
                    help="seconds per rung of the calibration ramp")
    ap.add_argument("--out", default=os.path.join(HERE, "results"))
    ap.add_argument("--run-id", default="load")
    a = ap.parse_args()

    os.makedirs(a.out, exist_ok=True)

    if a.calibrate:
        rows, knee = calibrate(a.broker, a.topic, a.payload, a.calibrate_step,
                               a.calibrate_rates)
        p = os.path.join(a.out, f"{a.run_id}_calibration.json")
        json.dump({"rows": rows, "knee_pps": knee, "payload_bytes": a.payload},
                  open(p, "w"), indent=2)
        print(f"\nsaturation knee: {knee} pps (delivery ratio first below 0.95)")
        print(f"use the rung BELOW it as --saturation-pps.  wrote {p}")
        return

    if a.saturation_pps is None or a.level is None:
        ap.error("--saturation-pps and --level are required unless --calibrate")

    counter = None
    if not a.no_subscriber:
        counter = Counter(a.broker, a.topic)
        if not counter.wait():
            print("[warn] counting subscriber never connected; figures will be "
                  "OFFERED only")
            counter.stop()
            counter = None

    stats = {"level_percent": a.level, "saturation_pps": a.saturation_pps}
    probe = {}
    t = threading.Thread(target=rtt_probe,
                         args=(a.broker, int(a.duration / 0.2), probe))
    t.start()
    stats.update(flood(a.broker, a.topic, a.payload,
                       a.saturation_pps * a.level // 100, a.duration, counter))
    t.join()
    stats.update(probe)
    if counter:
        counter.stop()

    p = os.path.join(a.out, f"{a.run_id}_load_{a.level}.json")
    json.dump(stats, open(p, "w"), indent=2)
    print(json.dumps(stats, indent=2))
    print(f"\nwrote {p}")
    print("Report received_pps / received_mbps in the paper. offered_* is the "
          "knob position and published_* is still offered, one layer down.")


if __name__ == "__main__":
    main()
