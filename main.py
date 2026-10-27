import sys
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
"""
main.py - Entry point and CLI runner for DevOps Job Hunter.

Usage:
  python main.py                     # Full run with default configured filters
  python main.py --max-days 1 --pune-remote # Strict 24-hour scan for Pune & India-Remote
  python main.py --strict            # Enforce strictly verified employer posting dates only
  python main.py --diagnostic        # Display full filtering funnel and diagnostic metrics
  python main.py --stats             # Display database metrics and breakdown
  python main.py --top 15            # Display top 15 ranked opportunities
  python main.py --rescore           # Recalculate scores & pillar matches for stored jobs
  python main.py --export-only       # Re-export CSV from SQLite with latest filters
  python main.py --generate-links    # Generate direct search links for LinkedIn, Naukri, Indeed
  python main.py --dry-run           # Test discovery and scoring without writing to SQLite

100% Free, runs locally on Windows, zero paid APIs.
"""

import argparse
import json
import logging
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

BASE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE_DIR))

from database import JobDatabase, format_utc_to_ist, VALID_OUTREACH_STATUSES, FOREIGN_REMOTE_PATTERN
from excel_export import ExcelJobExporter, deduplicate_jobs
from scoring import JobScorer
from sources import SourceManager, TargetedSearchLinkGenerator
from recruiter_scoring import RecruiterScorer
from recruiter_sources import RecruiterSourceManager, TargetedRecruiterSearchLinkGenerator
from recruiter_outreach import OutreachMessageGenerator


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S"
)
logger = logging.getLogger("devops_job_hunter")


def load_config(config_path: str) -> Dict[str, Any]:
    p = Path(config_path)
    if not p.is_absolute():
        p = BASE_DIR / config_path

    if not p.exists():
        logger.error(f"Configuration file not found: {p}")
        sys.exit(1)

    with open(p, "r", encoding="utf-8") as f:
        return json.load(f)


def print_banner() -> None:
    print(r"""
 ====================================================================
   DEVOPS & CLOUD JOB HUNTER (Pune & India Remote)
   Local, 100% Free, Multi-Source Discovery & Verified Ranking
 ====================================================================
""")


def print_source_report(report: list) -> None:
    print("\n--- SOURCE HEALTH & DISCOVERY AUDIT ---")
    print(f"{'Source':<26} | {'Status':<8} | {'Found':<6} | {'Time (s)':<8} | {'Notes / Errors'}")
    print("-" * 80)
    for row in report:
        err = row.get("error", "None")
        if err != "None" and len(err) > 35:
            err = err[:32] + "..."
        print(f"{row['source']:<26} | {row['status']:<8} | {row['jobs_found']:<6} | {row['duration_sec']:<8.2f} | {err}")
    print("-" * 80)


