# 🎯 DevOps & Cloud Job Hunter (Pune & India Remote)

A high-performance, local job search and technical recruiter discovery engine engineered specifically for early-career DevOps, Cloud, and Infrastructure engineers (0–3 years experience, operations/support background, and internship experience).

Runs **100% locally on Windows using standard Python**.
* **Zero Paid APIs**: No OpenAI, Anthropic, Apify, or commercial subscription required.
* **100% Python Standard Library Core**: Core discovery, scoring, SQLite persistence, and CSV exports require zero external packages.
* **Legitimate Public Sources**: Queries public career APIs (Greenhouse, Lever, Ashby, Workday CXS, SmartRecruiters), developer APIs (Jobicy, Remotive), and RSS feeds without bypassing authentication, paywalls, or CAPTCHAs.
* **Intelligent Seniority & Pillar Ranking**: Scores listings (0–100) based on role alignment, 5 DevOps pillars (AWS, Kubernetes, Docker, Terraform/OpenTofu, CI/CD), and immediate disqualification of senior/lead (>3 years) roles.
* **Dedicated Excel Reports**: Generates styled `.xlsx` workbooks with auto-filters, frozen headers, clickable hyperlinks, and global URL deduplication (via optional `openpyxl`).
* **Technical Recruiter Outreach Assistant**: Discovers recruiters and hiring managers from active postings and public announcements, scores them, and generates personalized LinkedIn connection notes (≤300 chars) and InMail templates.

---

## 📑 Table of Contents

