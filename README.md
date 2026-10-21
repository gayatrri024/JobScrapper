# 🎯 DevOps & Cloud Job Hunter (Pune & India Remote)

A complete, free, local job-search automation engine designed specifically for early-career DevOps, Cloud, and Infrastructure engineers (0–3 years experience, operations/support background, internship experience).

Runs **100% locally on your Windows PC using standard Python**. 
* **Zero paid APIs** (no OpenAI, no Claude API, no Apify actors, no paid subscriptions).
* **Zero credential scraping / anti-bot bypass** (uses legitimate public RSS feeds, Google News search queries, and public career board APIs).
* **Local SQLite persistence** with automatic URL and company/title deduplication.
* **Excel-friendly ranked CSV export** with UTF-8 BOM encoding.
* **Automated daily execution** via Windows Task Scheduler.

---

## 📋 Features & Design

| Feature | Implementation Details |
| :--- | :--- |
| **Target Roles** | DevOps Engineer, Junior DevOps Engineer, Cloud Engineer, Cloud Administrator, Kubernetes Engineer, Infrastructure Engineer, Junior SRE, Platform Engineer, Cloud Support Engineer. |
| **Location Strategy** | **Pune, India first** (+15 pts); **Remote roles across India** (+12 pts); secondary India locations (+5 pts); non-India onsite penalized (-25 pts). |
| **Seniority Alignment** | Tailored for 0–3 years (Amazon operations/support + DevOps internship). **Penalizes senior/lead/architect roles heavily (-35 to -40 pts)**. Never misrepresents you as a 3+ year senior engineer. |
| **Skill Matching** | AWS, Linux, Docker, Kubernetes, Terraform, OpenTofu, Git, GitHub Actions, Jenkins, CI/CD, Prometheus, Grafana, Python, Bash, Infrastructure Automation. |
| **Recency Prioritization** | Prioritizes postings within the last 24 hours (+5 pts), then within 3 days (+3 pts), then within 7 days (+1 pt). |
| **Date Confidence** | Strictly distinguishes verified employer post dates (`Verified`) from search engine indexing times (`Estimated (Feed Indexed Date)`). Never fabricates dates or employers. |
| **Deduplication** | Normalizes URLs (strips `utm_*`, `trk`, `trackingId`, `refId`, etc.) and computes deterministic `company + title` hashes. Preserves application status and notes across runs. |

---

## 🚀 Quick Start (Windows PowerShell)

Open PowerShell and navigate to the project directory:

```powershell
cd C:\Users\NISHTHA\Documents\DevOps-Learning\devops-job-hunter
```

### 1. Verify Prerequisites
The core system runs exclusively on Python's built-in standard library. No pip installations are mandatory.
To verify your Python environment:

```powershell
python --version
```
*(Requires Python 3.10+; Python 3.11+ is already available on your machine)*

### 2. Run the Job Hunter
Run discovery, scoring, database upsert, and ranked CSV export in one command:

```powershell
python main.py
```

### 3. Review the Output
1. **Console Summary**: Displays source health table, discovery count, and database stats.
2. **SQLite Database**: Saved to `jobs.db` (preserves all records permanently).
3. **Ranked CSV**: Saved to `devops_jobs_ranked.csv`. Double-click to open directly in **Microsoft Excel**.

---

## 🛠️ CLI Usage & Workflow Commands

### Check Current Statistics
View total jobs tracked, opportunities discovered today, priority breakdown, and top high-priority matches:
```powershell
python main.py --stats
```

### Test Discovery Without Modifying Database (Dry Run)
Simulates fetching and scoring without writing to SQLite:
```powershell
python main.py --dry-run
```

### Re-Export CSV with Custom Score Threshold
Export only jobs scoring 60 or higher:
```powershell
python main.py --export-only --min-score 60
```

