"""
test_recruiter_system.py - Comprehensive test suite for Recruiter Discovery & Outreach.

Tests:
1. Recruiter Deduplication (URL normalization, name+company hashing)
2. Database Schema Migration and Preservation of Existing Job & Recruiter Data
3. Verification Status and Confidence Scoring (no fabricated verification)
4. Evidence Scoring, Freshness & Seniority Penalties (prioritizing junior DevOps in Pune/India-Remote)
5. Outreach Status Updates & Preservation of User State during Re-Upsert
6. Spreadsheet-Friendly CSV Export (UTF-8 BOM, all required columns)
7. Source Robustness & Error Handling (mocked network failures, 500 errors, empty feeds)
8. Personalized Message Generation & LinkedIn Character Limits (<= 300 chars)
9. Recruiter-Job Relationship Linking
"""

import csv
import json
import os
import tempfile
import unittest
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, patch
import urllib.error

# Module imports
from database import JobDatabase, normalize_url, compute_recruiter_dedup_hash, VALID_OUTREACH_STATUSES
from recruiter_scoring import RecruiterScorer
from recruiter_sources import (
    BaseRecruiterSource,
    JobPostingRecruiterExtractor,
    PublicHiringPostRecruiterSource,
    TargetedRecruiterSearchLinkGenerator,
    RecruiterSourceManager
)
from recruiter_outreach import OutreachMessageGenerator

TEST_CONFIG = {
    "candidate_profile": {
        "target_roles": [
            "DevOps Engineer",
            "Junior DevOps Engineer",
            "Cloud Administrator",
            "Junior Cloud Engineer",
            "Infrastructure Engineer",
            "Platform Engineer"
        ],
        "target_locations_primary": ["Pune", "Pune, Maharashtra", "Pune, India"],
        "target_locations_secondary": ["Remote", "India", "Work from Home", "Worldwide"],
        "core_skills": [
            "AWS", "Linux", "Docker", "Kubernetes", "Terraform", "OpenTofu",
            "Git", "GitHub Actions", "Jenkins", "CI/CD", "Python", "Bash"
        ],
        "experience_profile": {
            "target_years_min": 0,
            "target_years_max": 3,
            "background_notes": "Operations and technical-support experience at Amazon; DevOps internship experience.",
            "positive_keywords": ["junior", "associate", "entry level", "fresher", "0-2 years", "1-3 years"],
            "penalty_keywords": ["senior", "lead", "staff", "principal", "architect", "5+ years", "7+ years"]
        }
    },
    "filters": {
        "max_posting_age_days": 7,
        "pune_or_remote_india_only": True
    },
    "recruiter_discovery": {
        "enable_job_posting_extractor": True,
        "enable_public_hiring_posts": True,
        "min_recruiter_score": 40.0,
        "hiring_post_queries": [
            'site:linkedin.com/posts ("hiring" OR "looking for") ("DevOps" OR "AWS") Pune'
        ]
    },
    "settings": {
        "database_file": "test_recruiters.db",
        "export_recruiters_csv_file": "test_recruiters.csv",
        "request_timeout_seconds": 5,
        "user_agent": "Mozilla/5.0 TestAgent"
    }
}