def print_diagnostic_report(raw_jobs: List[Dict[str, Any]], scored_jobs: List[Dict[str, Any]], max_days: Optional[float], pune_remote: bool, db: Optional[JobDatabase] = None) -> None:
    total_fetched = len(raw_jobs)
    valid_dates = 0
    unknown_dates = 0
    verified_dates = 0
    estimated_dates = 0
    
    rej_age = 0
    rej_location = 0
    rej_senior = 0
    rej_aggregator = 0
    retained_verified = 0
    retained_leads = 0

    now = datetime.now(timezone.utc)

    for j in scored_jobs:
        pdate = j.get("posting_date") or ""
        date_conf = j.get("date_confidence", "Unknown")

        if date_conf == "Verified":
            verified_dates += 1
            valid_dates += 1
        elif date_conf in ["Listing Updated", "Estimated (Feed Indexed Date)"]:
            estimated_dates += 1
            valid_dates += 1
        else:
            unknown_dates += 1

        score = j.get("match_score", 0.0)
        gaps = (j.get("potential_gaps") or "").lower()
        hours_ago = j.get("hours_ago")

        if "aggregator directory" in gaps or "no identifiable employer" in gaps:
            rej_aggregator += 1
            continue

        if "senior role" in gaps or "exceeds" in gaps:
            rej_senior += 1
            continue

        if pune_remote and ("excluded by strict pune/remote" in gaps or "outside india" in gaps):
            rej_location += 1
            continue

        if max_days is not None:
            if hours_ago is None or hours_ago > (max_days * 24.0):
                rej_age += 1
                continue

        if score >= 40.0:
            if date_conf == "Verified":
                retained_verified += 1
            else:
                retained_leads += 1

    age_str = f"{max_days*24:.0f}h" if max_days is not None else "N/A"
    print("\n--- DIAGNOSTIC FILTERING FUNNEL ---")
    print(f"Total Raw Jobs Fetched:             {total_fetched}")
    print(f"  * Jobs with Valid Dates:          {valid_dates} (Verified: {verified_dates}, Updated/Estimated: {estimated_dates})")
    print(f"  * Jobs with Unknown/Missing Date: {unknown_dates}")
    print("Filtered / Rejected Breakdowns:")
    print(f"  * Rejected as Aggregator / No-Co: {rej_aggregator}")
    print(f"  * Rejected for Senior / >3 Years: {rej_senior}")
    print(f"  * Rejected for Non-Pune/Foreign:  {rej_location}")
    print(f"  * Rejected by Age Cutoff ({age_str}):    {rej_age}")
    print("Retained Opportunities (Score >= 40):")
    print(f"  [+] Strictly Verified Matches:    {retained_verified}")
    print(f"  [?] Supplementary Leads (Unver):  {retained_leads}")
    print("-" * 80)

    if db is not None:
        stored = db.get_jobs(limit=2000, exclude_disqualified=False)
        db_total = len(stored)
        db_rej_age = 0
        db_rej_senior = 0
        db_rej_location = 0
        db_rej_unverified = 0

        for sj in stored:
            h_ago = sj.get("hours_ago")
            gaps = (sj.get("potential_gaps") or "").lower()
            d_conf = sj.get("date_confidence") or "Unknown"

            if "senior role" in gaps or "exceeds" in gaps:
                db_rej_senior += 1
            loc_low = (sj.get("location") or "").lower()
            arr_low = (sj.get("work_arrangement") or "").lower()
            if pune_remote:
                is_pune = "pune" in loc_low
                is_remote = "remote" in arr_low or "remote" in loc_low
                if not (is_pune or is_remote):
                    db_rej_location += 1
            if d_conf != "Verified":
                db_rej_unverified += 1
            if max_days is not None:
                if h_ago is None or h_ago > (max_days * 24.0):
                    db_rej_age += 1

        print("\n--- DATABASE STORED JOBS AUDIT ---")
        print(f"Total Stored Jobs in DB:            {db_total}")
        if db_total > 0:
            if max_days is not None:
                print(f"  * Rejected by Age Cutoff ({age_str}):    {db_rej_age} ({db_rej_age/db_total*100:.1f}%)")
            print(f"  * Rejected for Senior / >3 Years: {db_rej_senior} ({db_rej_senior/db_total*100:.1f}%)")
            if pune_remote:
                print(f"  * Rejected by Pune/Remote Filter: {db_rej_location} ({db_rej_location/db_total*100:.1f}%)")
            print(f"  * Non-Verified Date Confidence:   {db_rej_unverified} ({db_rej_unverified/db_total*100:.1f}%)")
            print("EXPLANATION: Previously stored records reflect prior scans (e.g. Oct 14-16).")
            print("When a strict 24-hour or 7-day cutoff is applied, historical jobs are correctly rejected.")
            print("Filters remain strict to guarantee data authenticity; only fresh discoveries qualify.")
        print("-" * 80)


def generate_search_links_doc(filepath: Path) -> None:
    links_data = TargetedSearchLinkGenerator.generate_all_links()
    with open(filepath, "w", encoding="utf-8") as f:
        f.write("# Pre-Filtered Job Search Links (Pune & India Remote)\n\n")
        f.write("Use these direct links to run manual job discovery on platforms protected by anti-bot controls.\n\n")
        for platform, items in links_data.items():
            f.write(f"## {platform}\n\n")
            for item in items:
                f.write(f"- [{item['title']}]({item['url']})\n")
            f.write("\n")
    print(f"[+] Generated pre-filtered search links: {filepath}")


def print_stats(db: JobDatabase, max_days: Optional[float] = None, pune_remote: bool = False, strict: bool = False) -> None:
    stats = db.get_stats()
    print("\n--- DATABASE SUMMARY ---")
    print(f"Total Jobs Tracked:     {stats['total_jobs']}")
    print(f"Discovered Today:       {stats['added_today']}")
    print("Priority Breakdown:    ", ", ".join(f"{k}: {v}" for k, v in sorted(stats["priority_breakdown"].items())))
    print("Application Status:    ", ", ".join(f"{k}: {v}" for k, v in sorted(stats["status_breakdown"].items())))
    print("Date Confidence:       ", ", ".join(f"{k}: {v}" for k, v in sorted(stats["date_confidence_breakdown"].items())))
    
    print_top_jobs(db, limit=5, max_days=max_days, pune_remote=pune_remote, strict=strict)


def print_top_jobs(db: JobDatabase, limit: int = 15, max_days: Optional[float] = None, pune_remote: bool = False, strict: bool = False) -> None:
    jobs = db.get_jobs(
        limit=limit,
        max_age_days=max_days,
        pune_or_remote_only=pune_remote,
        strict_verified_only=strict,
        exclude_disqualified=True
    )
    
    filter_desc = []
    if max_days is not None:
        filter_desc.append(f"<= {max_days*24:.0f} hours ({max_days} days)")
    if pune_remote:
        filter_desc.append("Pune & Remote India only")
    if strict:
        filter_desc.append("Strict Verified Dates only")
    desc_str = f" [Filters: {', '.join(filter_desc)}]" if filter_desc else ""

    print(f"\n--- TOP {len(jobs)} RANKED OPPORTUNITIES{desc_str} ---")
    if not jobs:
        print("[!] No jobs matched the specified filters. Try relaxing --max-days or checking unverified leads.")
        return

    for idx, j in enumerate(jobs, 1):
        age_str = f"{j['hours_ago']:.0f}h ago" if j.get('hours_ago') is not None else "Date unverified"
        ist_str = j.get('posting_date_ist', 'Unknown')
        print(f"{idx}. [{j['priority']} - {j['match_score']:.1f}/100] {j['job_title']} @ {j['company']}")
        print(f"    Location:   {j['location']} ({j['work_arrangement']}) | Posted: {age_str} ({ist_str}) [{j['date_confidence']}]")
        print(f"    Experience: {j['experience_required']} ({j.get('experience_evidence', 'N/A')})")
        
        skills_str = j.get('relevant_skills', '')
        if skills_str and skills_str != '[]':
            print(f"    Skills:     {skills_str}")
        print(f"    Match Why:  {j['match_reasons']}")
        if j.get('potential_gaps') and j['potential_gaps'] != 'None detected':
            print(f"    Gaps:       {j['potential_gaps']}")
        
        canon_url = j.get('canonical_application_url')
        app_url = canon_url if canon_url else j['application_url']
        print(f"    Apply URL:  {app_url}")
        print("")