### Track Application Status & Notes
Update your job hunt progress in the SQLite database:
```powershell
# Mark job #101 as Applied with custom notes:
python main.py --set-status 101 Applied --notes "Applied on company portal with AWS/Docker tailored resume."

# Mark job #104 as Archived (e.g. skipped or closed):
python main.py --set-status 104 Archived
```
*Note: Available statuses: `New`, `Applied`, `Reviewed`, `Archived`, `Interviewing`.*

### Run the Test Suite
Run the 12 automated verification tests for scoring, deduplication, state preservation, and missing field handling:
```powershell
python test_system.py
```
*(Or with pytest: `pytest test_system.py -v`)*

---

## ⏰ Windows Task Scheduler Setup (Daily Morning Run)

You can set up Windows to run the job search automatically every morning (e.g., at 8:30 AM), even when Antigravity and your browser are closed.

### Option A: One-Liner Setup via PowerShell (Recommended)
Open PowerShell as your current user and run:

```powershell
$action = New-ScheduledTaskAction -Execute "python.exe" -Argument "main.py" -WorkingDirectory "C:\Users\NISHTHA\Documents\DevOps-Learning\devops-job-hunter"
$trigger = New-ScheduledTaskTrigger -Daily -At "08:30AM"
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable
Register-ScheduledTask -TaskName "DevOpsJobHunterDaily" -Action $action -Trigger $trigger -Settings $settings -Description "Daily morning DevOps & Cloud job hunt scanner"
```

### Option B: Using `schtasks` (Command Prompt / PowerShell)
```powershell
schtasks /create /tn "DevOpsJobHunterDaily" /tr "cmd.exe /c cd /d C:\Users\NISHTHA\Documents\DevOps-Learning\devops-job-hunter && python main.py" /sc daily /st 08:30
```

### Option C: GUI Setup (Windows Task Scheduler)
1. Press `Win + R`, type `taskschd.msc`, and press **Enter**.
2. Click **Create Basic Task...** in the right sidebar.
3. **Name**: `DevOps Job Hunter Daily`.
4. **Trigger**: Select **Daily**, set time to `08:30:00 AM`.
5. **Action**: Select **Start a program**.
   * **Program/script**: `python`
   * **Add arguments**: `main.py`
   * **Start in**: `C:\Users\NISHTHA\Documents\DevOps-Learning\devops-job-hunter`
6. Click **Finish**.

Every morning, the system will wake up, query the feeds, update `jobs.db`, and regenerate your ranked `devops_jobs_ranked.csv`.

---

## ⚙️ Configuration (`config.json`)

You can customize target roles, locations, skills, and sources anytime in `config.json`:

```json
{
  "candidate_profile": {
    "target_roles": [
      "DevOps Engineer",
      "Junior DevOps Engineer",
      "Cloud Engineer",
      "Cloud Administrator",
      "Kubernetes Engineer",
      "Infrastructure Engineer",
      "Junior Site Reliability Engineer",
      "Platform Engineer",
      "Cloud Support Engineer"
    ],
    "target_locations_primary": ["Pune", "Pune, Maharashtra", "Pune, India"],
    "target_locations_secondary": ["Remote", "India", "Work from Home", "Worldwide"],
    "core_skills": [
      "AWS", "Linux", "Docker", "Kubernetes", "Terraform", "OpenTofu",
      "Git", "GitHub Actions", "Jenkins", "CI/CD", "Prometheus", "Grafana",
      "Python", "Bash", "infrastructure automation"
    ]
  }
}
```

---

## 📊 Scoring Breakdown & Logic

Total Match Score is calculated from **0 to 100 points**:

```
Total Score = clamp(Role Match + Skill Overlap + Seniority Fit + Location Fit + Recency Bonus, 0, 100)
```

1. **Role Alignment (up to 30 pts)**:
   * Target role match (Junior DevOps, Cloud Support, Cloud Engineer, etc.): **+25 to +30 pts**
   * Related domain match (SRE, Infrastructure, Kubernetes): **+18 pts**
   * Unrelated role (Frontend, Sales, Recruiter): **-15 pts**
2. **Skill Overlap (up to 35 pts)**:
   * ~2.5 pts per matched candidate skill (AWS, Docker, K8s, Linux, Terraform, etc.).
