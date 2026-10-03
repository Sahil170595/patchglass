"""Parser-module tests (TDD).

For every framework parser: a canned raw output (or artifacts dict) + expected_ids -> exact status map.
Mandatory coverage: a MISSING case (expected id absent) and a pytest case with ANSI + timing noise
proving normalize() runs before matching. Parsers are resolved through the registry after load_builtins().
"""

from __future__ import annotations

import json

import pytest

from taskbundle.errors import GradeError
from taskbundle.parsers.base import RunOutput
from taskbundle.parsers.base import TestStatus as Status  # alias: avoid pytest collecting the StrEnum
from taskbundle.parsers.builtins import load_builtins
from taskbundle.parsers.registry import available, get_parser

load_builtins()


# --- pytest ---------------------------------------------------------------------------------------


def test_pytest_text_basic_statuses() -> None:
    parser = get_parser("pytest")
    stdout = (
        "PASSED tests/test_a.py::test_ok\n"
        "FAILED tests/test_a.py::test_bad\n"
        "SKIPPED tests/test_a.py::test_skip\n"
        "XFAIL tests/test_a.py::test_xf\n"
        "ERROR tests/test_a.py::test_err\n"
    )
    ids = [
        "tests/test_a.py::test_ok",
        "tests/test_a.py::test_bad",
        "tests/test_a.py::test_skip",
        "tests/test_a.py::test_xf",
        "tests/test_a.py::test_err",
    ]
    assert parser.parse(RunOutput(stdout=stdout), ids) == {
        "tests/test_a.py::test_ok": Status.PASSED,
        "tests/test_a.py::test_bad": Status.FAILED,
        "tests/test_a.py::test_skip": Status.SKIPPED,
        "tests/test_a.py::test_xf": Status.XFAIL,
        "tests/test_a.py::test_err": Status.ERROR,
    }


def test_pytest_id_after_status_form() -> None:
    # pytest -v emits "nodeid STATUS" (status trailing); support both orderings.
    parser = get_parser("pytest")
    stdout = "tests/test_b.py::test_one PASSED\ntests/test_b.py::test_two FAILED\n"
    ids = ["tests/test_b.py::test_one", "tests/test_b.py::test_two"]
    assert parser.parse(RunOutput(stdout=stdout), ids) == {
        "tests/test_b.py::test_one": Status.PASSED,
        "tests/test_b.py::test_two": Status.FAILED,
    }


def test_pytest_ansi_and_timing_noise_normalized() -> None:
    # ANSI color codes + "[ 50%]" / "(0.12s)" timing annotations must be stripped before matching.
    parser = get_parser("pytest")
    stdout = (
        "\x1b[32mPASSED\x1b[0m tests/test_a.py::test_ok [ 50%]\r\n"
        "\x1b[31mFAILED\x1b[0m tests/test_a.py::test_bad (0.12s) [100%]\r\n"
    )
    ids = ["tests/test_a.py::test_ok", "tests/test_a.py::test_bad"]
    assert parser.parse(RunOutput(stdout=stdout), ids) == {
        "tests/test_a.py::test_ok": Status.PASSED,
        "tests/test_a.py::test_bad": Status.FAILED,
    }


def test_pytest_missing_id_is_missing_not_failed() -> None:
    # Edge-case #2: an expected id that never appears is MISSING, never a silent FAILED.
    parser = get_parser("pytest")
    stdout = "PASSED tests/test_a.py::test_ok\n"
    ids = ["tests/test_a.py::test_ok", "tests/test_a.py::test_gone"]
    assert parser.parse(RunOutput(stdout=stdout), ids) == {
        "tests/test_a.py::test_ok": Status.PASSED,
        "tests/test_a.py::test_gone": Status.MISSING,
    }


def test_pytest_json_report_preferred_over_text() -> None:
    parser = get_parser("pytest")
    report = {
        "tests": [
            {"nodeid": "tests/test_a.py::test_ok", "outcome": "passed"},
            {"nodeid": "tests/test_a.py::test_bad", "outcome": "failed"},
            {"nodeid": "tests/test_a.py::test_skip", "outcome": "skipped"},
            {"nodeid": "tests/test_a.py::test_xf", "outcome": "xfailed"},
            {"nodeid": "tests/test_a.py::test_err", "outcome": "error"},
        ]
    }
    # Deliberately wrong/empty stdout: the JSON report must win.
    out = RunOutput(stdout="garbage", artifacts={"report.json": json.dumps(report)})
    ids = [
        "tests/test_a.py::test_ok",
        "tests/test_a.py::test_bad",
        "tests/test_a.py::test_skip",
        "tests/test_a.py::test_xf",
        "tests/test_a.py::test_err",
        "tests/test_a.py::test_absent",
    ]
    assert parser.parse(out, ids) == {
        "tests/test_a.py::test_ok": Status.PASSED,
        "tests/test_a.py::test_bad": Status.FAILED,
        "tests/test_a.py::test_skip": Status.SKIPPED,
        "tests/test_a.py::test_xf": Status.XFAIL,
        "tests/test_a.py::test_err": Status.ERROR,
        "tests/test_a.py::test_absent": Status.MISSING,
    }


