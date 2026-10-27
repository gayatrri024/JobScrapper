"""
scoring.py - Rule-based matching and scoring engine for DevOps & Cloud job opportunities.

Evaluates:
1. Target Role & Title alignment (Max 25 pts)
   Targets: Cloud Administrator, AWS Cloud Support, Junior Cloud Engineer,
   Junior DevOps, Infrastructure Engineer, Platform Engineer, Entry-level SRE.
2. Core Skill Overlap, weighted by 5 DevOps Pillars:
   AWS, Kubernetes, Docker, Terraform/OpenTofu, CI/CD (Max 35 pts)
3. Experience & Seniority fit (Max 15 pts, or immediate disqualification for >3 years / Senior titles)
   Never assigns false "0-3 years" when evidence is missing. Uses "Unknown" with neutral score.
4. Location & Work Arrangement (Max 15 pts, Pune first (+15), then Remote India (+12))
   Strict exclusion of foreign-only remote roles (US, UK, EMEA, etc.).
5. Recency (Max 15 pts, <=24h strongly prioritized, excludes future timestamps)
6. Shift / Work hours preference (Day shift preferred, rotational shift noted in gaps)

Clamped score: 0 to 100.
Generates explicit match reasons, pillar breakdown, and identifies gaps.
"""

import re
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Set, Tuple


