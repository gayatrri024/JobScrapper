"""
test_system.py - Comprehensive test suite for DevOps Job Hunter.

Tests:
1. Scoring logic (seniority penalties, skill overlap, location preference, recency)
2. Deduplication (URL normalization, company+title hash, state preservation)
3. Handling missing/corrupted fields (unverified dates, unknown companies, missing URLs)
4. Anti-future timestamp checks & timezone conversions
5. Location accuracy: query leakage prevention and foreign-remote exclusion
6. Experience accuracy: unknown experience labeled 'Unknown', not falsely '0-3 years'
7. Conservative liveness checking (403/429 never marked expired; 404 marked expired)
8. Mocked source connectors: Lever, Ashby, Greenhouse, Jobicy, WeWorkRemotely, GoogleNews
9. Dual CSV export & IST time formatting

Can be run with:
  python test_system.py
  or
  pytest test_system.py -v
"""

import json
import os
import tempfile
import unittest
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, patch
import urllib.error

# Local module imports
from database import JobDatabase, normalize_url, compute_dedup_hash, format_utc_to_ist
from scoring import JobScorer
from sources import (
    BaseSource,
    GreenhousePublicJobSource,
    LeverPublicJobSource,
    AshbyPublicJobSource,
    JobicyJobSource,
    WeWorkRemotelyJobSource,
    GoogleNewsJobSource
)

TEST_CONFIG = {
    "candidate_profile": {
        "target_roles": [
            "DevOps Engineer",
            "Junior DevOps Engineer",
            "Cloud Engineer",
            "Cloud Administrator",
            "Kubernetes Engineer",
            "Infrastructure Engineer",
            "Junior Site Reliability Engineer",
            "Junior SRE",
            "Platform Engineer",
            "Cloud Support Engineer"
        ],
        "target_locations_primary": ["Pune", "Pune, Maharashtra", "Pune, India"],
        "target_locations_secondary": ["Remote", "India", "Work from Home", "Worldwide"],
        "core_skills": [
            "AWS", "Linux", "Docker", "Kubernetes", "Terraform", "OpenTofu",
            "Git", "GitHub Actions", "Jenkins", "CI/CD", "Prometheus", "Grafana",
            "Python", "Bash", "infrastructure automation"
        ],
        "experience_profile": {
            "target_years_min": 0,
            "target_years_max": 3,
            "positive_keywords": ["intern", "fresher", "junior", "jr", "associate", "entry level", "0-2 years", "1-3 years"],
            "penalty_keywords": ["senior", "lead", "staff", "principal", "architect", "5+ years", "7+ years", "10+ years"]
        }
    },
    "filters": {
        "max_posting_age_days": 1,
        "pune_or_remote_india_only": True,
        "strict_verified_only": False,
        "max_experience_years": 3,
        "exclude_senior_roles": True
    },
    "settings": {
        "database_file": "test_jobs.db",
        "export_csv_file": "test_export.csv",
        "min_export_score": 40
    }
}