def test_pytest_malformed_json_report_raises() -> None:
    parser = get_parser("pytest")
    out = RunOutput(stdout="PASSED tests/test_a.py::test_ok", artifacts={"output.json": "{not json"})
    with pytest.raises(GradeError):
        parser.parse(out, ["tests/test_a.py::test_ok"])


def test_pytest_rA_summary_with_trailing_reason() -> None:
    # Real `pytest -rA` failure/error summary lines append " - <reason>" after the nodeid.
    # Edge-case #2: the reason must NOT be captured as part of the id, or a real FAILED/ERROR
    # silently becomes MISSING (wrong-answer-no-error). This is the regression the basic test missed.
    parser = get_parser("pytest")
    stdout = (
        "PASSED tests/test_a.py::test_ok\n"
        "FAILED tests/test_a.py::test_bad - assert 1 == 2\n"
        "ERROR tests/test_a.py::test_err - ImportError: boom\n"
    )
    ids = ["tests/test_a.py::test_ok", "tests/test_a.py::test_bad", "tests/test_a.py::test_err"]
    assert parser.parse(RunOutput(stdout=stdout), ids) == {
        "tests/test_a.py::test_ok": Status.PASSED,
        "tests/test_a.py::test_bad": Status.FAILED,
        "tests/test_a.py::test_err": Status.ERROR,
    }


def test_pytest_parametrized_id_with_internal_space_preserved() -> None:
    # Parametrized nodeids legitimately contain spaces inside "[...]"; the reason-stripper must not
    # truncate them (there is no " - " separator, so the full id is kept).
    parser = get_parser("pytest")
    stdout = "PASSED tests/test_a.py::test_p[case one]\n"
    ids = ["tests/test_a.py::test_p[case one]"]
    assert parser.parse(RunOutput(stdout=stdout), ids) == {
        "tests/test_a.py::test_p[case one]": Status.PASSED,
    }


# --- django ---------------------------------------------------------------------------------------


def test_django_unittest_statuses() -> None:
    parser = get_parser("django")
    stdout = (
        "test_login (auth.tests.LoginTests) ... ok\n"
        "test_logout (auth.tests.LoginTests) ... FAIL\n"
        "test_session (auth.tests.SessionTests) ... ERROR\n"
        "test_optional (auth.tests.SessionTests) ... skipped 'no backend'\n"
    )
    ids = [
        "test_login (auth.tests.LoginTests)",
        "test_logout (auth.tests.LoginTests)",
        "test_session (auth.tests.SessionTests)",
        "test_optional (auth.tests.SessionTests)",
        "test_absent (auth.tests.SessionTests)",
    ]
    assert parser.parse(RunOutput(stdout=stdout), ids) == {
        "test_login (auth.tests.LoginTests)": Status.PASSED,
        "test_logout (auth.tests.LoginTests)": Status.FAILED,
        "test_session (auth.tests.SessionTests)": Status.ERROR,
        "test_optional (auth.tests.SessionTests)": Status.SKIPPED,
        "test_absent (auth.tests.SessionTests)": Status.MISSING,
    }


# --- go test --------------------------------------------------------------------------------------


def test_gotest_statuses() -> None:
    parser = get_parser("go")
    stdout = (
        "=== RUN   TestAlpha\n"
        "--- PASS: TestAlpha (0.00s)\n"
        "=== RUN   TestBeta\n"
        "--- FAIL: TestBeta (0.01s)\n"
        "--- SKIP: TestGamma (0.00s)\n"
    )
    ids = ["TestAlpha", "TestBeta", "TestGamma", "TestDelta"]
    assert parser.parse(RunOutput(stdout=stdout), ids) == {
        "TestAlpha": Status.PASSED,
        "TestBeta": Status.FAILED,
        "TestGamma": Status.SKIPPED,
        "TestDelta": Status.MISSING,
    }


def test_gotest_subtest_ids() -> None:
    parser = get_parser("go")
    stdout = "--- PASS: TestTable/case_one (0.00s)\n--- FAIL: TestTable/case_two (0.00s)\n"
    ids = ["TestTable/case_one", "TestTable/case_two"]
    assert parser.parse(RunOutput(stdout=stdout), ids) == {
        "TestTable/case_one": Status.PASSED,
        "TestTable/case_two": Status.FAILED,
    }


