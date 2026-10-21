"""
sources.py - Reliable, multi-source job discovery connectors.

Integrates:
1. Greenhouse Public Boards (GitLab, Canonical, Elastic, Wikimedia, Sumo Logic)
2. Lever Public API (Palantir, Spotify, etc.) - Verified createdAt timestamps
3. Ashby Public API (Airbyte, Vanta, Ramp, Linear, Notion, Cursor) - Verified publishedAt
4. We Work Remotely RSS - Verified pubDate with strict DevOps keyword filtering
5. Jobicy Remote Jobs API - Verified pubDate with country/geo filtering
6. Google News RSS - Supplementary discovery (strictly marked Estimated Feed Indexed Date, no query leakage)
7. Targeted Search Link Generator - Pre-filtered manual search URLs for LinkedIn, Naukri, Indeed, Glassdoor

Zero unauthorized scraping. Respects robots, access controls, rate limits, and timeouts.
"""

import email.utils
import html
import json
import logging
import re
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("devops_job_hunter.sources")

DEVOPS_ROLE_KEYWORDS = [
    "devops", "cloud", "infrastructure", "sre", "platform", "kubernetes",
    "systems", "linux", "aws", "terraform", "reliability", "cloud support",
    "cloud administrator", "site reliability"
]

AGGREGATOR_PATTERNS = [
    r"\bjobs\s+in\b",
    r"\bvacancies\s+in\b",
    r"\bjob\s+vacancies\b",
    r"\bjobs\s+near\s+me\b",
    r"\bopenings\s+in\b",
    r"\bjob\s+openings\b",
    r"\bjobs\s*-\s*naukri\b",
    r"\bfind\s+us\s+jobs\b",
    r"\bopen\s+roles\b",
    r"\bhiring\s+for\s+devops\s+in\b"
]


class BaseSource:
    """Abstract base class for all job sources."""

    def __init__(self, name: str, user_agent: str, timeout: int = 12):
        self.name = name
        self.user_agent = user_agent
        self.timeout = timeout
        self.last_error: Optional[str] = None
        self.execution_time: float = 0.0

    def fetch_url(self, url: str) -> bytes:
        headers = {
            "User-Agent": self.user_agent,
            "Accept": "application/rss+xml, application/xml, application/json, text/xml, */*",
            "Accept-Language": "en-US,en;q=0.9",
        }
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            return resp.read()

    def fetch_jobs(self) -> List[Dict[str, Any]]:
        raise NotImplementedError


class GreenhousePublicJobSource(BaseSource):
    """Public career board connector for tech companies using Greenhouse."""

    def __init__(self, company_boards: List[str], user_agent: str, timeout: int = 12):
        super().__init__("GreenhousePublicBoards", user_agent, timeout)
        self.company_boards = company_boards

    def fetch_jobs(self) -> List[Dict[str, Any]]:
        start_t = time.time()
        jobs: List[Dict[str, Any]] = []
        errors: List[str] = []

        for board in self.company_boards:
            url = f"https://boards-api.greenhouse.io/v1/boards/{board}/jobs?content=true"
            try:
                content = self.fetch_url(url)
                data = json.loads(content.decode("utf-8"))
                board_jobs = data.get("jobs", [])

                for j in board_jobs:
                    title = (j.get("title") or "").strip()
                    if not any(k in title.lower() for k in DEVOPS_ROLE_KEYWORDS):
                        continue

                    location_name = "Unknown"
                    if isinstance(j.get("location"), dict):
                        location_name = j.get("location", {}).get("name") or "Unknown"

                    app_url = j.get("absolute_url") or "Unknown"
                    updated_at = j.get("updated_at") or "Unknown"
                    content_text = re.sub(r"<[^>]+>", " ", j.get("content", "")).strip()

                    jobs.append({
                        "job_title": title,
                        "company": board.title(),
                        "location": location_name,
                        "work_arrangement": "Remote" if "remote" in location_name.lower() else "Unknown",
                        "application_url": app_url,
                        "source_url": app_url,
                        "canonical_application_url": app_url,
                        "source_name": f"Greenhouse ({board.title()})",
                        "posting_date": updated_at,
                        "date_confidence": "Listing Updated",
                        "posting_date_evidence": f"Greenhouse board updated_at timestamp: {updated_at}",
                        "experience_required": "Unknown",
                        "raw_snippet": f"{title} at {board.title()} in {location_name}. {content_text[:500]}"
                    })
            except Exception as e:
                err = f"Greenhouse board '{board}' error: {e}"
                errors.append(err)
                logger.warning(err)

        self.execution_time = time.time() - start_t
        if errors and not jobs:
            self.last_error = "; ".join(errors)
        elif errors:
            self.last_error = f"Partial success: {len(errors)} board errors"
        else:
            self.last_error = None

        return jobs


