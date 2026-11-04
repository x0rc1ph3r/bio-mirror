#!/usr/bin/env python3
"""Run the complete BIO-Mirror reference/pass/fault demonstration.

This script intentionally has no fixture or synthetic-log fallback. Every trace
used in the demonstration is produced by ``container-run`` in this invocation.
"""

from __future__ import annotations

import argparse
import difflib
import json
import os
import platform
import shlex
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Mapping, Sequence


ROOT = Path(__file__).resolve().parent
ARTIFACT_DIR = ROOT / "artifacts" / "biomirror"
SIM_IMAGE = "bio-sim:latest"
BUILDER_IMAGE = "bio-fw-build:latest"

SMOKE_CONFIG = Path("configs/smoke.jsonc")
REFERENCE_CONFIG = Path("configs/biomirror-blink.jsonc")
FAULT_CONFIG = Path("configs/biomirror-blink-fault.jsonc")
CONTRACT = Path("contracts/blink.json")
REFERENCE_SOURCE = Path("sw/blink_reference/main.c")
FAULT_SOURCE = Path("sw/blink_fault/main.c")
REFERENCE_FIRMWARE = Path("sw/blink_reference/blink_reference.bin")
FAULT_FIRMWARE = Path("sw/blink_fault/blink_fault.bin")


class DemoError(RuntimeError):
    """A setup, simulation, or verification step did not meet the demo contract."""


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Build BIO firmware, run real Baochip BIO RTL through Verilator, "
            "and demonstrate one passing and one fault-detecting comparison."
        )
    )
    parser.add_argument(
        "--skip-build",
        action="store_true",
        help="reuse existing blink_reference.bin and blink_fault.bin artifacts",
    )
    parser.add_argument(
        "--skip-smoke",
        action="store_true",
        help="skip the preliminary configs/smoke.jsonc simulator check",
    )
    parser.add_argument(
        "--engine",
        choices=("docker", "podman"),
        help=(
            "container engine to use (default: CONTAINER_ENGINE, then podman, "
            "then docker)"
        ),
    )
    return parser.parse_args(argv)


def display_path(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(ROOT))
    except ValueError:
        return str(path)


def banner(title: str) -> None:
    print(f"\n{'=' * 72}\n{title}\n{'=' * 72}", flush=True)


def command_text(command: Sequence[str]) -> str:
    return shlex.join(str(part) for part in command)


def choose_engine(requested: str | None) -> str:
    configured = requested or os.environ.get("CONTAINER_ENGINE")
    if configured:
        if configured not in {"docker", "podman"}:
            raise DemoError(
                "CONTAINER_ENGINE must be 'docker' or 'podman' "
                f"(got {configured!r})"
            )
        if shutil.which(configured) is None:
            raise DemoError(f"container engine not found on PATH: {configured}")
        return configured

    for candidate in ("podman", "docker"):
        if shutil.which(candidate) is not None:
            return candidate
    raise DemoError("neither podman nor docker is available on PATH")


def require_files(paths: Sequence[Path]) -> None:
    missing = [display_path(ROOT / path) for path in paths if not (ROOT / path).is_file()]
    if missing:
        raise DemoError("required file(s) missing: " + ", ".join(missing))


