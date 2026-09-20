# Provenance

Every file in this repository was copied unmodified from the paper's working
tree. Nothing was edited in the move. Original paths are relative to
`papers/paper_mesas/` in the `rv-3-layer` research repository.

| Here | Original |
|---|---|
| `experiments/*.py` | `paper1_dtloop/experiments/*.py` |
| `experiments/requirements.txt` | `paper1_dtloop/experiments/requirements.txt` |
| `experiments/HARNESS_NOTES.md` | `paper1_dtloop/experiments/README.md` |
| `experiments/results/*` | `paper1_dtloop/experiments/results/*` |
| `Thesis/0_overhead-delay_stats_rest_api_without_channel_commotion/*` | same path under `paper1_dtloop/Thesis/` |
| `Thesis/100percent_overhead-delay_stats_rest_api/*` | same path under `paper1_dtloop/Thesis/` |
| `rig_reference/carla_v2i_mqtt_master.py` | `paper1_dtloop/Thesis/carla_v2i_mqtt_master.py` |
| `rig_reference/esp32_attestation_obu.c` | `paper1_dtloop/Thesis/esp32_attestation_obu.c` |
| `rig_reference/fedora_rsu_verifier.py` | `paper1_dtloop/Thesis/fedora_rsu_verifier.py` |
| `rig_reference/fedora_final.py` | `paper1_dtloop/Thesis/fedora_final.py` |
| `rig_reference/fedora_final_journal.py` | `paper1_dtloop/Thesis/fedora_final_journal.py` |

Written for this repository, not copied: `README.md`, `run_all.sh`,
`CITATION.cff`, `LICENSE`, `PROVENANCE.md`, `.gitignore`.

## Deliberately excluded

- `paper1_dtloop/Thesis/esp32_v2x_obu.c` — contains a hardcoded Wi-Fi SSID and
  password on lines 7 and 8. Excluded rather than redacted. Strip the
  credentials at source before adding it.
- The rest of `paper1_dtloop/Thesis/` (~215 MB: ESP32 build trees, video,
  screenshots, the thesis PDF). Only the two pilot-data directories the analysis
  scripts read, and the rig sources the paper names, were taken.
- `paper1_dtloop/Makefile`, `build_anon.py`, `variants/` — LaTeX build
  machinery, not experiment code.

## Integrity check performed

All nine files in `experiments/results/` were regenerated from the pilot data by
re-running `reanalyse_pilot.py`, `envelope.py`, `interval_sweep.py` and
`boundary.py`, and compared byte-for-byte against the copies that were already
there. All nine matched, so the committed results are a faithful product of the
committed code and data.