3. **Seniority & Experience Fit (up to 15 pts or heavy penalty)**:
   * 0–3 years / Junior / Entry / Intern: **+15 pts**
   * Senior / Lead / Principal / Architect / >3 years: **-35 to -40 pts penalty** (protects you from misrepresentation).
4. **Location & Work Arrangement (up to 15 pts)**:
   * Pune, India: **+15 pts** (Primary target)
   * Remote India / Worldwide: **+12 pts** (Secondary target)
   * Non-India Onsite (e.g. US/UK): **-25 pts penalty**
5. **Recency (up to 5 pts)**:
   * Posted within 24 hours: **+5 pts**
   * Posted within 3 days: **+3 pts**
   * Posted within 7 days: **+1 pt**

### Priority Tiers:
* **High Priority**: Score ≥ 75
* **Medium Priority**: 50 ≤ Score < 75
* **Low Priority**: Score < 50

---

## 🛡️ Reliability, Ethics, and Access Control

1. **Zero Access Bypass**: The system never attempts to bypass CAPTCHAs, paywalls, or authenticated pages.
2. **Graceful Error Handling**: If a source encounters a network failure or 403 access restriction, it is logged and presented in the `Source Health Audit` table without interrupting other sources.
3. **Data Integrity**: Unknown dates, salaries, or employers are marked as `Unknown`. Feed index times are explicitly labeled as `Estimated (Feed Indexed Date)`.
4. **Safety**: Discovery and ranking only. Does NOT automatically message recruiters, apply to listings, or transmit personal details.

---

## 🚀 ATS Platform Connectors & Employer Career Portals

The engine queries verified public Applicant Tracking System (ATS) endpoints directly without requiring third-party aggregators:

| ATS Platform | Endpoint Type | Authentication | Date Confidence | Supported Target Employers |
| :--- | :--- | :--- | :--- | :--- |
| **Workday** | CXS Public REST API (`/wday/cxs/.../jobs`) | None (Public) | **Verified** (`postedOn`: Today, Yesterday, X Days Ago) | Salesforce, Mastercard |
| **Ashby** | Public REST API (`/api/non-auth/.../postings`) | None (Public) | **Verified** (`publishedAt` ISO-8601) | Snowflake, Notion, Linear, Ramp, Vanta, Cursor, Airbyte |
| **Greenhouse** | Public Boards API (`/v1/boards/.../jobs`) | None (Public) | **Listing Updated** (`updated_at` ISO-8601) | Druva, Zscaler, LogicMonitor, SolarWinds, Rubrik, GitLab, Elastic, PureStorage, PagerDuty, JFrog, Canonical |
| **Lever** | Public Postings API (`/v0/postings/...`) | None (Public) | **Verified** (`createdAt` timestamp) | Toptal, Sonatype, Palantir, Spotify |
| **SmartRecruiters**| Public API (`/v1/companies/.../postings`) | None (Public) | **Verified** (`releasedDate` ISO-8601) | Colliers and verified enterprise tenants |
| **We Work Remotely**| Category RSS Feed | None (Public) | **Verified** (`pubDate` RFC-822) | Global Remote tech listings |
| **Jobicy / Remotive**| Public Developer API | None (Public) | **Verified** (`pubDate` / `publication_date`) | Verified remote engineering listings |
| **Google News RSS** | Topic Query Feed | None (Public) | **Estimated (Feed Indexed Date)** | Supplementary news index (routed to `unverified_leads.csv`) |

### Company Target Registry (`company_targets.json`)

The target employer roster is maintained in `company_targets.json` covering 171 target enterprises (cloud leaders, enterprise IT, product MNCs, Indian tech unicorns, and Pune GCCs). You can add, edit, or adjust company endpoints without modifying Python source code.

---

## 🛠️ CLI Commands & Verification Workflows

### 1. Fresh Live Discovery (Strict Pune / Remote India)
```bash
python main.py --max-days 1 --pune-remote
```