def inspect_image(engine: str, image: str) -> str:
    command = [engine, "image", "inspect", image]
    try:
        result = subprocess.run(
            command,
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
            timeout=20,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise DemoError(f"could not inspect container image {image}: {exc}") from exc

    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        setup_hint = (
            "See BIOMIRROR.md for the exact pull/tag/build commands. "
            f"Engine response: {detail or 'no diagnostic'}"
        )
        raise DemoError(f"required container image is unavailable: {image}. {setup_hint}")

    try:
        records = json.loads(result.stdout)
        record = records[0] if isinstance(records, list) else records
        repo_digests = record.get("RepoDigests") or []
        if repo_digests:
            return str(repo_digests[0])
        image_id = record.get("Id") or record.get("ID")
        if image_id:
            return str(image_id)
    except (AttributeError, IndexError, TypeError, json.JSONDecodeError):
        pass
    return image


def run_streamed(
    label: str,
    command: Sequence[str],
    *,
    log_path: Path,
    env: Mapping[str, str] | None = None,
) -> int:
    """Run a command, mirroring merged output to the terminal and a fresh log."""

    print(f"\n[{label}] $ {command_text(command)}", flush=True)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    merged_env = os.environ.copy()
    if env:
        merged_env.update(env)

    try:
        process = subprocess.Popen(
            [str(part) for part in command],
            cwd=ROOT,
            env=merged_env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )
    except OSError as exc:
        raise DemoError(f"could not start {label}: {exc}") from exc

    assert process.stdout is not None
    try:
        with log_path.open("w", encoding="utf-8") as log:
            for line in process.stdout:
                log.write(line)
                log.flush()
                print(line, end="", flush=True)
        return process.wait()
    except KeyboardInterrupt:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
        raise
    finally:
        process.stdout.close()


def require_success(label: str, command: Sequence[str], log_path: Path, **kwargs: object) -> None:
    return_code = run_streamed(label, command, log_path=log_path, **kwargs)
    if return_code != 0:
        raise DemoError(
            f"{label} exited with status {return_code}; see {display_path(log_path)}"
        )


def builder_command(engine: str, module: str) -> list[str]:
    command = [
        engine,
        "run",
        "--rm",
        "-e",
        "ZIG_GLOBAL_CACHE_DIR=/sw/.zig-global-cache",
        "-e",
        "ZIG_LOCAL_CACHE_DIR=/sw/.zig-cache",
    ]
    if engine == "docker" and hasattr(os, "getuid") and hasattr(os, "getgid"):
        command.extend(["--user", f"{os.getuid()}:{os.getgid()}"])
    command.extend(
        [
            "-v",
            f"{ROOT / 'sw'}:/sw",
            BUILDER_IMAGE,
            f"-Dmodule={module}",
            "-Demit-listing=true",
        ]
    )
    return command


def build_firmware(engine: str) -> None:
    for module, output in (
        ("blink_reference", REFERENCE_FIRMWARE),
        ("blink_fault", FAULT_FIRMWARE),
    ):
        require_success(
            f"build {module}",
            builder_command(engine, module),
            ARTIFACT_DIR / f"build-{module}.log",
        )
        firmware_path = ROOT / output
        if not firmware_path.is_file() or firmware_path.stat().st_size == 0:
            raise DemoError(
                f"builder completed but did not produce {display_path(firmware_path)}"
            )


def print_mutation() -> None:
    before = (ROOT / REFERENCE_SOURCE).read_text(encoding="utf-8").splitlines()
    after = (ROOT / FAULT_SOURCE).read_text(encoding="utf-8").splitlines()
    diff = list(
        difflib.unified_diff(
            before,
            after,
            fromfile=str(REFERENCE_SOURCE),
            tofile=str(FAULT_SOURCE),
            lineterm="",
        )
    )
    if not diff:
        raise DemoError("fault source is identical to the reference source")
    banner("DELIBERATE SOURCE MUTATION")
    print("\n".join(diff), flush=True)


def run_simulator(
    engine: str,
    label: str,
    config: Path,
    output: Path,
) -> None:
    command = [str(ROOT / "container-run"), str(config)]
    simulator_env = {"CONTAINER_ENGINE": engine}
    if engine == "docker" and platform.machine().lower() in {"arm64", "aarch64"}:
        simulator_env["DOCKER_DEFAULT_PLATFORM"] = "linux/amd64"
    require_success(
        label,
        command,
        output,
        env=simulator_env,
    )


def comparison_command(
    reference: Path,
    actual: Path,
    *,
    firmware: Path,
    sim_config: Path,
    report: Path,
    container_image: str,
) -> list[str]:
    return [
        sys.executable,
        "-m",
        "biomirror",
        "compare",
        display_path(reference),
        display_path(actual),
        "--contract",
        str(CONTRACT),
        "--strict",
        "--firmware",
        str(firmware),
        "--sim-config",
        str(sim_config),
        "--repo",
        ".",
        "--container-image",
        container_image,
        "--json-out",
        display_path(report),
    ]


def load_report(path: Path) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DemoError(f"could not read report {display_path(path)}: {exc}") from exc
    if not isinstance(value, dict):
        raise DemoError(f"report is not a JSON object: {display_path(path)}")
    return value


def verify_comparisons(container_image: str) -> None:
    reference_log = ARTIFACT_DIR / "reference.log"
    known_good_log = ARTIFACT_DIR / "known-good.log"
    fault_log = ARTIFACT_DIR / "fault.log"
    good_report_path = ARTIFACT_DIR / "known-good-report.json"
    fault_report_path = ARTIFACT_DIR / "fault-report.json"

    for stale_report in (good_report_path, fault_report_path):
        try:
            stale_report.unlink()
        except FileNotFoundError:
            pass

    banner("CHECK 1/2: INDEPENDENT KNOWN-GOOD RUN MUST PASS")
    good_return_code = run_streamed(
        "compare known-good",
        comparison_command(
            reference_log,
            known_good_log,
            firmware=REFERENCE_FIRMWARE,
            sim_config=REFERENCE_CONFIG,
            report=good_report_path,
            container_image=container_image,
        ),
        log_path=ARTIFACT_DIR / "known-good-report.txt",
    )
    if good_return_code != 0:
        raise DemoError(
            "independent known-good run did not pass "
            f"(comparator status {good_return_code})"
        )
    good_report = load_report(good_report_path)
    if good_report.get("status") != "pass":
        raise DemoError("known-good comparator exited 0 but report status was not pass")

    banner("CHECK 2/2: DELIBERATE FAULT MUST FAIL AT A REAL DIVERGENCE")
    fault_return_code = run_streamed(
        "compare fault",
        comparison_command(
            reference_log,
            fault_log,
            firmware=FAULT_FIRMWARE,
            sim_config=FAULT_CONFIG,
            report=fault_report_path,
            container_image=container_image,
        ),
        log_path=ARTIFACT_DIR / "fault-report.txt",
    )
    if fault_return_code == 0:
        raise DemoError("deliberate fault unexpectedly passed")
    if fault_return_code != 1:
        raise DemoError(
            "fault comparison failed as an infrastructure/input error instead of a "
            f"behavioral divergence (status {fault_return_code})"
        )

    fault_report = load_report(fault_report_path)
    comparison = fault_report.get("comparison")
    if (
        fault_report.get("status") != "fail"
        or not isinstance(comparison, dict)
        or comparison.get("first_divergence") is None
    ):
        raise DemoError("fault report did not contain a first trace divergence")


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    os.chdir(ROOT)

    required = [
        Path("container-run"),
        SMOKE_CONFIG,
        REFERENCE_CONFIG,
        FAULT_CONFIG,
        CONTRACT,
        REFERENCE_SOURCE,
        FAULT_SOURCE,
        Path("sw/Dockerfile"),
    ]
    if args.skip_build:
        required.extend([REFERENCE_FIRMWARE, FAULT_FIRMWARE])

    try:
        require_files(required)
        engine = choose_engine(args.engine)
        sim_image_provenance = inspect_image(engine, SIM_IMAGE)
        if not args.skip_build:
            inspect_image(engine, BUILDER_IMAGE)

        ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
        banner("BIO-MIRROR: REAL RTL BEHAVIORAL REGRESSION DEMO")
        print(f"Repository:       {ROOT}")
        print(f"Container engine: {engine}")
        print(f"Simulator image:  {sim_image_provenance}")
        print(f"Artifacts:        {display_path(ARTIFACT_DIR)}")
        print("Fixture fallback: disabled")

        if args.skip_build:
            banner("FIRMWARE BUILD SKIPPED")
            print("Reusing the two existing firmware binaries requested by --skip-build.")
        else:
            banner("BUILD REAL BIO FIRMWARE")
            build_firmware(engine)

        print_mutation()

        if args.skip_smoke:
            banner("SMOKE TEST SKIPPED")
            print("The preliminary RTL smoke test was skipped by explicit request.")
        else:
            banner("SMOKE TEST: VERILATED BIO RTL")
            run_simulator(
                engine,
                "RTL smoke test",
                SMOKE_CONFIG,
                ARTIFACT_DIR / "smoke.log",
            )

        banner("CAPTURE 1/3: BASELINE REFERENCE TRACE")
        run_simulator(
            engine,
            "reference capture",
            REFERENCE_CONFIG,
            ARTIFACT_DIR / "reference.log",
        )
        banner("CAPTURE 2/3: INDEPENDENT KNOWN-GOOD TRACE")
        run_simulator(
            engine,
            "known-good capture",
            REFERENCE_CONFIG,
            ARTIFACT_DIR / "known-good.log",
        )
        banner("CAPTURE 3/3: FAULT-INJECTED TRACE")
        run_simulator(
            engine,
            "fault capture",
            FAULT_CONFIG,
            ARTIFACT_DIR / "fault.log",
        )

        verify_comparisons(sim_image_provenance)

        banner("DEMO SUCCESS")
        print("PASS: an independent known-good RTL run matched the baseline.")
        print("EXPECTED FAIL: the deliberate timing mutation was rejected.")
        print("The fault report contains the exact first divergent GPIO event.")
        print(f"Reports and raw traces: {display_path(ARTIFACT_DIR)}")
        return 0
    except DemoError as exc:
        print(f"\nBIO-Mirror demo ERROR: {exc}", file=sys.stderr)
        print("No fixture traces were substituted.", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("\nBIO-Mirror demo interrupted; no fixture traces were substituted.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