# --- jest -----------------------------------------------------------------------------------------


def test_jest_json_artifact() -> None:
    parser = get_parser("jest")
    report = {
        "testResults": [
            {
                "assertionResults": [
                    {"fullName": "Adder adds two numbers", "status": "passed"},
                    {"fullName": "Adder rejects strings", "status": "failed"},
                    {"fullName": "Adder pending case", "status": "pending"},
                    {"fullName": "Adder todo case", "status": "todo"},
                ]
            }
        ]
    }
    out = RunOutput(stdout="", artifacts={"jest.json": json.dumps(report)})
    ids = [
        "Adder adds two numbers",
        "Adder rejects strings",
        "Adder pending case",
        "Adder todo case",
        "Adder absent case",
    ]
    assert parser.parse(out, ids) == {
        "Adder adds two numbers": Status.PASSED,
        "Adder rejects strings": Status.FAILED,
        "Adder pending case": Status.SKIPPED,
        "Adder todo case": Status.SKIPPED,
        "Adder absent case": Status.MISSING,
    }


def test_jest_text_fallback() -> None:
    parser = get_parser("jest")
    stdout = (
        "  ✓ Adder adds two numbers (3 ms)\n" "  ✕ Adder rejects strings (5 ms)\n" "  ○ skipped Adder pending case\n"
    )
    ids = ["Adder adds two numbers", "Adder rejects strings", "Adder pending case", "Adder absent"]
    assert parser.parse(RunOutput(stdout=stdout), ids) == {
        "Adder adds two numbers": Status.PASSED,
        "Adder rejects strings": Status.FAILED,
        "Adder pending case": Status.SKIPPED,
        "Adder absent": Status.MISSING,
    }


# --- mocha ----------------------------------------------------------------------------------------


def test_mocha_json_reporter() -> None:
    # SWE-bench Pro finding: mocha test ids are "{file} | {fullTitle}".
    parser = get_parser("mocha")
    report = {
        "passes": [{"file": "test/user.js", "fullTitle": "User can register"}],
        "failures": [{"file": "test/user.js", "fullTitle": "User rejects dup email"}],
        "pending": [{"file": "test/user.js", "fullTitle": "User can delete"}],
    }
    out = RunOutput(stdout="", artifacts={"mocha.json": json.dumps(report)})
    ids = [
        "test/user.js | User can register",
        "test/user.js | User rejects dup email",
        "test/user.js | User can delete",
        "test/user.js | User absent",
    ]
    assert parser.parse(out, ids) == {
        "test/user.js | User can register": Status.PASSED,
        "test/user.js | User rejects dup email": Status.FAILED,
        "test/user.js | User can delete": Status.SKIPPED,
        "test/user.js | User absent": Status.MISSING,
    }


# --- ansible --------------------------------------------------------------------------------------


def test_ansible_gw_pattern() -> None:
    parser = get_parser("ansible")
    stdout = (
        "[gw0] [ 25%] PASSED test/units/module_utils/test_basic.py::TestBasic::test_run\n"
        "[gw1] [ 50%] FAILED test/units/module_utils/test_basic.py::TestBasic::test_fail\n"
        "[gw0] [ 75%] SKIPPED test/units/plugins/test_loader.py::TestLoader::test_skip\n"
    )
    ids = [
        "test/units/module_utils/test_basic.py::TestBasic::test_run",
        "test/units/module_utils/test_basic.py::TestBasic::test_fail",
        "test/units/plugins/test_loader.py::TestLoader::test_skip",
        "test/units/plugins/test_loader.py::TestLoader::test_absent",
    ]
    assert parser.parse(RunOutput(stdout=stdout), ids) == {
        "test/units/module_utils/test_basic.py::TestBasic::test_run": Status.PASSED,
        "test/units/module_utils/test_basic.py::TestBasic::test_fail": Status.FAILED,
        "test/units/plugins/test_loader.py::TestLoader::test_skip": Status.SKIPPED,
        "test/units/plugins/test_loader.py::TestLoader::test_absent": Status.MISSING,
    }


# --- scale_run_script -----------------------------------------------------------------------------