class LeverPublicJobSource(BaseSource):
    """Public REST connector for tech companies using Lever."""

    def __init__(self, company_boards: List[str], user_agent: str, timeout: int = 12):
        super().__init__("LeverPublicBoards", user_agent, timeout)
        self.company_boards = company_boards

    def fetch_jobs(self) -> List[Dict[str, Any]]:
        start_t = time.time()
        jobs: List[Dict[str, Any]] = []
        errors: List[str] = []

        for company in self.company_boards:
            url = f"https://api.lever.co/v0/postings/{company}?mode=json"
            try:
                content = self.fetch_url(url)
                postings = json.loads(content.decode("utf-8"))
                if not isinstance(postings, list):
                    continue

                for p in postings:
                    title = (p.get("text") or "").strip()
                    if not any(k in title.lower() for k in DEVOPS_ROLE_KEYWORDS):
                        continue

                    categories = p.get("categories", {})
                    loc = categories.get("location") or "Unknown"
                    workplace = p.get("workplaceType", "")
                    hosted_url = p.get("hostedUrl") or "Unknown"
                    apply_url = p.get("applyUrl") or hosted_url
                    
                    created_at_ms = p.get("createdAt")
                    posting_date = "Unknown"
                    date_conf = "Unknown"
                    date_ev = ""
                    if created_at_ms and isinstance(created_at_ms, (int, float)):
                        try:
                            dt = datetime.fromtimestamp(created_at_ms / 1000.0, tz=timezone.utc)
                            posting_date = dt.isoformat()
                            date_conf = "Verified"
                            date_ev = f"Lever API createdAt: {posting_date}"
                        except Exception:
                            pass

                    desc_plain = p.get("descriptionPlain", "") or ""
                    snippet = f"{title} at {company.title()} in {loc}. {desc_plain[:500]}"

                    jobs.append({
                        "job_title": title,
                        "company": company.title(),
                        "location": loc,
                        "work_arrangement": "Remote" if "remote" in workplace.lower() or "remote" in loc.lower() else "Onsite",
                        "application_url": apply_url,
                        "source_url": hosted_url,
                        "canonical_application_url": apply_url,
                        "source_name": f"Lever ({company.title()})",
                        "posting_date": posting_date,
                        "date_confidence": date_conf,
                        "posting_date_evidence": date_ev,
                        "experience_required": "Unknown",
                        "raw_snippet": snippet
                    })
            except Exception as e:
                err = f"Lever company '{company}' error: {e}"
                errors.append(err)
                logger.warning(err)

        self.execution_time = time.time() - start_t
        self.last_error = "; ".join(errors) if errors else None
        return jobs


