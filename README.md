# linux_patching

Patch a mixed Linux fleet, in AWS, VMware and anywhere else, from **Ansible Automation
Platform (AAP)**:

| Operating system | How it is patched | How CVEs are fixed |
|---|---|---|
| Oracle Linux 8 | `dnf` | `dnf upgrade --cve` (Oracle ELSA advisories) |
| Amazon Linux 2023 | `dnf` | `dnf upgrade --cve` (Amazon ALAS advisories) |
| Ubuntu 22.04 LTS | `apt` | Ubuntu Pro client (`pro fix` API) |
| Ubuntu 24.04 LTS | `apt` | Ubuntu Pro client (`pro fix` API) |

Servers are found automatically from three **sites**. Each site has its own inventory and
SSH credential:

| Site | Where the servers come from | How a server is opted in |
|---|---|---|
| `aws` | AWS EC2 API | Every running instance in the listed regions; EC2 tag `patch_group` adds it to a patch group |
| `vmware` | VMware vCenter | vSphere tag in the category `patch_group` |
| `cmdb` | ServiceNow CMDB: bare metal, other clouds, other hypervisors, remote sites | The CI's patch group field |

**Config as code** creates three playbooks, each available as a job template (one site at a
time) and as an *All Sites* workflow (every site in one launch):

| Playbook | Job template / workflow | What it does |
|---|---|---|
| `patch_assessment.yml` | **Linux Patching - Patch Assessment** [*(All Sites)*] | Read-only. Counts pending updates (and security updates) per host and, optionally, checks which CVEs affect each host. |
| `patch_linux.yml` | **Linux Patching - Full Patch** [*(All Sites)*] | Installs all updates, or security updates only, in rolling batches, then reboots if needed. |
| `patch_cves.yml` | **Linux Patching - CVE Patch** [*(All Sites)*] | You type in CVE numbers; it installs only the updates that fix those CVEs. |

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
                   ┌────────────────────────────── AAP (in AWS) ───────────────────────────────┐
  GitHub ──sync──▶ │ Project "Linux Patching"                                                  │
  (this repo)      │                                                                           │
                   │ Workflow "Full Patch (All Sites)" ── one survey, runs in parallel:        │
                   │   ├─ node aws    → JT Full Patch + inventory AWS EC2 + SSH (AWS)    ──────┼──▶ EC2 instances
  AWS API ◀────────┼───────── inventory "Linux Patching - AWS EC2"  (inventory/aws_ec2.yml)    │
                   │   ├─ node vmware → JT Full Patch + inventory VMware  + SSH (VMware) ──────┼──▶ VMware VMs
  vCenter ◀────────┼───────── inventory "Linux Patching - VMware"   (inventory/vmware.vms.yml) │
                   │   └─ node cmdb   → JT Full Patch + inventory CMDB    + SSH (CMDB)   ──────┼──▶ other servers
  ServiceNow ◀─────┼───────── inventory "Linux Patching - CMDB"     (inventory/cmdb.now.yml)   │
                   └───────────────────────────────────────────────────────────────────────────┘
        Each inventory refreshes itself before every job, so new and retired servers are picked up.
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
of a batch fails, the remaining batches are not started. In an *All Sites* workflow, each
site runs its own batches in parallel with the other sites.

---

## Prerequisites

### 1. Ansible Automation Platform

* AAP 2.5 or later, with an account that can create organizations, credentials, projects,
  inventories and templates. An administrator account is the simplest choice.
* The job templates use AAP's built-in **Default execution environment**. It normally
  includes everything needed: `amazon.aws`, `vmware.vmware` and `servicenow.itsm`.
  * Reading vSphere tags also needs the *vSphere Automation SDK* (Python package
    `vmware-vcenter`).
  * If the VMware inventory sync reports it missing, build the image from
    [`execution-environment.yml`](execution-environment.yml) and set `lp_custom_ee_image`.

> **Oracle Linux 8 note:** OL8's system Python is 3.6. ansible-core **2.16** is the last
> release that can manage it, and that is what the AAP supported execution environment
> provides. If you use a custom execution environment, keep it on ansible-core 2.16.

### 2. Inventory sources: how servers are found

VMware and the CMDB only include a server if it is opted in with a patch group. AWS includes
every running instance in the listed regions, and the `patch_group` tag is optional. Patch
group names become inventory groups named `patch_group_<value>` in every
site, so `patch_group_prod` means the same thing in AWS, VMware and the CMDB.