def run_pipeline(
    config: Dict[str, Any],
    dry_run: bool = False,
    min_score_override: Optional[float] = None,
    max_days_override: Optional[float] = None,
    pune_remote_override: Optional[bool] = None,
    strict_override: Optional[bool] = None,
    diagnostic: bool = False,
    top_n: Optional[int] = None,
    output_format: str = "excel"
) -> Optional[Path]:
    settings = config.get("settings", {})
    filters = config.get("filters", {})

    db_file = BASE_DIR / settings.get("database_file", "jobs.db")
    csv_file = BASE_DIR / settings.get("export_csv_file", "devops_jobs_ranked.csv")
    leads_csv_file = BASE_DIR / settings.get("export_leads_csv_file", "devops_jobs_unverified_leads.csv")
    links_file = BASE_DIR / settings.get("search_links_md_file", "job_search_links.md")
    
    min_score = min_score_override if min_score_override is not None else settings.get("min_export_score", 40.0)
    max_days = max_days_override if max_days_override is not None else filters.get("max_posting_age_days")
    pune_remote = pune_remote_override if pune_remote_override is not None else filters.get("pune_or_remote_india_only", False)
    strict = strict_override if strict_override is not None else filters.get("strict_verified_only", False)

    db = JobDatabase(str(db_file))
    scorer = JobScorer(config)
    source_mgr = SourceManager(config)

    print(f"[*] Discovering opportunities from configured public sources...")
    raw_jobs, report = source_mgr.fetch_all()
    print_source_report(report)

    total_fetched = len(raw_jobs)
    if not raw_jobs:
        print("[!] No jobs discovered across enabled sources.")
        return None

    # Global URL Deduplication across all queries and sources
    deduped_raw_jobs, dupes_removed = deduplicate_jobs(raw_jobs)
    print(f"[*] Deduplicated {total_fetched} raw listings into {len(deduped_raw_jobs)} unique vacancies ({dupes_removed} duplicates removed).")

    inserted_count = 0
    updated_count = 0
    scored_jobs = []

    for raw in deduped_raw_jobs:
        score_data = scorer.score_job(raw)
        raw.update(score_data)
        scored_jobs.append(raw)

        if not dry_run:
            action, _ = db.upsert_job(raw)
            if action == "inserted":
                inserted_count += 1
            else:
                updated_count += 1

    # 1. Categorize Discovered Listings into Verified, Leads, and Rejected
    verified_jobs = []
    unverified_leads = []
    rejected_jobs = []

    rejection_reasons_count: Dict[str, int] = {}
    rejection_sources_count: Dict[str, int] = {}

    for j in scored_jobs:
        score = j.get("match_score", 0.0)
        h_ago = j.get("hours_ago")
        d_conf = (j.get("date_confidence") or "Unknown").strip()
        loc_low = (j.get("location") or "").lower()
        arr_low = (j.get("work_arrangement") or "").lower()
        raw_low = (j.get("raw_snippet") or "").lower()
        src_name = j.get("source_name") or "Unknown"

        is_pune = "pune" in loc_low or "pune" in raw_low
        is_remote = "remote" in arr_low or "remote" in loc_low or "work from home" in raw_low or "anywhere" in loc_low or "worldwide" in loc_low
        has_foreign = bool(re.search(r"\b(united\s+states|usa|u\.s\.|canada|united\s+kingdom|uk|london|germany|poland|brazil|mexico|argentina|australia|singapore|ireland|california|san\s+francisco|san\s+jose|washington|seattle|new\s+york|texas|austin|chicago|bellevue)\b", loc_low))
        has_india_global = bool(re.search(r"\b(india|pune|bangalore|bengaluru|hyderabad|worldwide|anywhere|global)\b", loc_low))
        foreign_restricted = bool(FOREIGN_REMOTE_PATTERN.search(loc_low) or FOREIGN_REMOTE_PATTERN.search(raw_low) or (has_foreign and not has_india_global))

        gaps = (j.get("potential_gaps") or "").lower()
        reasons = (j.get("match_reasons") or "").lower()

        is_senior_disqualified = (score == 0.0 and ("senior" in gaps or "exceeds" in gaps or "senior" in reasons))
        is_location_rejected = pune_remote and not (is_pune or (is_remote and not foreign_restricted))
        is_score_rejected = (score < min_score)
        is_age_rejected = (max_days is not None and h_ago is not None and h_ago > (max_days * 24.0))

        if is_senior_disqualified:
            reason = "Senior / Lead (5+ years exp disqualified)"
            j["rejection_reason"] = reason
            rejected_jobs.append(j)
            rejection_reasons_count[reason] = rejection_reasons_count.get(reason, 0) + 1
            rejection_sources_count[src_name] = rejection_sources_count.get(src_name, 0) + 1
        elif is_location_rejected:
            reason = "Foreign Location / Restricted Remote"
            j["rejection_reason"] = reason
            rejected_jobs.append(j)
            rejection_reasons_count[reason] = rejection_reasons_count.get(reason, 0) + 1
            rejection_sources_count[src_name] = rejection_sources_count.get(src_name, 0) + 1
        elif is_score_rejected:
            reason = f"Score Below Threshold ({score:.1f} < {min_score})"
            j["rejection_reason"] = reason
            rejected_jobs.append(j)
            rejection_reasons_count[reason] = rejection_reasons_count.get(reason, 0) + 1
            rejection_sources_count[src_name] = rejection_sources_count.get(src_name, 0) + 1
        elif is_age_rejected:
            reason = f"Posting Age Exceeded ({h_ago/24.0:.1f}d > {max_days}d)"
            j["rejection_reason"] = reason
            rejected_jobs.append(j)
            rejection_reasons_count[reason] = rejection_reasons_count.get(reason, 0) + 1
            rejection_sources_count[src_name] = rejection_sources_count.get(src_name, 0) + 1
        else:
            if d_conf in ("Verified", "Listing Updated") and (h_ago is not None if max_days is not None else True):
                verified_jobs.append(j)
            elif strict:
                reason = "Unverified Date (Strict Mode Active)"
                j["rejection_reason"] = reason
                rejected_jobs.append(j)
                rejection_reasons_count[reason] = rejection_reasons_count.get(reason, 0) + 1
                rejection_sources_count[src_name] = rejection_sources_count.get(src_name, 0) + 1
            else:
                unverified_leads.append(j)

    filtered_jobs = verified_jobs + unverified_leads

    if diagnostic:
        print_diagnostic_report(raw_jobs, scored_jobs, max_days, pune_remote, db=db)

    # Export to Multi-Tab Excel Workbook
    summary_stats = {
        "total_fetched": total_fetched,
        "dupes_removed": dupes_removed,
        "total_unique": len(deduped_raw_jobs),
        "verified_count": len(verified_jobs),
        "leads_count": len(unverified_leads),
        "rejected_count": len(rejected_jobs)
    }

    excel_exporter = ExcelJobExporter(output_dir=BASE_DIR / "output")
    excel_path = None
    exported_cnt = 0

    if not dry_run:
        excel_path, exported_cnt, _ = excel_exporter.export_categorized_jobs(
            verified_jobs=verified_jobs,
            leads=unverified_leads,
            rejected=rejected_jobs,
            source_report=report,
            summary_stats=summary_stats,
            deduplicate=False
        )
        # Update backward-compatible CSV exports and search links
        db.export_to_csv(
            str(csv_file),
            min_score=min_score,
            max_age_days=max_days,
            pune_or_remote_only=pune_remote,
            strict_verified_only=True
        )
        db.export_to_csv(
            str(leads_csv_file),
            min_score=min_score,
            max_age_days=max_days,
            pune_or_remote_only=pune_remote,
            strict_verified_only=False
        )
        generate_search_links_doc(links_file)
    else:
        excel_path = excel_exporter.generate_filepath()
        exported_cnt = len(verified_jobs)

    # Collect source failure warnings
    source_warnings = []
    for r in report:
        if r.get("status") in ["FAILED", "PARTIAL"] or r.get("error") not in [None, "None"]:
            err_msg = r.get("error") or r.get("status")
            source_warnings.append(f"{r['source']} ({err_msg})")

    # Comprehensive Terminal Summary with Filtering Funnel Audit (Task 9)
    print("\n" + "=" * 76)
    print("  SCAN & FILTERING FUNNEL AUDIT")
    print("=" * 76)
    print(f"Total listings fetched:            {total_fetched}")
    print(f"Duplicate listings removed:        {dupes_removed}")
    print(f"Unique vacancies evaluated:        {len(deduped_raw_jobs)}")
    print("-" * 76)
    print("Qualified Opportunity Breakdown:")
    print(f"  [+] Sheet 1: Verified Opportunities (0-3y, Pune/Remote): {len(verified_jobs)}")
    print(f"  [?] Sheet 2: Unverified Leads (Score >= 40):            {len(unverified_leads)}")
    print(f"  [-] Sheet 3: Disqualified & Excluded Listings:          {len(rejected_jobs)}")
    print("-" * 76)
    print("Rejection Breakdown by Reason (Excluded Listings):")
    for r_reason, r_count in sorted(rejection_reasons_count.items(), key=lambda x: x[1], reverse=True):
        pct = (r_count / len(rejected_jobs) * 100.0) if rejected_jobs else 0.0
        print(f"  * {r_reason:<45}: {r_count:>4} ({pct:>5.1f}%)")
    print("-" * 76)
    print("Rejection Breakdown by Source:")
    for s_name, s_count in sorted(rejection_sources_count.items(), key=lambda x: x[1], reverse=True):
        print(f"  * {s_name:<45}: {s_count:>4}")
    print("-" * 76)
    if dry_run:
        print(f"Excel destination (Dry-Run):       {excel_path}")
        print("[DRY RUN] Evaluated jobs. Database writes skipped.")
    else:
        print(f"Excel report saved:                {excel_path}")
        print(f"  (Sheets: Verified Opportunities, Unverified Leads, Rejected, Source Health)")
    if source_warnings:
        print(f"Source warnings:                   {'; '.join(source_warnings[:2])}")
    print("=" * 76 + "\n")

    # Only print full card details in terminal if explicitly requested
    if output_format == "terminal" or top_n is not None:
        limit = top_n if top_n is not None else 10
        print_top_jobs(db, limit=limit, max_days=max_days, pune_remote=pune_remote, strict=strict)

    return excel_path


