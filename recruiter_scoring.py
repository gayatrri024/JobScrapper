"""
recruiter_scoring.py - Evidence-based ranking and scoring engine for Technical Recruiters.

Prioritizes:
1. Recruiters who recently posted a relevant vacancy matching candidate target level (0-3 years, Junior DevOps / Cloud).
2. Recruiters whose public profiles or hiring posts demonstrate DevOps, AWS, or Cloud recruitment.
3. Internal recruiters working for companies with relevant open positions in the local database.
4. Agency recruiters with verifiable technical recruitment specialisms.

Maintains distinct evidence freshness and profile verification scores.
Clamped score: 0 to 100.
"""

import re
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Set, Tuple

RECRUITER_SPECIALISM_PATTERNS = {
    "DevOps": re.compile(r"\b(devops|dev\s*ops|site\s+reliability|sre|platform\s+engineer(ing)?)\b", re.IGNORECASE),
    "AWS & Cloud": re.compile(r"\b(aws|amazon\s+web\s+services|cloud\s+infrastructure|cloud\s+engineer(ing)?|cloud\s+architect)\b", re.IGNORECASE),
    "Kubernetes & Containers": re.compile(r"\b(kubernetes|k8s|docker|containers?)\b", re.IGNORECASE),
    "Infrastructure & IaC": re.compile(r"\b(terraform|opentofu|iac|infrastructure\s+as\s+code|linux|ansible)\b", re.IGNORECASE),
    "CI/CD & Automation": re.compile(r"\b(ci[\s\/-]?cd|github\s+actions|jenkins|pipeline|automation)\b", re.IGNORECASE),
    "Technical Recruitment": re.compile(r"\b(tech(nical)?\s+recruit(er|ing)|talent\s+acquisition|it\s+recruit(er|ing)|engineering\s+recruit(er|ing))\b", re.IGNORECASE)
}

JUNIOR_TARGET_PATTERNS = re.compile(
    r"\b(junior|jr\.?|associate|entry[\s\/-]?level|fresher|intern(ship)?|graduate|0-1\s*years?|0-2\s*years?|1-3\s*years?|0-3\s*years?)\b",
    re.IGNORECASE
)

SENIOR_PATTERNS = re.compile(
    r"\b(senior|sr\.?|lead|principal|staff|architect|director|head\s+of|vp|5\+\s*years?|7\+\s*years?|10\+\s*years?)\b",
    re.IGNORECASE
)

FOREIGN_REMOTE_PATTERN = re.compile(
    r"\b(us\s+only|usa\s+only|uk\s+only|europe\s+only|americas\s+only|north\s+america|"
    r"us\/canada|emea|latam|canada\s+only|united\s+states\s+only|us\s+citizen|"
    r"must\s+reside\s+in\s+the\s+us|must\s+reside\s+in\s+the\s+united\s+states|"
    r"us\s+timezone|pst\s+timezone|est\s+timezone)\b",
    re.IGNORECASE
)


