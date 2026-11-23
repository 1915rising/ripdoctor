#!/usr/bin/env python3
"""
Rip Doctor test suite.

Builds the synthetic fixture (tests/make_synthetic_fixture.py), which plants
a known set of defects in a fake AssetRipper export, then runs ripdoctor.py
on it and asserts that every planted defect is caught and that the one
healthy material is left alone.

Planted defects:
  * a dummy (stub) shader with 3 materials on it        -> FAIL
  * a material whose primary map slot is empty          -> WARN
  * a material referencing a texture absent from export -> FAIL
  * a zero-byte texture file                            -> reported

Usage: python3 tests/run_tests.py
Exit code 0 = all assertions passed, 1 = at least one failed.
"""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FAILURES = []


def check(label, ok, detail=""):
    print(f"{'PASS' if ok else 'FAIL'}  {label}" + (f" — {detail}" if detail else ""))
    if not ok:
        FAILURES.append(label)


def main():
    with tempfile.TemporaryDirectory() as tmp:
        fixture = Path(tmp) / "synthetic-export"
        r = subprocess.run(
            [sys.executable, str(ROOT / "tests" / "make_synthetic_fixture.py"),
             str(fixture)],
            capture_output=True, text=True)
        check("fixture builds", r.returncode == 0, r.stderr.strip()[:200])

        json_out = Path(tmp) / "report.json"
        r = subprocess.run(
            [sys.executable, str(ROOT / "ripdoctor.py"), str(fixture),
             "--json", str(json_out)],
            capture_output=True, text=True)
        check("ripdoctor exits 1 on a FAIL export", r.returncode == 1,
              f"exit={r.returncode}")
        report = json.loads(json_out.read_text())
        checks = {c["id"]: c for c in report["checks"]}

        dummy = checks["dummy_shaders"]
        check("dummy shader check FAILs", dummy["status"] == "FAIL")
        d = dummy["details"]["dummy_shaders"]
        check("exactly one dummy shader found", len(d) == 1)
        if d:
            check("dummy shader is the planted TCP2 stub",
                  d[0]["shader"] == "Toony Colors Pro 2/Standard")
            check("all 3 planted materials mapped to the stub",
                  d[0]["material_count"] == 3
                  and sorted(d[0]["materials"]) == ["holly_body", "holly_hair",
                                                    "sam_body"])

        slots = checks["texture_slots"]
        check("texture slot check WARNs", slots["status"] == "WARN")
        empty = slots["details"]["empty_primary_slots"]
        check("the planted empty-primary-map material is the only one flagged",
              len(empty) == 1 and empty[0]["material"] == "flat_prop")

        files = checks["texture_files"]
        check("texture file check FAILs on the dangling reference",
              files["status"] == "FAIL")
        missing = files["details"]["missing_referenced_textures"]
        check("the planted dangling texture reference is caught",
              len(missing) == 1 and missing[0]["material"] == "broken_ref")
        check("the planted zero-byte texture is listed",
              len(files["details"]["zero_byte_textures"]) == 1)

        census = checks["material_census"]["details"]["by_shader"]
        check("healthy material counted on the good shader, not flagged",
              census.get("Custom/GoodLit") == 3)

    print()
    if FAILURES:
        print(f"{len(FAILURES)} assertion(s) FAILED")
        return 1
    print("all assertions passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