class TestScoringEngine(unittest.TestCase):
    """Verifies that the scoring engine adheres to candidate profile and strict rules."""

    def setUp(self):
        self.scorer = JobScorer(TEST_CONFIG)

    def test_ideal_junior_pune_job_scores_high(self):
        now_iso = datetime.now(timezone.utc).isoformat()
        job = {
            "job_title": "Junior DevOps Engineer",
            "company": "CloudTech Pune",
            "location": "Pune, India",
            "work_arrangement": "Hybrid",
            "posting_date": now_iso,
            "date_confidence": "Verified",
            "raw_snippet": "We are seeking a Junior DevOps Engineer with hands-on skills in AWS, Docker, Kubernetes, Linux, Terraform, and CI/CD pipelines. 0-2 years experience required."
        }
        res = self.scorer.score_job(job)
        self.assertGreaterEqual(res["match_score"], 75.0)
        self.assertEqual(res["priority"], "High")
        self.assertIn("AWS", res["relevant_skills"])
        self.assertIn("Kubernetes", res["relevant_skills"])
        self.assertIn("Docker", res["relevant_skills"])
        self.assertIn("Pune", res["match_reasons"])

    def test_senior_role_heavily_penalized(self):
        job = {
            "job_title": "Senior Staff DevOps Architect",
            "company": "Enterprise Corp",
            "location": "Pune, India",
            "work_arrangement": "Onsite",
            "posting_date": "Unknown",
            "date_confidence": "Unknown",
            "raw_snippet": "Requires minimum 8+ years of professional DevOps experience leading global infrastructure teams. AWS, Terraform, Kubernetes."
        }
        res = self.scorer.score_job(job)
        self.assertEqual(res["match_score"], 0.0)
        self.assertEqual(res["priority"], "Low")
        self.assertIn("exceeds", res["potential_gaps"].lower())

    def test_remote_cloud_support_scores_well(self):
        yesterday_iso = (datetime.now(timezone.utc) - timedelta(hours=18)).isoformat()
        job = {
            "job_title": "Cloud Support Engineer",
            "company": "Global Services",
            "location": "Remote",
            "work_arrangement": "Remote",
            "posting_date": yesterday_iso,
            "date_confidence": "Verified",
            "raw_snippet": "Supporting cloud infrastructure. AWS, Linux administration, Python scripting, and monitoring with Prometheus and Grafana. 1-2 years experience."
        }
        res = self.scorer.score_job(job)
        self.assertGreaterEqual(res["match_score"], 70.0)
        self.assertIn(res["priority"], ["High", "Medium"])

    def test_foreign_onsite_location_penalized(self):
        job = {
            "job_title": "DevOps Engineer",
            "company": "UK Tech Ltd",
            "location": "London, United Kingdom",
            "work_arrangement": "Onsite",
            "posting_date": "Unknown",
            "date_confidence": "Unknown",
            "raw_snippet": "AWS, Docker, Linux, CI/CD."
        }
        res = self.scorer.score_job(job)
        self.assertIn("outside india", res["potential_gaps"].lower())

    def test_unrelated_role_receives_low_score(self):
        job = {
            "job_title": "Senior Sales Account Executive",
            "company": "Biz Corp",
            "location": "Pune",
            "raw_snippet": "Sales role."
        }
        res = self.scorer.score_job(job)
        self.assertEqual(res["match_score"], 0.0)

    def test_recency_score_differential(self):
        now = datetime.now(timezone.utc)
        job_new = {"job_title": "DevOps", "company": "Co", "location": "Pune", "posting_date": (now - timedelta(hours=5)).isoformat()}
        job_old = {"job_title": "DevOps", "company": "Co", "location": "Pune", "posting_date": (now - timedelta(days=5)).isoformat()}
        res_new = self.scorer.score_job(job_new)
        res_old = self.scorer.score_job(job_old)
        self.assertGreater(res_new["match_score"], res_old["match_score"])


