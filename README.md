# backportipatests

Compares upstream FreeIPA branch `ipa-4-13` commits whose subjects start with `ipatest` against the upstream release implied by the **`python3-ipatests` version** published in compose metadata (SRPM unpacking is optional because compose SRPM trees are often absent).

## Install

```bash
cd backportipatests
python3 -m venv .venv && source .venv/bin/activate   # optional
pip install -e .
```

## Examples

### Basic (RHEL 10 latest nightly, ~two‑month window)

```bash
backport-ipa-tests --rhel-version 10.3 --major 10 --track latest \
  --since-days 60 --output /tmp/list
```

### RHEL 9 latest nightly

```bash
backport-ipa-tests --rhel-version 9.9 --major 9 --track latest \
  --since-days 60 --output /tmp/list-rhel9-latest.txt
```

### Z‑stream compose paths

RHEL 10 Z‑stream uses the PNQ host in the default URL builder; RHEL 9 Z‑stream uses the `updates` path under `devel`.

```bash
# RHEL 10 — product/version labeling 10.2 while pulling Z‑stream compose for 10.2
backport-ipa-tests --rhel-version 10.2 --major 10 --track zstream \
  --since-days 45 --output /tmp/list-rhel10-z.txt

# RHEL 9 — same idea for 9.7-style Z‑stream compose
backport-ipa-tests --rhel-version 9.7 --major 9 --track zstream \
  --since-days 90 --output /tmp/list-rhel9-z.txt
```

### Compose path minor ≠ product minor (`--compose-minor`)

Use this when reporting/Jira uses one minor (e.g. **10.3**) but the **`latest-RHEL-10.{minor}`** segment in the compose URL must track another minor (e.g. compose still under **10.2**):

```bash
backport-ipa-tests --rhel-version 10.3 --major 10 --track latest \
  --compose-minor 2 \
  --since-days 60 --output /tmp/list-compose-minor-override.txt
```

### Full compose URL override (`--compose-url`)

Skips the built‑in URL templates entirely:

```bash
backport-ipa-tests --rhel-version 10.3 --compose-url \
  'http://download.devel.redhat.com/rhel-10/nightly/RHEL-10/latest-RHEL-10.3/compose/CRB/x86_64/os/' \
  --major 10 \
  --since-days 60 --output /tmp/list
```

### Time window (`--since-days`)

```bash
# Last ~14 days
backport-ipa-tests --rhel-version 10.3 --major 10 --track latest \
  --since-days 14 --output /tmp/list-recent.txt

# Roughly four months
backport-ipa-tests --rhel-version 10.3 --major 10 --track latest \
  --since-days 120 --output /tmp/list-long.txt
```

### Upstream Git branch and baseline (`--upstream-branch`, `--baseline-tag`)

Default upstream branch is **`ipa-4-13`**. Set **`--upstream-branch`** when comparing another Pagure branch. Use **`--baseline-tag`** to pin the baseline instead of inferring it from the compose **`python3-ipatests`** version:

```bash
backport-ipa-tests --rhel-version 10.3 --major 10 --track latest \
  --upstream-branch ipa-4-13 \
  --baseline-tag release-4-13-1 \
  --since-days 60 --output /tmp/list-fixed-baseline.txt
```


### Optional SRPM → spec (`--unpack-spec-from-compose-srpm`, `--keep-srpm`)

Only useful when compose actually publishes the SRPM at one of the guessed paths (often 404):

```bash
backport-ipa-tests --rhel-version 10.3 --major 10 --track latest \
  --unpack-spec-from-compose-srpm \
  --keep-srpm /tmp/ipa.src.rpm \
  --since-days 60 --output /tmp/list
```

### Jira helpers (`--print-jira-summary`, `--print-jira-fields-json`)

```bash
# One-line summary for the bug title
backport-ipa-tests --rhel-version 10.3 --major 10 --track latest \
  --since-days 60 --output /tmp/list --print-jira-summary

# Full MCP/API-oriented payload (components + AssignedTeam + Affects versions)
backport-ipa-tests --rhel-version 10.3 --major 10 --track latest \
  --since-days 60 --output /tmp/list --print-jira-fields-json

# Override Jira metadata while printing JSON
backport-ipa-tests --rhel-version 9.9 --major 9 --track latest \
  --since-days 60 --output /tmp/list \
  --print-jira-fields-json \
  --jira-component ipa \
  --jira-assigned-team rhel-idm-ipa \
  --jira-affects-version rhel-9.9
```

**Z‑stream** compose (`--track zstream`): default Affects versions becomes **`rhel-<major>.<minor>.z`** (e.g. `rhel-9.8.z`) unless you set **`--jira-affects-version`**:

```bash
backport-ipa-tests --rhel-version 9.8 --major 9 --track zstream \
  --since-days 60 --output /tmp/list --print-jira-fields-json
```

### Update an existing Jira bug (append missing commits)

The tool scans Pagure for **`ipatest*`** commits after the compose baseline (subject to **`--since-days`**). To **sync an existing bug**, it reads commits already mentioned via **`https://pagure.io/freeipa/c/<40-char-hash>`**, skips those, and **appends only new lines**. Stdout summarizes how many commits were added and the **last Pagure hash that appeared in the old description** (document order).

**From a saved plain-text export** (paste from Jira into a file):

```bash
backport-ipa-tests --rhel-version 10.3 --major 10 --track latest \
  --since-days 120 \
  --merge-with-existing-description-file ~/rhel-bug-description.txt \
  --output /tmp/jira-description-updated.txt
```

**Fetched from Jira** (same env vars as REST: **`JIRA_EMAIL`**, **`JIRA_API_TOKEN`**, optional **`JIRA_URL`** defaulting to `https://redhat.atlassian.net`):

```bash
export JIRA_EMAIL='you@redhat.com'
export JIRA_API_TOKEN='...'
backport-ipa-tests --rhel-version 10.3 --major 10 --track latest \
  --since-days 120 \
  --merge-with-jira-issue RHEL-173351 \
  --output /tmp/jira-description-updated.txt
```

Then paste **`/tmp/jira-description-updated.txt`** into the bug **Description** (or use MCP **`jira_update_issue`** with `fields.description`).

**`--merge-preserve-jira-preamble`** — keep everything **before** `Missing ipatest* commits:` from the old description and only rebuild the commit list (still deduped + appended). Default behavior **refreshes** the compose snapshot lines under your opener while preserving lines above **`Compose repo:`** (e.g. “Automated Bug By Cursor”).

Widen **`--since-days`** when older upstream commits should be considered for the diff.

### Discover all flags

```bash
backport-ipa-tests --help
```

## Notes

- **`--track latest`** uses the `devel` nightly path template; **`zstream`** uses the Z‑stream style paths (RHEL 10 defaults to the PNQ host per internal compose layout).
- Override naming with **`--compose-url`** or **`--compose-minor`** when your compose path does not match `--rhel-version`.

## Jira (RHEL project)

When creating the bug via MCP/API, set:

- **Components:** `ipa` (override with `--jira-component`)
- **AssignedTeam** (`customfield_10606`): `rhel-idm-ipa` (override with `--jira-assigned-team`)
- **Affects versions:** `rhel-<major>.<minor>` when **`--track latest`**; **`rhel-<major>.<minor>.z`** when **`--track zstream`** (override anytime with `--jira-affects-version`)

Emit a ready-to-copy payload:

```bash
backport-ipa-tests ... --print-jira-fields-json
```

Then create with summary/description as usual: pass **`components`** as the top-level `ipa` string and JSON-stringify only the **`additional_fields`** object for **`versions`** (Affected version/s) + AssignedTeam.

### Create the issue from this tool (`--create-jira-issue`)

With **`JIRA_EMAIL`** and **`JIRA_API_TOKEN`** set (optional **`JIRA_URL`**, default `https://redhat.atlassian.net`), the same run can open the bug after writing **`--output`**:

```bash
export JIRA_EMAIL='you@redhat.com'
export JIRA_API_TOKEN='...'
backport-ipa-tests --rhel-version 10.2 --major 10 --track zstream --since-days 60 --output /tmp/list --create-jira-issue
```

Uses **`--jira-project-key`** (default `RHEL`), **`--jira-issue-type`** (default `Bug`), **`--jira-component`**, **`--jira-assigned-team`**, and default **Affects versions** from **`--track`**. The API v3 body uses ADF for the description; v2 string description is tried if v3 rejects the payload.

Before creating, the tool searches for an **open** bug whose summary contains **`python3-ipatests`**, **Components** is **`ipa`** (or **`--jira-component`**), status is **New**, **Planning**, **In Progress**, or **Integration**, and the RHEL version matches (Affected version/s, Fix version/s, or Target RHEL Version). If one exists, **no new issue is created**. When **Fixed in Build** is empty on that issue, a **comment** is added with compose metadata and any commits from this scan not already listed in the description or prior comments.

Environment for optional Jira REST (if not using Cursor MCP): `JIRA_URL`, `JIRA_EMAIL`, `JIRA_API_TOKEN` — also used by **`--merge-with-jira-issue`** and **`--create-jira-issue`**.