- [Features & Implementation Status](#-features--implementation-status)
- [System Architecture](#-system-architecture)
- [Prerequisites & Installation](#-prerequisites--installation)
- [Quick Start](#-quick-start)
- [CLI Reference & Workflows](#-cli-reference--workflows)
- [Supported Job Sources & Connectors](#-supported-job-sources--connectors)
- [Scoring, Ranking & Filtering Engine](#-scoring-ranking--filtering-engine)
- [Deduplication & Database Persistence](#-deduplication--database-persistence)
- [Excel & CSV Report Structure](#-excel--csv-report-structure)
- [Recruiter Discovery & Outreach Assistant](#-recruiter-discovery--outreach-assistant)
- [Verification Limitations & Ethics](#-verification-limitations--ethics)
- [Windows Task Scheduler Automation](#-windows-task-scheduler-automation)
- [Configuration Guide](#-configuration-guide)
- [Realistic Roadmap](#-realistic-roadmap)

---

## 📋 Features & Implementation Status

To maintain technical transparency, all features are categorized by their exact verification status in the codebase:

| Capability | Status | Description |
| :--- | :---: | :--- |
| **Direct ATS REST Connectors** | **Implemented** | Connectors for Greenhouse, Lever, Ashby, Workday CXS, and SmartRecruiters public career endpoints. |
| **Remote Developer APIs & Feeds** | **Implemented** | Connectors for Jobicy, Remotive, and WeWorkRemotely RSS feeds with keyword validation. |
| **Supplementary Feed Discovery** | **Implemented** | Google News RSS feed queries with anti-query leakage location filters and feed indexed date tagging. |
| **Pre-Filtered Direct Links** | **Implemented** | Generator for direct, search-filtered URLs across LinkedIn, Naukri, Indeed, and Glassdoor (`job_search_links.md`). |
| **DevOps Pillar Scoring** | **Implemented** | 0–100 matching engine weighted by AWS, Kubernetes, Docker, Terraform/OpenTofu, CI/CD, location, and seniority. |
| **Senior Role Disqualification** | **Implemented** | Immediate rejection (-100 pts) for Senior, Lead, Principal, Architect, or >3 years experience requirements. |
| **Global Deduplication** | **Implemented** | Normalizes URL schemes, hosts, strips 20+ tracking parameters (`utm_*`, `trk`, etc.), and hashes company + title. |
| **SQLite Persistence** | **Implemented** | Local `jobs.db` database storing 28 columns for jobs and 29 columns for recruiters across runs. |
| **Active URL Liveness Audit** | **Implemented** | Revalidates stored application URLs via HTTP inspection, detecting closed vacancy markers and redirects. |
| **Styled Excel Generation** | **Implemented** | Formats reports into `output/jobs_YYYY-MM-DD_HH-MM.xlsx` with freeze panes, auto-filter, and hyperlinks. |
| **Recruiter Discovery & CRM** | **Implemented** | Discovers recruiters from job postings and public feeds, tracks outreach status, and generates LinkedIn notes. |
| **Automated ATS Board Probe** | **Experimental** | `scratch_probe_ats.py` probes Ashby, Greenhouse, and Lever endpoints across 56+ tech enterprises. |
| **Instahyre Page Probe** | **Experimental** | `scratch_instahyre.py` tests direct DOM extraction of Instahyre job posting timestamps and metadata. |
| **Additional ATS Connectors** | **Planned** | Native connectors for Workable, BambooHR, Taleo, SuccessFactors, and Recruitee. |
| **Automated Alerts** | **Planned** | Desktop, Telegram, or Discord webhook notifications for High-Priority (≥75 pts) vacancies. |

---

## 🏗️ System Architecture

```text
 ┌────────────────────────────────────────────────────────────────────────┐
 │                     Public Sources & API Connectors                    │
 │  Greenhouse │ Lever │ Ashby │ Workday │ SmartRecruiters │ Remotive     │
 │  Jobicy │ WeWorkRemotely │ Google News RSS │ Targeted Search Links     │
 └───────────────────────────────────┬────────────────────────────────────┘
                                     │ (Raw listings)
                                     ▼
 ┌────────────────────────────────────────────────────────────────────────┐
 │              Global Normalization & Deduplication Engine               │
 │  * Strip tracking parameters (utm_*, trk, trackingid, ref, etc.)       │
 │  * Canonical URL resolution & trailing slash cleanup                   │
 │  * Deterministic SHA-256 company + title hash                          │
 └───────────────────────────────────┬────────────────────────────────────┘
                                     │ (Unique listings)
                                     ▼
 ┌────────────────────────────────────────────────────────────────────────┐
 │                   Rule-Based DevOps Scoring Engine                     │
 │  * Role Alignment (max 25 pts)        * 5 DevOps Pillars (max 35 pts)  │
 │  * Seniority Fit (max 15 pts / -100)  * Location Fit (max 15 pts)      │
 │  * Recency Prioritization (max 15 pts)* Shift Preference (+2 bonus)    │
 └───────────────────────────────────┬────────────────────────────────────┘
                                     │
                  ┌──────────────────┴──────────────────┐
                  ▼                                     ▼
 ┌─────────────────────────────────┐   ┌─────────────────────────────────┐
 │    Local SQLite DB (jobs.db)    │   │         Report Generator        │
 │ * jobs table (28 columns)       │   │ * Excel (.xlsx) in output/      │
 │ * recruiters table (29 columns) │   │ * Ranked CSV (devops_jobs_*.csv)│
 │ * Status & Outreach tracking    │   │ * Pre-filtered search links     │
 └─────────────────────────────────┘   └─────────────────────────────────┘
```

---

## 💻 Prerequisites & Installation

### 1. System Requirements
* **Operating System**: Windows 10 / 11 (PowerShell 5.1+ or PowerShell 7+).
* **Python**: Version 3.10 or higher (Python 3.11+ recommended).

### 2. Verify Python
Check that Python is installed and accessible in your shell:

```powershell
python --version
```

### 3. Clone / Navigate to Project Directory
```powershell
cd path\to\devops-job-hunter
```

### 4. Optional Dependencies
The core engine runs **100% on Python's built-in standard library** (`sqlite3`, `urllib.request`, `xml.etree.ElementTree`, `json`, `csv`, `hashlib`).

To enable professional Excel (`.xlsx`) generation and run the automated test suites, install the optional packages:

```powershell
pip install openpyxl pytest
```

*(If `openpyxl` is not installed, you can still export full reports via CSV using `--output-format csv`).*

---

## ⚡ Quick Start

Run a complete discovery, scoring, database upsert, and Excel report generation for Pune and India-Remote roles posted in the last 24 hours:

```powershell
python main.py --max-days 1 --pune-remote
```

### What Happens:
1. **Queries** enabled public ATS endpoints and feeds.
2. **Deduplicates** raw listings globally by canonical URL and company/title hash.
3. **Scores** listings against early-career DevOps criteria (0–3 years).
4. **Persists** all unique records and updates `jobs.db`.
5. **Exports** a styled Excel report to `output/jobs_YYYY-MM-DD_HH-MM.xlsx`.
6. **Displays** a concise scan summary and source audit in the terminal.

---

## 🛠️ CLI Reference & Workflows

All commands are executed through `main.py` using standard PowerShell or Command Prompt.

### 1. Live Job Discovery

```powershell
# Standard 24-hour scan for Pune & Remote India (generates Excel report)
python main.py --max-days 1 --pune-remote

# Strict scan requiring verified employer posting dates only
python main.py --strict --pune-remote

# 7-day scan with full diagnostic funnel (explains why jobs passed or failed)
python main.py --diagnostic --max-days 7 --pune-remote

# Output directly to terminal cards instead of Excel
python main.py --output-format terminal --top 10

# Test run without writing to SQLite database
python main.py --dry-run --max-days 1 --pune-remote
```

### 2. Database Inspection & Maintenance

```powershell
# View database statistics, priority breakdown, and top opportunities
python main.py --stats

# View top 15 ranked opportunities stored in the database
python main.py --top 15

# Recalculate match scores and pillar rankings for all stored jobs
python main.py --rescore

# Re-check active liveness status for 50 stored jobs (detects closed roles)
python main.py --revalidate-urls 50
```

### 3. Offline Exporting

```powershell
# Export deduplicated jobs from SQLite to a fresh timestamped Excel workbook
python main.py --export-only --output-format excel --max-days 7 --pune-remote

# Export jobs from SQLite to CSV with a custom minimum score threshold
python main.py --export-only --output-format csv --min-score 50.0
```

### 4. Application Status Tracking (CRM)

Track your application pipeline directly in `jobs.db`:

```powershell
# Mark job #42 as Applied with submission notes
python main.py --set-status 42 Applied --notes "Applied via Workday portal with tailored AWS/Terraform resume"

# Mark job #58 as Reviewed
python main.py --set-status 58 Reviewed --notes "Follow-up via email next week"

# Archive a filled or irrelevant job
python main.py --set-status 104 Archived --notes "Role filled"
```
*Supported statuses*: `New`, `Applied`, `Reviewed`, `Archived`, `Interviewing`.

### 5. Technical Recruiter Discovery & Outreach

```powershell
# Discover technical recruiters from active job listings and public hiring feeds
python main.py --discover-recruiters

# List top ranked recruiters currently tracked in SQLite
python main.py --list-recruiters

# Filter recruiters by technical specialism (e.g. AWS, Kubernetes, DevOps)
python main.py --list-recruiters --specialism AWS

# Generate personalized LinkedIn Connection Note (<300 chars) and InMail template
python main.py --generate-message 1

# Update recruiter outreach status and set follow-up date
python main.py --set-recruiter-status 1 "Contacted" --follow-up 2026-11-01 --notes "Sent LinkedIn connection note"

# Export recruiters to devops_recruiters.csv
python main.py --export-recruiters
```

### 6. Search Link Generation

```powershell
# Generate pre-filtered search links for LinkedIn, Naukri, Indeed, Glassdoor
python main.py --generate-links

# Generate pre-filtered recruiter search links for LinkedIn and Naukri
python main.py --generate-recruiter-links
```

### 7. Automated Test Suites

```powershell
# Run the core system test suite (37 tests)
python test_system.py

# Run the recruiter system test suite (15 tests)
python test_recruiter_system.py

# Or run both via pytest
pytest test_system.py test_recruiter_system.py -v
```

### 8. Experimental Probe Scripts

```powershell
# Probe 56+ tech companies across Ashby, Greenhouse, and Lever public APIs
python scratch_probe_ats.py

# Test DOM timestamp extraction on an Instahyre vacancy
python scratch_instahyre.py
```

---

## 📡 Supported Job Sources & Connectors

The engine interacts only with authorized, public endpoints:

| Source | Connector Type | Authentication | Date Confidence | Target Scope / Employers |
| :--- | :--- | :---: | :---: | :--- |
| **Greenhouse** | Public Boards REST API (`/v1/boards/{board}/jobs`) | None | `Listing Updated` (`updated_at`) | Druva, Zscaler, LogicMonitor, SolarWinds, Rubrik, GitLab, Elastic, PureStorage, PagerDuty, JFrog, Canonical |
| **Lever** | Public Postings REST API (`/v0/postings/{company}`) | None | `Verified` (`createdAt` ms) | Palantir, Spotify, Toptal, Sonatype |
| **Ashby** | Public Job Board API (`/posting-api/job-board/{company}`) | None | `Verified` (`publishedAt` ISO) | Snowflake, Airbyte, Vanta, Ramp, Linear, Notion, Cursor |
| **Workday** | CXS Public Endpoints (`/wday/cxs/{tenant}/{site}/jobs`) | None | `Verified` (`postedOn` relative) | Salesforce, Mastercard |
| **SmartRecruiters** | Public Postings API (`/v1/companies/{co}/postings`) | None | `Verified` (`releasedDate`) | Colliers and verified enterprise tenants |
| **Jobicy** | Remote Jobs Developer API | None | `Verified` (`pubDate`) | Verified remote engineering vacancies |
| **Remotive** | Remote DevOps Public API | None | `Verified` (`publication_date`) | Verified remote DevOps & SysAdmin listings |
| **We Work Remotely** | DevOps Category RSS Feed | None | `Verified` (`pubDate`) | Global remote tech postings |
| **Google News RSS** | Supplementary Topic Search Feed | None | `Estimated (Feed Indexed Date)` | Recent vacancy news & announcements |
| **Targeted Links** | Markdown Link Generator | N/A | Manual Review | LinkedIn, Naukri, Indeed, Glassdoor |

### Company Target Registry (`company_targets.json`)
The repository includes a curated registry of **171 target companies** (enterprise cloud leaders, Pune GCCs, and product unicorns). It maps company names, aliases, official career portals, and supported ATS identifiers without requiring code changes.

---

## 🎯 Scoring, Ranking & Filtering Engine

Total Match Score is calculated from **0 to 100 points**:

$$\text{Final Score} = \text{clamp}(\text{Role} + \text{Skills} + \text{Seniority} + \text{Location} + \text{Shift} + \text{Recency},\; 0,\; 100)$$

### 1. Target Role & Title Alignment (Max 25 pts)
* **Prime Targets** (+25 pts): `Junior DevOps Engineer`, `Cloud Support Engineer`, `Cloud Administrator`, `Infrastructure Engineer`, `Platform Engineer`, `Junior SRE`, `Systems Engineer`.
* **Related Roles** (+16 pts): Contains `devops`, `cloud`, `infrastructure`, `sre`, `platform`, `kubernetes`.
* **Unrelated Roles** (-25 pts): `sales`, `recruiter`, `marketing`, `frontend`, `react`, `graphic designer`.

### 2. Skill Overlap & 5 DevOps Pillars (Max 35 pts)
Evaluates candidates across the 5 core pillars of modern DevOps:
* **AWS** (`aws`, `amazon web services`): 5 pts
* **Kubernetes** (`kubernetes`, `k8s`): 5 pts
* **Docker** (`docker`, `containers`): 5 pts
* **Terraform / IaC** (`terraform`, `opentofu`, `tofu`, `iac`): 5 pts
* **CI/CD** (`ci/cd`, `github actions`, `jenkins`, `gitlab ci`): 5 pts
* **Secondary Skills** (+2 pts each, up to 10 pts): Linux, Python, Bash, Prometheus, Grafana, Git, Infrastructure Automation.

### 3. Seniority & Experience Fit (Max 15 pts or Hard Disqualification)
* **Target Fit (0–3 Years)** (+15 pts): Explicit match for `fresher`, `intern`, `junior`, `associate`, `trainee`, `0-1 year`, `1-2 years`, `0-3 years`.
* **Unspecified Experience** (0 pts): Scored neutrally as `Unknown`. The engine **never falsely assumes 0-3 years** when evidence is absent.
* **Immediate Disqualification** (-100 pts / Score = 0): Any role with `senior`, `sr`, `lead`, `principal`, `staff`, `architect`, `director`, `manager`, or `>3 years` experience is disqualified to protect early-career candidates from misrepresentation.

### 4. Location & Work Arrangement (Max 15 pts)
* **Primary Target (Pune, Maharashtra)**: +15 pts
* **Secondary Target (Remote across India / Worldwide)**: +12 pts
* **Foreign Remote Exclusion**: Any listing restricted to foreign residencies (e.g., `US only`, `must reside in US`, `UK only`, `EMEA only`) is immediately excluded when filtering for Pune & Remote India.

### 5. Shift & Schedule Preference (+2 pts bonus)
* Day shift preferred. Rotational / night shifts are flagged in potential gaps for informed decision-making.

### 6. Recency Prioritization (Max 15 pts)
* Posted within 24 hours: **+15 pts**
* Posted within 3 days: **+10 pts**
* Posted within 7 days: **+5 pts**
* Future timestamps are strictly rejected.

### Priority Tiers:
* 🟢 **High Priority**: Score ≥ 75.0 (Immediate attention)
* 🟡 **Medium Priority**: 50.0 ≤ Score < 75.0 (Strong potential)
* ⚪ **Low Priority**: Score < 50.0 (Backlog or weak fit)

---

## 🗄️ Deduplication & Database Persistence

### Global URL Deduplication
1. **URL Normalization**:
   - Lowercases scheme and netloc; strips standard default ports (`:80`, `:443`).
   - Strips 20+ tracking parameters: `utm_source`, `utm_medium`, `utm_campaign`, `utm_term`, `utm_content`, `trk`, `trackingid`, `refid`, `position`, `eid`, `origin`, `ref`, `gclid`, `fbclid`, `mc_cid`, etc.
   - Strips trailing slashes and fragment identifiers (`#...`).
2. **Secondary Company + Title Hash**:
   - Computes deterministic SHA-256 hash on normalized company name (stripping corporate suffixes like `Pvt Ltd`, `Inc`, `LLC`) and role title.

### Database Schema (`jobs.db`)
SQLite database with two primary tables and a relational join table:

* **`jobs` Table (28 Columns)**:
  `id`, `normalized_url`, `dedup_hash`, `job_title`, `company`, `location`, `work_arrangement`, `application_url`, `source_url`, `source_name`, `posting_date`, `date_confidence`, `experience_required`, `experience_evidence`, `relevant_skills`, `match_reasons`, `potential_gaps`, `match_score`, `priority`, `first_seen_date`, `last_seen_date`, `status`, `notes`, `raw_snippet`, `liveness_status`, `posting_date_evidence`, `canonical_application_url`, `last_checked_date`.

* **`recruiters` Table (29 Columns)**:
  `id`, `full_name`, `linkedin_url`, `normalized_url`, `dedup_hash`, `current_company`, `recruiter_type`, `technical_specialisms`, `location`, `remote_coverage`, `related_job_title`, `related_job_id`, `hiring_evidence_url`, `evidence_summary`, `evidence_date`, `first_discovered_date`, `last_verified_date`, `contact_details`, `contact_status`, `last_contacted_date`, `follow_up_date`, `response_notes`, `verification_status`, `confidence_score`, `match_score`, `priority`, `match_reasons`, `outreach_templates`, `raw_snippet`.

* **`recruiter_jobs` Table**:
  Links identified recruiters directly to specific job vacancies (`recruiter_id`, `job_id`, `relationship_type`, `created_at`).

---

## 📊 Excel & CSV Report Structure

### 1. Dedicated Excel Workbook (`output/jobs_YYYY-MM-DD_HH-MM.xlsx`)
Discovery runs generate a clean, formatted `.xlsx` spreadsheet in the `output/` folder:

| Column | Header | Type | Description |
| :---: | :--- | :---: | :--- |
| **A** | `#` | Integer | Sequential item number |
| **B** | `Company` | Text | Employer name (blank if unverified) |
| **C** | `Job Location` | Text | Listed office location or Remote status |
| **D** | `Date Posted` | Date (YYYY-MM-DD) | Verified posting date (blank if unverified) |
| **E** | `Job Role` | Text | Official source role title |
| **F** | `Careers Page` | Hyperlink | Direct application link (clickable blue link) |
| **G** | `LinkedIn / Company URL` | Hyperlink | Official company career page or LinkedIn link |

#### Excel Formatting Highlights:
* **Header Row**: Dark blue fill (`#1F4E79`) with bold white text and centered alignment.
* **Freeze Panes**: Row 1 is frozen (`A2`) so headers stay visible during scrolling.
* **Auto-Filter**: Enabled across columns `A1:G...` for easy sorting in Excel.
* **Dynamic Widths**: Columns automatically adjust to content with a maximum cap to prevent runaway URL widths.
* **Date Sorting**: Jobs with verified or updated publication dates are sorted **newest first**.

### 2. CSV Exports
* `devops_jobs_ranked.csv`: Strictly verified employer dates meeting the export score threshold.
* `devops_jobs_unverified_leads.csv`: Full discovery capture, including feed index updates.
* `devops_recruiters.csv`: Technical recruiters, hiring managers, and outreach statuses.
* All CSVs use **`utf-8-sig` (UTF-8 with BOM)** for native opening in Microsoft Excel without character corruption.

---

## 🤝 Recruiter Discovery & Outreach Assistant

The recruiter subsystem provides a targeted pipeline for connecting with talent acquisition professionals and hiring managers:

### 1. Discovery Channels
* **Job Posting Extractor**: Scans verified job listings in `jobs.db` to extract named recruiters, talent acquisition partners, and hiring manager contact points.
* **Public Hiring Announcements**: Indexes public LinkedIn posts and recruiter announcements from public search feeds without requiring login credentials.

### 2. Personalized Message Generation (`--generate-message <ID>`)
The assistant generates three distinct outreach templates for any tracked recruiter:
1. **LinkedIn Connection Request Note**: Guaranteed **≤ 300 characters** (strictly adhering to LinkedIn's limit).
2. **InMail / Direct Message**: Structured 100–180 word message highlighting relevant projects, Amazon support background, and target alignment.
3. **Follow-Up Message**: Polite check-in template designed for 5–7 days after initial outreach.

> [!IMPORTANT]
> **Outreach Ethics Policy**: Messages are **never sent automatically**. The user reviews, personalizes, and manually dispatches all outreach through their own profile.

---

## ⚠️ Verification Limitations & Ethics

To ensure responsible and reliable operation, please review these architectural limitations:

1. **Job Posting Dates**:
   - **Direct ATS (Lever, Ashby, SmartRecruiters, Workday)**: Provide true creation timestamps (`Verified`).
   - **Greenhouse**: Exposes `updated_at`, which may reflect board updates or minor edits rather than original publication (`Listing Updated`).
   - **RSS / Search Feeds**: Timestamps indicate when a search engine crawler indexed the page, not when the employer published it (`Estimated (Feed Indexed Date)`).
   - If an employer does not provide a timestamp, the engine marks it `Unknown` and assigns no recency bonus.

2. **Application URLs & Anti-Bot Protections**:
   - Platforms like LinkedIn, Naukri, and Indeed actively block automated crawlers using Cloudflare, CAPTCHAs, and login gates.
   - The engine **never attempts to bypass CAPTCHAs, hijack sessions, or scrape authenticated areas**.
   - Instead, pre-filtered direct search links are generated in `job_search_links.md` and `recruiter_search_links.md` for fast manual review.

3. **Listing Availability (Liveness)**:
   - Job listings can be closed by employers before external feeds reflect the change.
   - The `--revalidate-urls` command inspects HTTP response codes and searches for closed markers (`no longer accepting applications`, `position has been filled`, etc.). However, client-rendered Single Page Applications (SPAs) may return HTTP 200 without rendering status text in static HTML.

---

## ⏰ Windows Task Scheduler Automation

Run the scanner automatically every morning (e.g. 8:30 AM) on your Windows PC:

### Option A: PowerShell Scheduled Task Registration
Open PowerShell and run (adjust the working directory if necessary):

```powershell
$workDir = (Get-Item .).FullName
$action = New-ScheduledTaskAction -Execute "python.exe" -Argument "main.py --max-days 1 --pune-remote" -WorkingDirectory $workDir
$trigger = New-ScheduledTaskTrigger -Daily -At "08:30AM"
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable
Register-ScheduledTask -TaskName "DevOpsJobHunterDaily" -Action $action -Trigger $trigger -Settings $settings -Description "Daily morning DevOps & Cloud job hunt scanner"
```

### Option B: Windows Task Scheduler GUI
1. Press `Win + R`, type `taskschd.msc`, and press **Enter**.
2. Click **Create Basic Task...** in the right sidebar.
3. Set Trigger to **Daily** at **08:30 AM**.
4. Action: **Start a program**.
   * **Program/script**: `python`
   * **Add arguments**: `main.py --max-days 1 --pune-remote`
   * **Start in**: *(Your project root directory)*
5. Click **Finish**.

---

## ⚙️ Configuration Guide

All search parameters, thresholds, and target skills are customizable in `config.json`:

```json
{
  "candidate_profile": {
    "target_roles": [
      "Junior DevOps Engineer",
      "Cloud Support Engineer",
      "Cloud Administrator",
      "Infrastructure Engineer",
      "Platform Engineer"
    ],
    "target_locations_primary": ["Pune", "Pune, Maharashtra"],
    "target_locations_secondary": ["Remote", "India", "Worldwide"],
    "core_skills": [
      "AWS", "Linux", "Docker", "Kubernetes", "Terraform",
      "OpenTofu", "Git", "GitHub Actions", "Jenkins", "CI/CD",
      "Python", "Bash", "Prometheus", "Grafana"
    ],
    "experience_profile": {
      "target_years_min": 0,
      "target_years_max": 3,
      "positive_keywords": ["junior", "fresher", "intern", "associate", "0-2 years", "1-3 years"],
      "penalty_keywords": ["senior", "lead", "architect", "principal", "5+ years"]
    }
  },
  "filters": {
    "max_posting_age_days": 1,
    "pune_or_remote_india_only": true,
    "strict_verified_only": false,
    "max_experience_years": 3,
    "exclude_senior_roles": true
  },
  "settings": {
    "database_file": "jobs.db",
    "export_csv_file": "devops_jobs_ranked.csv",
    "export_leads_csv_file": "devops_jobs_unverified_leads.csv",
    "export_recruiters_csv_file": "devops_recruiters.csv",
    "min_export_score": 40.0,
    "request_timeout_seconds": 12
  }
}
```

---

## 🗺️ Realistic Roadmap

### Phase 1: Near-Term Enhancements
- [ ] **Automated ATS Target Onboarding**: Scripted import of verified boards identified by `scratch_probe_ats.py` into `company_targets.json`.
- [ ] **Additional Workday Tenants**: Expand configured CXS endpoints for additional major employers in Pune (e.g., Barclays, Amdocs, Whirlpool).
- [ ] **Instahyre Structured Adapter**: Formalize `scratch_instahyre.py` DOM extraction into a standardized connector.

### Phase 2: Additional ATS Platform Connectors
- [ ] **Workable Public API**: Integration for tech startups utilizing Workable job widgets.
- [ ] **BambooHR / Recruitee API**: Public job list connectors for boutique tech companies.
- [ ] **Pinpoint / Teamtailor Public Endpoints**: Expanded coverage for remote-first software companies.

### Phase 3: Notifications & Insights
- [ ] **Instant Match Alerts**: Optional webhook dispatch (Discord / Telegram / Windows Toast) when a job scores ≥ 75 points.
- [ ] **Skill Demand Insights**: Aggregated analytics on trending keywords across junior cloud roles in Pune.

---

## 🔒 Privacy & Safety

* **No Credentials Stored**: The application requires no login accounts, passwords, or personal access tokens.
* **Local Data Sovereignty**: All job records, recruiter contacts, and outreach notes are stored strictly on your local computer in `jobs.db`.
* **Zero Unauthorized Actions**: The tool performs read-only public queries. It will never submit job applications or transmit automated messages on your behalf.