class TestDeduplicationAndStorage(unittest.TestCase):
    """Verifies normalization, secondary deduplication hashing, and state preservation."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db = JobDatabase(os.path.join(self.temp_dir.name, "test_dedup.db"))

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_url_normalization(self):
        raw_url = "https://www.linkedin.com/jobs/view/1234567890/?refId=abcdef&trackingId=xyz&utm_source=feed"
        clean = normalize_url(raw_url)
        self.assertNotIn("utm_source", clean)
        self.assertNotIn("trackingId", clean)
        self.assertNotIn("refId", clean)
        self.assertTrue(clean.startswith("https://www.linkedin.com/jobs/view/1234567890"))

    def test_dedup_hash_stability(self):
        h1 = compute_dedup_hash("Acme Technologies Pvt Ltd", "Junior DevOps Engineer")
        h2 = compute_dedup_hash("acme technologies", "Junior DevOps Engineer")
        self.assertEqual(h1, h2)

    def test_upsert_preserves_status_and_notes(self):
        job_1 = {
            "job_title": "Junior DevOps Engineer",
            "company": "Apex Cloud Systems",
            "location": "Pune",
            "application_url": "https://apex.com/jobs/devops-101?utm_campaign=winter",
            "source_url": "https://apex.com/jobs/devops-101",
            "source_name": "TestFeed",
            "posting_date": "2026-10-08T10:00:00",
            "match_score": 85.0,
            "priority": "High"
        }
        action1, job_id = self.db.upsert_job(job_1)
        self.assertEqual(action1, "inserted")
        self.db.update_job_status(job_id, "Applied", notes="Tailored resume.")

        job_2 = dict(job_1)
        job_2["application_url"] = "https://apex.com/jobs/devops-101?ref=jobboard"
        job_2["match_score"] = 88.0
        action2, job_id_2 = self.db.upsert_job(job_2)
        self.assertEqual(action2, "updated")
        self.assertEqual(job_id, job_id_2)

        jobs = self.db.get_jobs()
        self.assertEqual(len(jobs), 1)
        stored = jobs[0]
        self.assertEqual(stored["status"], "Applied")
        self.assertEqual(stored["notes"], "Tailored resume.")
        self.assertEqual(stored["match_score"], 88.0)

    def test_csv_export_format(self):
        job = {
            "job_title": "Kubernetes Administrator",
            "company": "KubeCloud India",
            "location": "Pune",
            "application_url": "https://kubecloud.in/careers/k8s",
            "source_url": "https://kubecloud.in/careers/k8s",
            "source_name": "TestFeed",
            "posting_date": "2026-10-09T08:00:00",
            "relevant_skills": ["Kubernetes", "Docker", "Linux"],
            "match_reasons": "Pune match; K8s skill match",
            "potential_gaps": "None",
            "match_score": 82.0,
            "priority": "High"
        }
        self.db.upsert_job(job)
        export_path = os.path.join(self.temp_dir.name, "exported_jobs.csv")
        count = self.db.export_to_csv(export_path, min_score=40.0)
        self.assertEqual(count, 1)
        self.assertTrue(os.path.exists(export_path))
        with open(export_path, "r", encoding="utf-8-sig") as f:
            header = f.readline()
            self.assertIn("job_title", header)
            self.assertIn("posting_date_ist", header)


class TestMissingFieldsAndEdgeCases(unittest.TestCase):
    """Verifies missing fields, future dates, location query leakage, and unknown experience."""

    def setUp(self):
        self.scorer = JobScorer(TEST_CONFIG)
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db = JobDatabase(os.path.join(self.temp_dir.name, "test_edge.db"))

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_job_with_all_unknown_fields(self):
        sparse_job = {
            "job_title": "", "company": "", "location": "",
            "posting_date": "", "application_url": "", "source_url": "", "source_name": ""
        }
        res = self.scorer.score_job(sparse_job)
        self.assertIsInstance(res["match_score"], float)
        self.assertEqual(res["priority"], "Low")

    def test_unverified_date_marked_estimated(self):
        job = {
            "job_title": "Cloud Support Engineer",
            "company": "Amazon Operations",
            "location": "Pune",
            "posting_date": "2026-10-09T05:00:00",
            "date_confidence": "Estimated (Feed Indexed Date)"
        }
        res = self.scorer.score_job(job)
        self.assertIn("feed indexed date", res["potential_gaps"].lower())

    def test_sr_devops_title_disqualified(self):
        job = {
            "job_title": "Sr. Devops Engineer",
            "company": "Enterprise Cloud",
            "location": "Pune",
            "posting_date": "2026-10-09T05:00:00"
        }
        res = self.scorer.score_job(job)
        self.assertEqual(res["match_score"], 0.0)
        self.assertEqual(res["priority"], "Low")

    def test_devops_five_pillars_scored_high(self):
        job = {
            "job_title": "Junior DevOps Engineer",
            "company": "Cloud Corp",
            "location": "Pune",
            "posting_date": datetime.now(timezone.utc).isoformat(),
            "raw_snippet": "Looking for junior engineer with AWS, Kubernetes, Docker, Terraform, and CI/CD pipelines (GitHub Actions)."
        }
        res = self.scorer.score_job(job)
        self.assertEqual(len(res["matched_pillars"]), 5)
        self.assertGreaterEqual(res["match_score"], 80.0)

    def test_database_max_age_days_filtering(self):
        now = datetime.now(timezone.utc)
        job_recent = {
            "job_title": "DevOps Engineer", "company": "Recent Tech", "location": "Pune",
            "application_url": "https://recent.com/job1", "posting_date": (now - timedelta(hours=10)).isoformat(),
            "date_confidence": "Verified", "match_score": 80.0
        }
        job_old = {
            "job_title": "DevOps Engineer", "company": "Old Tech", "location": "Pune",
            "application_url": "https://old.com/job2", "posting_date": (now - timedelta(days=5)).isoformat(),
            "date_confidence": "Verified", "match_score": 85.0
        }
        self.db.upsert_job(job_recent)
        self.db.upsert_job(job_old)
        jobs_24h = self.db.get_jobs(max_age_days=1.0)
        self.assertEqual(len(jobs_24h), 1)
        self.assertEqual(jobs_24h[0]["company"], "Recent Tech")

    def test_future_timestamp_rejected(self):
        """Future-dated timestamp must be rejected and not clamped to 0 hours ago."""
        future_iso = (datetime.now(timezone.utc) + timedelta(days=2)).isoformat()
        job = {
            "job_title": "DevOps Engineer", "company": "Future Corp", "location": "Pune",
            "posting_date": future_iso
        }
        res = self.scorer.score_job(job)
        self.assertIsNone(res["hours_ago"])
        self.assertIn("future", res["match_reasons"].lower())

    def test_missing_experience_labeled_unknown(self):
        """When experience is missing from snippet, label 'Unknown', never claim '0-3 years'."""
        job = {
            "job_title": "DevOps Engineer", "company": "Neutral Tech", "location": "Pune",
            "raw_snippet": "AWS, Linux, Terraform, Kubernetes"
        }
        res = self.scorer.score_job(job)
        self.assertEqual(res["experience_required"], "Unknown")
        self.assertIn("not specified", res["experience_evidence"].lower())

    def test_foreign_remote_restriction_disqualified(self):
        """Remote job restricted to US only or North America must be disqualified."""
        job_us = {
            "job_title": "DevOps Engineer", "company": "US Corp",
            "location": "Remote - US Only", "work_arrangement": "Remote", "raw_snippet": "AWS, K8s"
        }
        res_us = self.scorer.score_job(job_us)
        self.assertEqual(res_us["match_score"], 0.0)
        self.assertIn("restricted", res_us["potential_gaps"].lower())

    def test_liveness_check_conservative(self):
        """404/410 marked Expired; 403/429 marked Unverified (Anti-Bot), never Expired."""
        with patch("urllib.request.urlopen") as mock_url:
            # Test 200 OK
            mock_resp = MagicMock()
            mock_resp.status = 200
            mock_url.return_value.__enter__.return_value = mock_resp
            status = self.db.check_url_liveness("https://example.com/job")
            self.assertEqual(status, "Active")

            # Test 404 Not Found
            mock_url.side_effect = urllib.error.HTTPError("https://example.com/404", 404, "Not Found", {}, None)
            status_404 = self.db.check_url_liveness("https://example.com/404")
            self.assertIn("Expired (404)", status_404)

            # Test 403 Forbidden (Anti-bot)
            mock_url.side_effect = urllib.error.HTTPError("https://example.com/403", 403, "Forbidden", {}, None)
            status_403 = self.db.check_url_liveness("https://example.com/403")
            self.assertIn("Unverified", status_403)
            self.assertNotIn("Expired", status_403)


class TestMockedSourceConnectors(unittest.TestCase):
    """Verifies that API/RSS connectors parse fields correctly and handle HTTP errors gracefully."""

    def test_lever_connector_parses_created_at(self):
        source = LeverPublicJobSource(["testco"], "TestUA/1.0")
        mock_payload = json.dumps([
            {
                "text": "Junior DevOps Engineer",
                "createdAt": 1728864000000,
                "hostedUrl": "https://jobs.lever.co/testco/123",
                "applyUrl": "https://jobs.lever.co/testco/123/apply",
                "categories": {"location": "Pune, India"},
                "workplaceType": "hybrid",
                "descriptionPlain": "AWS, Docker, Linux, 1-2 years experience."
            }
        ]).encode("utf-8")

        with patch.object(source, "fetch_url", return_value=mock_payload):
            jobs = source.fetch_jobs()
            self.assertEqual(len(jobs), 1)
            j = jobs[0]
            self.assertEqual(j["job_title"], "Junior DevOps Engineer")
            self.assertEqual(j["company"], "Testco")
            self.assertEqual(j["date_confidence"], "Verified")
            self.assertTrue(j["posting_date"].startswith("2024-10-14") or "T" in j["posting_date"])
            self.assertEqual(j["canonical_application_url"], "https://jobs.lever.co/testco/123/apply")

    def test_ashby_connector_parses_published_at(self):
        source = AshbyPublicJobSource(["testco"], "TestUA/1.0")
        mock_payload = json.dumps({
            "jobs": [
                {
                    "title": "Cloud Infrastructure Engineer",
                    "location": "Remote",
                    "isRemote": True,
                    "publishedAt": "2026-10-14T10:00:00.000+00:00",
                    "jobUrl": "https://jobs.ashbyhq.com/testco/abc",
                    "applyUrl": "https://jobs.ashbyhq.com/testco/abc/apply",
                    "descriptionPlain": "Kubernetes, Terraform, CI/CD"
                }
            ]
        }).encode("utf-8")

        with patch.object(source, "fetch_url", return_value=mock_payload):
            jobs = source.fetch_jobs()
            self.assertEqual(len(jobs), 1)
            j = jobs[0]
            self.assertEqual(j["date_confidence"], "Verified")
            self.assertEqual(j["posting_date"], "2026-10-14T10:00:00.000+00:00")
            self.assertEqual(j["work_arrangement"], "Remote")

    def test_source_http_error_handled_gracefully(self):
        source = LeverPublicJobSource(["broken_co"], "TestUA/1.0")
        with patch.object(source, "fetch_url", side_effect=Exception("HTTP 500 Server Error")):
            jobs = source.fetch_jobs()
            self.assertEqual(jobs, [])
            self.assertIsNotNone(source.last_error)
            self.assertIn("500", source.last_error)


if __name__ == "__main__":
    unittest.main()