def print_recruiter_source_report(report: list) -> None:
    print("\n--- RECRUITER DISCOVERY SOURCE HEALTH AUDIT ---")
    print(f"{'Source':<32} | {'Status':<8} | {'Found':<6} | {'Time (s)':<8} | {'Notes / Errors'}")
    print("-" * 88)
    for row in report:
        err = row.get("error", "None")
        if err != "None" and len(err) > 35:
            err = err[:32] + "..."
        print(f"{row['source']:<32} | {row['status']:<8} | {row['recruiters_found']:<6} | {row['duration_sec']:<8.2f} | {err}")
    print("-" * 88)


def print_recruiter_stats(db: JobDatabase) -> None:
    stats = db.get_recruiter_stats()
    print("\n--- RECRUITER DATABASE SUMMARY ---")
    print(f"Total Recruiters Tracked: {stats['total_recruiters']}")
    print(f"Follow-ups Due:           {stats['follow_ups_due']}")
    print("Priority Breakdown:      ", ", ".join(f"{k}: {v}" for k, v in sorted(stats["priority_breakdown"].items())))
    print("Outreach Status:         ", ", ".join(f"{k}: {v}" for k, v in sorted(stats["status_breakdown"].items())))
    print("Recruiter Types:         ", ", ".join(f"{k}: {v}" for k, v in sorted(stats["type_breakdown"].items())))
    print("Verification Status:     ", ", ".join(f"{k}: {v}" for k, v in sorted(stats["verification_breakdown"].items())))