You don't need all three sites. Remove any you don't use, or aren't ready for, from
`lp_sites_enabled` in [`config_as_code/group_vars/aap/main.yml`](config_as_code/group_vars/aap/main.yml).

#### Site `aws`: EC2 tags ([`inventory/aws_ec2.yml`](inventory/aws_ec2.yml))

All **running** instances in the listed regions are included, whether they are tagged or not.
Instances without a `patch_group` tag are still patched by a run that targets `all` or names
them directly, but they are not in any `patch_group_*` group.

| EC2 tag | Required? | Example values | Creates inventory group |
|---|---|---|---|
| `patch_group` | optional | `dev`, `prod`, `wave1` | `patch_group_dev`, … |
| `os` | optional | `oracle8`, `ubuntu22`, `ubuntu24`, `al2023` | `os_ubuntu24`, … |
| `ansible_user` | optional | `opc` | sets the SSH user for that instance |
| `Name` | recommended | `web01` | used as the host name in AAP |

* The SSH user defaults to `ubuntu` for Ubuntu and `ec2-user` for Amazon Linux and Oracle
  Linux. Those are the defaults of the official AMIs.
* Ubuntu is detected from the `os` tag or the AMI name.
* Edit `regions:` to list your regions. The default is `us-west-1`.

#### Site `vmware`: vSphere tags ([`inventory/vmware.vms.yml`](inventory/vmware.vms.yml))

Only **powered-on** VMs that are not templates and carry a tag in the `patch_group` category
are included. In the vSphere Client go to **Menu → Tags & Custom Attributes**:

1. Create a tag **category** named `patch_group`, set to *One tag* per object and associated
   with *Virtual Machine*.
2. In that category, create tags such as `dev`, `prod` or `wave1`.
3. Assign a tag to each VM you want patched. You can also assign it to a folder or cluster
   and let the tag cascade through your normal processes.
4. *Optional:* create a category `os` with tags `oracle8`, `ubuntu22`, `ubuntu24`. These
   create `os_*` groups.

The host name is the VM name, and AAP connects to the IP reported by VMware Tools. To speed
up large vCenters, set `search_paths` to the datacenters or folders that hold your Linux VMs.

#### Site `cmdb`: ServiceNow ([`inventory/cmdb.now.yml`](inventory/cmdb.now.yml))

Use this for servers that are neither EC2 instances nor vCenter VMs. The CMDB is the source of
truth: setting a server's patch group in ServiceNow adds it to patching.

* The source reads the **Linux Server** class (`cmdb_ci_linux_server`). It includes CIs that
  are *Operational* and have a patch group.
* **The patch group field:** the CMDB has none out of the box. Ask your ServiceNow admin to
  add a choice field **`u_patch_group`** to the Linux Server class, with values such as
  `dev`, `prod` or `wave1`. Alternatively, edit `cmdb.now.yml` to use a column you already
  maintain, such as `environment`.
* AAP connects to the CI's `fqdn`, falling back to `ip_address`.
* `os_oracle8`, `os_ubuntu22`, `os_ubuntu24` and `os_al2023` groups are built from the
  CI's `os` and `os_version`.

> **Avoid double patching.** ServiceNow Discovery usually records EC2 instances and VMware
> VMs too. Set the patch group in ServiceNow only on servers that are *not* covered by the
> `aws` or `vmware` site. Or extend `sysparm_query` in `cmdb.now.yml` to exclude them;
> examples are in the file. A server that appears in two sites would be patched twice by an
> *All Sites* workflow.

### 3. Network access: AAP in AWS reaching every site

AAP must reach every server on **TCP 22**. It must also reach vCenter and ServiceNow on
**TCP 443**, for the inventory syncs. There are two ways to give AAP in AWS a path to
VMware and other datacenters:

| Option | How it works | Config as code |
|---|---|---|
| **Routed network** (VPN or Direct Connect) | AAP's nodes in AWS SSH straight to on-prem servers. | Nothing to change. |
| **AAP execution nodes** (recommended for remote sites) | Install an AAP execution node in each datacenter. It joins AAP's automation mesh, connecting outbound over port 27199, optionally through a hop node. SSH then stays inside the datacenter and only the mesh connection crosses the WAN. | Put the node's instance group in `lp_site_instance_groups`, e.g. `vmware: ["DC1 execution nodes"]`. That site's jobs and inventory syncs then run there. |

Installing execution nodes is part of the AAP installer: add the node to the installer
inventory and re-run it. That step is outside this repo.

