"""excel_export.py - Professional Excel report generator for DevOps Job Hunter.

Exports deduplicated, verified jobs to Excel (.xlsx) using openpyxl with:
- Dedicated output folder: output/jobs_YYYY-MM-DD_HH-MM.xlsx
- Exact columns: #, Company, Job Location, Date Posted, Job Role, Careers Page, LinkedIn / Company URL
- Clickable hyperlinks on Careers Page and LinkedIn / Company URL
- Bold, professional styled header row with freeze panes and auto-filters
- Clean sortable table with auto-adjusted column widths and text wrapping
- Sorted by posting date (newest first) for reliable dates
- Global deduplication based on normalized application URLs
"""

import json
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

try:
    import openpyxl
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    from openpyxl.utils import get_column_letter
    HAS_OPENPYXL = True
except ImportError:
    HAS_OPENPYXL = False

from database import normalize_url

logger = logging.getLogger("devops_job_hunter.excel_export")

EXCEL_COLUMNS = [
    "#",
    "Company",
    "Job Location",
    "Date Posted",
    "Job Role",
    "Careers Page",
    "LinkedIn / Company URL"
]


def load_company_targets_map(config_path: Optional[Union[str, Path]] = None) -> Dict[str, Dict[str, Any]]:
    """Loads company_targets.json into a lookup dictionary keyed by lowercase name & aliases."""
    if config_path is None:
        config_path = Path(__file__).resolve().parent / "company_targets.json"
    else:
        config_path = Path(config_path)

    targets_map: Dict[str, Dict[str, Any]] = {}
    if not config_path.exists():
        return targets_map

    try:
        with open(config_path, "r", encoding="utf-8") as f:
            data = json.load(f)
            companies = data.get("companies", [])
            for c in companies:
                name = (c.get("name") or "").strip().lower()
                if name:
                    targets_map[name] = c
                for alias in c.get("aliases", []):
                    a_low = alias.strip().lower()
                    if a_low:
                        targets_map[a_low] = c
    except Exception as e:
        logger.warning(f"Could not load company_targets.json for Excel enrichment: {e}")

    return targets_map


def deduplicate_jobs(jobs: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], int]:
    """
    Deduplicates a list of job dictionaries across all sources and queries.
    - Normalizes URLs (strips tracking params, fragments, trailing slashes).
    - Keeps one record per unique URL.
    - If URL is missing, does not merge unrelated jobs (only merges if company & title match).
    """
    seen_urls = set()
    seen_company_titles = set()
    deduped: List[Dict[str, Any]] = []
    dupes_removed = 0

    for j in jobs:
        url = ((j.get("canonical_application_url") or j.get("application_url") or "")).strip()

        if url.lower().startswith(("http://", "https://")):
            norm = normalize_url(url)
            if norm in seen_urls:
                dupes_removed += 1
                continue
            seen_urls.add(norm)
            deduped.append(j)
        else:
            comp = (j.get("company") or "").strip().lower()
            title = (j.get("job_title") or "").strip().lower()
            if comp and title:
                key = (comp, title)
                if key in seen_company_titles:
                    dupes_removed += 1
                    continue
                seen_company_titles.add(key)
                deduped.append(j)
            else:
                deduped.append(j)

    return deduped, dupes_removed


