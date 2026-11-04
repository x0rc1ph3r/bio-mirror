#!/usr/bin/env python3
"""Build and detect an isolated one-line BIO RTL mutation.

The reference workspace and ``bio-sim:latest`` image are never modified. The
script copies only the simulator build context to a temporary directory, applies
one GPIO-21 polarity mutation, and builds a separately tagged image.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Sequence

from demo_biomirror import (
    ARTIFACT_DIR,
    CONTRACT,
    REFERENCE_CONFIG,
    REFERENCE_FIRMWARE,
    ROOT,
    DemoError,
    banner,
    choose_engine,
    comparison_command,
    display_path,
    inspect_image,
    load_report,
    require_files,
    require_success,
    run_streamed,
)


MUTANT_IMAGE = "bio-sim:rtl-gpio21-invert"
RTL_FILE = Path("rtl/bio_bdma.sv")
PATCH_FILE = Path("mutations/gpio21-invert.patch")
REFERENCE_LOG = ARTIFACT_DIR / "reference.log"
MUTANT_LOG = ARTIFACT_DIR / "rtl-fault.log"
MUTANT_REPORT = ARTIFACT_DIR / "rtl-fault-report.json"

ORIGINAL_ASSIGNMENT = (
    b"assign bio_gpio[gp].po = gpio_out[gp] ^ out_invert[gp];"
)
MUTATED_ASSIGNMENT = (
    b"assign bio_gpio[gp].po = gpio_out[gp] ^ out_invert[gp] ^ (gp == 21);"
)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Build a separately tagged one-line RTL mutant, run the unchanged "
            "reference firmware, and prove BIO-Mirror detects the state divergence."
        )
    )
    parser.add_argument(
        "--skip-build",
        action="store_true",
        help=f"reuse the existing {MUTANT_IMAGE} image",
    )
    parser.add_argument(
        "--engine",
        choices=("docker", "podman"),
        help="container engine (default: CONTAINER_ENGINE, then podman, then docker)",
    )
    return parser.parse_args(argv)


def _platform_args(engine: str) -> list[str]:
    if engine == "docker" and platform.machine().lower() in {"arm64", "aarch64"}:
        return ["--platform", "linux/amd64"]
    return []


def _copy_and_mutate(build_context: Path) -> None:
    for filename in ("Dockerfile", "Makefile", "rtl.f"):
        shutil.copy2(ROOT / filename, build_context / filename)
    shutil.copytree(ROOT / "rtl", build_context / "rtl")
    shutil.copytree(ROOT / "sim", build_context / "sim")

    target = build_context / RTL_FILE
    source = target.read_bytes()
    occurrences = source.count(ORIGINAL_ASSIGNMENT)
    if occurrences != 1:
        raise DemoError(
            f"expected exactly one RTL mutation target, found {occurrences} in {RTL_FILE}"
        )
    target.write_bytes(source.replace(ORIGINAL_ASSIGNMENT, MUTATED_ASSIGNMENT, 1))


def build_mutant(engine: str) -> None:
    with tempfile.TemporaryDirectory(prefix="biomirror-rtl-mutant-") as temporary:
        build_context = Path(temporary)
        _copy_and_mutate(build_context)
        command = [engine, "build", *_platform_args(engine), "-t", MUTANT_IMAGE, str(build_context)]
        require_success(
            "build isolated RTL mutant",
            command,
            ARTIFACT_DIR / "build-rtl-mutant.log",
        )


def run_mutant(engine: str) -> None:
    command = [engine, "run", "--rm", *_platform_args(engine)]
    if engine == "docker" and hasattr(os, "getuid") and hasattr(os, "getgid"):
        command.extend(["--user", f"{os.getuid()}:{os.getgid()}"])
    command.extend(
        [
            "-v",
            f"{ROOT}:/work",
            MUTANT_IMAGE,
            str(REFERENCE_CONFIG),
        ]
    )
    require_success("run unchanged firmware on RTL mutant", command, MUTANT_LOG)


def verify_mutant(image_identity: str) -> None:
    try:
        MUTANT_REPORT.unlink()
    except FileNotFoundError:
        pass

    command = comparison_command(
        REFERENCE_LOG,
        MUTANT_LOG,
        firmware=REFERENCE_FIRMWARE,
        sim_config=REFERENCE_CONFIG,
        report=MUTANT_REPORT,
        container_image=image_identity,
    )
    return_code = run_streamed(
        "compare RTL mutant",
        command,
        log_path=ARTIFACT_DIR / "rtl-fault-report.txt",
    )
    if return_code != 1:
        raise DemoError(
            "RTL-mutant comparison must return behavioral-mismatch status 1 "
            f"(observed {return_code})"
        )

    report = load_report(MUTANT_REPORT)
    comparison = report.get("comparison")
    divergence = comparison.get("first_divergence") if isinstance(comparison, dict) else None
    if (
        report.get("status") != "fail"
        or not isinstance(divergence, dict)
        or divergence.get("kind") != "transition"
        or divergence.get("event_index") != 1
    ):
        raise DemoError("RTL mutant did not produce the expected event-1 transition divergence")

    provenance = report.get("provenance")
    files = provenance.get("files") if isinstance(provenance, dict) else None
    firmware = files.get("firmware") if isinstance(files, dict) else None
    firmware_hash = firmware.get("sha256") if isinstance(firmware, dict) else None
    if not isinstance(firmware_hash, str):
        raise DemoError("RTL-mutant report did not record the unchanged firmware hash")

    banner("RTL MUTATION DEMO SUCCESS")
    print("EXPECTED FAIL: unchanged firmware diverged only after the RTL mutation.")
    print("First divergence: gpio_out[21] event 1, expected 0->1, observed 1->0.")
    print(f"Firmware SHA-256: {firmware_hash}")
    print(f"Raw trace/report: {display_path(MUTANT_LOG)}, {display_path(MUTANT_REPORT)}")


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    os.chdir(ROOT)
    try:
        require_files(
            [
                Path("Dockerfile"),
                Path("Makefile"),
                Path("rtl.f"),
                RTL_FILE,
                PATCH_FILE,
                REFERENCE_CONFIG,
                REFERENCE_FIRMWARE,
            ]
        )
        if not REFERENCE_LOG.is_file():
            raise DemoError(
                "baseline trace is missing; run ./demo_biomirror.py first"
            )

        engine = choose_engine(args.engine)
        ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
        banner("BIO-MIRROR: ISOLATED RTL MUTATION STRETCH DEMO")
        print(f"Reference RTL:  {ROOT / RTL_FILE} (left unchanged)")
        print(f"Mutation patch: {ROOT / PATCH_FILE}")
        print(f"Mutant image:   {MUTANT_IMAGE}")
        print("Firmware:       unchanged blink_reference binary")

        if not args.skip_build:
            build_mutant(engine)
        image_identity = inspect_image(engine, MUTANT_IMAGE)
        run_mutant(engine)
        verify_mutant(image_identity)
        return 0
    except DemoError as exc:
        print(f"\nBIO-Mirror RTL mutation demo ERROR: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("\nBIO-Mirror RTL mutation demo interrupted.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
