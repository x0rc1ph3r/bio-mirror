# BIO-Mirror

BIO-Mirror is a behavioral regression harness for the Baochip BIO subsystem. It
executes real BIO firmware on the repository's Verilated RTL, turns monitor
records into canonical GPIO events, checks a declarative behavior contract, and
reports the first event where a candidate run diverges from a known-good run.

The demonstration proves two things in one command:

1. A fresh, independent run of the known-good firmware passes.
2. A firmware copy with one deliberately shortened GPIO pulse fails at the
   exact first divergent transition.

There is no recorded-fixture fallback. A simulator, firmware build, or checker
failure stops the demo and leaves the partial logs for diagnosis.

## What BIO-Mirror adds

The official `bio-sim` project already supplies the essential execution layer:
real BIO RTL, firmware loading, GPIO monitors, deterministic cycle counts, and
FST waveforms. BIO-Mirror deliberately builds on that work instead of replacing
it.

| Existing Baochip tool | Existing capability | BIO-Mirror layer |
| --- | --- | --- |
| `bio-sim` | Execute firmware on Verilated BIO RTL and emit monitor/FST traces | Normalize observations, compare a candidate with a golden run, and locate the first divergence |
| BIO-surfer / GTKWave | Inspect a waveform visually | Produce deterministic terminal and JSON verdicts suitable for regression testing |
| `bio-loader` | Load a `bio-sim` `.bin` onto a Dabao | Future backend: evaluate captured physical-pad transitions with the same contract |

The current public Baochip tools do not document a golden-trace comparator,
declarative GPIO invariants, mutation-test orchestration, or first-divergence
reporting. An adjacent project, `baobit`, verifies reproducible boot firmware by
hash; it does not compare runtime BIO behavior.

## One-time setup: Docker on an Arm64 Mac

Run these commands from the repository root. Baochip's published simulator
image is currently an `linux/amd64` image, so request that platform explicitly:

```bash
docker pull --platform linux/amd64 ghcr.io/baochip/bio-sim:latest
docker tag ghcr.io/baochip/bio-sim:latest bio-sim:latest

docker build \
  --tag bio-fw-build:latest \
  --file sw/Dockerfile \
  sw
```

Only the simulator image needs x86-64 emulation. Build the firmware-builder
image natively on the Arm64 Mac; do not give that `docker build` command an
`linux/amd64` platform override.

Confirm that both local tags exist and that Docker can run the simulator image:

```bash
docker image inspect bio-sim:latest --format '{{.Id}}'
docker image inspect bio-fw-build:latest --format '{{.Id}}'
CONTAINER_ENGINE=docker ./container-run configs/smoke.jsonc
```

The end-to-end demo was verified against simulator image digest
`sha256:05dc63ee0b8fd1371363474871d46b238c415b60f2efb6b07f1444b6ab35af68`.
The runner records the locally resolved repository digest or image ID in each
comparison report. Because `latest` is a mutable tag, save the value printed by
`docker image inspect` with demo results.

Podman is also supported. Use the same pull, tag, and build sequence with
`podman` in place of `docker`. The runner auto-detects Podman first and Docker
second; `--engine docker`, `--engine podman`, or `CONTAINER_ENGINE` selects one
explicitly.

## Run the complete demonstration

```bash
./demo_biomirror.py
```

The script performs, in order:

1. Preflight checks for the simulator and firmware-builder images.
2. Containerized builds of `sw/blink_reference` and `sw/blink_fault`.
3. `configs/smoke.jsonc` against the real Verilated BIO RTL.
4. Three independent RTL runs: baseline reference, known-good candidate, and
   fault candidate.
5. A known-good comparison that must pass.
6. A fault comparison that must return a behavioral mismatch and include a
   first trace divergence.

For a faster repeat after a successful build and smoke run:

```bash
./demo_biomirror.py --skip-build --skip-smoke
```

Those flags skip only the named setup steps. The three demonstration traces are
always generated afresh; the runner never substitutes fixture logs. Overall
exit status is `0` only when the known-good check passes and the injected fault
is rejected. Setup, simulator, malformed-input, and missing-divergence failures
return `2`.

## Expected terminal story

First, the runner prints the real source mutation. The important line is:

```diff
-        for(int i = 0; i < 10; i++) {
+        for(int i = 0; i < 5; i++) {
```

The independent known-good trace must produce output shaped like:

```text
BIO-Mirror PASS
Golden comparison: PASS (13/13 events, tolerance +/-4 cycles)
Contracts: PASS (6/6 passed)
```

