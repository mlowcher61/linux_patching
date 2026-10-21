"""Unit tests for filter_plugins/patching.py.

The pro API payloads below are trimmed copies of real output captured from
ubuntu-pro-client 38 on Ubuntu 22.04 and 24.04.
"""

import importlib.util
import json
import pathlib

_PATH = pathlib.Path(__file__).resolve().parents[1] / "filter_plugins" / "patching.py"
_spec = importlib.util.spec_from_file_location("patching", _PATH)
patching = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(patching)


def _pro(cves):
    return json.dumps({"data": {"attributes": {"cves_data": {"cves": cves}}}, "result": "success"})


def test_package_snapshot_keeps_all_versions_and_releases():
    packages = {
        "kernel": [
            {"name": "kernel", "version": "4.18.0", "release": "553.el8"},
            {"name": "kernel", "version": "4.18.0", "release": "513.el8"},
        ],
        "curl": [{"name": "curl", "version": "7.81.0-1ubuntu1.30"}],
    }
    assert patching.package_snapshot(packages) == {
        "kernel": "4.18.0-513.el8, 4.18.0-553.el8",
        "curl": "7.81.0-1ubuntu1.30",
    }


def test_package_changes_reports_updates_installs_and_removals():
    before = {"a": "1", "b": "1", "c": "1"}
    after = {"a": "2", "b": "1", "d": "1"}
    assert patching.package_changes(before, after) == [
        {"name": "a", "before": "1", "after": "2"},
        {"name": "c", "before": "1", "after": ""},
        {"name": "d", "before": "", "after": "1"},
    ]


def test_dnf_cve_status_and_recheck():
    ids = ["CVE-1", "CVE-2", "CVE-3"]
    available = [
        {"item": "CVE-1", "stdout": "ELSA-2026-1 Important/Sec. openssl-libs-1.1.1k-18.el8"},
        {"item": "CVE-2", "stdout": ""},
        {"item": "CVE-3", "stdout": ""},
    ]
    installed = [
        {"item": "CVE-1", "stdout": ""},
        {"item": "CVE-2", "stdout": "ELSA-2023-1 Important/Sec. openssl-libs-1.1.1k-9.el8"},
        {"item": "CVE-3", "stdout": ""},
    ]
    status = patching.dnf_cve_status(ids, available, installed)
    assert status == {"CVE-1": "pending", "CVE-2": "already_patched", "CVE-3": "not_applicable"}
    assert patching.dnf_cve_recheck(status, [{"item": "CVE-1", "stdout": ""}])["CVE-1"] == "fixed"
    assert patching.dnf_cve_recheck(status, [{"item": "CVE-1", "stdout": "still"}])["CVE-1"] == "still_vulnerable"


def test_pro_plan_status_covers_every_case():
    plan = _pro([
        {"title": "CVE-A", "current_status": "not-affected", "expected_status": "not-affected", "error": None, "plan": []},
        {"title": "CVE-B", "current_status": "still-affected", "expected_status": "fixed", "error": None,
         "plan": [{"operation": "apt-upgrade"}]},
        {"title": "CVE-C", "current_status": None, "expected_status": "error", "plan": [],
         "error": {"code": "security-fix-not-found-issue", "msg": "Error: CVE-C not found."}},
        {"title": "CVE-D", "current_status": "still-affected", "expected_status": "fixed", "error": None,
         "plan": [{"operation": "attach"}, {"operation": "apt-upgrade"}]},
        {"title": "CVE-E", "current_status": "fixed", "expected_status": "fixed", "error": None, "plan": []},
        {"title": "CVE-F", "current_status": "still-affected", "expected_status": "still-affected", "error": None, "plan": []},
    ])
    assert patching.pro_plan_status(plan) == {
        "CVE-A": "not_affected",
        "CVE-B": "pending",
        "CVE-C": "not_found",
        "CVE-D": "requires_pro",
        "CVE-E": "already_patched",
        "CVE-F": "no_fix_available",
    }


def test_pro_execute_status():
    result = _pro([
        {"title": "CVE-A", "status": "fixed"},
        {"title": "CVE-B", "status": "not-affected"},
        {"title": "CVE-C", "status": "still-affected"},
        {"title": "CVE-D", "status": "error"},
    ])
    assert patching.pro_execute_status(result) == {
        "CVE-A": "fixed",
        "CVE-B": "not_affected",
        "CVE-C": "still_vulnerable",
        "CVE-D": "failed",
    }


def test_unresolved_by_host_ignores_safe_statuses():
    reports = [
        {"host": "h1", "cves": {"CVE-1": "fixed", "CVE-2": "not_found", "CVE-3": "not_affected"}},
        {"host": "h2", "cves": {"CVE-1": "still_vulnerable", "CVE-2": "requires_pro"}},
        {"host": "h3"},
    ]
    assert patching.unresolved_by_host(reports) == {"h2": ["CVE-1", "CVE-2"]}


def test_patch_summary_line():
    line = patching.patch_summary_line({
        "host": "web1", "os": "Ubuntu 24.04", "status": "patched",
        "packages_changed_count": 3, "reboot_required": True, "rebooted": False,
        "cves": {"CVE-2": "fixed", "CVE-1": "not_affected"},
    })
    assert line == "web1 | Ubuntu 24.04 | PATCHED | 3 package(s) changed | REBOOT REQUIRED | CVE-1=not_affected, CVE-2=fixed"
