"""
database.py - SQLite persistence, URL normalization, deduplication, liveness checking, and CSV export.

Designed for DevOps Job Hunter. 
Runs 100% locally with Python standard library (sqlite3, urllib, csv, hashlib).
"""

import csv
import hashlib
import json
import logging
import os
import re
import sqlite3
import urllib.request
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

logger = logging.getLogger("devops_job_hunter.database")

# Common URL tracking parameters stripped during normalization
TRACKING_PARAMS = {
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "trk", "trackingid", "refid", "position", "pagenum", "midsig", "eid",
    "origin", "ref", "gclid", "fbclid", "si", "spm", "_hsenc", "_hsmi",
    "mc_cid", "mc_eid", "context", "tracking", "originalsubdomain"
}

FOREIGN_REMOTE_PATTERN = re.compile(
    r"\b(us\s+only|usa\s+only|uk\s+only|europe\s+only|americas\s+only|north\s+america|"
    r"us\/canada|emea|latam|canada\s+only|united\s+states\s+only|us\s+citizen|"
    r"must\s+reside\s+in\s+the\s+us|must\s+reside\s+in\s+the\s+united\s+states|"
    r"us\s+timezone|pst\s+timezone|est\s+timezone)\b",
    re.IGNORECASE
)


def normalize_url(url: str) -> str:
    """
    Normalizes a job URL by lowercasing scheme/host, removing tracking parameters,
    and stripping trailing slashes.
    """
    if not url or url.strip() == "" or url.lower() == "unknown":
        return "Unknown"

    try:
        parsed = urlparse(url.strip())
        if not parsed.scheme or not parsed.netloc:
            return url.strip()

        query_pairs = parse_qsl(parsed.query, keep_blank_values=False)
        clean_pairs = [
            (k, v) for k, v in query_pairs
            if k.lower() not in TRACKING_PARAMS
        ]
        clean_query = urlencode(sorted(clean_pairs))

        scheme = parsed.scheme.lower()
        netloc = parsed.netloc.lower()
        if netloc.endswith(":80"):
            netloc = netloc[:-3]
        elif netloc.endswith(":443"):
            netloc = netloc[:-4]

        path = parsed.path.rstrip("/") if parsed.path != "/" else "/"

        clean_url = urlunparse((
            scheme,
            netloc,
            path,
            "",
            clean_query,
            ""
        ))
        return clean_url
    except Exception:
        return url.strip()


def compute_dedup_hash(company: str, title: str) -> str:
    """
    Generates a deterministic hash for secondary deduplication
    based on normalized company and job title.
    """
    norm_c = re.sub(r"[^a-z0-9]", "", (company or "").lower())
    norm_t = re.sub(r"[^a-z0-9]", "", (title or "").lower())
    
    for suffix in ["pvtltd", "ltd", "inc", "corp", "llc", "technologies", "technology", "solutions", "india"]:
        if norm_c.endswith(suffix) and len(norm_c) > len(suffix):
            norm_c = norm_c[:-len(suffix)]
            
    raw = f"{norm_c}::{norm_t}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


VALID_OUTREACH_STATUSES = [
    "Not Contacted",
    "Connection Requested",
    "Contacted",
    "Replied",
    "Follow-up Due",
    "Referred",
    "Not Relevant"
]


def compute_recruiter_dedup_hash(name: str, company: str, linkedin_url: Optional[str] = None) -> str:
    """
    Generates a deterministic hash for recruiter deduplication
    based on normalized name, company, and optional profile URL.
    """
    if linkedin_url and linkedin_url.lower() not in ["unknown", ""]:
        norm_url = normalize_url(linkedin_url)
        slug_match = re.search(r"linkedin\.com/in/([^/?#]+)", norm_url)
        if slug_match:
            slug = slug_match.group(1).lower().strip()
            return hashlib.sha256(f"linkedin::{slug}".encode("utf-8")).hexdigest()[:16]

    norm_n = re.sub(r"[^a-z0-9]", "", (name or "").lower())
    for prefix in ["mr", "ms", "mrs", "dr"]:
        if norm_n.startswith(prefix) and len(norm_n) > len(prefix):
            norm_n = norm_n[len(prefix):]
    
    norm_c = re.sub(r"[^a-z0-9]", "", (company or "").lower())
    for suffix in ["pvtltd", "ltd", "inc", "corp", "llc", "technologies", "technology", "solutions", "india"]:
        if norm_c.endswith(suffix) and len(norm_c) > len(suffix):
            norm_c = norm_c[:-len(suffix)]
            
    raw = f"{norm_n}::{norm_c}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]