def print_top_recruiters(
    db: JobDatabase,
    limit: int = 15,
    min_score: Optional[float] = None,
    status: Optional[str] = None,
    specialism: Optional[str] = None,
    location: Optional[str] = None,
    max_age_days: Optional[float] = None,
    pune_remote_only: bool = False
) -> None:
    recruiters = db.get_recruiters(
        status=status,
        min_score=min_score,
        specialism=specialism,
        location=location,
        max_age_days=max_age_days,
        pune_remote_only=pune_remote_only,
        limit=limit
    )

    desc_parts = []
    if specialism:
        desc_parts.append(f"Specialism: {specialism}")
    if pune_remote_only:
        desc_parts.append("Pune & Remote-India only")
    if status:
        desc_parts.append(f"Status: {status}")
    if max_age_days:
        desc_parts.append(f"Evidence <= {max_age_days}d")
    filter_str = f" [Filters: {', '.join(desc_parts)}]" if desc_parts else ""

    print(f"\n--- TOP {len(recruiters)} RANKED RECRUITERS{filter_str} ---")
    if not recruiters:
        print("[!] No recruiters found matching the specified criteria.")
        return

    for idx, r in enumerate(recruiters, 1):
        age_str = f"{r['evidence_hours_ago']:.0f}h ago" if r.get('evidence_hours_ago') is not None else "Unknown"
        conf_str = f"Confidence: {r.get('confidence_score', 0.0):.2f} [{r.get('verification_status')}]"
        specs = r.get("technical_specialisms", "[]")
        if isinstance(specs, str) and specs.startswith("["):
            try:
                specs = ", ".join(json.loads(specs))
            except Exception:
                pass

        print(f"{idx}. [{r['priority']} - {r['match_score']:.1f}/100] {r['full_name']} | {r['recruiter_type']} (ID #{r['id']})")
        print(f"    Company:     {r['current_company']} | Location: {r['location']} ({r['remote_coverage']})")
        print(f"    Specialisms: {specs}")
        print(f"    Status:      {r['contact_status']} | Follow-up: {r.get('follow_up_date') or 'None'}")
        print(f"    Evidence:    {r.get('evidence_summary')[:130]} ({age_str})")
        if r.get('linkedin_url') and r['linkedin_url'] != 'Unknown':
            print(f"    LinkedIn:    {r['linkedin_url']}")
        elif r.get('hiring_evidence_url') and r['hiring_evidence_url'] != 'Unknown':
            print(f"    Evidence URL:{r['hiring_evidence_url']}")
        print(f"    Match Why:   {r['match_reasons']}")
        if r.get('potential_gaps') and r['potential_gaps'] != 'None detected':
            print(f"    Gaps:        {r['potential_gaps']}")
        print("")