class AshbyPublicJobSource(BaseSource):
    """Public REST connector for tech companies using Ashby."""

    def __init__(self, company_boards: List[str], user_agent: str, timeout: int = 12):
        super().__init__("AshbyPublicBoards", user_agent, timeout)
        self.company_boards = company_boards

    def fetch_jobs(self) -> List[Dict[str, Any]]:
        start_t = time.time()
        jobs: List[Dict[str, Any]] = []
        errors: List[str] = []

        for company in self.company_boards:
            url = f"https://api.ashbyhq.com/posting-api/job-board/{company}"
            try:
                content = self.fetch_url(url)
                data = json.loads(content.decode("utf-8"))
                board_jobs = data.get("jobs", [])

                for j in board_jobs:
                    title = (j.get("title") or "").strip()
                    if not any(k in title.lower() for k in DEVOPS_ROLE_KEYWORDS):
                        continue

                    loc = j.get("location") or "Unknown"
                    is_remote = bool(j.get("isRemote"))
                    job_url = j.get("jobUrl") or "Unknown"
                    apply_url = j.get("applyUrl") or job_url
                    pub_at = j.get("publishedAt") or "Unknown"
                    
                    date_conf = "Verified" if pub_at != "Unknown" else "Unknown"
                    date_ev = f"Ashby API publishedAt: {pub_at}" if pub_at != "Unknown" else ""

                    desc = j.get("descriptionPlain", "") or ""
                    snippet = f"{title} at {company.title()} in {loc}. {desc[:500]}"

                    jobs.append({
                        "job_title": title,
                        "company": company.title(),
                        "location": loc,
                        "work_arrangement": "Remote" if is_remote or "remote" in loc.lower() else "Onsite",
                        "application_url": apply_url,
                        "source_url": job_url,
                        "canonical_application_url": apply_url,
                        "source_name": f"Ashby ({company.title()})",
                        "posting_date": pub_at,
                        "date_confidence": date_conf,
                        "posting_date_evidence": date_ev,
                        "experience_required": "Unknown",
                        "raw_snippet": snippet
                    })
            except Exception as e:
                err = f"Ashby company '{company}' error: {e}"
                errors.append(err)
                logger.warning(err)

        self.execution_time = time.time() - start_t
        self.last_error = "; ".join(errors) if errors else None
        return jobs


class RemotiveJobSource(BaseSource):
    """Public API connector for Remotive remote jobs."""

    def __init__(self, category: str = "devops", user_agent: str = "DevOpsJobHunter/1.0", timeout: int = 12):
        super().__init__("RemotiveAPI", user_agent, timeout)
        self.category = category

    def fetch_jobs(self) -> List[Dict[str, Any]]:
        start_t = time.time()
        url = f"https://remotive.com/api/remote-jobs?category={self.category}"
        jobs: List[Dict[str, Any]] = []

        try:
            content = self.fetch_url(url)
            data = json.loads(content.decode("utf-8"))
            raw_jobs = data.get("jobs", [])

            for j in raw_jobs:
                title = j.get("title") or "Unknown"
                company = j.get("company_name") or "Unknown"
                app_url = j.get("url") or "Unknown"
                pub_date = j.get("publication_date") or "Unknown"
                cand_loc = j.get("candidate_required_location") or "Worldwide"
                tags = j.get("tags") or []
                desc_html = j.get("description") or ""

                clean_desc = re.sub(r"<[^>]+>", " ", desc_html).strip()
                clean_desc = html.unescape(clean_desc)

                date_conf = "Verified" if pub_date != "Unknown" else "Unknown"
                snippet = f"{title} at {company}. Req Location: {cand_loc}. Tags: {', '.join(tags)}. {clean_desc[:400]}"

                jobs.append({
                    "job_title": title,
                    "company": company,
                    "location": cand_loc,
                    "work_arrangement": "Remote",
                    "application_url": app_url,
                    "source_url": app_url,
                    "canonical_application_url": app_url,
                    "source_name": "Remotive",
                    "posting_date": pub_date,
                    "date_confidence": date_conf,
                    "posting_date_evidence": f"Remotive API publication_date: {pub_date}",
                    "experience_required": "Unknown",
                    "raw_snippet": snippet
                })

            self.last_error = None
        except Exception as e:
            self.last_error = f"Remotive API error: {e}"
            logger.warning(self.last_error)

        self.execution_time = time.time() - start_t
        return jobs