def test_scale_run_script_reads_output_json() -> None:
    parser = get_parser("scale_run_script")
    report = {
        "tests": [
            {"name": "suite::test_a", "status": "PASSED"},
            {"name": "suite::test_b", "status": "FAILED"},
            {"name": "suite::test_c", "status": "SKIPPED"},
            {"name": "suite::test_d", "status": "ERROR"},
        ]
    }
    out = RunOutput(stdout="", artifacts={"output.json": json.dumps(report)})
    ids = ["suite::test_a", "suite::test_b", "suite::test_c", "suite::test_d", "suite::test_missing"]
    assert parser.parse(out, ids) == {
        "suite::test_a": Status.PASSED,
        "suite::test_b": Status.FAILED,
        "suite::test_c": Status.SKIPPED,
        "suite::test_d": Status.ERROR,
        "suite::test_missing": Status.MISSING,
    }


def test_scale_run_script_missing_artifact_raises() -> None:
    parser = get_parser("scale_run_script")
    with pytest.raises(GradeError):  # in-container parser needs its output.json.
        parser.parse(RunOutput(stdout="nope"), ["suite::test_a"])


def test_scale_run_script_unknown_status_raises() -> None:
    parser = get_parser("scale_run_script")
    report = {"tests": [{"name": "suite::test_a", "status": "WAT"}]}
    out = RunOutput(stdout="", artifacts={"output.json": json.dumps(report)})
    with pytest.raises(GradeError):
        parser.parse(out, ["suite::test_a"])


# --- secb_sanitizer -------------------------------------------------------------------------------

# Authored protocol fixtures, not captured run output or third-party reproduction data.
_SECB_BASELINE = (
    "BUILD COMPLETED SUCCESSFULLY!\n"
    "synthetic reproduction started\n"
    "ERROR: AddressSanitizer: synthetic invalid access\n"
    "SUMMARY: AddressSanitizer: synthetic failure\n"
)

# A successful build plus an ordinary application exception has no sanitizer marker.
_SECB_PATCHED = (
    "BUILD COMPLETED SUCCESSFULLY!\n" "synthetic reproduction started\n" "TypeError: synthetic application exception\n"
)


def test_secb_baseline_crash_is_failed() -> None:
    # Baseline: built fine but the sanitizer still fired -> the fail2pass `repro` FAILS (bug present).
    parser = get_parser("secb_sanitizer")
    assert parser.parse(RunOutput(stdout=_SECB_BASELINE), ["repro"]) == {"repro": Status.FAILED}


def test_secb_patched_clean_is_passed() -> None:
    # Patched: built fine and the PoC ran with no sanitizer marker -> `repro` PASSES (bug fixed).
    parser = get_parser("secb_sanitizer")
    assert parser.parse(RunOutput(stdout=_SECB_PATCHED), ["repro"]) == {"repro": Status.PASSED}


def test_secb_build_failure_is_failed_even_without_crash() -> None:
    # A broken build is not a fix: no crash marker, but no successful build either -> FAILED.
    parser = get_parser("secb_sanitizer")
    stdout = "BUILDING THE PROJECT...\nBUILD FAILED!\n"
    assert parser.parse(RunOutput(stdout=stdout), ["repro"]) == {"repro": Status.FAILED}


def test_secb_crash_on_stderr_is_detected() -> None:
    # Some runners emit the sanitizer report on stderr; the parser must scan both streams.
    parser = get_parser("secb_sanitizer")
    out = RunOutput(
        stdout="BUILD COMPLETED SUCCESSFULLY!\nREPRODUCING...\n",
        stderr="==1==ERROR: AddressSanitizer: heap-buffer-overflow\n",
    )
    assert parser.parse(out, ["repro"]) == {"repro": Status.FAILED}


def test_secb_ubsan_runtime_error_is_crash() -> None:
    # A UBSan "runtime error:" line (no ASAN summary) still counts as a sanitizer crash -> FAILED.
    parser = get_parser("secb_sanitizer")
    stdout = "BUILD COMPLETED SUCCESSFULLY!\nsample.c:4:2: runtime error: synthetic invalid access\n"
    assert parser.parse(RunOutput(stdout=stdout), ["repro"]) == {"repro": Status.FAILED}


def test_secb_unknown_id_is_missing() -> None:
    # An expected id this parser does not grade is MISSING (never a silent pass/fail).
    parser = get_parser("secb_sanitizer")
    assert parser.parse(RunOutput(stdout=_SECB_PATCHED), ["repro", "other"]) == {
        "repro": Status.PASSED,
        "other": Status.MISSING,
    }


# --- registry / builtins --------------------------------------------------------------------------


def test_all_parsers_registered() -> None:
    for name in ("pytest", "django", "go", "jest", "mocha", "ansible", "scale_run_script", "secb_sanitizer"):
        assert name in available()
        assert get_parser(name).name == name


def test_empty_expected_ids_returns_empty_map() -> None:
    for name in ("pytest", "django", "go", "jest", "mocha", "ansible"):
        assert get_parser(name).parse(RunOutput(stdout="anything"), []) == {}