### 2. Fallback Discovery (Last 7 Days)
```bash
python main.py --max-days 7 --pune-remote
```

### 3. Diagnostic Funnel (Explains All Filters & Stored DB Records)
```bash
python main.py --diagnostic --max-days 7 --pune-remote
```

### 4. Database Statistics & Current Pipeline Summary
```bash
python main.py --stats
```

### 5. Dry-Run Scan (Evaluates Live Web Without Persisting to SQLite)
```bash
python main.py --dry-run --diagnostic --max-days 7 --pune-remote
```

### 6. Active URL Liveness Revalidation
Re-resolves stored vacancies in `jobs.db`, inspects body content for closed vacancy markers, and flags redirects:
```bash
python main.py --revalidate-urls 50
```

### 7. Offline Export to Ranked CSV
```bash
python main.py --export-only --max-days 7 --pune-remote
```

### 8. Recruiter Discovery & Outreach Assistant
```bash
# Discover technical recruiters and hiring leads
python main.py --discover-recruiters

# List ranked recruiters
python main.py --list-recruiters

# Generate personalized LinkedIn Connection Note (<300 chars) & InMail
python main.py --generate-message <RECRUITER_ID>
```

---

## ⏰ Windows Task Scheduler Automation (Daily Scan)

To automate the daily 24-hour job scan dynamically via Windows Task Scheduler, create a scheduled task using the following command template:

* **Program / script**: `python` (or full path to your virtualenv python executable)
* **Arguments**: `main.py --max-days 1 --pune-remote`
* **Start in**: `C:\Users\NISHTHA\Documents\DevOps-Learning\devops-job-hunter`

*(Note: Review and test the system locally before enabling automated scheduling).*

---

## 📗 Dedicated Excel Workbook Export (`output/jobs_YYYY-MM-DD_HH-MM.xlsx`)

Every discovery run automatically generates a professional, deduplicated Excel workbook (`.xlsx`) in the dedicated `output/` directory:

```
output/jobs_YYYY-MM-DD_HH-MM.xlsx
```

### 1. Required Columns in Exact Order:
| # | Company | Job Location | Date Posted | Job Role | Careers Page | LinkedIn / Company URL |
| :-: | :--- | :--- | :-: | :--- | :--- | :--- |
| Sequential ID | Employer Name | Listed Location | Actual Posting Date | Source Job Title | Direct Vacancy Link | Official Company / LinkedIn URL |

### 2. Professional Excel Formatting:
- **Bold, Styled Header Row**: Professional dark blue fill (`#1F4E79`), white bold text, centered.
- **Freeze Panes**: Header row frozen (`A2`) so column headers remain visible while scrolling.
- **Auto-Filter**: Enabled across all columns (`A1:G...`) for sorting and filtering directly in Excel.
- **Clickable Hyperlinks**: Careers Page and Company URLs are formatted as active, clickable links with single underline.
- **Text Wrapping & Dynamic Widths**: Columns are automatically padded to avoid truncated text.
- **Date Sorting**: Jobs with verified or updated publication dates are sorted **newest first**.
- **Data Integrity**: Missing fields are left blank rather than filled with placeholder text.

### 3. Global URL Deduplication:
- Normalizes application URLs across all queries and sources by stripping fragments (`#...`) and tracking parameters (`utm_*`, `trk`, `trackingid`).
- Merges duplicates into a single record while preserving distinct jobs with missing URLs.

### 4. CLI Output Formatting:
* **Default Excel Mode** (Concise terminal summary):
  ```bash
  python main.py --max-days 1 --pune-remote
  # Generates: output/jobs_YYYY-MM-DD_HH-MM.xlsx
  ```
* **Terminal Display Mode** (Prints full vacancy detail cards to stdout):
  ```bash
  python main.py --output-format terminal --max-days 7 --pune-remote
  ```
* **Offline Export from SQLite to Excel**:
  ```bash
  python main.py --export-only --output-format excel --max-days 7 --pune-remote
  ```