def run_recruiter_pipeline(
    config: Dict[str, Any],
    dry_run: bool = False,
    min_score_override: Optional[float] = None,
    max_days_override: Optional[float] = None,
    pune_remote_override: Optional[bool] = None,
    specialism: Optional[str] = None,
    top_n: Optional[int] = None
) -> None:
    settings = config.get("settings", {})
    rec_cfg = config.get("recruiter_discovery", {})

    db_file = BASE_DIR / settings.get("database_file", "jobs.db")
    rec_csv_file = BASE_DIR / settings.get("export_recruiters_csv_file", "devops_recruiters.csv")
    rec_links_file = BASE_DIR / settings.get("recruiter_search_links_md_file", "recruiter_search_links.md")

    db = JobDatabase(str(db_file))

    active_jobs = db.get_jobs(limit=250, exclude_disqualified=True)
    known_companies = {j.get("company", "").strip() for j in active_jobs if j.get("company")}

    scorer = RecruiterScorer(config, known_hiring_companies=known_companies)
    source_mgr = RecruiterSourceManager(config, db_jobs=active_jobs)

    print(f"[*] Discovering technical recruiters and hiring posts from legitimate public sources...")
    raw_recruiters, report = source_mgr.fetch_all()
    print_recruiter_source_report(report)

    if not raw_recruiters:
        print("[!] No recruiters discovered across enabled sources.")
        return

    print(f"[*] Scoring and deduplicating {len(raw_recruiters)} discovered recruiters...")
    inserted_count = 0
    updated_count = 0
    scored_recruiters = []

    for raw in raw_recruiters:
        score_data = scorer.score_recruiter(raw)
        raw.update(score_data)
        scored_recruiters.append(raw)

        if not dry_run:
            action, _ = db.upsert_recruiter(raw)
            if action == "inserted":
                inserted_count += 1
            else:
                updated_count += 1

    if dry_run:
        print(f"[DRY RUN] Evaluated {len(raw_recruiters)} recruiters. Database writes skipped.")
        sorted_recs = sorted(raw_recruiters, key=lambda x: x["match_score"], reverse=True)
        print("\n--- TOP RECRUITERS (DRY RUN) ---")
        for r in sorted_recs[:5]:
            print(f"[{r['priority']} - {r['match_score']:.1f}/100] {r['full_name']} @ {r['current_company']} ({r['location']})")
            print(f"   Why: {r['match_reasons']}\n")
        return

    print(f"[+] Recruiter pipeline complete: {inserted_count} new recruiters added, {updated_count} existing refreshed.")

    # Export to CSV
    print(f"[*] Exporting ranked recruiters to: {rec_csv_file}")
    min_score = min_score_override if min_score_override is not None else rec_cfg.get("min_recruiter_score", 40.0)
    pune_remote = pune_remote_override if pune_remote_override is not None else False

    exported_count = db.export_recruiters_to_csv(
        str(rec_csv_file),
        min_score=min_score,
        specialism=specialism,
        max_age_days=max_days_override,
        pune_remote_only=pune_remote
    )
    print(f"[+] Exported {exported_count} recruiters to: {rec_csv_file}")

    # Generate recruiter search links
    TargetedRecruiterSearchLinkGenerator.write_links_markdown(rec_links_file)
    print(f"[+] Generated pre-filtered recruiter search links: {rec_links_file}")

    # Display summary
    print_recruiter_stats(db)
    print_top_recruiters(
        db,
        limit=top_n or 15,
        min_score=min_score,
        specialism=specialism,
        max_age_days=max_days_override,
        pune_remote_only=pune_remote
    )