def parse_posting_datetime(job: Dict[str, Any]) -> Optional[datetime]:
    """Extracts timezone-aware datetime from job posting_date if available."""
    pdate = (job.get("posting_date") or "").strip()
    if not pdate or pdate.lower() in ["unknown", "unverified"]:
        return None

    try:
        if "t" in pdate.lower():
            clean = pdate.replace("Z", "+00:00")
            return datetime.fromisoformat(clean)
        elif len(pdate) >= 10:
            return datetime.strptime(pdate[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except Exception:
        pass
    return None


def sort_jobs_by_date(jobs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Sorts jobs by posting date, newest first, when reliable dates are available."""
    def _sort_key(j: Dict[str, Any]) -> Tuple[int, float]:
        dt = parse_posting_datetime(j)
        if dt is not None:
            return (1, dt.timestamp())
        score = j.get("match_score", 0.0) or 0.0
        return (0, float(score))

    return sorted(jobs, key=_sort_key, reverse=True)


def resolve_company_or_linkedin_url(job: Dict[str, Any], targets_map: Dict[str, Dict[str, Any]]) -> str:
    """Resolves a relevant LinkedIn URL or official company career URL without fabricating."""
    # 1. Direct fields on the job dictionary
    for field in ["linkedin_url", "company_url", "official_company_url", "company_careers_url"]:
        val = (job.get(field) or "").strip()
        if val.lower().startswith(("http://", "https://")):
            return val

    # 2. Check if source_url is a LinkedIn link
    src_url = (job.get("source_url") or "").strip()
    if "linkedin.com" in src_url.lower():
        return src_url

    # 3. Match against target company registry
    comp = (job.get("company") or "").strip().lower()
    if comp and comp in targets_map:
        target = targets_map[comp]
        c_url = (target.get("career_url") or target.get("website") or "").strip()
        if c_url.lower().startswith(("http://", "https://")):
            return c_url

    return ""


class ExcelJobExporter:
    """Handles formatting and exporting deduplicated jobs to Excel (.xlsx)."""

    def __init__(self, output_dir: Optional[Union[str, Path]] = None, company_targets_file: Optional[Union[str, Path]] = None):
        if output_dir is None:
            self.output_dir = Path(__file__).resolve().parent / "output"
        else:
            self.output_dir = Path(output_dir)

        self.company_targets = load_company_targets_map(company_targets_file)

    def generate_filepath(self) -> Path:
        """Generates a timestamped file path: output/jobs_YYYY-MM-DD_HH-MM.xlsx."""
        self.output_dir.mkdir(parents=True, exist_ok=True)
        now = datetime.now()
        base_name = f"jobs_{now.strftime('%Y-%m-%d_%H-%M')}"
        target = self.output_dir / f"{base_name}.xlsx"

        counter = 1
        while target.exists():
            target = self.output_dir / f"{base_name}_{counter}.xlsx"
            counter += 1

        return target

    def export_jobs(
        self,
        jobs: List[Dict[str, Any]],
        output_filepath: Optional[Union[str, Path]] = None,
        deduplicate: bool = True
    ) -> Tuple[Path, int, int]:
        """
        Exports the given jobs to a styled Excel workbook.
        Returns: (saved_filepath, jobs_exported_count, duplicates_removed_count)
        """
        if not HAS_OPENPYXL:
            raise RuntimeError("openpyxl is not installed. Please install openpyxl to export to Excel.")

        dupes_removed = 0
        if deduplicate:
            jobs, dupes_removed = deduplicate_jobs(jobs)

        sorted_jobs = sort_jobs_by_date(jobs)

        if output_filepath is not None:
            target_path = Path(output_filepath)
            target_path.parent.mkdir(parents=True, exist_ok=True)
        else:
            target_path = self.generate_filepath()

        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "DevOps & Cloud Jobs"

        # Styling constants
        header_fill = PatternFill(start_color="1F4E79", end_color="1F4E79", fill_type="solid")
        header_font = Font(name="Calibri", size=11, bold=True, color="FFFFFF")
        header_align = Alignment(horizontal="center", vertical="center", wrap_text=True)

        alt_row_fill = PatternFill(start_color="F8FAFC", end_color="F8FAFC", fill_type="solid")
        thin_border = Border(
            left=Side(style="thin", color="E2E8F0"),
            right=Side(style="thin", color="E2E8F0"),
            top=Side(style="thin", color="E2E8F0"),
            bottom=Side(style="thin", color="E2E8F0")
        )
        link_font = Font(name="Calibri", size=10, color="0563C1", underline="single")
        regular_font = Font(name="Calibri", size=10)

        # 1. Write Header Row
        ws.append(EXCEL_COLUMNS)
        ws.row_dimensions[1].height = 28

        for col_idx in range(1, len(EXCEL_COLUMNS) + 1):
            cell = ws.cell(row=1, column=col_idx)
            cell.fill = header_fill
            cell.font = header_font
            cell.alignment = header_align
            cell.border = thin_border

        # 2. Write Job Rows
        for idx, j in enumerate(sorted_jobs, 1):
            row_idx = idx + 1
            ws.row_dimensions[row_idx].height = 22

            company_val = (j.get("company") or "").strip()
            if company_val.lower() in ["unknown", "none"]:
                company_val = ""

            location_val = (j.get("location") or "").strip()
            if location_val.lower() in ["unknown", "none"]:
                location_val = ""

            # Date Posted: actual date when available, never invent
            dt = parse_posting_datetime(j)
            date_val = ""
            if dt is not None and j.get("date_confidence") != "Unknown":
                date_val = dt.strftime("%Y-%m-%d")

            role_val = (j.get("job_title") or "").strip()

            careers_url = ((j.get("canonical_application_url") or j.get("application_url") or "")).strip()
            company_url = resolve_company_or_linkedin_url(j, self.company_targets)

            is_even = (idx % 2 == 0)

            # Cell 1: #
            c1 = ws.cell(row=row_idx, column=1, value=idx)
            c1.alignment = Alignment(horizontal="center", vertical="center")

            # Cell 2: Company
            c2 = ws.cell(row=row_idx, column=2, value=company_val)
            c2.alignment = Alignment(horizontal="left", vertical="center")

            # Cell 3: Job Location
            c3 = ws.cell(row=row_idx, column=3, value=location_val)
            c3.alignment = Alignment(horizontal="left", vertical="center")

            # Cell 4: Date Posted
            c4 = ws.cell(row=row_idx, column=4, value=date_val)
            c4.alignment = Alignment(horizontal="center", vertical="center")

            # Cell 5: Job Role
            c5 = ws.cell(row=row_idx, column=5, value=role_val)
            c5.alignment = Alignment(horizontal="left", vertical="center", wrap_text=True)

            # Cell 6: Careers Page (Clickable Hyperlink)
            c6 = ws.cell(row=row_idx, column=6)
            if careers_url.lower().startswith(("http://", "https://")):
                c6.value = careers_url
                c6.hyperlink = careers_url
                c6.font = link_font
            else:
                c6.value = ""
                c6.font = regular_font
            c6.alignment = Alignment(horizontal="left", vertical="center")

            # Cell 7: LinkedIn / Company URL (Clickable Hyperlink)
            c7 = ws.cell(row=row_idx, column=7)
            if company_url.lower().startswith(("http://", "https://")):
                c7.value = company_url
                c7.hyperlink = company_url
                c7.font = link_font
            else:
                c7.value = ""
                c7.font = regular_font
            c7.alignment = Alignment(horizontal="left", vertical="center")

            # Apply font, borders and alternating row fill
            for c in [c1, c2, c3, c4, c5]:
                c.font = regular_font

            for c in [c1, c2, c3, c4, c5, c6, c7]:
                c.border = thin_border
                if is_even:
                    c.fill = alt_row_fill

        # 3. Freeze Header Row and Enable Filters
        ws.freeze_panes = "A2"
        max_row = max(ws.max_row, 1)
        ws.auto_filter.ref = f"A1:G{max_row}"

        # 4. Adjust Column Widths for Readability
        col_width_defaults = {
            "A": 7,    # #
            "B": 24,   # Company
            "C": 26,   # Job Location
            "D": 16,   # Date Posted
            "E": 36,   # Job Role
            "F": 46,   # Careers Page
            "G": 38    # LinkedIn / Company URL
        }

        for col_letter, default_w in col_width_defaults.items():
            max_len = default_w
            col_cells = ws[col_letter]
            for cell in col_cells:
                if cell.value:
                    s = str(cell.value)
                    if cell.hyperlink:
                        # Don't expand full 100-char URLs to 100 columns wide
                        s_len = min(len(s), 45)
                    else:
                        s_len = len(s)
                    if s_len > max_len:
                        max_len = s_len
            ws.column_dimensions[col_letter].width = min(max_len + 3, 60)

        wb.save(target_path)
        logger.info(f"Exported {len(sorted_jobs)} jobs to Excel: {target_path}")
        return target_path, len(sorted_jobs), dupes_removed