The servers themselves must reach their package repositories:

* Oracle: `yum.oracle.com`, or your ULN or Oracle Linux Manager mirror
* Amazon Linux: the AL2023 repositories in your region
* Ubuntu: the archive and security mirrors, plus `ubuntu.com` for CVE lookups by the Pro
  client

### 4. Credentials you will need

Only the rows for the sites you enable are needed.

| What | Site | Where it goes | Used for |
|---|---|---|---|
| AWS access key and secret | aws | `local.yml` → **Linux Patching - AWS** | Reading EC2 instances (`ec2:DescribeInstances`, `ec2:DescribeRegions`) |
| vCenter host, user and password | vmware | `local.yml` → **Linux Patching - vCenter** | Reading VMs and tags (read-only role) |
| ServiceNow instance, user and password | cmdb | `local.yml` → **Linux Patching - ServiceNow** | Reading `cmdb_ci_linux_server` |
| SSH user, private key and optional sudo password | each site | `local.yml` → `lp_ssh` → **Linux Patching - SSH (AWS / VMware / CMDB)** | SSH plus `sudo` on that site's servers |
| Ubuntu Pro token (optional) | all | `local.yml` → **Linux Patching - Ubuntu Pro** | CVE fixes published only to Ubuntu Pro (ESM). Free for up to 5 machines at <https://ubuntu.com/pro/dashboard>. |

**Why one SSH credential per site?** An AAP job template can carry only one Machine (SSH)
credential. Each *All Sites* workflow therefore runs the job template once per site and
hands each run that site's inventory and SSH credential.

Secrets are **never stored in git**. They live in a gitignored `local.yml` on your laptop.
They are written once into encrypted AAP credentials, and AAP injects them at run time. Two
custom credential types are created for this:

* **Ubuntu Pro Token**, injected as an extra var
* **ServiceNow ITSM**, injected as `SN_HOST`, `SN_USERNAME` and `SN_PASSWORD`

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
| Credential types | Ubuntu Pro Token, ServiceNow ITSM | `controller_credential_types.yml` |
| Credentials | AWS, vCenter, ServiceNow, SSH (AWS), SSH (VMware), SSH (CMDB), Ubuntu Pro | `controller_credentials.yml` |
| Project | Linux Patching (this Git repo; syncs on launch) | `controller_projects.yml` |
| Inventories and sources | Linux Patching - AWS EC2 / - VMware / - CMDB | `controller_inventories.yml`, `controller_inventory_sources.yml` |
| Job templates (with surveys) | Patch Assessment, Full Patch, CVE Patch | `controller_templates.yml` (questions in `lp_surveys.yml`) |
| Workflows (with surveys) | the same three, suffixed *(All Sites)* | `controller_workflows.yml` |
| Execution environment | only if `lp_custom_ee_image` is set | `controller_execution_environments.yml` |

Objects that belong to one site are marked `site: aws`, `site: vmware` or `site: cmdb` in
these files. They are created only for the sites in `lp_sites_enabled`. If a site is enabled
but its secrets are missing from `local.yml`, the playbook stops before changing anything and
lists what is missing.

### Customizing

All names and locations are in
[`config_as_code/group_vars/aap/main.yml`](config_as_code/group_vars/aap/main.yml):

