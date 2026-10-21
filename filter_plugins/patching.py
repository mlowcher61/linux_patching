"""Filters used by the linux_patching playbooks.

They turn raw command output (package_facts, dnf updateinfo, the Ubuntu Pro
client API) into plain dictionaries the report can print, so the playbooks
stay readable and the logic is unit-tested in tests/test_patching_filters.py.
"""

import json

# CVE statuses that mean "this host is still exposed after the run".
UNRESOLVED_STATUSES = (
    "pending",
    "still_vulnerable",
    "requires_pro",
    "no_fix_available",
    "failed",
)


def package_snapshot(packages):
    """Reduce ansible_facts.packages to {name: "ver-rel[, ver-rel]"}.

    Several versions of one package can be installed at once (kernels), so
    every installed version is kept, sorted, in a single string.
    """
    snapshot = {}
    for name, items in (packages or {}).items():
        versions = set()
        for item in items:
            version = str(item.get("version", ""))
            release = str(item.get("release") or "")
            versions.add(f"{version}-{release}" if release else version)
        snapshot[name] = ", ".join(sorted(versions))
    return snapshot


def package_changes(before, after):
    """Compare two package_snapshot results.

    Returns a sorted list of {name, before, after}. A package that was newly
    installed has before == "" and a removed package has after == "".
    """
    before = before or {}
    after = after or {}
    changes = []
    for name in sorted(set(before) | set(after)):
        old = before.get(name, "")
        new = after.get(name, "")
        if old != new:
            changes.append({"name": name, "before": old, "after": new})
    return changes


def dnf_cve_status(cve_ids, pending_results, installed_results=None):
    """Classify CVEs from looped `dnf updateinfo list` results.

    pending_results   -- loop results of `dnf updateinfo list --cve X`
    installed_results -- loop results of `dnf updateinfo list --installed --cve X`

    Returns {cve: "pending" | "already_patched" | "not_applicable"}.
    """
    pending = {r["item"]: r.get("stdout", "").strip() for r in pending_results or []}
    installed = {r["item"]: r.get("stdout", "").strip() for r in installed_results or []}
    status = {}
    for cve in cve_ids:
        if pending.get(cve):
            status[cve] = "pending"
        elif installed.get(cve):
            status[cve] = "already_patched"
        else:
            status[cve] = "not_applicable"
    return status


def dnf_cve_recheck(before, pending_results):
    """After `dnf upgrade --cve`, turn "pending" into "fixed" or "still_vulnerable"."""
    pending = {r["item"]: r.get("stdout", "").strip() for r in pending_results or []}
    result = dict(before or {})
    for cve, status in result.items():
        if status == "pending":
            result[cve] = "still_vulnerable" if pending.get(cve) else "fixed"
    return result


def _pro_cves(api_output):
    data = json.loads(api_output) if isinstance(api_output, str) else api_output
    return data.get("data", {}).get("attributes", {}).get("cves_data", {}).get("cves", []) or []


def pro_plan_status(api_output):
    """Classify CVEs from `pro api u.pro.security.fix.cve.plan.v1`.

    Returns {cve: status} where status is one of not_affected,
    already_patched, pending, requires_pro, no_fix_available, not_found, failed.
    """
    status = {}
    for cve in _pro_cves(api_output):
        title = cve.get("title")
        error = cve.get("error")
        operations = {step.get("operation") for step in cve.get("plan") or []}
        current = cve.get("current_status")
        if error:
            code = error.get("code", "")
            status[title] = "not_found" if code == "security-fix-not-found-issue" else "failed"
        elif current == "not-affected":
            status[title] = "not_affected"
        elif current == "fixed":
            status[title] = "already_patched"
        elif operations & {"attach", "enable"}:
            status[title] = "requires_pro"
        elif cve.get("expected_status") != "fixed":
            status[title] = "no_fix_available"
        else:
            status[title] = "pending"
    return status


def pro_execute_status(api_output):
    """Classify CVEs from `pro api u.pro.security.fix.cve.execute.v1`."""
    status = {}
    for cve in _pro_cves(api_output):
        title = cve.get("title")
        result = cve.get("status")
        if result == "fixed":
            status[title] = "fixed"
        elif result == "not-affected":
            status[title] = "not_affected"
        elif result == "still-affected":
            status[title] = "still_vulnerable"
        else:
            status[title] = "failed"
    return status


def unresolved_cves(cve_status):
    """Return the sorted CVE IDs whose status means the host is still exposed."""
    return sorted(cve for cve, status in (cve_status or {}).items() if status in UNRESOLVED_STATUSES)


def unresolved_by_host(reports):
    """Map host -> unresolved CVE list, for hosts that have any."""
    result = {}
    for report in reports or []:
        cves = unresolved_cves(report.get("cves"))
        if cves:
            result[report.get("host")] = cves
    return result


def patch_summary_line(report):
    """Render one host's patch report as a single summary line."""
    parts = [
        str(report.get("host")),
        str(report.get("os", "")),
        str(report.get("status", "")).upper(),
    ]
    if "packages_changed_count" in report:
        parts.append(f"{report['packages_changed_count']} package(s) changed")
    if "updates_available" in report:
        parts.append(f"{report['updates_available']} update(s) available, {report.get('security_updates', 0)} security")
    if report.get("rebooted"):
        parts.append("rebooted")
    elif report.get("reboot_required"):
        parts.append("REBOOT REQUIRED")
    if report.get("cves"):
        parts.append(", ".join(f"{cve}={status}" for cve, status in sorted(report["cves"].items())))
    if report.get("message"):
        parts.append(str(report["message"]))
    return " | ".join(parts)


class FilterModule:
    def filters(self):
        return {
            "package_snapshot": package_snapshot,
            "package_changes": package_changes,
            "dnf_cve_status": dnf_cve_status,
            "dnf_cve_recheck": dnf_cve_recheck,
            "pro_plan_status": pro_plan_status,
            "pro_execute_status": pro_execute_status,
            "unresolved_cves": unresolved_cves,
            "unresolved_by_host": unresolved_by_host,
            "patch_summary_line": patch_summary_line,
        }