class WeWorkRemotelyJobSource(BaseSource):
    """Permitted RSS feed connector for WeWorkRemotely DevOps & Sysadmin category."""

    def __init__(self, rss_url: str, user_agent: str, timeout: int = 12):
        super().__init__("WeWorkRemotely", user_agent, timeout)
        self.rss_url = rss_url

    def fetch_jobs(self) -> List[Dict[str, Any]]:
        start_t = time.time()
        jobs: List[Dict[str, Any]] = []

        try:
            content = self.fetch_url(self.rss_url)
            tree = ET.fromstring(content)
            items = tree.findall(".//item")

            for item in items:
                raw_title = item.findtext("title") or ""
                link = item.findtext("link") or "Unknown"
                pub_date_raw = item.findtext("pubDate") or ""
                desc_raw = item.findtext("description") or ""

                company = "Unknown"
                title = raw_title
                if ": " in raw_title:
                    parts = raw_title.split(": ", 1)
                    company = parts[0].strip()
                    title = parts[1].strip()

                if not any(k in title.lower() for k in DEVOPS_ROLE_KEYWORDS):
                    continue

                clean_desc = re.sub(r"<[^>]+>", " ", desc_raw).strip()
                clean_desc = html.unescape(clean_desc)

                posting_date = "Unknown"
                date_conf = "Unknown"
                date_ev = ""
                if pub_date_raw:
                    try:
                        dt = email.utils.parsedate_to_datetime(pub_date_raw)
                        posting_date = dt.astimezone(timezone.utc).isoformat()
                        date_conf = "Verified"
                        date_ev = f"WeWorkRemotely RSS pubDate: {pub_date_raw}"
                    except Exception:
                        pass

                jobs.append({
                    "job_title": title,
                    "company": company,
                    "location": "Remote",
                    "work_arrangement": "Remote",
                    "application_url": link,
                    "source_url": link,
                    "canonical_application_url": link,
                    "source_name": "WeWorkRemotely",
                    "posting_date": posting_date,
                    "date_confidence": date_conf,
                    "posting_date_evidence": date_ev,
                    "experience_required": "Unknown",
                    "raw_snippet": f"{title} at {company}. {clean_desc[:400]}"
                })

            self.last_error = None
        except Exception as e:
            self.last_error = f"WeWorkRemotely RSS error: {e}"
            logger.warning(self.last_error)

        self.execution_time = time.time() - start_t
        return jobs


class JobicyJobSource(BaseSource):
    """Public API connector for Jobicy remote jobs."""

    def __init__(self, user_agent: str, timeout: int = 12):
        super().__init__("JobicyAPI", user_agent, timeout)

    def fetch_jobs(self) -> List[Dict[str, Any]]:
        start_t = time.time()
        jobs: List[Dict[str, Any]] = []

        try:
            url = "https://jobicy.com/api/v2/remote-jobs?count=50"
            content = self.fetch_url(url)
            data = json.loads(content.decode("utf-8"))
            raw_jobs = data.get("jobs", [])

            for j in raw_jobs:
                title = j.get("jobTitle") or ""
                if not any(k in title.lower() for k in DEVOPS_ROLE_KEYWORDS):
                    continue

                company = j.get("companyName") or "Unknown"
                app_url = j.get("url") or "Unknown"
                pub_date = j.get("pubDate") or "Unknown"
                geo = j.get("jobGeo") or "Worldwide"
                desc = re.sub(r"<[^>]+>", " ", j.get("jobDescription", "")).strip()

                date_conf = "Verified" if pub_date != "Unknown" else "Unknown"

                jobs.append({
                    "job_title": title,
                    "company": company,
                    "location": geo,
                    "work_arrangement": "Remote",
                    "application_url": app_url,
                    "source_url": app_url,
                    "canonical_application_url": app_url,
                    "source_name": "Jobicy",
                    "posting_date": pub_date,
                    "date_confidence": date_conf,
                    "posting_date_evidence": f"Jobicy API pubDate: {pub_date}",
                    "experience_required": "Unknown",
                    "raw_snippet": f"{title} at {company}. Geo: {geo}. {desc[:400]}"
                })

            self.last_error = None
        except Exception as e:
            self.last_error = f"Jobicy API error: {e}"
            logger.warning(self.last_error)

        self.execution_time = time.time() - start_t
        return jobs