class JobScorer:
    """Scores job listings based on candidate profile, target skills, location, and seniority."""

    PILLAR_DEFS = {
        "AWS": re.compile(r"\b(aws|amazon\s+web\s+services)\b", re.IGNORECASE),
        "Kubernetes": re.compile(r"\b(kubernetes|k8s)\b", re.IGNORECASE),
        "Docker": re.compile(r"\b(docker|containers?)\b", re.IGNORECASE),
        "Terraform": re.compile(r"\b(terraform|opentofu|tofu|iac)\b", re.IGNORECASE),
        "CI/CD": re.compile(r"\b(ci[\s\/-]?cd|github\s+actions|jenkins|gitlab\s+ci)\b", re.IGNORECASE),
    }

    SENIOR_TITLE_PATTERN = re.compile(
        r"\b(sr\.?|sr\s+|senior|lead|principal|staff|architect|director|manager|head\s+of|vice\s+president|vp)\b",
        re.IGNORECASE
    )

    FOREIGN_REMOTE_PATTERN = re.compile(
        r"\b(us\s+only|usa\s+only|uk\s+only|europe\s+only|americas\s+only|north\s+america|"
        r"us\/canada|emea|latam|canada\s+only|united\s+states\s+only|us\s+citizen|"
        r"must\s+reside\s+in\s+the\s+us|must\s+reside\s+in\s+the\s+united\s+states|"
        r"us\s+timezone|pst\s+timezone|est\s+timezone)\b",
        re.IGNORECASE
    )

    def __init__(self, config: Dict[str, Any]):
        profile = config.get("candidate_profile", {})
        filters = config.get("filters", {})
        
        self.target_roles = [r.lower() for r in profile.get("target_roles", [])]
        self.primary_locations = [l.lower() for l in profile.get("target_locations_primary", ["pune"])]
        self.secondary_locations = [l.lower() for l in profile.get("target_locations_secondary", ["remote", "india"])]
        self.core_skills = profile.get("core_skills", [])
        
        exp_profile = profile.get("experience_profile", {})
        self.pos_exp_keywords = [k.lower() for k in exp_profile.get("positive_keywords", [])]
        self.pen_exp_keywords = [k.lower() for k in exp_profile.get("penalty_keywords", [])]

        self.pune_or_remote_india_only = filters.get("pune_or_remote_india_only", False)
        self.max_experience_years = filters.get("max_experience_years", 3)
        self.exclude_senior_roles = filters.get("exclude_senior_roles", True)

        self.skill_patterns = {}
        for skill in self.core_skills:
            s_clean = skill.strip()
            if s_clean.lower() == "ci/cd":
                pat = re.compile(r"\bci[\s\/-]?cd\b", re.IGNORECASE)
            elif s_clean.lower() == "c++":
                pat = re.compile(r"\bc\+\+\b", re.IGNORECASE)
            elif s_clean.lower() == "bash":
                pat = re.compile(r"\b(bash|shell\s+scripting)\b", re.IGNORECASE)
            elif s_clean.lower() == "git":
                pat = re.compile(r"\bgit\b(?!\s*hub|\s*lab)", re.IGNORECASE)
            elif s_clean.lower() == "infrastructure automation":
                pat = re.compile(r"\binfrastructure\s+automation\b", re.IGNORECASE)
            elif s_clean.lower() == "aws":
                pat = re.compile(r"\b(aws|amazon\s+web\s+services)\b", re.IGNORECASE)
            elif s_clean.lower() in ["kubernetes", "k8s"]:
                pat = re.compile(r"\b(kubernetes|k8s)\b", re.IGNORECASE)
            elif s_clean.lower() in ["terraform", "opentofu"]:
                pat = re.compile(r"\b(terraform|opentofu)\b", re.IGNORECASE)
            else:
                pat = re.compile(rf"\b{re.escape(s_clean)}\b", re.IGNORECASE)
            self.skill_patterns[s_clean] = pat

    def score_job(self, job: Dict[str, Any]) -> Dict[str, Any]:
        """
        Computes a 0-100 score, priority level, match reasons, and potential gaps.
        Returns a dictionary with score metadata.
        """
        title = (job.get("job_title") or "").strip()
        company = (job.get("company") or "").strip()
        location = (job.get("location") or "").strip()
        arrangement = (job.get("work_arrangement") or "").strip()
        posting_date = (job.get("posting_date") or "").strip()
        raw_snippet = job.get("raw_snippet", "")
        raw_text = f"{title} {location} {arrangement} {raw_snippet} {job.get('experience_required', '')}".lower()

        reasons: List[str] = []
        gaps: List[str] = []

        # Disqualify aggregator directory index pages
        aggregator_patterns = [
            r"\bjobs\s+in\b", r"\bvacancies\s+in\b", r"\bjob\s+vacancies\b",
            r"\bjobs\s+near\s+me\b", r"\bopenings\s+in\b", r"\bjob\s+openings\b",
            r"\bjobs\s*-\s*naukri\b", r"\bfind\s+us\s+jobs\b", r"\bopen\s+roles\b"
        ]
        is_aggregator = any(re.search(ap, title, re.IGNORECASE) for ap in aggregator_patterns)
        if is_aggregator:
            gaps.append(f"Listing appears to be an aggregator directory index ('{title}') rather than a direct job opening")

        # Reject listings with no identifiable employer AND generic title
        if (company == "Unknown" or not company) and any(g in title.lower() for g in ["devops", "cloud engineer"]):
            is_aggregator = True
            gaps.append("Listing has no identifiable employer name")

        # --- 1. Target Role & Title Alignment (Max 25 pts) ---
        role_score, role_reason, role_gap = self._score_role(title)
        if role_reason:
            reasons.append(role_reason)
        if role_gap:
            gaps.append(role_gap)

        # --- 2. Skill Overlap & DevOps Pillars (Max 35 pts) ---
        skill_score, matched_skills, matched_pillars, missing_pillars = self._score_skills(raw_text)
        if matched_pillars:
            reasons.append(f"Top Pillars: {', '.join(matched_pillars)} (+{skill_score:.1f} pts)")
        elif matched_skills:
            reasons.append(f"Matched skills: {', '.join(matched_skills[:4])} (+{skill_score:.1f} pts)")
        if missing_pillars:
            gaps.append(f"Missing core DevOps pillars: {', '.join(missing_pillars)}")

        # --- 3. Experience & Seniority Fit (Max 15 pts, or Disqualification) ---
        exp_score, exp_reason, exp_gap, exp_label, exp_evidence, is_disqualified_exp = self._score_experience(title, raw_text)
        if exp_reason:
            reasons.append(exp_reason)
        if exp_gap:
            gaps.append(exp_gap)

        # --- 4. Location & Work Arrangement (Max 15 pts) ---
        loc_score, loc_reason, loc_gap, is_disqualified_loc = self._score_location(location, arrangement, raw_text)
        if loc_reason:
            reasons.append(loc_reason)
        if loc_gap:
            gaps.append(loc_gap)

        # --- 5. Shift & Work Schedule Preference (+2 pts bonus or note) ---
        shift_score, shift_reason, shift_gap = self._score_shift(raw_text)
        if shift_reason:
            reasons.append(shift_reason)
        if shift_gap:
            gaps.append(shift_gap)

        # --- 6. Recency (Max 15 pts, <=24h strongly prioritized) ---
        recency_score, recency_reason, hours_ago = self._score_recency(posting_date)
        if recency_reason:
            reasons.append(recency_reason)

        # Total Calculation
        raw_total = role_score + skill_score + exp_score + loc_score + shift_score + recency_score
        
        # Hard exclusion / disqualification check
        if is_aggregator or is_disqualified_exp or (self.pune_or_remote_india_only and is_disqualified_loc):
            final_score = 0.0
            priority = "Low"
        else:
            final_score = max(0.0, min(100.0, round(raw_total, 1)))
            if final_score >= 75.0:
                priority = "High"
            elif final_score >= 50.0:
                priority = "Medium"
            else:
                priority = "Low"

        # Date confidence note in gaps if unverified
        date_conf = job.get("date_confidence", "Unknown")
        if date_conf == "Estimated (Feed Indexed Date)":
            gaps.append("Posting date reflects search index time (feed indexed date), not verified employer post date")
        elif date_conf == "Listing Updated":
            gaps.append("Posting date reflects job board update/refresh time, original creation date unconfirmed")
        elif date_conf in ["Unknown", "Unverified"]:
            gaps.append("Posting date could not be verified from original employer source")

        return {
            "match_score": final_score,
            "priority": priority,
            "relevant_skills": matched_skills,
            "matched_pillars": matched_pillars,
            "experience_required": exp_label,
            "experience_evidence": exp_evidence,
            "hours_ago": hours_ago,
            "match_reasons": "; ".join(reasons),
            "potential_gaps": "; ".join(gaps) if gaps else "None detected"
        }

    def _score_role(self, title: str) -> Tuple[float, str, str]:
        t_low = title.lower()

        # Check configured target roles first
        for target in self.target_roles:
            if target and target in t_low:
                return 25.0, f"Target role match: '{title}' matches candidate target domain (+25 pts)", ""

        prime_targets = [
            "junior devops engineer", "jr devops", "devops engineer",
            "cloud engineer", "cloud support engineer", "cloud administrator",
            "cloud systems engineer", "cloud operations engineer", "aws engineer",
            "aws cloud support", "aws support engineer", "kubernetes engineer",
            "infrastructure engineer", "infrastructure automation", "platform engineer",
            "site reliability engineer", "site reliability", "junior site reliability engineer",
            "junior sre", "entry level sre", "systems engineer", "system administrator",
            "linux engineer", "linux administrator", "build and release engineer",
            "release engineer", "ci/cd engineer", "devops intern", "cloud intern",
            "devops trainee", "cloud trainee", "cloud associate"
        ]
        for role in prime_targets:
            if role in t_low:
                return 25.0, f"Target role match: '{title}' matches candidate target domain (+25 pts)", ""

        # SRE standalone acronym check (word boundary)
        if re.search(r"\bsre\b", t_low):
            return 25.0, f"Target role match: '{title}' matches SRE target domain (+25 pts)", ""

        secondary_keywords = [
            "devops", "cloud", "infrastructure", "sre", "platform", "kubernetes",
            "systems", "linux", "aws", "terraform", "automation", "sysadmin", "docker"
        ]
        for kw in secondary_keywords:
            if re.search(rf"\b{re.escape(kw)}\b", t_low):
                return 18.0, f"Related role match: contains '{kw}' in '{title}' (+18 pts)", ""

        unrelated = ["sales", "recruiter", "marketing", "account executive", "frontend", "react", "graphic designer", "copywriter", "finance"]
        for un in unrelated:
            if re.search(rf"\b{re.escape(un)}\b", t_low):
                return -25.0, "", f"Role title '{title}' appears unrelated to DevOps/Cloud (-25 pts)"

        return 5.0, "", f"Title '{title}' is generic or does not explicitly specify DevOps/Cloud"

    def _score_skills(self, text: str) -> Tuple[float, List[str], List[str], List[str]]:
        matched_skills: List[str] = []
        matched_pillars: List[str] = []
        missing_pillars: List[str] = []

        for pillar, pat in self.PILLAR_DEFS.items():
            if pat.search(text):
                matched_pillars.append(pillar)
            else:
                missing_pillars.append(pillar)

        for skill, pattern in self.skill_patterns.items():
            if pattern.search(text):
                matched_skills.append(skill)

        pillar_pts = len(matched_pillars) * 5.0
        secondary_count = len(set(matched_skills) - set(matched_pillars))
        secondary_pts = min(10.0, secondary_count * 2.0)

        total_pts = min(35.0, pillar_pts + secondary_pts)
        return total_pts, sorted(matched_skills), sorted(matched_pillars), sorted(missing_pillars)

    def _score_experience(self, title: str, text: str) -> Tuple[float, str, str, str, str, bool]:
        t_low = title.lower()

        if self.SENIOR_TITLE_PATTERN.search(t_low):
            m = self.SENIOR_TITLE_PATTERN.search(t_low).group(0)
            return -100.0, "", f"Senior role detected ('{m}' in title). Exceeds 0-3 years candidate experience limit", f"Senior ({m})", f"Senior title keyword '{m}' found in title", True

        yrs_pattern = re.search(r"(\d+)\s*(?:-|to|\+)\s*(\d*)\s*(?:years?|yrs?)", text)
        if yrs_pattern:
            min_yrs = int(yrs_pattern.group(1))
            max_yrs = int(yrs_pattern.group(2)) if yrs_pattern.group(2) else min_yrs
            extracted_exp = f"{min_yrs}-{max_yrs} years" if max_yrs > min_yrs else f"{min_yrs}+ years"
            
            if min_yrs > 3 or (min_yrs == 3 and max_yrs > 4):
                return -100.0, "", f"Role requires {extracted_exp} which substantially exceeds 0-3 years experience (-100 pts)", extracted_exp, f"Explicit {extracted_exp} requirement found in listing", True
            elif min_yrs <= 2 and max_yrs <= 3:
                return 15.0, f"Experience requirement ({extracted_exp}) matches your 0-3 year range (+15 pts)", "", extracted_exp, f"Explicit {extracted_exp} requirement matches target", False
            elif min_yrs <= 2 and max_yrs > 3:
                return 6.0, f"Minimum experience ({min_yrs} yrs) is reachable, but upper range ({max_yrs} yrs) suggests mid expectations (+6 pts)", f"Range {extracted_exp} exceeds 0-3 yrs at the upper end", extracted_exp, f"Range {extracted_exp} requires review", False

        # Explicit experience requirement penalty check (e.g. "minimum 5 years", "requires 4+ years")
        for kw in self.pen_exp_keywords:
            if any(yr in kw for yr in ["4+", "5+", "6+", "7+", "8+", "10+"]):
                if re.search(rf"\b{re.escape(kw)}\b", text):
                    return -100.0, "", f"Requires {kw} experience. Exceeds target 0-3 years profile", f"Senior ({kw})", f"Penalty keyword '{kw}' found in listing", True

        # Check explicit senior role declaration in snippet (avoid incidental mentions like 'work with senior engineers')
        explicit_senior_body = re.search(r"\b(we\s+are\s+looking\s+for\s+a|seeking\s+a|hiring\s+a)\s+(?:senior|lead|principal|staff|architect|director)\b", text, re.IGNORECASE)
        if explicit_senior_body:
            m = explicit_senior_body.group(0)
            return -50.0, "", f"Senior opening indicated in listing body ('{m}'). Exceeds target 0-3 years profile", "Senior", f"Seniority phrase '{m}' in description", True

        for kw in self.pos_exp_keywords:
            kw_clean = kw.strip()
            if re.search(rf"\b{re.escape(kw_clean)}\b", t_low) or re.search(rf"\b{re.escape(kw_clean)}\b", text):
                return 15.0, f"Seniority matches entry/junior level ('{kw}' found) (+15 pts)", "", f"Junior / {kw.title()}", f"Junior keyword '{kw}' found in listing", False

        # If evidence is missing, NEVER falsely assign "0-3 years"
        return 0.0, "", "Experience requirement not specified in snippet/listing; scored neutrally", "Unknown", "Not specified in snippet/listing", False

    def _score_location(self, location: str, arrangement: str, text: str) -> Tuple[float, str, str, bool]:
        loc_low = location.lower()
        arr_low = arrangement.lower()
        comb_loc = f"{loc_low} {arr_low} {text}".lower()

        # Check Pune first (Primary Target)
        if any(p in loc_low or p in text for p in self.primary_locations):
            return 15.0, "Location match: Pune, Maharashtra is your primary target location (+15 pts)", "", False

        # Check Remote
        is_remote = "remote" in arr_low or "remote" in loc_low or "work from home" in comb_loc or "home based" in comb_loc or "anywhere" in loc_low or "worldwide" in loc_low
        if is_remote:
            has_foreign_country = bool(re.search(r"\b(united\s+states|usa|u\.s\.|canada|united\s+kingdom|uk|london|germany|poland|brazil|mexico|argentina|australia|singapore|ireland|california|san\s+francisco|san\s+jose|washington|seattle|new\s+york|texas|austin|chicago|bellevue)\b", loc_low))
            has_india_or_global = bool(re.search(r"\b(india|pune|bangalore|bengaluru|hyderabad|worldwide|anywhere|global)\b", loc_low))
            if self.FOREIGN_REMOTE_PATTERN.search(loc_low) or self.FOREIGN_REMOTE_PATTERN.search(text) or (has_foreign_country and not has_india_or_global):
                return -40.0, "", f"Remote role restricted to region outside India ('{location}') (-40 pts)", True
            return 12.0, "Location match: Remote role accessible across India (+12 pts)", "", False

        india_cities = ["bengaluru", "bangalore", "hyderabad", "mumbai", "noida", "gurgaon", "delhi", "chennai", "india"]
        if any(c in loc_low for c in india_cities):
            if self.pune_or_remote_india_only:
                return -30.0, "", f"Non-Pune India location ('{location}') excluded by strict Pune/Remote filter", True
            return 5.0, f"Secondary location: India onsite/hybrid ({location}) (+5 pts)", "Located in India but outside Pune", False

        foreign_locs = ["united states", "usa", "united kingdom", "germany", "canada", "singapore", "australia", "netherlands", "london", "new york"]
        if any(f in loc_low for f in foreign_locs):
            return -50.0, "", f"Onsite location '{location}' is outside India (-50 pts)", True

        if not location or loc_low == "unknown":
            return 0.0, "", "Location unknown; scored neutrally", False

        return 0.0, "", f"Location '{location}' not in priority Pune/Remote list", False

    def _score_shift(self, text: str) -> Tuple[float, str, str]:
        t_low = text.lower()
        if re.search(r"\b(day\s+shift|general\s+shift|regular\s+hours|standard\s+business\s+hours)\b", t_low):
            return 2.0, "Shift match: Day shift / regular working hours specified (+2 pts)", ""
        if re.search(r"\b(rotational\s+shift|night\s+shift|graveyard\s+shift|24\/7\s+support)\b", t_low):
            return 0.0, "", "Rotational / 24x7 / night shift schedule indicated"
        return 0.0, "", ""

    def _score_recency(self, posting_date: str) -> Tuple[float, str, Optional[float]]:
        if not posting_date or posting_date.lower() in ["unknown", "unverified"]:
            return 0.0, "", None

        try:
            if "t" in posting_date.lower():
                dt = datetime.fromisoformat(posting_date.replace("Z", "+00:00"))
            else:
                dt = datetime.strptime(posting_date[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc)
            
            now = datetime.now(timezone.utc)
            
            # Anti-future check: Clock drift allowance is at most 15 minutes
            if dt > now + timedelta(minutes=15):
                return 0.0, "Invalid timestamp: Posting date is in the future (+0 pts)", None

            delta = now - dt
            hours = delta.total_seconds() / 3600.0

            if hours <= 24.0:
                return 15.0, f"High recency: Posted within the last 24 hours ({hours:.0f}h ago) (+15 pts)", round(hours, 1)
            elif hours <= 72.0:
                return 10.0, f"Good recency: Posted within the last 3 days ({hours/24:.1f}d ago) (+10 pts)", round(hours, 1)
            elif hours <= 168.0:
                return 3.0, f"Moderate recency: Posted within the last week ({hours/24:.1f}d ago) (+3 pts)", round(hours, 1)
            else:
                return 0.0, f"Stale listing: Posted {hours/24:.0f} days ago (+0 pts)", round(hours, 1)
        except Exception:
            pass

        return 0.0, "", None