class RecruiterScorer:
    """Evaluates and scores technical recruiters based on target skills, geography, and hiring evidence."""

    def __init__(self, config: Dict[str, Any], known_hiring_companies: Optional[Set[str]] = None):
        self.config = config
        profile = config.get("candidate_profile", {})
        self.target_roles = [r.lower() for r in profile.get("target_roles", [])]
        self.primary_locations = [l.lower() for l in profile.get("target_locations_primary", ["pune"])]
        self.secondary_locations = [l.lower() for l in profile.get("target_locations_secondary", ["remote", "india"])]
        self.known_hiring_companies = {c.lower() for c in known_hiring_companies} if known_hiring_companies else set()

    def score_recruiter(self, recruiter: Dict[str, Any]) -> Dict[str, Any]:
        """
        Computes match score (0-100), priority ('High'/'Medium'/'Low'), confidence score (0.0-1.0),
        match reasons, and potential gaps.
        """
        name = (recruiter.get("full_name") or "").strip()
        company = (recruiter.get("current_company") or "Unknown").strip()
        rec_type = recruiter.get("recruiter_type") or "Unverified"
        location = recruiter.get("location") or "Unknown"
        remote_coverage = recruiter.get("remote_coverage") or "Unknown"
        evidence_summary = recruiter.get("evidence_summary") or ""
        evidence_url = recruiter.get("hiring_evidence_url") or ""
        evidence_date = recruiter.get("evidence_date") or "Unknown"
        raw_snippet = recruiter.get("raw_snippet") or ""
        related_title = recruiter.get("related_job_title") or ""

        search_corpus = f"{name} {company} {rec_type} {location} {remote_coverage} {evidence_summary} {raw_snippet} {related_title}".lower()

        reasons: List[str] = []
        gaps: List[str] = []

        total_score = 0.0

        # --- 1. Technical Specialism Match (Max 30 pts) ---
        detected_specialisms: List[str] = []
        spec_score = 0.0

        if RECRUITER_SPECIALISM_PATTERNS["DevOps"].search(search_corpus):
            spec_score += 12.0
            detected_specialisms.append("DevOps")

        if RECRUITER_SPECIALISM_PATTERNS["AWS & Cloud"].search(search_corpus):
            spec_score += 8.0
            detected_specialisms.append("AWS & Cloud")

        if RECRUITER_SPECIALISM_PATTERNS["Kubernetes & Containers"].search(search_corpus):
            spec_score += 5.0
            detected_specialisms.append("Kubernetes & Containers")

        if RECRUITER_SPECIALISM_PATTERNS["Infrastructure & IaC"].search(search_corpus):
            spec_score += 3.0
            detected_specialisms.append("Infrastructure & IaC")

        if RECRUITER_SPECIALISM_PATTERNS["CI/CD & Automation"].search(search_corpus):
            spec_score += 2.0
            detected_specialisms.append("CI/CD & Automation")

        spec_score = min(30.0, spec_score)
        total_score += spec_score

        if detected_specialisms:
            reasons.append(f"Domain specialisms: {', '.join(detected_specialisms)} (+{spec_score:.0f} pts)")
        else:
            gaps.append("No explicit DevOps or Cloud specialisms identified in profile or post")

        # --- 2. Target Level & Experience Alignment (Max 20 pts) ---
        is_junior_matched = bool(JUNIOR_TARGET_PATTERNS.search(search_corpus))
        is_senior_matched = bool(SENIOR_PATTERNS.search(search_corpus))

        if is_junior_matched:
            total_score += 20.0
            reasons.append("Hiring/specialism targets junior/associate/entry-level (0-3 years) (+20 pts)")
        elif is_senior_matched and not is_junior_matched:
            total_score -= 15.0
            gaps.append("Post or headline focuses on Senior/Lead/Staff level (penalty -15 pts)")
        else:
            total_score += 10.0
            reasons.append("General/unspecified experience level (+10 pts)")

        # --- 3. Recruiter Type & Company Hiring Status (Max 25 pts) ---
        is_known_company = bool(company.lower() in self.known_hiring_companies and company.lower() != "unknown")
        type_lower = rec_type.lower()

        if "internal" in type_lower or "talent acquisition" in type_lower:
            if is_known_company:
                total_score += 25.0
                reasons.append(f"Internal TA at active hiring company '{company}' (+25 pts)")
            else:
                total_score += 20.0
                reasons.append(f"Internal technical recruiter at {company} (+20 pts)")
        elif "hiring manager" in type_lower:
            total_score += 22.0
            reasons.append(f"Hiring manager/engineering lead at {company} (+22 pts)")
        elif "agency" in type_lower or "sourcer" in type_lower:
            total_score += 15.0
            reasons.append(f"Agency recruiter / technical sourcer (+15 pts)")
        else:
            if is_known_company:
                total_score += 18.0
                reasons.append(f"Affiliated with active employer '{company}' (+18 pts)")
            else:
                total_score += 5.0
                reasons.append("Unverified recruiter type (+5 pts)")

        # --- 4. Geography & Location (Max 15 pts) ---
        is_foreign = bool(FOREIGN_REMOTE_PATTERN.search(location.lower()) or FOREIGN_REMOTE_PATTERN.search(search_corpus))
        is_pune = any(p in search_corpus for p in self.primary_locations)
        is_remote_india = (
            any(r in search_corpus for r in ["remote", "work from home", "anywhere", "worldwide"]) and
            any(ind in search_corpus for ind in ["india", "pune", "in", "apac"])
        )

        if is_foreign:
            total_score -= 30.0
            gaps.append(f"Hiring restricted to foreign location ({location}) (penalty -30 pts)")
        elif is_pune:
            total_score += 15.0
            reasons.append("Target location matched: Pune, Maharashtra (+15 pts)")
        elif is_remote_india or "india" in location.lower():
            total_score += 12.0
            reasons.append("Target location matched: India / India-Remote (+12 pts)")
        else:
            total_score += 5.0
            reasons.append("Location general / global (+5 pts)")

        # --- 5. Evidence Recency & Freshness (Max 10 pts) ---
        hours_ago = self._calculate_hours_ago(evidence_date)
        if hours_ago is not None:
            if hours_ago <= 48.0:
                total_score += 10.0
                reasons.append(f"Very fresh hiring evidence ({hours_ago:.0f}h ago) (+10 pts)")
            elif hours_ago <= 168.0:  # 7 days
                total_score += 8.0
                reasons.append(f"Fresh hiring evidence ({hours_ago/24.0:.1f} days ago) (+8 pts)")
            elif hours_ago <= 336.0:  # 14 days
                total_score += 5.0
                reasons.append(f"Recent hiring evidence ({hours_ago/24.0:.1f} days ago) (+5 pts)")
            elif hours_ago <= 720.0:  # 30 days
                total_score += 2.0
                reasons.append(f"Hiring evidence within 30 days (+2 pts)")
            else:
                gaps.append(f"Evidence is older than 30 days ({hours_ago/24.0:.0f} days old)")
        else:
            gaps.append("Evidence date is unverified or missing")

        # --- 6. Verification Status & Confidence Score (0.0 to 1.0) ---
        confidence, verif_status = self._evaluate_verification(recruiter)

        # Final clamped score
        clamped_score = max(0.0, min(100.0, total_score))

        priority = "Low"
        if clamped_score >= 75.0:
            priority = "High"
        elif clamped_score >= 50.0:
            priority = "Medium"

        return {
            "match_score": round(clamped_score, 1),
            "priority": priority,
            "confidence_score": round(confidence, 2),
            "verification_status": verif_status,
            "technical_specialisms": detected_specialisms,
            "match_reasons": "; ".join(reasons) if reasons else "General recruitment match",
            "potential_gaps": "; ".join(gaps) if gaps else "None detected"
        }

    def _calculate_hours_ago(self, date_str: str) -> Optional[float]:
        if not date_str or date_str.lower() in ["unknown", "unverified"]:
            return None
        now = datetime.now(timezone.utc)
        try:
            if "t" in date_str.lower():
                dt = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
            else:
                dt = datetime.strptime(date_str[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc)
            if dt > now + timedelta(minutes=15):
                return None
            return max(0.0, (now - dt).total_seconds() / 3600.0)
        except Exception:
            return None

    def _evaluate_verification(self, recruiter: Dict[str, Any]) -> Tuple[float, str]:
        """
        Determines verification confidence separately from match score.
        Never fabricates verification. Clearly labels unverified leads.
        """
        linkedin = recruiter.get("linkedin_url", "")
        evidence_url = recruiter.get("hiring_evidence_url", "")
        verif_in = recruiter.get("verification_status", "")
        company = (recruiter.get("current_company") or "Unknown").strip()

        has_profile = bool(linkedin and "linkedin.com/in/" in linkedin.lower())
        has_direct_evidence = bool(evidence_url and evidence_url.startswith("http"))
        has_known_company = bool(company.lower() in self.known_hiring_companies and company.lower() != "unknown")

        if verif_in == "Verified" or (has_profile and has_direct_evidence and has_known_company):
            return (0.95, "Verified")
        elif has_profile and has_direct_evidence:
            return (0.80, "Verified Profile & Post")
        elif has_profile:
            return (0.65, "Profile Verified")
        elif has_direct_evidence:
            return (0.50, "Estimated (Public Feed Post)")
        else:
            return (0.25, "Unverified Lead")