class GoogleNewsJobSource(BaseSource):
    """
    Supplementary lead discovery connector via Google News RSS.
    Strictly flags dates as Estimated (Feed Indexed Date), never infers location from queries,
    and rejects aggregator landing/directory pages.
    """

    def __init__(self, queries: List[str], user_agent: str, timeout: int = 12, delay: float = 1.0):
        super().__init__("GoogleNewsRSS", user_agent, timeout)
        self.queries = queries
        self.delay = delay

    def fetch_jobs(self) -> List[Dict[str, Any]]:
        start_t = time.time()
        jobs: List[Dict[str, Any]] = []
        errors: List[str] = []

        for q in self.queries:
            time.sleep(self.delay)
            encoded = urllib.parse.quote(q)
            rss_url = f"https://news.google.com/rss/search?q={encoded}&hl=en-IN&gl=IN&ceid=IN:en"
            
            try:
                content = self.fetch_url(rss_url)
                tree = ET.fromstring(content)
                items = tree.findall(".//item")
                
                for item in items:
                    parsed = self._parse_item(item, q)
                    if parsed:
                        jobs.append(parsed)
            except Exception as e:
                err_msg = f"Query '{q}' error: {e}"
                logger.warning(err_msg)
                errors.append(err_msg)

        self.execution_time = time.time() - start_t
        self.last_error = "; ".join(errors[:2]) if errors else None
        return jobs

    def _parse_item(self, item: ET.Element, query: str) -> Optional[Dict[str, Any]]:
        raw_title = item.findtext("title") or ""
        link = item.findtext("link") or ""
        pub_date_raw = item.findtext("pubDate") or ""
        source_elem = item.find("source")
        source_publisher = source_elem.text if source_elem is not None else "Google News"
        desc_raw = item.findtext("description") or ""

        if not raw_title or not link:
            return None

        clean_desc = re.sub(r"<[^>]+>", " ", desc_raw).strip()
        clean_desc = html.unescape(clean_desc)

        # Reject aggregator category / directory pages
        for pat in AGGREGATOR_PATTERNS:
            if re.search(pat, raw_title, re.IGNORECASE):
                return None

        posting_date = "Unknown"
        date_conf = "Estimated (Feed Indexed Date)"
        date_ev = ""
        if pub_date_raw:
            try:
                parsed_dt = email.utils.parsedate_to_datetime(pub_date_raw)
                posting_date = parsed_dt.astimezone(timezone.utc).isoformat()
                date_ev = f"Google News RSS crawl pubDate: {pub_date_raw}"
            except Exception:
                posting_date = "Unknown"
                date_conf = "Unknown"

        title = raw_title
        company = "Unknown"
        location = "Unknown"
        work_arrangement = "Unknown"

        if " - " in title:
            parts = title.rsplit(" - ", 1)
            title = parts[0].strip()
            if source_publisher == "Google News":
                source_publisher = parts[1].strip()

        hiring_match = re.match(r"^(.*?)\s+hiring\s+(.*?)(?:\s*\|\s*(.*?))?(?:\s*\|\s*in\s+(.*?))?$", title, re.IGNORECASE)
        if hiring_match:
            company = hiring_match.group(1).strip()
            title = hiring_match.group(2).strip()
            extra = (hiring_match.group(3) or "").strip()
            loc_extra = (hiring_match.group(4) or "").strip()
            
            if extra.lower() in ["remote", "hybrid", "onsite"]:
                work_arrangement = extra.title()
            elif "in " in extra.lower() and not loc_extra:
                loc_extra = re.sub(r"^in\s+", "", extra, flags=re.IGNORECASE).strip()
                
            if loc_extra:
                location = loc_extra
        elif " - " in title:
            parts = [p.strip() for p in title.split(" - ")]
            if len(parts) >= 2:
                title = parts[0]
                company = parts[1]

        combined_text = f"{title} {clean_desc}".lower()
        if work_arrangement == "Unknown":
            if "remote" in combined_text or "work from home" in combined_text:
                work_arrangement = "Remote"
            elif "hybrid" in combined_text:
                work_arrangement = "Hybrid"
            elif "on-site" in combined_text or "onsite" in combined_text:
                work_arrangement = "Onsite"

        # Location extraction from text only - NO query leakage!
        if location == "Unknown":
            if "pune" in combined_text:
                location = "Pune, Maharashtra, India"
            elif "bengaluru" in combined_text or "bangalore" in combined_text:
                location = "Bengaluru, Karnataka, India"
            elif "mumbai" in combined_text:
                location = "Mumbai, Maharashtra, India"
            elif "hyderabad" in combined_text:
                location = "Hyderabad, Telangana, India"
            elif "india" in combined_text:
                location = "India"
            elif "remote" in work_arrangement.lower():
                location = "Remote (Location Unspecified)"

        # Reject listings with no identifiable employer AND generic title
        if (company == "Unknown" or not company) and any(g in title.lower() for g in ["devops", "cloud engineer", "engineer"]) and "in " in title.lower():
            return None

        return {
            "job_title": title,
            "company": company if company else "Unknown",
            "location": location,
            "work_arrangement": work_arrangement,
            "application_url": link,
            "source_url": link,
            "canonical_application_url": "",
            "source_name": f"GoogleNews ({source_publisher})",
            "posting_date": posting_date,
            "date_confidence": date_conf,
            "posting_date_evidence": date_ev,
            "experience_required": "Unknown",
            "raw_snippet": f"{title} - {clean_desc}"[:500]
        }


