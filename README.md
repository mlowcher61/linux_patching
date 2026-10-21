# linux_patching

Patch a mixed Linux fleet from **Ansible Automation Platform (AAP)**:

| Operating system | How it is patched | How CVEs are fixed |
|---|---|---|
| Oracle Linux 8 | `dnf` | `dnf upgrade --cve` (Oracle ELSA advisories) |
| Amazon Linux 2023 | `dnf` | `dnf upgrade --cve` (Amazon ALAS advisories) |
| Ubuntu 22.04 LTS | `apt` | Ubuntu Pro client (`pro fix` API) |
| Ubuntu 24.04 LTS | `apt` | Ubuntu Pro client (`pro fix` API) |

You get three job templates, all created for you by **config as code**:

| Job template | Playbook | What it does |
|---|---|---|
| **Linux Patching - Patch Assessment** | `patch_assessment.yml` | Read-only. Counts pending updates (and security updates) per host and, optionally, checks which CVEs affect each host. |
| **Linux Patching - Full Patch** | `patch_linux.yml` | Installs all updates, or security updates only, in rolling batches, then reboots if needed. |
| **Linux Patching - CVE Patch** | `patch_cves.yml` | You type in CVE numbers; it installs only the updates that fix those CVEs. |

Every run ends with a **patch report**: one line per host in the job output, plus a
`patch_results` job artifact (machine-readable, usable by workflows or ServiceNow).

---

## Contents