class TestRecruiterDeduplicationAndDatabase(unittest.TestCase):
    """Tests SQLite persistence, deduplication, state preservation, and migrations."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, "test_recruiters.db")
        self.db = JobDatabase(self.db_path)

    def tearDown(self):
        if hasattr(self, "db"):
            del self.db
        import gc
        gc.collect()
        if os.path.exists(self.temp_dir):
            import shutil
            shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_recruiter_dedup_hash_stability(self):
        """Verifies hash stability across company suffixes and title prefixes."""
        h1 = compute_recruiter_dedup_hash("Priya Sharma", "Thoughtworks India Pvt Ltd")
        h2 = compute_recruiter_dedup_hash("Ms. Priya Sharma", "Thoughtworks")
        self.assertEqual(h1, h2, "Dedup hash should normalize honorifics and corporate suffixes")

        # LinkedIn profile URL slug stability
        h3 = compute_recruiter_dedup_hash("John Doe", "AnyCo", "https://www.linkedin.com/in/johndoe?trk=public_profile")
        h4 = compute_recruiter_dedup_hash("John Doe", "OtherCo", "https://linkedin.com/in/johndoe/")
        self.assertEqual(h3, h4, "Dedup hash should match identical LinkedIn profile slugs")

    def test_upsert_preserves_outreach_status_and_user_notes(self):
        """Ensures repeated pipeline discovery does not overwrite user contact status and notes."""
        recruiter = {
            "full_name": "Anita Kulkarni",
            "current_company": "CloudTech Pune",
            "linkedin_url": "https://www.linkedin.com/in/anita-kulkarni/",
            "recruiter_type": "Internal Technical Recruiter",
            "location": "Pune, India",
            "technical_specialisms": ["DevOps", "AWS"],
            "match_score": 85.0,
            "priority": "High"
        }

        # 1. First insert
        action, rec_id = self.db.upsert_recruiter(recruiter)
        self.assertEqual(action, "inserted")

        # 2. User updates status and notes
        self.db.update_recruiter_status(
            rec_id,
            status="Connection Requested",
            notes="Sent connection request mentioning AWS and Docker skills.",
            follow_up_date="2026-10-25"
        )

        fetched = self.db.get_recruiter_by_id(rec_id)
        self.assertEqual(fetched["contact_status"], "Connection Requested")
        self.assertEqual(fetched["follow_up_date"], "2026-10-25")
        self.assertIn("Sent connection request", fetched["response_notes"])

        # 3. Pipeline runs again with new score/evidence
        recruiter_update = dict(recruiter)
        recruiter_update["match_score"] = 90.0
        recruiter_update["evidence_summary"] = "New hiring post discovered today"
        action2, rec_id2 = self.db.upsert_recruiter(recruiter_update)

        self.assertEqual(action2, "updated")
        self.assertEqual(rec_id, rec_id2)

        # 4. Verify user outreach state was preserved
        refetched = self.db.get_recruiter_by_id(rec_id)
        self.assertEqual(refetched["contact_status"], "Connection Requested")
        self.assertEqual(refetched["follow_up_date"], "2026-10-25")
        self.assertIn("Sent connection request", refetched["response_notes"])
        self.assertEqual(refetched["match_score"], 90.0)

    def test_recruiter_job_relationship_linking(self):
        """Verifies recruiter to job vacancy linking in recruiter_jobs table."""
        # Insert a job first
        job_data = {
            "job_title": "Junior DevOps Engineer",
            "company": "PuneInfra",
            "location": "Pune",
            "application_url": "https://puneinfra.com/jobs/101"
        }
        _, job_id = self.db.upsert_job(job_data)

        # Insert recruiter linked to that job
        recruiter_data = {
            "full_name": "Sameer Joshi",
            "current_company": "PuneInfra",
            "related_job_id": job_id,
            "related_job_title": "Junior DevOps Engineer"
        }
        _, rec_id = self.db.upsert_recruiter(recruiter_data)

        # Verify link in database
        with self.db._get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT job_id, recruiter_id FROM recruiter_jobs WHERE recruiter_id = ?", (rec_id,))
            row = cur.fetchone()
            self.assertIsNotNone(row)
            self.assertEqual(row[0], job_id)
            self.assertEqual(row[1], rec_id)

    def test_database_migration_preserves_existing_jobs(self):
        """Verifies that JobDatabase schema initialization does not corrupt existing jobs."""
        job = {
            "job_title": "AWS Cloud Support",
            "company": "Amazon AWS",
            "location": "Pune",
            "application_url": "https://amazon.jobs/en/jobs/12345"
        }
        self.db.upsert_job(job)

        # Re-initialize database
        self.db.init_db()

        jobs = self.db.get_jobs()
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]["company"], "Amazon AWS")


class TestRecruiterScoringEngine(unittest.TestCase):
    """Tests prioritization, junior fit, evidence freshness, and location matching."""

    def setUp(self):
        self.scorer = RecruiterScorer(TEST_CONFIG, known_hiring_companies={"cloudtech", "elastic"})

    def test_ideal_junior_devops_recruiter_in_pune_scores_high(self):
        """A Pune internal recruiter with fresh hiring for Junior DevOps should receive a high score."""
        now_iso = datetime.now(timezone.utc).isoformat()
        recruiter = {
            "full_name": "Rohan Deshmukh",
            "current_company": "CloudTech",
            "recruiter_type": "Internal Technical Recruiter",
            "location": "Pune, Maharashtra",
            "remote_coverage": "Pune",
            "related_job_title": "Junior DevOps Engineer",
            "evidence_date": now_iso,
            "evidence_summary": "Looking for a Junior DevOps Engineer with AWS, Docker, Kubernetes & Terraform skills in Pune (0-2 years experience).",
            "raw_snippet": "Hiring junior devops freshers and associates in Pune",
            "linkedin_url": "https://www.linkedin.com/in/rohan-deshmukh/"
        }

        result = self.scorer.score_recruiter(recruiter)
        self.assertGreaterEqual(result["match_score"], 80.0)
        self.assertEqual(result["priority"], "High")
        self.assertIn("Pune", result["match_reasons"])
        self.assertIn("junior/associate", result["match_reasons"])

    def test_senior_only_focus_penalized(self):
        """Recruiters hiring strictly for Senior/Staff/Lead >5+ years should be penalized."""
        recruiter = {
            "full_name": "Vikram Seth",
            "current_company": "Enterprise Tech",
            "recruiter_type": "Agency Recruiter",
            "location": "Pune",
            "related_job_title": "Senior Principal DevOps Architect",
            "evidence_summary": "Urgent requirement for Senior Lead DevOps Architect with 10+ years experience",
            "raw_snippet": "Senior / Lead architect hiring only."
        }

        result = self.scorer.score_recruiter(recruiter)
        self.assertIn("Senior/Lead/Staff", result["potential_gaps"])
        self.assertLess(result["match_score"], 60.0)

    def test_foreign_restricted_location_penalized(self):
        """Recruiters hiring strictly for US/UK/Canada on-site/remote should receive heavy penalty."""
        recruiter = {
            "full_name": "Alice Smith",
            "current_company": "US Corp",
            "recruiter_type": "Internal Technical Recruiter",
            "location": "San Francisco, CA (US Only)",
            "remote_coverage": "US Only - Must reside in the US",
            "evidence_summary": "Hiring Junior DevOps Engineer. US Citizen or Green Card required. US Only."
        }

        result = self.scorer.score_recruiter(recruiter)
        self.assertIn("restricted to foreign location", result["potential_gaps"])
        self.assertLess(result["match_score"], 50.0)

    def test_evidence_freshness_differential(self):
        """Fresh hiring evidence (<=48h) should outscore older evidence."""
        now = datetime.now(timezone.utc)
        fresh_date = (now - timedelta(hours=12)).isoformat()
        old_date = (now - timedelta(days=45)).isoformat()

        base = {
            "full_name": "Meera Patel",
            "current_company": "TechIndia",
            "recruiter_type": "Internal Technical Recruiter",
            "location": "Pune",
            "evidence_summary": "Hiring DevOps Engineer with AWS and CI/CD"
        }

        rec_fresh = dict(base, evidence_date=fresh_date)
        rec_old = dict(base, evidence_date=old_date)

        score_fresh = self.scorer.score_recruiter(rec_fresh)["match_score"]
        score_old = self.scorer.score_recruiter(rec_old)["match_score"]
        self.assertGreater(score_fresh, score_old, "Fresh evidence should yield higher score")

    def test_verification_status_does_not_hallucinate(self):
        """Unverified search leads without LinkedIn URL or direct evidence should be labeled unverified."""
        lead = {
            "full_name": "Anonymous Sourcer",
            "current_company": "Unknown",
            "verification_status": ""
        }
        res = self.scorer.score_recruiter(lead)
        self.assertEqual(res["verification_status"], "Unverified Lead")
        self.assertLessEqual(res["confidence_score"], 0.35)


class TestOutreachMessageGenerator(unittest.TestCase):
    """Tests message personalization and LinkedIn 300-character constraint."""

    def setUp(self):
        self.generator = OutreachMessageGenerator(TEST_CONFIG)

    def test_linkedin_connection_note_strict_character_limit(self):
        """LinkedIn connection notes MUST NEVER exceed 300 characters."""
        recruiter = {
            "full_name": "Dr. Chandrashekhar Venkatraman",
            "current_company": "International Advanced Cloud Solutions Private Limited",
            "related_job_title": "Associate Platform & Site Reliability Automation Engineer"
        }

        note = self.generator.generate_linkedin_connection_note(recruiter)
        self.assertLessEqual(
            len(note),
            300,
            f"LinkedIn note exceeds 300 character limit: {len(note)} chars ('{note}')"
        )
        self.assertIn("Chandrashekhar", note)
        self.assertIn("AWS", note)

    def test_inmail_and_follow_up_templates_generated(self):
        """InMail and Follow-up templates should include candidate skills, Pune location, and company hook."""
        recruiter = {
            "full_name": "Kavita Rao",
            "current_company": "Elastic",
            "related_job_title": "Junior DevOps Engineer"
        }

        templates = self.generator.generate_all_templates(recruiter)
        inmail = templates["inmail"]
        follow_up = templates["follow_up"]

        self.assertIn("Kavita", inmail["body"])
        self.assertIn("Elastic", inmail["body"])
        self.assertIn("Pune", inmail["body"])
        self.assertIn("Docker", inmail["body"])
        self.assertIn("Terraform", inmail["body"])

        self.assertIn("Following up", follow_up["subject"])
        self.assertIn("Elastic", follow_up["body"])


class TestCSVExportAndSearchLinks(unittest.TestCase):
    """Tests Excel-friendly CSV export and markdown search link generation."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, "test_csv.db")
        self.csv_path = os.path.join(self.temp_dir, "test_export.csv")
        self.md_path = os.path.join(self.temp_dir, "test_links.md")
        self.db = JobDatabase(self.db_path)

    def tearDown(self):
        if hasattr(self, "db"):
            del self.db
        import gc
        gc.collect()
        if os.path.exists(self.temp_dir):
            import shutil
            shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_recruiter_csv_export_format(self):
        """Validates CSV format, BOM header for Excel, and required columns."""
        recruiter = {
            "full_name": "Deepak Shinde",
            "current_company": "Pune DevOps Labs",
            "recruiter_type": "Internal Technical Recruiter",
            "location": "Pune, India",
            "remote_coverage": "Pune",
            "technical_specialisms": ["AWS", "Kubernetes", "CI/CD"],
            "contact_status": "Not Contacted",
            "follow_up_date": "2026-10-30",
            "match_score": 88.5,
            "priority": "High",
            "evidence_summary": "Looking for DevOps Fresher/Junior with AWS skills"
        }
        self.db.upsert_recruiter(recruiter)

        count = self.db.export_recruiters_to_csv(self.csv_path)
        self.assertEqual(count, 1)

        # Verify UTF-8 BOM encoding
        with open(self.csv_path, "rb") as f:
            raw = f.read(3)
            self.assertEqual(raw, b"\xef\xbb\xbf", "CSV should begin with UTF-8 BOM for Microsoft Excel")

        # Verify headers and row data
        with open(self.csv_path, "r", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            rows = list(reader)
            self.assertEqual(len(rows), 1)
            row = rows[0]
            self.assertEqual(row["full_name"], "Deepak Shinde")
            self.assertEqual(row["current_company"], "Pune DevOps Labs")
            self.assertEqual(row["contact_status"], "Not Contacted")
            self.assertEqual(row["follow_up_date"], "2026-10-30")
            self.assertIn("AWS", row["technical_specialisms"])

    def test_search_links_markdown_generation(self):
        """Validates that search links document contains valid LinkedIn and Naukri links."""
        TargetedRecruiterSearchLinkGenerator.write_links_markdown(self.md_path)
        self.assertTrue(os.path.exists(self.md_path))

        with open(self.md_path, "r", encoding="utf-8") as f:
            content = f.read()
            self.assertIn("LinkedIn People Search", content)
            self.assertIn("Naukri Recruiter Directory", content)
            self.assertIn("https://www.linkedin.com/search/results/people", content)


class TestMockedRecruiterSources(unittest.TestCase):
    """Tests network fault-tolerance and connector behavior under simulated conditions."""

    @patch.object(BaseRecruiterSource, "fetch_url")
    def test_public_hiring_post_source_parses_feed(self, mock_fetch):
        """Simulates Google News RSS returning public LinkedIn hiring posts."""
        sample_rss = b"""<?xml version="1.0" encoding="UTF-8"?>
        <rss version="2.0">
            <channel>
                <title>Google News</title>
                <item>
                    <title>Sneha Gaikwad on LinkedIn: We are hiring a Junior DevOps Engineer in Pune!</title>
                    <link>https://news.google.com/rss/articles/CBMiSampleLink</link>
                    <pubDate>Mon, 19 Oct 2026 06:00:00 GMT</pubDate>
                    <description>&lt;a href="..."&gt;We have an exciting opening for a Junior DevOps Engineer in Pune with AWS and Docker skills.&lt;/a&gt;</description>
                </item>
            </channel>
        </rss>"""
        mock_fetch.return_value = sample_rss

        source = PublicHiringPostRecruiterSource(["test_query"], "Mozilla/5.0")
        results = source.fetch_recruiters()

        self.assertEqual(len(results), 1)
        r = results[0]
        self.assertEqual(r["full_name"], "Sneha Gaikwad")
        self.assertEqual(r["location"], "Pune, Maharashtra")
        self.assertIn("Junior DevOps", r["evidence_summary"])
        self.assertEqual(r["verification_status"], "Estimated (Public Feed Post)")

    @patch.object(BaseRecruiterSource, "fetch_url")
    def test_source_http_error_handled_gracefully(self, mock_fetch):
        """Network timeouts and HTTP 500 errors must not crash the pipeline."""
        mock_fetch.side_effect = urllib.error.HTTPError(
            "https://test.com", 500, "Internal Server Error", {}, None
        )

        source = PublicHiringPostRecruiterSource(["test_query"], "Mozilla/5.0")
        results = source.fetch_recruiters()
        self.assertEqual(results, [])
        self.assertIsNotNone(source.last_error)


if __name__ == "__main__":
    unittest.main()