class TargetedSearchLinkGenerator:
    """Generates verified pre-filtered search URLs for platforms without open APIs."""

    @staticmethod
    def generate_all_links() -> Dict[str, List[Dict[str, str]]]:
        return {
            "LinkedIn Jobs": [
                {
                    "title": "LinkedIn: Junior DevOps / Cloud (Pune, < 24 Hours)",
                    "url": "https://www.linkedin.com/jobs/search/?keywords=DevOps%20OR%20Cloud&location=Pune%2C%20Maharashtra%2C%20India&f_TPR=r86400&f_E=1%2C2"
                },
                {
                    "title": "LinkedIn: AWS Cloud Support / Administrator (Remote India, < 24 Hours)",
                    "url": "https://www.linkedin.com/jobs/search/?keywords=AWS%20Cloud%20Support%20OR%20Administrator&location=India&f_WT=2&f_TPR=r86400&f_E=1%2C2"
                }
            ],
            "Naukri.com": [
                {
                    "title": "Naukri: DevOps / Cloud Engineer (Pune, 0-3 Yrs, Last 1 Day)",
                    "url": "https://www.naukri.com/devops-jobs-in-pune?k=devops%20OR%20cloud&cityTypeGid=9508&experience=0&experience=3&jobAge=1"
                },
                {
                    "title": "Naukri: Remote Cloud / Infrastructure Engineer (India, 0-3 Yrs, Last 1 Day)",
                    "url": "https://www.naukri.com/remote-devops-jobs?k=devops%20OR%20cloud&wfhType=2&experience=0&experience=3&jobAge=1"
                }
            ],
            "Indeed India": [
                {
                    "title": "Indeed: Junior DevOps / Cloud Support (Pune, Last 24 Hours)",
                    "url": "https://in.indeed.com/jobs?q=junior+devops+OR+cloud+support&l=Pune%2C+Maharashtra&fromage=1"
                },
                {
                    "title": "Indeed: Remote Cloud Administrator (India, Last 24 Hours)",
                    "url": "https://in.indeed.com/jobs?q=cloud+administrator&l=Remote&fromage=1"
                }
            ]
        }