The deliberately mutated trace must then produce output shaped like:

```text
BIO-Mirror FAIL
Golden comparison: FAIL (17/13 events, tolerance +/-4 cycles)
First divergence: event 2 [timing_early] gpio_out[21] 1->0 was 70 cycles early
  expected: gpio_out[21] 1->0 (raw=202, rel=144, interval=144 cycles)
  actual:   gpio_out[21] 1->0 (raw=132, rel=74, interval=74 cycles)
  interval difference: -70 cycles, -48.6%
Contracts: FAIL (4/6 passed)
```

That comparator exit status is intentionally nonzero. The demo runner verifies
that it is specifically status `1` (a behavioral failure), verifies that the
JSON contains a first trace divergence, labels it **EXPECTED FAIL**, and then
exits successfully.

## Generated evidence

Everything is written beneath `artifacts/biomirror/`:

| Artifact | Purpose |
| --- | --- |
| `smoke.log` | Preliminary RTL smoke run |
| `build-blink_reference.log` | Known-good firmware build transcript |
| `build-blink_fault.log` | Mutated firmware build transcript |
| `reference.log` | Baseline Verilator monitor output |
| `known-good.log` | Independent known-good monitor output |
| `fault.log` | Fault-injected monitor output |
| `known-good-report.txt` | Human-readable passing report |
| `known-good-report.json` | Machine-readable passing report and provenance |
| `fault-report.txt` | Human-readable expected-failure report |
| `fault-report.json` | Machine-readable failure, context, and provenance |

The JSON reports hash the traces, contract, firmware binary, and simulator
configuration. They also record the Git revision/dirty state, BIO-Mirror
version, host Python runtime, and resolved simulator image identity.

## Use the CLI directly

Inspect the normalized GPIO event stream:

```bash
python3 -m biomirror normalize \
  artifacts/biomirror/reference.log \
  --signal gpio_out \
  --pin 21
```

Re-run the passing comparison:

```bash
python3 -m biomirror compare \
  artifacts/biomirror/reference.log \
  artifacts/biomirror/known-good.log \
  --contract contracts/blink.json \
  --strict \
  --firmware sw/blink_reference/blink_reference.bin \
  --sim-config configs/biomirror-blink.jsonc \
  --repo . \
  --container-image bio-sim:latest \
  --json-out artifacts/biomirror/known-good-report.json
```

Re-run the expected failing comparison:

```bash
python3 -m biomirror compare \
  artifacts/biomirror/reference.log \
  artifacts/biomirror/fault.log \
  --contract contracts/blink.json \
  --strict \
  --firmware sw/blink_fault/blink_fault.bin \
  --sim-config configs/biomirror-blink-fault.jsonc \
  --repo . \
  --container-image bio-sim:latest \
  --json-out artifacts/biomirror/fault-report.json
```

CLI exit codes are `0` for conformance, `1` for a behavior mismatch, and `2`
for invalid input or an execution error.

## How the prototype fits together

```text
blink_reference/main.c ─┐
                        ├─ firmware builder ─ .bin ─┐
blink_fault/main.c ─────┘                            │
                                                    v
JSONC simulator config ──────────────> bio-sim / Verilated BIO RTL
                                                    │
                                                    v
                                          raw [mon] GPIO records
                                                    │
                         ┌──────────────────────────┴───────────────┐
                         v                                          v
                canonical event stream                   blink.json invariants
                         └──────────────────────────┬───────────────┘
                                                    v
                                    first-divergence + JSON provenance
```

Normalization selects `gpio_out[21]`, anchors time to the first selected event,
and preserves the transition sequence and inter-event timing. Comparison checks
the expected transition and normalized cycle position with the contract's
timing tolerance. The same contract also checks alternation, high/low pulse
widths, minimum interval, edge count, and allowed pins.

## Two-minute live demo

- **0:00-0:12** — “BIO-Mirror checks observable behavior of real BIO firmware
  running on Baochip's Verilated RTL.”
- **0:12-0:35** — Start `./demo_biomirror.py --skip-build --skip-smoke`; point
  out that it launches three fresh simulator runs.
- **0:35-0:50** — Show the printed one-line `10` to `5` loop mutation.
- **0:50-1:15** — Point to the independent known-good `PASS` and contract pass.
- **1:15-1:42** — Point to the fault `FAIL`, exact GPIO transition, timing delta,
  expected event, and actual event.
- **1:42-2:00** — “Today both traces come from RTL simulation. A physical GPIO
  capture adapter can emit the same canonical events for RTL-versus-silicon
  differential testing.”