def format_utc_to_ist(utc_iso: Optional[str]) -> str:
    """Converts a UTC ISO timestamp to a human-readable IST string."""
    if not utc_iso or utc_iso.lower() in ["unknown", "unverified"]:
        return "Unknown"
    try:
        if "t" in utc_iso.lower():
            dt = datetime.fromisoformat(utc_iso.replace("Z", "+00:00"))
        else:
            dt = datetime.strptime(utc_iso[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc)
        ist_tz = timezone(timedelta(hours=5, minutes=30))
        ist_dt = dt.astimezone(ist_tz)
        return ist_dt.strftime("%Y-%m-%d %H:%M IST")
    except Exception:
        return utc_iso


class JobDatabase:
    """Manages SQLite storage and query operations for discovered jobs."""

    def __init__(self, db_path: str = "jobs.db"):
        self.db_path = db_path
        self.init_db()

    @contextmanager
    def _get_connection(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    def init_db(self) -> None:
        """Initializes database schema and ensures all required columns exist."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS jobs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    normalized_url TEXT UNIQUE,
                    dedup_hash TEXT,
                    job_title TEXT NOT NULL,
                    company TEXT NOT NULL,
                    location TEXT,
                    work_arrangement TEXT,
                    application_url TEXT,
                    source_url TEXT,
                    source_name TEXT,
                    posting_date TEXT,
                    date_confidence TEXT,
                    experience_required TEXT,
                    relevant_skills TEXT,
                    match_reasons TEXT,
                    potential_gaps TEXT,
                    match_score REAL DEFAULT 0.0,
                    priority TEXT DEFAULT 'Low',
                    first_seen_date TEXT,
                    last_seen_date TEXT,
                    status TEXT DEFAULT 'New',
                    notes TEXT DEFAULT '',
                    raw_snippet TEXT DEFAULT '',
                    liveness_status TEXT DEFAULT 'Active',
                    experience_evidence TEXT DEFAULT '',
                    posting_date_evidence TEXT DEFAULT '',
                    canonical_application_url TEXT DEFAULT '',
                    last_checked_date TEXT DEFAULT ''
                )
            """)
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_jobs_score ON jobs(match_score)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_jobs_hash ON jobs(dedup_hash)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status)")
            
            # Check for column presence and dynamically add if missing
            cursor.execute("PRAGMA table_info(jobs)")
            cols = [r[1] for r in cursor.fetchall()]
            new_columns = [
                ("liveness_status", "TEXT DEFAULT 'Active'"),
                ("experience_evidence", "TEXT DEFAULT ''"),
                ("posting_date_evidence", "TEXT DEFAULT ''"),
                ("canonical_application_url", "TEXT DEFAULT ''"),
                ("last_checked_date", "TEXT DEFAULT ''")
            ]
            for col_name, col_def in new_columns:
                if col_name not in cols:
                    cursor.execute(f"ALTER TABLE jobs ADD COLUMN {col_name} {col_def}")

            # Initialize recruiters table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS recruiters (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    full_name TEXT NOT NULL,
                    linkedin_url TEXT,
                    normalized_url TEXT UNIQUE,
                    dedup_hash TEXT,
                    current_company TEXT DEFAULT 'Unknown',
                    recruiter_type TEXT DEFAULT 'Unverified',
                    technical_specialisms TEXT DEFAULT '[]',
                    location TEXT DEFAULT 'Unknown',
                    remote_coverage TEXT DEFAULT 'Unknown',
                    related_job_title TEXT DEFAULT '',
                    related_job_id INTEGER DEFAULT NULL,
                    hiring_evidence_url TEXT DEFAULT '',
                    evidence_summary TEXT DEFAULT '',
                    evidence_date TEXT DEFAULT 'Unknown',
                    first_discovered_date TEXT,
                    last_verified_date TEXT,
                    contact_details TEXT DEFAULT '',
                    contact_status TEXT DEFAULT 'Not Contacted',
                    last_contacted_date TEXT DEFAULT '',
                    follow_up_date TEXT DEFAULT '',
                    response_notes TEXT DEFAULT '',
                    verification_status TEXT DEFAULT 'Estimated / Unverified',
                    confidence_score REAL DEFAULT 0.0,
                    match_score REAL DEFAULT 0.0,
                    priority TEXT DEFAULT 'Low',
                    match_reasons TEXT DEFAULT '',
                    potential_gaps TEXT DEFAULT '',
                    notes TEXT DEFAULT '',
                    raw_snippet TEXT DEFAULT ''
                )
            """)
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_recruiters_score ON recruiters(match_score)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_recruiters_hash ON recruiters(dedup_hash)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_recruiters_status ON recruiters(contact_status)")

            # Initialize recruiter-to-jobs relationship table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS recruiter_jobs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    recruiter_id INTEGER NOT NULL,
                    job_id INTEGER NOT NULL,
                    relationship_type TEXT DEFAULT 'Hiring Contact',
                    created_at TEXT,
                    UNIQUE(recruiter_id, job_id)
                )
            """)
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_recruiter_jobs ON recruiter_jobs(recruiter_id, job_id)")

            # Migrate recruiters columns if table was created previously
            cursor.execute("PRAGMA table_info(recruiters)")
            rec_cols = [r[1] for r in cursor.fetchall()]
            rec_new_columns = [
                ("match_reasons", "TEXT DEFAULT ''"),
                ("potential_gaps", "TEXT DEFAULT ''"),
                ("remote_coverage", "TEXT DEFAULT 'Unknown'"),
                ("response_notes", "TEXT DEFAULT ''"),
                ("confidence_score", "REAL DEFAULT 0.0"),
                ("contact_details", "TEXT DEFAULT ''"),
                ("follow_up_date", "TEXT DEFAULT ''"),
                ("last_contacted_date", "TEXT DEFAULT ''")
            ]
            for col_name, col_def in rec_new_columns:
                if col_name not in rec_cols:
                    cursor.execute(f"ALTER TABLE recruiters ADD COLUMN {col_name} {col_def}")

            conn.commit()


    def upsert_job(self, job_data: Dict[str, Any]) -> Tuple[str, int]:
        """
        Inserts a new job or updates an existing job while preserving user state.
        Returns: ('inserted' | 'updated', job_id)
        """
        raw_app_url = job_data.get("application_url", "")
        raw_src_url = job_data.get("source_url", "")
        clean_url = normalize_url(raw_app_url or raw_src_url)
        
        job_title = (job_data.get("job_title") or "").strip()
        company = (job_data.get("company") or "").strip()
        dedup_hash = compute_dedup_hash(company, job_title)

        now_iso = datetime.now(timezone.utc).isoformat()
        skills_str = json.dumps(job_data.get("relevant_skills", []))

        with self._get_connection() as conn:
            cursor = conn.cursor()
            
            # Check primary match by normalized URL
            cursor.execute("SELECT id, status, notes FROM jobs WHERE normalized_url = ?", (clean_url,))
            existing = cursor.fetchone()

            # Check secondary match by dedup hash
            if not existing and dedup_hash:
                cursor.execute("SELECT id, status, notes FROM jobs WHERE dedup_hash = ?", (dedup_hash,))
                existing = cursor.fetchone()

            if existing:
                existing_id = existing["id"]
                cursor.execute("""
                    UPDATE jobs SET
                        last_seen_date = ?,
                        match_score = ?,
                        priority = ?,
                        match_reasons = ?,
                        potential_gaps = ?,
                        posting_date = ?,
                        date_confidence = ?,
                        experience_required = ?,
                        experience_evidence = ?,
                        posting_date_evidence = ?,
                        canonical_application_url = ?,
                        last_checked_date = ?
                    WHERE id = ?
                """, (
                    now_iso,
                    float(job_data.get("match_score", 0.0)),
                    job_data.get("priority") or "Low",
                    job_data.get("match_reasons") or "",
                    job_data.get("potential_gaps") or "",
                    job_data.get("posting_date") or "Unknown",
                    job_data.get("date_confidence") or "Unknown",
                    job_data.get("experience_required") or "Unknown",
                    job_data.get("experience_evidence") or "",
                    job_data.get("posting_date_evidence") or "",
                    job_data.get("canonical_application_url") or "",
                    now_iso,
                    existing_id
                ))
                conn.commit()
                return ("updated", existing_id)
            else:
                final_norm_url = clean_url if clean_url != "Unknown" else f"synthetic://{dedup_hash}"
                cursor.execute("""
                    INSERT INTO jobs (
                        normalized_url, dedup_hash, job_title, company, location,
                        work_arrangement, application_url, source_url, source_name,
                        posting_date, date_confidence, experience_required,
                        relevant_skills, match_reasons, potential_gaps, match_score,
                        priority, first_seen_date, last_seen_date, status, notes, raw_snippet,
                        liveness_status, experience_evidence, posting_date_evidence,
                        canonical_application_url, last_checked_date
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    final_norm_url,
                    dedup_hash,
                    job_title,
                    company,
                    job_data.get("location") or "Unknown",
                    job_data.get("work_arrangement") or "Unknown",
                    raw_app_url,
                    raw_src_url,
                    job_data.get("source_name") or "Unknown",
                    job_data.get("posting_date") or "Unknown",
                    job_data.get("date_confidence") or "Unknown",
                    job_data.get("experience_required") or "Unknown",
                    skills_str,
                    job_data.get("match_reasons") or "",
                    job_data.get("potential_gaps") or "",
                    float(job_data.get("match_score", 0.0)),
                    job_data.get("priority") or "Low",
                    now_iso,
                    now_iso,
                    "New",
                    job_data.get("notes") or "",
                    job_data.get("raw_snippet") or "",
                    job_data.get("liveness_status") or "Active",
                    job_data.get("experience_evidence") or "",
                    job_data.get("posting_date_evidence") or "",
                    job_data.get("canonical_application_url") or "",
                    now_iso
                ))
                job_id = cursor.lastrowid
                conn.commit()
                return ("inserted", job_id)

    def get_jobs(
        self,
        status: Optional[str] = None,
        min_score: Optional[float] = None,
        priority: Optional[str] = None,
        max_age_days: Optional[float] = None,
        pune_or_remote_only: bool = False,
        strict_verified_only: bool = False,
        exclude_disqualified: bool = False,
        limit: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        """Queries jobs with configurable filtering ordered by match score desc."""
        query = "SELECT * FROM jobs WHERE 1=1"
        params: List[Any] = []

        if status:
            query += " AND status = ?"
            params.append(status)
        if min_score is not None:
            query += " AND match_score >= ?"
            params.append(min_score)
        if priority:
            query += " AND priority = ?"
            params.append(priority)
        if exclude_disqualified:
            query += " AND match_score > 0"

        query += " ORDER BY match_score DESC, last_seen_date DESC"

        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(query, params)
            rows = cursor.fetchall()
            jobs = [dict(row) for row in rows]

        now = datetime.now(timezone.utc)
        filtered_jobs = []

        for job in jobs:
            # 1. Strict verified date filter
            date_conf = job.get("date_confidence") or "Unknown"
            if strict_verified_only and date_conf != "Verified":
                continue

            # 2. Dynamic Recency calculation with anti-future timestamp check
            pdate = job.get("posting_date") or ""
            hours_ago: Optional[float] = None
            if pdate and pdate.lower() not in ["unknown", "unverified"]:
                try:
                    if "t" in pdate.lower():
                        dt = datetime.fromisoformat(pdate.replace("Z", "+00:00"))
                    else:
                        dt = datetime.strptime(pdate[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc)
                    
                    # Reject future-dated timestamps
                    if dt > now + timedelta(minutes=15):
                        hours_ago = None
                    else:
                        hours_ago = max(0.0, (now - dt).total_seconds() / 3600.0)
                except Exception:
                    pass

            job["hours_ago"] = round(hours_ago, 1) if hours_ago is not None else None
            job["posting_date_ist"] = format_utc_to_ist(pdate)

            if max_age_days is not None:
                if hours_ago is None or hours_ago > (max_age_days * 24.0):
                    continue

            # 3. Location filter (Pune or Remote accessible in India)
            if pune_or_remote_only:
                loc_low = (job.get("location") or "").lower()
                arr_low = (job.get("work_arrangement") or "").lower()
                raw_low = (job.get("raw_snippet") or "").lower()

                is_pune = "pune" in loc_low or "pune" in raw_low
                is_remote = "remote" in arr_low or "remote" in loc_low or "work from home" in raw_low or "anywhere" in loc_low or "worldwide" in loc_low
                foreign_restricted = bool(FOREIGN_REMOTE_PATTERN.search(loc_low) or FOREIGN_REMOTE_PATTERN.search(raw_low))

                if not (is_pune or (is_remote and not foreign_restricted)):
                    continue

            filtered_jobs.append(job)

        if limit:
            return filtered_jobs[:limit]

        return filtered_jobs

    def check_url_liveness(self, url: str, timeout: int = 5) -> str:
        """
        Conservative liveness check.
        Never marks 403 or 429 as expired (anti-bot protections).
        Only marks 404 or 410 as expired.
        """
        if not url or url.lower() in ["unknown", ""] or url.startswith("synthetic://"):
            return "Unverified (No URL)"

        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
            "Accept": "*/*"
        }
        try:
            req = urllib.request.Request(url, headers=headers, method="HEAD")
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                if resp.status in [200, 301, 302, 307, 308]:
                    return "Active"
                return f"Active ({resp.status})"
        except urllib.error.HTTPError as e:
            if e.code in [404, 410]:
                return f"Expired ({e.code})"
            elif e.code in [403, 429]:
                return f"Unverified (Anti-Bot / HTTP {e.code})"
            elif e.code == 405:
                # Method Not Allowed for HEAD; try GET with small read
                try:
                    req_get = urllib.request.Request(url, headers=headers, method="GET")
                    with urllib.request.urlopen(req_get, timeout=timeout) as resp_get:
                        return "Active"
                except urllib.error.HTTPError as eg:
                    if eg.code in [404, 410]:
                        return f"Expired ({eg.code})"
                    return f"Unverified (HTTP {eg.code})"
                except Exception:
                    return "Unverified (Timeout)"
            return f"Unverified (HTTP {e.code})"
        except Exception as ex:
            return "Unverified (Timeout / Connection Error)"

    def update_job_status(self, job_id: int, status: str, notes: Optional[str] = None) -> bool:
        """Updates user workflow status ('New', 'Applied', 'Reviewed', 'Archived') and notes."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            if notes is not None:
                cursor.execute("UPDATE jobs SET status = ?, notes = ? WHERE id = ?", (status, notes, job_id))
            else:
                cursor.execute("UPDATE jobs SET status = ? WHERE id = ?", (status, job_id))
            conn.commit()
            return cursor.rowcount > 0

    def get_stats(self) -> Dict[str, Any]:
        """Returns summary statistics for reporting."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM jobs")
            total = cursor.fetchone()[0]

            cursor.execute("SELECT priority, COUNT(*) FROM jobs GROUP BY priority")
            priority_counts = {row[0]: row[1] for row in cursor.fetchall()}

            cursor.execute("SELECT status, COUNT(*) FROM jobs GROUP BY status")
            status_counts = {row[0]: row[1] for row in cursor.fetchall()}

            cursor.execute("SELECT date_confidence, COUNT(*) FROM jobs GROUP BY date_confidence")
            date_conf_counts = {row[0]: row[1] for row in cursor.fetchall()}

            cursor.execute("SELECT COUNT(*) FROM jobs WHERE date(first_seen_date) = date('now')")
            added_today = cursor.fetchone()[0]

            return {
                "total_jobs": total,
                "added_today": added_today,
                "priority_breakdown": priority_counts,
                "status_breakdown": status_counts,
                "date_confidence_breakdown": date_conf_counts
            }

    def export_to_csv(
        self,
        filepath: str,
        min_score: float = 0.0,
        status_filter: Optional[str] = None,
        max_age_days: Optional[float] = None,
        pune_or_remote_only: bool = False,
        strict_verified_only: bool = False,
        limit: Optional[int] = None
    ) -> int:
        """
        Exports ranked jobs to a clean CSV formatted for Microsoft Excel on Windows.
        Uses 'utf-8-sig' (UTF-8 with BOM) and includes IST posting date string.
        """
        jobs = self.get_jobs(
            status=status_filter,
            min_score=min_score,
            max_age_days=max_age_days,
            pune_or_remote_only=pune_or_remote_only,
            strict_verified_only=strict_verified_only,
            limit=limit
        )
        fieldnames = [
            "id",
            "priority",
            "match_score",
            "job_title",
            "company",
            "location",
            "work_arrangement",
            "posting_date",
            "posting_date_ist",
            "hours_ago",
            "date_confidence",
            "experience_required",
            "experience_evidence",
            "relevant_skills",
            "match_reasons",
            "potential_gaps",
            "application_url",
            "canonical_application_url",
            "liveness_status",
            "source_name",
            "source_url",
            "status",
            "first_seen_date",
            "last_seen_date",
            "notes"
        ]

        os.makedirs(os.path.dirname(os.path.abspath(filepath)), exist_ok=True)
        with open(filepath, mode="w", newline="", encoding="utf-8-sig") as csvfile:
            writer = csv.DictWriter(csvfile, fieldnames=fieldnames, extrasaction="ignore")
            writer.writeheader()
            for job in jobs:
                skills = job.get("relevant_skills", "[]")
                if isinstance(skills, str) and skills.startswith("["):
                    try:
                        skills = ", ".join(json.loads(skills))
                    except Exception:
                        pass
                row = dict(job)
                row["relevant_skills"] = skills
                writer.writerow(row)

        return len(jobs)

    def link_recruiter_to_job(self, recruiter_id: int, job_id: int, relationship_type: str = "Hiring Contact") -> bool:
        """Links a recruiter to a specific job vacancy in recruiter_jobs."""
        now_iso = datetime.now(timezone.utc).isoformat()
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO recruiter_jobs (recruiter_id, job_id, relationship_type, created_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(recruiter_id, job_id) DO UPDATE SET
                    relationship_type = excluded.relationship_type
            """, (recruiter_id, job_id, relationship_type, now_iso))
            conn.commit()
            return cursor.rowcount > 0

    def upsert_recruiter(self, recruiter_data: Dict[str, Any]) -> Tuple[str, int]:
        """
        Inserts a new recruiter or updates an existing recruiter while preserving user state.
        Preserves: contact_status, last_contacted_date, follow_up_date, response_notes, user notes.
        Returns: ('inserted' | 'updated', recruiter_id)
        """
        raw_linkedin = recruiter_data.get("linkedin_url", "")
        clean_url = normalize_url(raw_linkedin)
        
        full_name = (recruiter_data.get("full_name") or "").strip()
        company = (recruiter_data.get("current_company") or "Unknown").strip()
        dedup_hash = compute_recruiter_dedup_hash(full_name, company, clean_url)

        now_iso = datetime.now(timezone.utc).isoformat()
        new_specs = recruiter_data.get("technical_specialisms", [])
        if isinstance(new_specs, str):
            try:
                new_specs = json.loads(new_specs)
            except Exception:
                new_specs = [s.strip() for s in new_specs.split(",") if s.strip()]

        with self._get_connection() as conn:
            cursor = conn.cursor()
            
            # Check primary match by normalized LinkedIn URL (if valid)
            existing = None
            if clean_url != "Unknown" and not clean_url.startswith("synthetic://"):
                cursor.execute(
                    "SELECT id, contact_status, last_contacted_date, follow_up_date, response_notes, notes, technical_specialisms "
                    "FROM recruiters WHERE normalized_url = ?",
                    (clean_url,)
                )
                existing = cursor.fetchone()

            # Check secondary match by dedup hash
            if not existing and dedup_hash:
                cursor.execute(
                    "SELECT id, contact_status, last_contacted_date, follow_up_date, response_notes, notes, technical_specialisms "
                    "FROM recruiters WHERE dedup_hash = ?",
                    (dedup_hash,)
                )
                existing = cursor.fetchone()

            if existing:
                existing_id = existing["id"]
                # Merge technical specialisms
                try:
                    existing_specs = set(json.loads(existing["technical_specialisms"] or "[]"))
                except Exception:
                    existing_specs = set()
                merged_specs = sorted(list(existing_specs.union(set(new_specs))))

                incoming_type = recruiter_data.get("recruiter_type") or "Unverified"
                
                cursor.execute("""
                    UPDATE recruiters SET
                        full_name = ?,
                        current_company = ?,
                        recruiter_type = CASE WHEN recruiter_type != 'Unverified' AND ? = 'Unverified' THEN recruiter_type ELSE ? END,
                        technical_specialisms = ?,
                        location = ?,
                        remote_coverage = ?,
                        related_job_title = CASE WHEN ? != '' THEN ? ELSE related_job_title END,
                        related_job_id = CASE WHEN ? IS NOT NULL THEN ? ELSE related_job_id END,
                        hiring_evidence_url = CASE WHEN ? != '' THEN ? ELSE hiring_evidence_url END,
                        evidence_summary = CASE WHEN ? != '' THEN ? ELSE evidence_summary END,
                        evidence_date = CASE WHEN ? != 'Unknown' THEN ? ELSE evidence_date END,
                        last_verified_date = ?,
                        contact_details = CASE WHEN ? != '' THEN ? ELSE contact_details END,
                        verification_status = ?,
                        confidence_score = ?,
                        match_score = ?,
                        priority = ?,
                        match_reasons = ?,
                        potential_gaps = ?,
                        raw_snippet = CASE WHEN ? != '' THEN ? ELSE raw_snippet END
                    WHERE id = ?
                """, (
                    full_name,
                    company,
                    incoming_type,
                    incoming_type,
                    json.dumps(merged_specs),
                    recruiter_data.get("location") or "Unknown",
                    recruiter_data.get("remote_coverage") or "Unknown",
                    recruiter_data.get("related_job_title") or "",
                    recruiter_data.get("related_job_title") or "",
                    recruiter_data.get("related_job_id"),
                    recruiter_data.get("related_job_id"),
                    recruiter_data.get("hiring_evidence_url") or "",
                    recruiter_data.get("hiring_evidence_url") or "",
                    recruiter_data.get("evidence_summary") or "",
                    recruiter_data.get("evidence_summary") or "",
                    recruiter_data.get("evidence_date") or "Unknown",
                    recruiter_data.get("evidence_date") or "Unknown",
                    now_iso,
                    recruiter_data.get("contact_details") or "",
                    recruiter_data.get("contact_details") or "",
                    recruiter_data.get("verification_status") or "Estimated / Unverified",
                    float(recruiter_data.get("confidence_score", 0.0)),
                    float(recruiter_data.get("match_score", 0.0)),
                    recruiter_data.get("priority") or "Low",
                    recruiter_data.get("match_reasons") or "",
                    recruiter_data.get("potential_gaps") or "",
                    recruiter_data.get("raw_snippet") or "",
                    recruiter_data.get("raw_snippet") or "",
                    existing_id
                ))
                conn.commit()

                if recruiter_data.get("related_job_id"):
                    self.link_recruiter_to_job(existing_id, int(recruiter_data["related_job_id"]))

                return ("updated", existing_id)
            else:
                final_norm_url = clean_url if clean_url != "Unknown" else f"synthetic://recruiter/{dedup_hash}"
                contact_status = recruiter_data.get("contact_status") or "Not Contacted"
                cursor.execute("""
                    INSERT INTO recruiters (
                        full_name, linkedin_url, normalized_url, dedup_hash,
                        current_company, recruiter_type, technical_specialisms,
                        location, remote_coverage, related_job_title, related_job_id,
                        hiring_evidence_url, evidence_summary, evidence_date,
                        first_discovered_date, last_verified_date, contact_details,
                        contact_status, last_contacted_date, follow_up_date, response_notes,
                        verification_status, confidence_score, match_score, priority,
                        match_reasons, potential_gaps, notes, raw_snippet
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    full_name,
                    raw_linkedin,
                    final_norm_url,
                    dedup_hash,
                    company,
                    recruiter_data.get("recruiter_type") or "Unverified",
                    json.dumps(new_specs),
                    recruiter_data.get("location") or "Unknown",
                    recruiter_data.get("remote_coverage") or "Unknown",
                    recruiter_data.get("related_job_title") or "",
                    recruiter_data.get("related_job_id"),
                    recruiter_data.get("hiring_evidence_url") or "",
                    recruiter_data.get("evidence_summary") or "",
                    recruiter_data.get("evidence_date") or "Unknown",
                    now_iso,
                    now_iso,
                    recruiter_data.get("contact_details") or "",
                    contact_status,
                    recruiter_data.get("last_contacted_date") or "",
                    recruiter_data.get("follow_up_date") or "",
                    recruiter_data.get("response_notes") or "",
                    recruiter_data.get("verification_status") or "Estimated / Unverified",
                    float(recruiter_data.get("confidence_score", 0.0)),
                    float(recruiter_data.get("match_score", 0.0)),
                    recruiter_data.get("priority") or "Low",
                    recruiter_data.get("match_reasons") or "",
                    recruiter_data.get("potential_gaps") or "",
                    recruiter_data.get("notes") or "",
                    recruiter_data.get("raw_snippet") or ""
                ))
                new_id = cursor.lastrowid
                conn.commit()

                if recruiter_data.get("related_job_id"):
                    self.link_recruiter_to_job(new_id, int(recruiter_data["related_job_id"]))

                return ("inserted", new_id)

    def get_recruiters(
        self,
        status: Optional[str] = None,
        min_score: Optional[float] = None,
        priority: Optional[str] = None,
        specialism: Optional[str] = None,
        location: Optional[str] = None,
        max_age_days: Optional[float] = None,
        pune_remote_only: bool = False,
        limit: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        """Queries recruiters with configurable filtering ordered by match score desc."""
        query = "SELECT * FROM recruiters WHERE 1=1"
        params: List[Any] = []

        if status:
            query += " AND LOWER(contact_status) = LOWER(?)"
            params.append(status.strip())
        if min_score is not None:
            query += " AND match_score >= ?"
            params.append(min_score)
        if priority:
            query += " AND priority = ?"
            params.append(priority)

        query += " ORDER BY match_score DESC, last_verified_date DESC"

        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(query, params)
            rows = cursor.fetchall()
            recruiters = [dict(row) for row in rows]

        now = datetime.now(timezone.utc)
        filtered = []

        for rec in recruiters:
            # Specialism filter
            if specialism:
                spec_low = specialism.lower()
                specs = (rec.get("technical_specialisms") or "").lower()
                evidence = (rec.get("evidence_summary") or "").lower()
                raw_s = (rec.get("raw_snippet") or "").lower()
                title = (rec.get("related_job_title") or "").lower()
                if not (spec_low in specs or spec_low in evidence or spec_low in raw_s or spec_low in title):
                    continue

            # Location filter
            if location:
                loc_filter = location.lower()
                rec_loc = (rec.get("location") or "").lower()
                rec_rem = (rec.get("remote_coverage") or "").lower()
                if not (loc_filter in rec_loc or loc_filter in rec_rem):
                    continue

            # Pune / Remote India filter
            if pune_remote_only:
                rec_loc = (rec.get("location") or "").lower()
                rec_rem = (rec.get("remote_coverage") or "").lower()
                evidence = (rec.get("evidence_summary") or "").lower()
                raw_s = (rec.get("raw_snippet") or "").lower()
                
                is_pune = "pune" in rec_loc or "pune" in evidence or "pune" in raw_s
                is_remote_in = (
                    "remote" in rec_rem or "remote" in rec_loc or "india" in rec_loc or
                    "worldwide" in rec_loc or "work from home" in evidence or "india" in evidence
                )
                foreign_restricted = bool(FOREIGN_REMOTE_PATTERN.search(rec_loc) or FOREIGN_REMOTE_PATTERN.search(raw_s))
                if not (is_pune or (is_remote_in and not foreign_restricted)):
                    continue

            # Evidence freshness filter
            edate = rec.get("evidence_date") or ""
            hours_ago: Optional[float] = None
            if edate and edate.lower() not in ["unknown", "unverified"]:
                try:
                    if "t" in edate.lower():
                        dt = datetime.fromisoformat(edate.replace("Z", "+00:00"))
                    else:
                        dt = datetime.strptime(edate[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc)
                    if dt <= now + timedelta(minutes=15):
                        hours_ago = max(0.0, (now - dt).total_seconds() / 3600.0)
                except Exception:
                    pass

            rec["evidence_hours_ago"] = round(hours_ago, 1) if hours_ago is not None else None
            rec["evidence_date_ist"] = format_utc_to_ist(edate)

            if max_age_days is not None:
                if hours_ago is None or hours_ago > (max_age_days * 24.0):
                    continue

            filtered.append(rec)

        if limit:
            return filtered[:limit]

        return filtered

    def get_recruiter_by_id(self, recruiter_id: int) -> Optional[Dict[str, Any]]:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM recruiters WHERE id = ?", (recruiter_id,))
            row = cursor.fetchone()
            return dict(row) if row else None

    def update_recruiter_status(
        self,
        recruiter_id: int,
        status: str,
        notes: Optional[str] = None,
        follow_up_date: Optional[str] = None,
        last_contacted_date: Optional[str] = None
    ) -> bool:
        """
        Updates recruiter outreach status, notes, follow-up date, and last contacted date.
        """
        matched_status = None
        for valid in VALID_OUTREACH_STATUSES:
            if valid.lower() == status.strip().lower():
                matched_status = valid
                break

        if not matched_status:
            raise ValueError(f"Invalid outreach status '{status}'. Must be one of: {', '.join(VALID_OUTREACH_STATUSES)}")

        now_date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        effective_contacted = last_contacted_date
        if not effective_contacted and matched_status in ["Contacted", "Connection Requested"]:
            effective_contacted = now_date_str

        with self._get_connection() as conn:
            cursor = conn.cursor()
            query = "UPDATE recruiters SET contact_status = ?"
            params = [matched_status]

            if notes is not None:
                query += ", response_notes = ?"
                params.append(notes)
            if follow_up_date is not None:
                query += ", follow_up_date = ?"
                params.append(follow_up_date)
            if effective_contacted is not None:
                query += ", last_contacted_date = ?"
                params.append(effective_contacted)

            query += " WHERE id = ?"
            params.append(recruiter_id)

            cursor.execute(query, params)
            conn.commit()
            return cursor.rowcount > 0

    def get_recruiter_stats(self) -> Dict[str, Any]:
        """Returns summary metrics for recruiter outreach and discovery."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM recruiters")
            total = cursor.fetchone()[0]

            cursor.execute("SELECT contact_status, COUNT(*) FROM recruiters GROUP BY contact_status")
            status_counts = {row[0]: row[1] for row in cursor.fetchall()}

            cursor.execute("SELECT recruiter_type, COUNT(*) FROM recruiters GROUP BY recruiter_type")
            type_counts = {row[0]: row[1] for row in cursor.fetchall()}

            cursor.execute("SELECT priority, COUNT(*) FROM recruiters GROUP BY priority")
            priority_counts = {row[0]: row[1] for row in cursor.fetchall()}

            cursor.execute("SELECT verification_status, COUNT(*) FROM recruiters GROUP BY verification_status")
            verification_counts = {row[0]: row[1] for row in cursor.fetchall()}

            today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            cursor.execute(
                "SELECT COUNT(*) FROM recruiters WHERE contact_status = 'Follow-up Due' "
                "OR (follow_up_date != '' AND follow_up_date <= ?)",
                (today_str,)
            )
            follow_ups_due = cursor.fetchone()[0]

            return {
                "total_recruiters": total,
                "status_breakdown": status_counts,
                "type_breakdown": type_counts,
                "priority_breakdown": priority_counts,
                "verification_breakdown": verification_counts,
                "follow_ups_due": follow_ups_due
            }

    def export_recruiters_to_csv(
        self,
        filepath: str,
        min_score: float = 0.0,
        status_filter: Optional[str] = None,
        specialism: Optional[str] = None,
        max_age_days: Optional[float] = None,
        pune_remote_only: bool = False,
        limit: Optional[int] = None
    ) -> int:
        """
        Exports recruiters to a spreadsheet-friendly CSV with Excel UTF-8 BOM encoding.
        """
        recruiters = self.get_recruiters(
            status=status_filter,
            min_score=min_score,
            specialism=specialism,
            max_age_days=max_age_days,
            pune_remote_only=pune_remote_only,
            limit=limit
        )
        fieldnames = [
            "id",
            "priority",
            "match_score",
            "confidence_score",
            "full_name",
            "current_company",
            "recruiter_type",
            "technical_specialisms",
            "location",
            "remote_coverage",
            "contact_status",
            "last_contacted_date",
            "follow_up_date",
            "response_notes",
            "linkedin_url",
            "contact_details",
            "related_job_title",
            "related_job_id",
            "hiring_evidence_url",
            "evidence_summary",
            "evidence_date",
            "evidence_date_ist",
            "evidence_hours_ago",
            "verification_status",
            "match_reasons",
            "potential_gaps",
            "first_discovered_date",
            "last_verified_date",
            "notes"
        ]

        os.makedirs(os.path.dirname(os.path.abspath(filepath)), exist_ok=True)
        with open(filepath, mode="w", newline="", encoding="utf-8-sig") as csvfile:
            writer = csv.DictWriter(csvfile, fieldnames=fieldnames, extrasaction="ignore")
            writer.writeheader()
            for rec in recruiters:
                specs = rec.get("technical_specialisms", "[]")
                if isinstance(specs, str) and specs.startswith("["):
                    try:
                        specs = ", ".join(json.loads(specs))
                    except Exception:
                        pass
                row = dict(rec)
                row["technical_specialisms"] = specs
                writer.writerow(row)

        return len(recruiters)