def main():
    parser = argparse.ArgumentParser(description="DevOps & Cloud Job Hunter Automation")
    parser.add_argument("--config", default="config.json", help="Path to config.json")
    parser.add_argument("--stats", action="store_true", help="Display database statistics and top opportunities")
    parser.add_argument("--top", type=int, default=None, help="Display top N matching jobs or recruiters")
    parser.add_argument("--rescore", action="store_true", help="Recalculate scores and pillar rankings for all stored jobs")
    parser.add_argument("--export-only", action="store_true", help="Export CSV without network discovery")
    parser.add_argument("--min-score", type=float, default=None, help="Minimum score threshold for CSV export")
    parser.add_argument("--max-days", type=float, default=None, help="Filter for jobs or evidence posted within max days (e.g. 1 for 24h, 7 for 7d)")
    parser.add_argument("--pune-remote", action="store_true", help="Filter strictly for Pune and Remote India roles")
    parser.add_argument("--strict", action="store_true", help="Require verified employer posting dates only")
    parser.add_argument("--diagnostic", action="store_true", help="Show diagnostic filtering funnel and breakdown")
    parser.add_argument("--output-format", choices=["excel", "terminal", "csv"], default="excel", help="Output format for discovered jobs (default: excel)")
    parser.add_argument("--generate-links", action="store_true", help="Generate markdown file with pre-filtered search links")
    parser.add_argument("--revalidate-urls", type=int, nargs="?", const=50, default=None, help="Recheck active liveness status of stored URLs in database")
    parser.add_argument("--dry-run", action="store_true", help="Fetch and score jobs or recruiters without writing to SQLite")
    parser.add_argument("--set-status", nargs=2, metavar=("JOB_ID", "STATUS"), help="Set job status (New, Applied, Reviewed, Archived)")
    parser.add_argument("--notes", help="Notes to attach with --set-status or --set-recruiter-status")

    # Recruiter Discovery Options
    parser.add_argument("--discover-recruiters", action="store_true", help="Discover technical recruiters and hiring posts from public sources")
    parser.add_argument("--list-recruiters", action="store_true", help="Display top ranked recruiters with outreach status")
    parser.add_argument("--export-recruiters", action="store_true", help="Export recruiters from SQLite to devops_recruiters.csv")
    parser.add_argument("--specialism", help="Filter recruiters by technical specialism (e.g. DevOps, AWS, Kubernetes, Terraform)")
    parser.add_argument("--contact-status", help="Filter recruiters by outreach status (Not Contacted, Contacted, Replied, Follow-up Due, Referred, Not Relevant)")
    parser.add_argument("--recruiter-top", type=int, default=None, help="Limit number of recruiters displayed")
    parser.add_argument("--set-recruiter-status", nargs=2, metavar=("RECRUITER_ID", "STATUS"), help="Set recruiter outreach status")
    parser.add_argument("--follow-up", help="Set follow-up date (YYYY-MM-DD) with --set-recruiter-status")
    parser.add_argument("--generate-message", type=int, metavar="RECRUITER_ID", help="Generate personalized LinkedIn connection note and InMail for a recruiter")
    parser.add_argument("--generate-recruiter-links", action="store_true", help="Generate markdown file with pre-filtered recruiter search links")

    args = parser.parse_args()
    print_banner()

    config = load_config(args.config)
    settings = config.get("settings", {})
    filters = config.get("filters", {})

    db_file = BASE_DIR / settings.get("database_file", "jobs.db")
    csv_file = BASE_DIR / settings.get("export_csv_file", "devops_jobs_ranked.csv")
    rec_csv_file = BASE_DIR / settings.get("export_recruiters_csv_file", "devops_recruiters.csv")
    links_file = BASE_DIR / settings.get("search_links_md_file", "job_search_links.md")
    rec_links_file = BASE_DIR / settings.get("recruiter_search_links_md_file", "recruiter_search_links.md")

    db = JobDatabase(str(db_file))
    scorer = JobScorer(config)

    max_days = args.max_days if args.max_days is not None else filters.get("max_posting_age_days")
    pune_remote = args.pune_remote or filters.get("pune_or_remote_india_only", False)
    strict = args.strict or filters.get("strict_verified_only", False)
    recruiter_limit = args.recruiter_top or args.top or 15

    # 1. Recruiter Search Links
    if args.generate_recruiter_links:
        TargetedRecruiterSearchLinkGenerator.write_links_markdown(rec_links_file)
        print(f"[+] Generated pre-filtered recruiter search links: {rec_links_file}")
        return

    # 2. Generate Outreach Message
    if args.generate_message:
        rec_id = int(args.generate_message)
        rec = db.get_recruiter_by_id(rec_id)
        if not rec:
            print(f"[!] Recruiter #{rec_id} not found in database.")
            return
        msg_gen = OutreachMessageGenerator(config)
        all_msgs = msg_gen.generate_all_templates(rec)

        print("=" * 72)
        print(f"  PERSONALIZED OUTREACH FOR: {rec['full_name']} @ {rec['current_company']}")
        print("=" * 72)
        print("\n--- 1. LINKEDIN CONNECTION NOTE (Strict limit: 300 chars) ---")
        note = all_msgs["connection_note"]
        print(f"[{len(note)}/300 chars]:\n")
        print(f'"{note}"')

        print("\n--- 2. INMAIL / FULL MESSAGE TEMPLATE ---")
        print(f"Subject: {all_msgs['inmail']['subject']}\n")
        print(all_msgs['inmail']['body'])

        print("\n--- 3. FOLLOW-UP MESSAGE (5-7 days after initial note) ---")
        print(f"Subject: {all_msgs['follow_up']['subject']}\n")
        print(all_msgs['follow_up']['body'])
        print("\n" + "=" * 72)
        print("POLICY: Review and send manually. Messages are NEVER sent automatically.")
        print("=" * 72 + "\n")
        return

    # 3. Update Recruiter Outreach Status
    if args.set_recruiter_status:
        rec_id = int(args.set_recruiter_status[0])
        new_status = args.set_recruiter_status[1]
        try:
            ok = db.update_recruiter_status(
                rec_id,
                new_status,
                notes=args.notes,
                follow_up_date=args.follow_up
            )
            if ok:
                print(f"[+] Recruiter #{rec_id} status updated to '{new_status}' (Follow-up: {args.follow_up or 'Unset'}).")
            else:
                print(f"[!] Recruiter #{rec_id} not found.")
        except ValueError as ve:
            print(f"[!] {ve}")
        return

    # 4. Export Recruiters Only
    if args.export_recruiters:
        min_score = args.min_score if args.min_score is not None else 0.0
        cnt = db.export_recruiters_to_csv(
            str(rec_csv_file),
            min_score=min_score,
            status_filter=args.contact_status,
            specialism=args.specialism,
            max_age_days=args.max_days,
            pune_remote_only=pune_remote,
            limit=recruiter_limit
        )
        print(f"[+] Exported {cnt} recruiters to: {rec_csv_file}")
        print_top_recruiters(
            db,
            limit=recruiter_limit,
            min_score=min_score,
            status=args.contact_status,
            specialism=args.specialism,
            max_age_days=args.max_days,
            pune_remote_only=pune_remote
        )
        return

    # 5. List Recruiters
    if args.list_recruiters:
        print_recruiter_stats(db)
        print_top_recruiters(
            db,
            limit=recruiter_limit,
            min_score=args.min_score,
            status=args.contact_status,
            specialism=args.specialism,
            max_age_days=args.max_days,
            pune_remote_only=pune_remote
        )
        return

    # 6. Discover Recruiters Pipeline
    if args.discover_recruiters:
        run_recruiter_pipeline(
            config,
            dry_run=args.dry_run,
            min_score_override=args.min_score,
            max_days_override=args.max_days,
            pune_remote_override=pune_remote,
            specialism=args.specialism,
            top_n=recruiter_limit
        )
        return

    # --- EXISTING JOB-HUNTER CLI ACTIONS ---

    if args.revalidate_urls is not None:
        print(f"[*] Revalidating liveness for up to {args.revalidate_urls} stored job URLs...")
        counts = db.revalidate_stored_jobs(limit=args.revalidate_urls)
        print(r"--- ACTIVE URL LIVENESS AUDIT ---")
        for status, cnt in sorted(counts.items()):
            print(f"  * {status:<35}: {cnt}")
        return

    if args.generate_links:
        generate_search_links_doc(links_file)
        return

    if args.set_status:
        job_id = int(args.set_status[0])
        new_status = args.set_status[1]
        ok = db.update_job_status(job_id, new_status, args.notes)
        if ok:
            print(f"[+] Job #{job_id} updated to status '{new_status}'.")
        else:
            print(f"[!] Job #{job_id} not found.")
        return

    if args.rescore:
        all_jobs = db.get_jobs(exclude_disqualified=False)
        print(f"[*] Rescoring {len(all_jobs)} stored jobs...")
        updated = 0
        for j in all_jobs:
            scored = scorer.score_job(j)
            j.update(scored)
            action, _ = db.upsert_job(j)
            if action == "updated":
                updated += 1
        print(f"[+] Rescored {updated} jobs in database.")
        db.export_to_csv(str(csv_file), min_score=settings.get("min_export_score", 40.0), max_age_days=max_days, pune_or_remote_only=pune_remote, strict_verified_only=strict)
        print_top_jobs(db, limit=args.top or 15, max_days=max_days, pune_remote=pune_remote, strict=strict)
        return

    if args.top:
        print_top_jobs(db, limit=args.top, max_days=max_days, pune_remote=pune_remote, strict=strict)
        return

    if args.stats:
        print_stats(db, max_days=max_days, pune_remote=pune_remote, strict=strict)
        return

    if args.export_only:
        min_score = args.min_score if args.min_score is not None else settings.get("min_export_score", 40.0)
        if args.output_format == "excel":
            excel_path, cnt, dupes = db.export_to_excel(
                output_dir=BASE_DIR / "output",
                min_score=min_score,
                max_age_days=max_days,
                pune_or_remote_only=pune_remote,
                strict_verified_only=strict,
                limit=args.top
            )
            print("\n" + "=" * 68)
            print("  EXCEL EXPORT SUMMARY")
            print("=" * 68)
            print(f"Number of jobs exported:        {cnt}")
            print(f"Duplicates removed:             {dupes}")
            print(f"Excel report saved:             {excel_path}")
            print("=" * 68 + "\n")
        else:
            cnt = db.export_to_csv(
                str(csv_file),
                min_score=min_score,
                max_age_days=max_days,
                pune_or_remote_only=pune_remote,
                strict_verified_only=strict,
                limit=args.top
            )
            print(f"[+] Exported {cnt} jobs to {csv_file}")
            print_top_jobs(db, limit=args.top or 15, max_days=max_days, pune_remote=pune_remote, strict=strict)
        return

    run_pipeline(
        config,
        dry_run=args.dry_run,
        min_score_override=args.min_score,
        max_days_override=args.max_days,
        pune_remote_override=pune_remote,
        strict_override=args.strict,
        diagnostic=args.diagnostic,
        top_n=args.top,
        output_format=args.output_format
    )


if __name__ == "__main__":
    main()