| Variable | Default | Change it to… |
|---|---|---|
| `lp_organization` | `Linux Patching` | `Default` to use the existing organization |
| `lp_sites_enabled` | `[aws, vmware, cmdb]` | only the sites you use, e.g. `[aws]` to start |
| `lp_site_instance_groups` | all empty | the execution-node instance group per site (see [Network access](#3-network-access-aap-in-aws-reaching-every-site)) |
| `lp_scm_url` / `lp_scm_branch` | this repo / `main` | point AAP at your fork. Edit the files in `inventory/` there. |
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

In AAP go to **Automation Execution → Templates**, click the rocket icon, answer the survey
and launch.

### All Sites workflows: patch everything in one launch

Launch **Linux Patching - Full Patch (All Sites)**, or the CVE Patch or Assessment workflow.
The first survey question is **Target hosts**, a host pattern applied in every site:

| Target hosts | Targets |
|---|---|
| `all` | every opted-in server in every site |
| `patch_group_dev` | servers with patch group `dev`, whichever site they are in |
| `patch_group_prod:&os_ubuntu24` | prod **and** Ubuntu 24.04 |
| `web01,db01` | specific hosts |

Why not a job **limit**? A limit that matches no host in one site's inventory makes that
site's job fail. A target pattern that matches nothing in a site is simply skipped there.

The workflow fails if any site's job fails. Open the workflow's job graph to see each site's
job and its summary.

### Job templates: one site at a time

The plain job templates default to the AWS inventory and SSH credential. When you launch, you
can pick another site's **inventory** together with that site's **SSH credential**, and set
a **limit**. Always change both together. For example, use inventory *Linux Patching -
VMware* with credential *Linux Patching - SSH (VMware)*.

### Suggested first run

1. Run **Patch Assessment (All Sites)** to see what is pending. It changes nothing.
2. Run **Full Patch (All Sites)** with Target hosts `patch_group_dev` and `reboot_mode =
   if_required`.
3. Roll out to production with **Batch size `25%`** and **Maximum failure percentage `0`**.
   Each site then stops as soon as one of its hosts fails.

---

## Survey reference

The *(All Sites)* workflows ask the same questions, plus **Target hosts** (`patch_hosts`,
default `all`) first.

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

The other sites work the same way. Export the plugin's credentials, then pass that site's
inventory file, user and key:

```bash
# VMware
export VMWARE_HOST=vcenter.example.com VMWARE_USER=... VMWARE_PASSWORD=...
ansible-navigator run patch_assessment.yml -i inventory/vmware.vms.yml -m stdout \
  -u ansible --private-key ~/.ssh/ansible-vmware

# ServiceNow CMDB
export SN_HOST=https://example.service-now.com SN_USERNAME=... SN_PASSWORD=...
ansible-navigator run patch_assessment.yml -i inventory/cmdb.now.yml -m stdout \
  -u ansible --private-key ~/.ssh/ansible-datacenter
```

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
| AWS inventory sync finds no hosts | The instances are stopped, or are in a region not listed in `inventory/aws_ec2.yml`. The AWS key also needs `ec2:DescribeInstances`. |
| VMware sync finds no VMs | The VMs lack a tag in the `patch_group` category (the category name must match exactly), are powered off, or sit outside `search_paths`. The vCenter account also needs read access to tags. |
| VMware sync: `vSphere Automation SDK` / `vmware-vcenter` missing | The execution environment lacks the SDK needed for tags. Build `execution-environment.yml` and set `lp_custom_ee_image`. |
| CMDB sync fails with an invalid field or finds no hosts | `u_patch_group` does not exist on `cmdb_ci_linux_server`, or no operational CI has it set. Add the field, or change `cmdb.now.yml` to use an existing column. |
| A server is patched twice in an All Sites run | It is in two sites, e.g. a VMware VM that also has a patch group in the CMDB. Clear one of them, or exclude it in `cmdb.now.yml`. |
| `UNREACHABLE` / `Permission denied (publickey)` | The SSH key or user doesn't match that site's servers. AWS: set an `ansible_user` tag. Other sites: check `lp_ssh` and the matching SSH credential. When running a job template directly, check you picked the SSH credential that matches the inventory. Check firewalls allow port 22 from AAP, or from the site's execution node. |
| `Missing sudo password` | Set `become_password` for that site in `lp_ssh` (re-run with `lp_update_secrets=true`), or give the user passwordless sudo. |
| Inventory sync or job times out for an on-prem site | AAP in AWS has no network path to that site. See [Network access](#3-network-access-aap-in-aws-reaching-every-site): add a route, or an execution node plus `lp_site_instance_groups`. |
| Config as code stops with `Missing values for enabled sites` | Fill in those values in `local.yml`, or remove the site from `lp_sites_enabled`. |
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
├── inventory/
│   ├── aws_ec2.yml            # site aws:    AWS EC2 (amazon.aws.aws_ec2)
│   ├── vmware.vms.yml         # site vmware: vCenter VMs (vmware.vmware.vms)
│   └── cmdb.now.yml           # site cmdb:   ServiceNow CMDB (servicenow.itsm.now)
├── config_as_code/
│   ├── configure_aap.yml      # applies everything below to AAP
│   ├── inventory.yml          # "the AAP host" (local connection)
│   ├── requirements.yml       # collections for configure_aap.yml
│   ├── local.yml.example      # copy to local.yml (gitignored) for secrets
│   ├── filter_plugins/        # lp_for_sites: keeps objects for enabled sites
│   └── group_vars/aap/        # one file per AAP object type, plus main.yml (sites, names)
├── execution-environment.yml  # optional custom EE (ansible-builder v3)
├── tests/                     # pytest for the filter plugins
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
