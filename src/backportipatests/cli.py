"""CLI entrypoint."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from backportipatests.compose_urls import default_os_repo_url
from backportipatests.git_compare import (
    ensure_clone,
    format_report_lines,
    iter_missing_commits,
    nearest_release_4_13_tag,
    resolve_baseline_ref,
    utc_since_days,
)
from backportipatests.repodata import (
    compose_root_from_os_url,
    download_first_available,
    find_python3_ipatests,
    freeipa_version_to_tag_candidates,
    guess_srpm_urls,
    parse_spec_version,
)
from backportipatests.jira_create import browse_url, create_jira_issue, update_jira_issue
from backportipatests.jira_fetch import fetch_jira_issue_description, jira_credentials
from backportipatests.jira_spec import (
    DEFAULT_ASSIGNED_TEAM_VALUE,
    DEFAULT_JIRA_COMPONENT,
    coerce_jira_affects_version_display_name,
    jira_create_additional_fields,
    jira_summary_line,
)
from backportipatests.report_merge import (
    MISSING_HEADER,
    latest_listed_commit_full_hash,
    merge_report_with_existing_description,
)
from backportipatests.srpm_spec import extract_spec_text, extract_upstream_branch


def _parse_rhel_version(s: str) -> tuple[int, int]:
    parts = s.strip().split(".", 1)
    if len(parts) != 2:
        raise argparse.ArgumentTypeError("Expected MAJOR.MINOR like 10.3")
    major_s, minor_s = parts
    return int(major_s), int(minor_s)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description=(
            "List Pagure freeipa commits (subject starts with 'ipatest') not contained "
            "in the compose baseline implied by python3-ipatests."
        )
    )
    p.add_argument(
        "--rhel-version",
        type=_parse_rhel_version,
        required=True,
        help="Product version for reporting/Jira, e.g. 10.3",
    )
    p.add_argument(
        "--major",
        type=int,
        default=None,
        help="RHEL major for compose URL (defaults from --rhel-version)",
    )
    p.add_argument(
        "--track",
        choices=("latest", "zstream"),
        default="latest",
        help="Compose track (latest nightly vs Z-stream updates)",
    )
    p.add_argument(
        "--compose-url",
        default=None,
        help="Override CRB x86_64 os/ repo URL (skips default URL builder)",
    )
    p.add_argument(
        "--compose-minor",
        type=int,
        default=None,
        help="Override minor used only in latest-RHEL-X.{minor} compose path segment",
    )
    p.add_argument(
        "--since-days",
        type=int,
        default=60,
        help="Only include commits newer than this many days (default: 60 ~= two months)",
    )
    p.add_argument(
        "--upstream-branch",
        default="ipa-4-13",
        help="Pagure branch to compare (default: ipa-4-13)",
    )
    p.add_argument(
        "--baseline-tag",
        default=None,
        help="Override baseline tag/ref resolved from SRPM Version",
    )
    p.add_argument(
        "--git-cache",
        type=Path,
        default=Path.home() / ".cache" / "backportipatests" / "git",
        help="Directory for bare clone cache",
    )
    p.add_argument(
        "--unpack-spec-from-compose-srpm",
        action="store_true",
        help=(
            "Try downloading ipa/freeipa SRPM from compose paths (often unavailable); "
            "when successful, prefer upstream branch + Version lines parsed from the spec."
        ),
    )
    p.add_argument(
        "--keep-srpm",
        type=Path,
        default=None,
        help="When using --unpack-spec-from-compose-srpm, save SRPM to this path",
    )
    p.add_argument(
        "--output",
        type=Path,
        default=Path("/tmp/list"),
        help="Report output path",
    )
    p.add_argument(
        "--print-jira-summary",
        action="store_true",
        help="Print suggested Jira summary line to stdout",
    )
    p.add_argument(
        "--print-jira-fields-json",
        action="store_true",
        help=(
            "Print JSON with project_key, summary, issue_type, components, and "
            "additional_fields (AssignedTeam + Affects versions) for MCP/API create."
        ),
    )
    p.add_argument(
        "--jira-component",
        default=DEFAULT_JIRA_COMPONENT,
        help=f"Jira Components value (default: {DEFAULT_JIRA_COMPONENT})",
    )
    p.add_argument(
        "--jira-assigned-team",
        default=DEFAULT_ASSIGNED_TEAM_VALUE,
        help=(
            "AssignedTeam dropdown value customfield_10606 "
            f"(default: {DEFAULT_ASSIGNED_TEAM_VALUE})"
        ),
    )
    p.add_argument(
        "--jira-affects-version",
        default=None,
        help=(
            "Affects versions name for --create-jira-issue / --jira-update-issue "
            "(default: rhel-<rhel-version major>.<minor> with --track latest; "
            "rhel-<major>.<minor>.z with --track zstream). "
            "With --track zstream, an explicit rhel-<major>.<minor> value is "
            "normalized to rhel-<major>.<minor>.z."
        ),
    )
    p.add_argument(
        "--jira-no-affects-version",
        action="store_true",
        help=(
            "Do not send versions (Affected version/s) in Jira API payloads (use if create/update "
            "screens omit that field). Otherwise the client retries automatically when "
            "Jira returns 'cannot be set / not on the appropriate screen'."
        ),
    )
    jira_action = p.add_mutually_exclusive_group()
    jira_action.add_argument(
        "--create-jira-issue",
        action="store_true",
        help=(
            "After writing --output, create a Jira issue via REST using JIRA_EMAIL + "
            "JIRA_API_TOKEN (optional JIRA_URL). Uses --jira-project-key, "
            "--jira-issue-type, --jira-component, AssignedTeam, and Affects versions."
        ),
    )
    jira_action.add_argument(
        "--jira-update-issue",
        metavar="KEY",
        default=None,
        help=(
            "After writing --output, update this issue via REST: set description to the "
            "report text and set Affects versions (plus AssignedTeam from "
            "--jira-assigned-team) for the RHEL version from --rhel-version / --track / "
            "--jira-affects-version. Requires JIRA_EMAIL + JIRA_API_TOKEN."
        ),
    )
    p.add_argument(
        "--jira-project-key",
        default="RHEL",
        metavar="KEY",
        help="Project key for --create-jira-issue (default: RHEL)",
    )
    p.add_argument(
        "--jira-issue-type",
        default="Bug",
        metavar="NAME",
        help="Issue type for --create-jira-issue (default: Bug)",
    )

    merge_grp = p.add_argument_group("merge into existing Jira description")
    mx = merge_grp.add_mutually_exclusive_group()
    mx.add_argument(
        "--merge-with-existing-description-file",
        type=Path,
        metavar="PATH",
        default=None,
        help=(
            "Plain-text description copied from an existing bug; append commits found "
            "by this run that are not already listed (by Pagure URL hash)."
        ),
    )
    mx.add_argument(
        "--merge-with-jira-issue",
        metavar="KEY",
        default=None,
        help=(
            "Fetch description from Jira (REST). Requires JIRA_EMAIL + JIRA_API_TOKEN "
            "(optional JIRA_URL, default https://redhat.atlassian.net)."
        ),
    )
    merge_grp.add_argument(
        "--merge-preserve-jira-preamble",
        action="store_true",
        help=(
            "Keep the existing description text before 'Missing ipatest* commits:' and "
            "refresh only the commit list; default is to replace metadata lines with "
            "this run's compose snapshot header."
        ),
    )

    args = p.parse_args(argv)
    prod_major, prod_minor = args.rhel_version
    major = args.major if args.major is not None else prod_major

    os_repo = args.compose_url or default_os_repo_url(
        major=major,
        minor=prod_minor,
        track=args.track,
        compose_minor=args.compose_minor,
    )

    pkg = find_python3_ipatests(os_repo)
    compose_root = compose_root_from_os_url(os_repo)

    upstream_branch = args.upstream_branch
    upstream_ver = pkg.version

    if args.unpack_spec_from_compose_srpm:
        srpm_dest = args.keep_srpm
        if srpm_dest is None:
            srpm_dest = Path("/tmp") / pkg.sourcerpm
        srpm_urls = guess_srpm_urls(compose_root, pkg.sourcerpm)
        download_first_available(srpm_urls, str(srpm_dest))
        spec_body = extract_spec_text(Path(srpm_dest))
        upstream_branch = extract_upstream_branch(spec_body, default=args.upstream_branch)
        upstream_ver = parse_spec_version(spec_body)

    repo = ensure_clone(branch=upstream_branch, cache_dir=args.git_cache)

    if args.baseline_tag:
        baseline_candidates = [args.baseline_tag]
        baseline_ref = resolve_baseline_ref(repo, baseline_candidates)
    else:
        baseline_candidates = freeipa_version_to_tag_candidates(upstream_ver)
        try:
            baseline_ref = resolve_baseline_ref(repo, baseline_candidates)
        except RuntimeError as e:
            micro: int | None = None
            pv = upstream_ver.split(".")
            if len(pv) >= 3 and pv[0] == "4" and pv[1] == "13" and pv[2].isdigit():
                micro = int(pv[2])
            fallback_tag = nearest_release_4_13_tag(repo, max_micro=micro)
            if fallback_tag is None:
                raise RuntimeError(
                    "Could not resolve an upstream baseline tag for "
                    f"{upstream_ver!r} (candidates={baseline_candidates!r})"
                ) from e
            baseline_ref = resolve_baseline_ref(repo, [fallback_tag])
            baseline_candidates = baseline_candidates + [fallback_tag]
    since = utc_since_days(args.since_days)
    rows = iter_missing_commits(
        repo,
        branch=upstream_branch,
        baseline_ref=baseline_ref,
        since_utc=since,
    )

    scan_meta_lines = [
        f"Compose repo: {os_repo}",
        f"python3-ipatests: {pkg.version}-{pkg.release}.{pkg.arch}",
        f"SRPM (metadata): {pkg.sourcerpm}",
        f"Baseline upstream Version (NVRA or unpacked spec): {upstream_ver}",
        f"Baseline ref resolved to: {baseline_ref}",
        f"Upstream branch: {upstream_branch}",
        f"Since (UTC): {since.isoformat()}",
    ]
    body_lines = format_report_lines(rows)

    args.output.parent.mkdir(parents=True, exist_ok=True)

    merge_file = args.merge_with_existing_description_file
    merge_issue = args.merge_with_jira_issue

    if merge_file is not None:
        existing = merge_file.read_text(encoding="utf-8", errors="replace")
        latest_prior = latest_listed_commit_full_hash(existing)
        merged, n_new, n_total = merge_report_with_existing_description(
            existing_description=existing,
            scan_header_lines=scan_meta_lines,
            rows_from_scan=rows,
            format_lines=format_report_lines,
            refresh_scan_header=not args.merge_preserve_jira_preamble,
        )
        report_text = merged
        args.output.write_text(report_text, encoding="utf-8")
        print(
            f"Wrote {args.output} (merge: +{n_new} new commits, {n_total} total listed in bug; "
            f"latest Pagure hash previously listed: {latest_prior or 'n/a'})"
        )
    elif merge_issue is not None:
        existing = fetch_jira_issue_description(merge_issue.strip())
        latest_prior = latest_listed_commit_full_hash(existing)
        merged, n_new, n_total = merge_report_with_existing_description(
            existing_description=existing,
            scan_header_lines=scan_meta_lines,
            rows_from_scan=rows,
            format_lines=format_report_lines,
            refresh_scan_header=not args.merge_preserve_jira_preamble,
        )
        report_text = merged
        args.output.write_text(report_text, encoding="utf-8")
        print(
            f"Wrote {args.output} (merge from {merge_issue.strip()}: +{n_new} new commits, "
            f"{n_total} total listed; latest Pagure hash previously listed: {latest_prior or 'n/a'})"
        )
    else:
        header_lines = scan_meta_lines + ["", MISSING_HEADER, ""]
        out_body = body_lines if body_lines else ["(none)"]
        report_text = "\n".join(header_lines + out_body) + "\n"
        args.output.write_text(report_text, encoding="utf-8")
        print(f"Wrote {args.output} ({len(rows)} commits)")

    if args.create_jira_issue:
        base, _, _ = jira_credentials()
        extra = jira_create_additional_fields(
            prod_major=prod_major,
            prod_minor=prod_minor,
            track=args.track,
            assigned_team=args.jira_assigned_team,
            affects_version=args.jira_affects_version,
            include_affects_versions=not args.jira_no_affects_version,
        )
        created = create_jira_issue(
            description_plain=report_text,
            summary=jira_summary_line(prod_major=prod_major, prod_minor=prod_minor),
            project_key=args.jira_project_key,
            issue_type=args.jira_issue_type,
            components_csv=args.jira_component,
            extra_fields=extra,
        )
        key = created.get("key", "?")
        print(f"Created Jira issue {key}: {browse_url(base, key)}")
    elif args.jira_update_issue is not None:
        base, _, _ = jira_credentials()
        extra = jira_create_additional_fields(
            prod_major=prod_major,
            prod_minor=prod_minor,
            track=args.track,
            assigned_team=args.jira_assigned_team,
            affects_version=args.jira_affects_version,
            include_affects_versions=not args.jira_no_affects_version,
        )
        update_jira_issue(
            issue_key=args.jira_update_issue.strip(),
            description_plain=report_text,
            extra_fields=extra,
        )
        k = args.jira_update_issue.strip().upper()
        ver = coerce_jira_affects_version_display_name(
            prod_major=prod_major,
            prod_minor=prod_minor,
            track=args.track,
            affects_version=args.jira_affects_version,
        )
        print(
            f"Updated Jira issue {k} (description + Affects versions → {ver}): "
            f"{browse_url(base, k)}"
        )

    if args.print_jira_summary:
        print(jira_summary_line(prod_major=prod_major, prod_minor=prod_minor))

    if args.print_jira_fields_json:
        extra = jira_create_additional_fields(
            prod_major=prod_major,
            prod_minor=prod_minor,
            track=args.track,
            assigned_team=args.jira_assigned_team,
            affects_version=args.jira_affects_version,
            include_affects_versions=not args.jira_no_affects_version,
        )
        blob = {
            "project_key": args.jira_project_key,
            "summary": jira_summary_line(
                prod_major=prod_major, prod_minor=prod_minor
            ),
            "issue_type": args.jira_issue_type,
            "components": args.jira_component,
            "additional_fields": extra,
            "note": (
                "Pass components as the top-level components argument to jira_create_issue; "
                "pass additional_fields as JSON string of additional_fields only."
            ),
        }
        print(json.dumps(blob, indent=2))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