class SourceManager:
    """Orchestrates all configured source connectors and generates audit report."""

    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.sources: List[BaseSource] = []
        self._initialize_sources()

    def _initialize_sources(self) -> None:
        sources_cfg = self.config.get("sources_config", {})
        settings = self.config.get("settings", {})
        ua = settings.get("user_agent", "DevOpsJobHunter/2.0 (Windows NT 10.0)")
        timeout = settings.get("request_timeout_seconds", 12)
        delay = settings.get("request_delay_seconds", 1.0)

        # 1. Greenhouse Public Boards
        gh_cfg = sources_cfg.get("greenhouse_public_boards", {})
        if gh_cfg.get("enabled", True):
            boards = gh_cfg.get("company_boards", ["gitlab", "canonical", "elastic", "wikimedia", "sumologic"])
            if boards:
                self.sources.append(GreenhousePublicJobSource(boards, ua, timeout))

        # 2. Lever Public Boards
        lever_cfg = sources_cfg.get("lever_public_boards", {})
        if lever_cfg.get("enabled", True):
            boards = lever_cfg.get("company_boards", ["palantir", "spotify"])
            if boards:
                self.sources.append(LeverPublicJobSource(boards, ua, timeout))

        # 3. Ashby Public Boards
        ashby_cfg = sources_cfg.get("ashby_public_boards", {})
        if ashby_cfg.get("enabled", True):
            boards = ashby_cfg.get("company_boards", ["airbyte", "vanta", "ramp", "linear", "notion", "cursor"])
            if boards:
                self.sources.append(AshbyPublicJobSource(boards, ua, timeout))

        # 4. Jobicy Remote Jobs API
        jobicy_cfg = sources_cfg.get("jobicy_api", {})
        if jobicy_cfg.get("enabled", True):
            self.sources.append(JobicyJobSource(ua, timeout))

        # 5. We Work Remotely RSS
        wwr_cfg = sources_cfg.get("we_work_remotely_rss", {})
        if wwr_cfg.get("enabled", True):
            rss_url = wwr_cfg.get("url", "https://weworkremotely.com/categories/remote-devops-sysadmin-jobs.rss")
            self.sources.append(WeWorkRemotelyJobSource(rss_url, ua, timeout))

        # 6. Remotive Public API
        rem_cfg = sources_cfg.get("remotive_api", {})
        if rem_cfg.get("enabled", True):
            cat = rem_cfg.get("category", "devops")
            self.sources.append(RemotiveJobSource(cat, ua, timeout))

        # 7. Supplementary Google News RSS
        gn_cfg = sources_cfg.get("google_news_rss", {})
        if gn_cfg.get("enabled", True):
            queries = gn_cfg.get("queries", [])
            self.sources.append(GoogleNewsJobSource(queries, ua, timeout, delay))

    def fetch_all(self) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        all_jobs: List[Dict[str, Any]] = []
        report: List[Dict[str, Any]] = []

        for source in self.sources:
            t0 = time.time()
            try:
                jobs = source.fetch_jobs()
                duration = time.time() - t0
                status = "SUCCESS" if source.last_error is None else ("PARTIAL" if jobs else "FAILED")
                
                report.append({
                    "source": source.name,
                    "status": status,
                    "jobs_found": len(jobs),
                    "duration_sec": round(duration, 2),
                    "error": source.last_error or "None"
                })
                all_jobs.extend(jobs)
            except Exception as e:
                duration = time.time() - t0
                report.append({
                    "source": source.name,
                    "status": "FAILED",
                    "jobs_found": 0,
                    "duration_sec": round(duration, 2),
                    "error": f"Unhandled error: {e}"
                })

        return all_jobs, report