1. [How it works](#how-it-works)
2. [Prerequisites](#prerequisites)
3. [Quick start: configure AAP with config as code](#quick-start-configure-aap-with-config-as-code)
4. [Running the job templates](#running-the-job-templates)
5. [Survey reference](#survey-reference)
6. [Reading the patch report](#reading-the-patch-report)
7. [Running from the command line](#running-from-the-command-line)
8. [Things to know](#things-to-know)
9. [Troubleshooting](#troubleshooting)
10. [Repository layout](#repository-layout)

---

## How it works

```
                 ┌──────────────────────────── AAP ─────────────────────────────┐
                 │                                                              │
  GitHub ──sync──▶  Project "Linux Patching"                                    │
  (this repo)    │     │                                                        │
                 │     ├── Inventory "Linux Patching - AWS EC2"                 │
  AWS API ◀──────┼─────┤     source: inventory/aws_ec2.yml + AWS credential     │
                 │     │     (refreshed automatically before every job)         │
                 │     │                                                        │
                 │     └── Job templates (survey) ── SSH credential ──┐         │
                 │           Patch Assessment / Full Patch / CVE Patch│         │
                 └────────────────────────────────────────────────────┼─────────┘
                                                                      ▼
                     EC2 instances tagged patch_group=…   (Oracle 8, Ubuntu 22/24, AL2023)
```

Each playbook runs the same steps on every host:

1. **Preflight** (`roles/patch_common`): detects the OS and skips anything unsupported.
   It checks there is enough free disk space and records the installed package versions.
2. **Patch**: `roles/patch_full` handles full patching and `roles/patch_cve` handles CVE
   fixes. Each picks `dnf` or `apt` for the host.
3. **Reboot**: depends on the survey choice. On `dnf` hosts the check is
   `dnf needs-restarting -r`; on Ubuntu it is `/var/run/reboot-required`.
4. **Report**: compares package versions before and after, and records the reboot and CVE
   status. The results are published as a job artifact.
5. **Summary**: one line per host is printed at the end. For CVE runs, the job fails if a
   requested CVE is still unresolved, unless you turn that off.

Hosts are processed in **rolling batches** (`serial`). If more than the allowed percentage
of a batch fails, the remaining batches are not started.

---

## Prerequisites

### 1. Ansible Automation Platform

* AAP 2.5 or later, with an account that can create organizations, credentials, projects,
  inventories and templates. An administrator account is the simplest choice.
* The job templates use AAP's built-in **Default execution environment**. It already
  contains everything needed, including `amazon.aws` and `boto3`.

> **Oracle Linux 8 note:** OL8's system Python is 3.6. ansible-core **2.16** is the last
> release that can manage it, and that is what the AAP supported execution environment
> provides. If you use a custom execution environment, keep it on ansible-core 2.16.

### 2. EC2 instances and tags

The inventory only includes **running** instances that have a `patch_group` tag, so
nothing is patched unless you opt it in.

| Tag | Required? | Example values | Creates inventory group |
|---|---|---|---|
| `patch_group` | **yes** | `dev`, `prod`, `wave1` | `patch_group_dev`, … |
| `os` | optional | `oracle8`, `ubuntu22`, `ubuntu24`, `al2023` | `os_ubuntu24`, … |
| `ansible_user` | optional | `opc` | sets the SSH user for that instance |
| `Name` | recommended | `web01` | used as the host name in AAP |

The SSH user defaults to `ubuntu` for Ubuntu and `ec2-user` for Amazon Linux and Oracle
Linux. Those are the defaults of the official AMIs. Ubuntu is detected from the `os` tag
or the AMI name.

Edit `regions:` in [`inventory/aws_ec2.yml`](inventory/aws_ec2.yml) to list your regions.
The default is `us-east-1`.

### 3. Network access

* AAP must reach each instance on **TCP 22**. It uses the public IP when the instance has
  one, otherwise the private IP (AAP in the VPC, VPN or Direct Connect).
* The instances must reach their package repositories, for example through a NAT gateway:
  * Oracle: `yum.oracle.com`
  * Amazon Linux: the AL2023 repositories in your region
  * Ubuntu: the archive and security mirrors, plus `ubuntu.com` for CVE lookups by the
    Pro client

### 4. Credentials you will need

| What | Where it goes | Used for |
|---|---|---|
| AWS access key and secret | `local.yml` → credential **Linux Patching - AWS** | Reading EC2 instances. Needs `ec2:DescribeInstances` and `ec2:DescribeRegions`. |
| EC2 key pair private key (`.pem`) | `local.yml` → credential **Linux Patching - SSH** | SSH plus `sudo` on the instances |
| Ubuntu Pro token (optional) | `local.yml` → credential **Linux Patching - Ubuntu Pro** | CVE fixes that Canonical publishes only to Ubuntu Pro (ESM). Free for up to 5 machines at <https://ubuntu.com/pro/dashboard>. |

Secrets are **never stored in git**. They live in a gitignored `local.yml` on your laptop.
They are written once into encrypted AAP credentials, and AAP injects them into jobs at run
time. The Ubuntu Pro token uses a custom credential type, **Ubuntu Pro Token**, which this
repo creates.

### 5. On your laptop (for the one-time configuration)

* `ansible-core` 2.16 or later and Python 3.
* An **Automation Hub token** in `~/.ansible.cfg`. It is needed to install the Red Hat
  certified and validated collections:

  ```ini
  [galaxy]
  server_list = automation_hub, validated, galaxy

  [galaxy_server.automation_hub]
  url = https://console.redhat.com/api/automation-hub/content/published/
  auth_url = https://sso.redhat.com/auth/realms/redhat-external/protocol/openid-connect/token
  token = <your token from https://console.redhat.com/ansible/automation-hub/token>

  [galaxy_server.validated]
  url = https://console.redhat.com/api/automation-hub/content/validated/
  auth_url = https://sso.redhat.com/auth/realms/redhat-external/protocol/openid-connect/token
  token = <same token>

  [galaxy_server.galaxy]
  url = https://galaxy.ansible.com/
  ```

---

## Quick start: configure AAP with config as code

Everything AAP needs is described in YAML under
[`config_as_code/group_vars/aap/`](config_as_code/group_vars/aap). One playbook applies it,
using the Red Hat validated `infra.aap_configuration` collection.

```bash
# 1. Get the code (or your fork of it)
git clone https://github.com/mlowcher61/linux_patching.git
cd linux_patching

# 2. Install the collections the configuration playbook uses
ansible-galaxy collection install -r config_as_code/requirements.yml

# 3. Create your secrets file (gitignored) and fill it in
cp config_as_code/local.yml.example config_as_code/local.yml
vi config_as_code/local.yml

# 4. Optional: preview what would change
ansible-playbook -i config_as_code/inventory.yml config_as_code/configure_aap.yml \
  -e @config_as_code/local.yml --check

# 5. Apply it
ansible-playbook -i config_as_code/inventory.yml config_as_code/configure_aap.yml \
  -e @config_as_code/local.yml
```

### What gets created

| AAP object | Name | Defined in |
|---|---|---|
| Organization | Linux Patching | `aap_organizations.yml` |
| Credential type | Ubuntu Pro Token | `controller_credential_types.yml` |
| Credentials | Linux Patching - AWS / - SSH / - Ubuntu Pro | `controller_credentials.yml` |
| Project | Linux Patching (this Git repo; syncs on launch) | `controller_projects.yml` |
| Inventory and source | Linux Patching - AWS EC2 (from `inventory/aws_ec2.yml`) | `controller_inventories.yml`, `controller_inventory_sources.yml` |
| Job templates (with surveys) | Patch Assessment, Full Patch, CVE Patch | `controller_templates.yml` |
| Execution environment | only if `lp_custom_ee_image` is set | `controller_execution_environments.yml` |

### Customizing

All names and locations are in
[`config_as_code/group_vars/aap/main.yml`](config_as_code/group_vars/aap/main.yml):

| Variable | Default | Change it to… |
|---|---|---|
| `lp_organization` | `Linux Patching` | `Default` to use the existing organization |
| `lp_scm_url` / `lp_scm_branch` | this repo / `main` | point AAP at your fork. Edit `inventory/aws_ec2.yml` there. |
| `lp_custom_ee_image` | empty (use the Default EE) | your own image built from `execution-environment.yml` |
| `lp_update_secrets` | `false` | `true` for one run after rotating a key in `local.yml` |

Re-running the playbook is safe. It only changes what differs, and it does not overwrite
stored secrets unless `lp_update_secrets` is `true`.

The playbook also reads `AAP_HOSTNAME`, `AAP_USERNAME` and `AAP_PASSWORD` environment
variables. Use them if you prefer not to put the AAP login in `local.yml`.

> **Private fork?** Create a *Source Control* credential in AAP. Then add
> `credential: "<its name>"` to the project in `controller_projects.yml`.

---

## Running the job templates

In AAP go to **Automation Execution → Templates**, click the rocket icon next to a template,
answer the survey and launch.

**Limit** is prompted on every launch. Use it to target part of the fleet:

| Limit | Targets |
|---|---|
| *(empty)* | every opted-in instance |
| `patch_group_dev` | instances tagged `patch_group=dev` |
| `os_ubuntu24` | instances tagged `os=ubuntu24` |
| `patch_group_prod:&os_al2023` | prod **and** Amazon Linux 2023 |
| `web01` | a single host |

**Suggested first run:**

1. Run **Patch Assessment** to see what is pending. It changes nothing.
2. Run **Full Patch** on `patch_group_dev` with `reboot_mode = if_required`.
3. Roll out to production with **Batch size `25%`** and **Maximum failure percentage `0`**.
   The run then stops as soon as any host fails.

---

## Survey reference

### Full Patch (`patch_linux.yml`)

| Survey question | Variable | Default | Meaning |
|---|---|---|---|
| Patch scope | `patch_scope` | `all` | `all` installs every update. `security` installs security updates only: `dnf --security`, or the Ubuntu `-security` pocket via `unattended-upgrade`. |
| Reboot | `reboot_mode` | `if_required` | `if_required`, `always` or `never` |
| Exclude packages | `patch_exclude` | *(empty)* | Comma-separated names, e.g. `kernel*, docker-ce`. Wildcards work on dnf hosts. Ubuntu needs exact names; they are put on hold for the run and released afterwards. |
| Ubuntu upgrade type | `apt_upgrade_type` | `safe` | `safe` = `apt-get upgrade`. `dist` = `apt-get dist-upgrade`, which can install new dependencies such as kernels. |
| Amazon Linux 2023 release version | `al2023_releasever` | *(empty)* | See [AL2023 release versions](#amazon-linux-2023-release-versions) |
| Batch size | `patch_batch_size` | `100%` | Hosts per batch: a number (`2`) or a percentage (`25%`) |
| Maximum failure percentage per batch | `patch_max_fail_pct` | `100` | Stop if more than this % of a batch fails. `0` = stop on the first failure. |

### CVE Patch (`patch_cves.yml`)

| Survey question | Variable | Default | Meaning |
|---|---|---|---|
| CVE IDs | `cve_list` | *(required)* | e.g. `CVE-2024-6387, CVE-2023-38545`. Separate with commas, spaces, semicolons or new lines. Lower case is accepted. Invalid IDs stop the job before any host is touched. |
| Check only | `cve_check_only` | `false` | `true` = report exposure only and change nothing |
| Fail if any CVE remains unresolved | `fail_on_unresolved` | `true` | Fail the job when a host is still exposed after the run. This setting is ignored in check-only mode. |
| Reboot, AL2023 release, Batch size, Max failure % | | | Same as Full Patch |

### Patch Assessment (`patch_assessment.yml`)

| Survey question | Variable | Default | Meaning |
|---|---|---|---|
| CVE IDs (optional) | `cve_list` | *(empty)* | Also report exposure to these CVEs |
| Amazon Linux 2023 release version | `al2023_releasever` | *(empty)* | Assess against a newer AL2023 release |

---

## Reading the patch report

At the end of every job the **Summary** task prints one line per host:

```
web01 | Ubuntu 22.04 | PATCHED | 2 package(s) changed | CVE-2023-38545=fixed, CVE-2024-6387=not_affected
db01  | OracleLinux 8.10 | PATCHED | 3 package(s) changed | REBOOT REQUIRED | CVE-2026-63072=fixed
app07 | Amazon 2023 | NO_CHANGES | 0 package(s) changed | CVE-2023-38545=already_patched
web02 | Ubuntu 24.04 | ASSESSED | 3 update(s) available, 3 security
```

**Host status values**

| Status | Meaning |
|---|---|
| `PATCHED` | One or more packages were updated |
| `NO_CHANGES` | Nothing needed updating |
| `CHECK_ONLY` | CVE check-only run; nothing changed |
| `ASSESSED` | Assessment run; nothing changed |
| `FAILED` | Something failed. The report includes the failed task and the error message. |
| `UNSUPPORTED` | The OS is not one of the four supported ones, so the host was skipped |

**CVE status values**

| Status | Meaning | Counts as unresolved? |
|---|---|---|
| `fixed` | The fix was installed during this run | no |
| `already_patched` | The fix was already installed | no |
| `not_affected` | (Ubuntu) The vulnerable package is not installed | no |
| `not_applicable` | (dnf) No advisory for this CVE applies to the installed packages. Either the host is not affected, or the vendor has not published a fix. | no |
| `not_found` | (Ubuntu) Canonical has no record of this CVE | no |
| `pending` | Affected, and a fix is available but was not installed (check-only) | **yes** |
| `still_vulnerable` | The fix was attempted but the CVE is still reported | **yes** |
| `requires_pro` | (Ubuntu) The fix is only in Ubuntu Pro (ESM). Add a token to the Ubuntu Pro credential. | **yes** |
| `no_fix_available` | (Ubuntu) Affected, but Canonical has not released a fix | **yes** |
| `failed` | The fix attempt returned an error | **yes** |

**Job artifact:** every host's full report is published with `set_stats` as `patch_results`.
You can see it on the job's *Details* tab, and later workflow nodes can read it.
Example:

```yaml
patch_results:
  web01:
    host: web01
    os: Ubuntu 22.04
    status: patched
    packages_changed_count: 2
    packages_changed:
      - {name: curl, before: 7.81.0-1, after: 7.81.0-1ubuntu1.30}
      - {name: libcurl4, before: 7.81.0-1, after: 7.81.0-1ubuntu1.30}
    reboot_required: false
    rebooted: false
    cves: {CVE-2023-38545: fixed}
```

---

## Running from the command line

The playbooks also run without AAP. With
[ansible-navigator](https://docs.redhat.com/en/documentation/red_hat_ansible_automation_platform/latest/html/using_content_navigator/)
and the supported execution environment:

```bash
export AWS_ACCESS_KEY_ID=... AWS_SECRET_ACCESS_KEY=...

# See which hosts and groups the inventory finds
ansible-navigator inventory -i inventory/aws_ec2.yml --graph -m stdout

# Assessment
ansible-navigator run patch_assessment.yml -i inventory/aws_ec2.yml -m stdout \
  --private-key ~/.ssh/my-ec2-keypair.pem

# Security-only patching of dev, two hosts at a time
ansible-navigator run patch_linux.yml -i inventory/aws_ec2.yml -m stdout \
  --private-key ~/.ssh/my-ec2-keypair.pem --limit patch_group_dev \
  -e patch_scope=security -e patch_batch_size=2

# CVE remediation (JSON keeps a list with spaces intact)
ansible-navigator run patch_cves.yml -i inventory/aws_ec2.yml -m stdout \
  --private-key ~/.ssh/my-ec2-keypair.pem \
  -e '{"cve_list": "CVE-2024-6387, CVE-2023-38545", "ubuntu_pro_token": ""}'
```

> Pass lists with spaces as JSON (`-e '{"cve_list": "..."}'`). The `-e key=value` form
> splits on spaces.

---

## Things to know

### Amazon Linux 2023 release versions

AL2023 **locks each instance to the repository release it was built from**. A plain
`dnf upgrade` only installs fixes published to that release. Fixes in newer releases stay
invisible, so a CVE can show up as `not_applicable` even though a newer release fixes it.

* Leave **Amazon Linux 2023 release version** empty to stay on the locked release. This is
  what AWS recommends for controlled change.
* Set it to `latest` to move hosts to the newest release and receive everything published
  since.
* Or set a specific release such as `2023.12.20260930` to move the whole fleet to the same,
  known version.

### Ubuntu CVE fixes and Ubuntu Pro

Ubuntu has no `apt --cve`. The CVE playbook uses Canonical's Ubuntu Pro client, the same
engine as `pro fix CVE-…`, through its JSON API:

* `u.pro.security.fix.cve.plan.v1` reports what would be done.
* `u.pro.security.fix.cve.execute.v1` does it.

The client is brought up to date automatically.

* **No subscription is needed** for fixes in Ubuntu *main*, which covers most server
  packages.
* Fixes published only to **Ubuntu Pro / ESM** show as `requires_pro`. To install them, put
  a token in the **Linux Patching - Ubuntu Pro** credential. Unattached hosts are then
  attached automatically.

### Reboots

`if_required` only reboots hosts whose OS says a reboot is needed: a new kernel, glibc or
systemd. Hosts reboot within their batch. With small batches only a few hosts are down at
any one time.

### Failures and batches

* A host that fails is recorded as `FAILED` in the report and the job is marked failed.
* With **Maximum failure percentage** `0`, a single failure stops the run, and hosts in
  later batches are not touched.
* In that case the final summary is not printed. Use the job output and the `patch_results`
  artifact to see what happened.
* Unreachable hosts appear in the play recap rather than in the report.

### Read-only vs. changes

The **Patch Assessment** and **CVE Patch** (with *Check only* = `true`) jobs do not install
updates or reboot. They may still refresh package metadata. On Ubuntu they can also install
the `python3-apt` and `ubuntu-pro-client` packages the checks need; both ship with standard
Ubuntu server images.

---

## Troubleshooting

| Symptom | Likely cause and fix |
|---|---|
| Inventory sync finds no hosts | The instances lack a `patch_group` tag, are stopped, or are in a region not listed in `inventory/aws_ec2.yml`. The AWS key also needs `ec2:DescribeInstances`. |
| `UNREACHABLE` / `Permission denied (publickey)` | The SSH key doesn't match the instance's key pair, or the user is wrong. Set an `ansible_user` tag on the instance. Check that security groups allow port 22 from AAP. |
| `Missing sudo password` | The SSH user needs passwordless sudo. That is the default on official AMIs. |
| Oracle Linux 8: `SyntaxError` or Python version errors | The execution environment's ansible-core is newer than 2.16. Use the supported EE, or an EE built on ansible-core 2.16. |
| Every AL2023 CVE shows `not_applicable` | The hosts are locked to an old release. Run with `al2023_releasever = latest`. |
| Ubuntu CVEs show `requires_pro` | Add an Ubuntu Pro token to the **Linux Patching - Ubuntu Pro** credential. |
| `Could not get lock /var/lib/dpkg/lock` | `unattended-upgrades` was running. The playbook waits up to 5 minutes; re-run if it still fails. |
| Disk space assertion fails | Free space under `/var`, or lower `patch_min_free_mb` (default 1024 MB) as an extra var. |
| Config as code: `Playbook not found for project` | The project hadn't synced yet. Re-run `configure_aap.yml`; it syncs the project and waits. |

---

## Repository layout

```
linux_patching/
├── patch_linux.yml            # Full patching (all / security)
├── patch_cves.yml             # CVE-targeted patching
├── patch_assessment.yml       # Read-only assessment
├── roles/
│   ├── patch_common/          # preflight, package snapshots, reboot, report, summary, assessment
│   ├── patch_full/            # dnf / apt full patching
│   └── patch_cve/             # CVE validation, dnf --cve, Ubuntu Pro client
├── filter_plugins/patching.py # turns dnf / pro output into report data (unit-tested)
├── inventory/aws_ec2.yml      # AWS EC2 dynamic inventory (amazon.aws.aws_ec2)
├── config_as_code/
│   ├── configure_aap.yml      # applies everything below to AAP
│   ├── inventory.yml          # "the AAP host" (local connection)
│   ├── requirements.yml       # collections for configure_aap.yml
│   ├── local.yml.example      # copy to local.yml (gitignored) for secrets
│   └── group_vars/aap/        # one file per AAP object type
├── execution-environment.yml  # optional custom EE (ansible-builder v3)
├── tests/                     # pytest for the filter plugin
├── .ansible-lint, .yamllint   # lint settings (production profile)
└── .github/workflows/lint.yml # CI: yamllint, ansible-lint, syntax check, pytest
```

### Developing

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
yamllint .
ANSIBLE_HOME="$(mktemp -d)" ansible-lint
pytest -q tests
```

To add another dnf- or apt-based OS, add it to `patch_supported_os` in
`roles/patch_common/defaults/main.yml`. Then map its distribution name to `dnf` or `apt` in
the first task of `roles/patch_common/tasks/preflight.yml`. The patch, CVE, reboot and report
tasks then apply unchanged.