## Optional stretch: identical firmware, mutated RTL

After the primary demo is working, BIO-Mirror can also prove that it detects a
real RTL mutation while keeping the firmware and simulator configuration
identical:

```bash
./demo_rtl_mutation.py --engine docker
```

This creates a temporary simulator build context, applies the transparent
one-line patch in `mutations/gpio21-invert.patch`, and builds the separate image
`bio-sim:rtl-gpio21-invert`. Neither the checked-out RTL nor `bio-sim:latest` is
changed. The mutation inverts only the external value of GPIO 21:

```diff
-assign bio_gpio[gp].po = gpio_out[gp] ^ out_invert[gp];
+assign bio_gpio[gp].po = gpio_out[gp] ^ out_invert[gp] ^ (gp == 21);
```

The unchanged reference firmware then fails immediately at GPIO event 1:

```text
First divergence: event 1 [transition]
expected gpio_out[21] 0->1, observed 1->0
```

This is a controlled pad-polarity RTL mutation used to validate the harness,
not a discovered Baochip defect. Reuse the already-built mutant image with
`--skip-build`; the primary two-minute presentation should still lead with the
faster firmware-regression demonstration.

## Dabao hardware path

The physical-board backend is the next milestone, not a feature claimed by this
prototype. Baochip's loader accepts the same `.bin` built for `bio-sim`, so the
firmware artifact can be held constant across RTL and silicon runs.

One board-specific change is required before using the included blink example:
the upstream simulator example toggles BIO/GPIO 21, but BIO21 is not exposed on
the documented Dabao headers. A practical board variant should use an exposed
pad, for example:

| Layer | Board-demo value |
| --- | --- |
| Firmware GPIO constant | `19` |
| BIO signal | `BIO19` |
| Dabao pad label | `PC3` |
| Header position | physical pin 10 |
| Loader selection | `--pads PC3` |

The corresponding simulator config and behavior contract must also select pin
19. Confirm the current board revision and pad mapping before wiring hardware.

A defensible RTL-versus-silicon flow is:

1. Run the pin-19 firmware in `bio-sim` at a declared BIO quantum and save the
   canonical reference trace.
2. Load the identical binary onto Dabao with `bio-loader` and the same quantum.
3. Capture PC3 transitions with an external logic analyzer or oscilloscope and
   export timestamped edges as CSV.
4. Convert physical timestamps into BIO-Mirror's canonical event schema,
   preserving the raw measurements and capture metadata.
5. Compare transition order and calibrated pulse durations with a tolerance
   that accounts for capture resolution and clock variation.
6. Run the deliberate firmware mutation as a negative control and require the
   physical backend to identify its first divergent edge.

An external capture is preferable to having the BIO subsystem measure itself:
it keeps the observer independent of the device under test. Until that capture
adapter exists, describe the project as BIO RTL behavioral regression with a
credible physical-silicon extension.

## Automated validation

The Python checker has no third-party runtime dependencies. Run its complete
test suite with:

```bash
python3 -m unittest discover -v
python3 -m compileall -q biomirror demo_biomirror.py demo_rtl_mutation.py
```

`.github/workflows/biomirror-tests.yml` runs the same checks on supported Python
versions for pushes and pull requests. The full Verilator demonstration remains
a deliberate local integration test because it requires container images and,
on Arm64 hosts, x86-64 emulation.

## Provenance and license

This project is maintained as an extension of Baochip's Apache-2.0-licensed
`bio-sim` repository. The starting upstream revision for this prototype is:

```text
https://github.com/baochip/bio-sim.git
7e9c9615e1c900c7ddae1c4645a87078b217abc3
```

The existing `LICENSE` file applies to this repository. Generated firmware,
waveforms, reports, build caches, and local evidence under `artifacts/` are not
committed; the source, contracts, tests, configs, and mutation descriptions are
committed so another developer can reproduce them.

## Precise scope and limitations

BIO-Mirror currently provides trace-based behavioral regression testing for a
selected, observable BIO GPIO behavior. The fault in this demo is an intentional
firmware timing change. It is not a discovered Baochip RTL defect or hardware
vulnerability.

A pass establishes only that this firmware/configuration's selected GPIO event
sequence and timing satisfy the reference and contract. It does not prove the
whole processor correct, replace formal verification, exercise all four BIO
cores, or establish equivalence with fabricated silicon. There is not yet a
physical-capture backend. The credible next step is to map physical GPIO or BIO
execution observations into the same canonical event schema and compare them
with the simulator trace.
