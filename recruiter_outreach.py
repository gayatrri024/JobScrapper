"""
recruiter_outreach.py - Professional, personalized outreach note and message generator.

Generates:
1. LinkedIn Connection Request Note (strictly <= 300 characters for LinkedIn limits).
2. LinkedIn InMail / Direct Message (structured, 120-180 words).
3. Follow-up Message (for 'Follow-up Due' status).
4. Referral Inquiry Message (for hiring managers and engineering leads).

CRITICAL POLICY:
- Never sends messages automatically.
- Requires user to review, personalize if desired, and send manually.
"""

from typing import Any, Dict, Optional


class OutreachMessageGenerator:
    """Generates personalized outreach messages tailored to candidate profile and recruiter details."""

    def __init__(self, config: Dict[str, Any]):
        self.config = config
        profile = config.get("candidate_profile", {})
        self.core_skills = profile.get("core_skills", [
            "AWS", "Linux", "Docker", "Kubernetes", "Terraform", "CI/CD", "Python"
        ])
        exp_profile = profile.get("experience_profile", {})
        self.background_notes = exp_profile.get(
            "background_notes",
            "Operations and technical-support experience at Amazon; DevOps internship experience."
        )

    def _get_first_name(self, full_name: str) -> str:
        if not full_name or full_name.lower() in ["unknown", "unverified"]:
            return "there"
        parts = full_name.strip().split()
        return parts[0].title() if parts else "there"

    def generate_linkedin_connection_note(self, recruiter: Dict[str, Any]) -> str:
        """
        Generates a concise LinkedIn connection request note.
        STRICT REQUIREMENT: Must not exceed 300 characters.
        """
        first_name = self._get_first_name(recruiter.get("full_name", ""))
        company = (recruiter.get("current_company") or "").strip()
        job_title = (recruiter.get("related_job_title") or "DevOps/Cloud").strip()

        co_ref = f" at {company}" if company and company.lower() != "unknown" else ""

        # Option A: Job or company specific
        note = (
            f"Hi {first_name}, I saw your {job_title} hiring{co_ref}. "
            f"I have hands-on experience in AWS, Docker, Kubernetes, Terraform & CI/CD with an Amazon support background. "
            f"I'd love to connect regarding junior DevOps opportunities in Pune/Remote. Thanks!"
        )

        if len(note) <= 300:
            return note

        # Fallback shorter version to ensure strict <= 300 limit
        short_note = (
            f"Hi {first_name}, I saw your DevOps hiring{co_ref}. "
            f"I have hands-on AWS, Docker, K8s, Terraform & CI/CD skills + Amazon support background. "
            f"I'd welcome the chance to connect on junior DevOps openings in Pune/Remote. Thank you!"
        )
        if len(short_note) <= 300:
            return short_note

        # Ultra-compact fallback
        ultra = (
            f"Hi {first_name}, I'm a junior DevOps engineer (AWS, K8s, Docker, Terraform, CI/CD) "
            f"with an Amazon support background. I'd love to connect regarding opportunities{co_ref}."
        )
        return ultra[:297] + "..." if len(ultra) > 300 else ultra

    def generate_inmail_message(self, recruiter: Dict[str, Any]) -> Dict[str, str]:
        """
        Generates a structured InMail / Direct Message template (100-180 words).
        Returns dict with 'subject' and 'body'.
        """
        first_name = self._get_first_name(recruiter.get("full_name", ""))
        company = (recruiter.get("current_company") or "your team").strip()
        job_title = (recruiter.get("related_job_title") or "Junior DevOps / Cloud Engineer").strip()
        evidence_summary = (recruiter.get("evidence_summary") or "").strip()

        subject = f"Junior DevOps / Cloud Engineer - Application & Introduction ({company})"

        hook = f"I noticed your active hiring for {job_title} at {company}."
        if evidence_summary and len(evidence_summary) > 20:
            hook = f"I came across your hiring announcement regarding {job_title} at {company}."

        body = f"""Hi {first_name},

{hook}

I am a Junior DevOps & Cloud Engineer with 0-3 years of foundational experience, bringing hands-on practice in infrastructure automation combined with real-world technical operations experience from Amazon.

Core competencies:
* Cloud & Linux: AWS (EC2, S3, IAM, VPC), Linux administration, Bash scripting
* Containers & Orchestration: Docker containerization, Kubernetes cluster fundamentals
* Infrastructure as Code: Terraform / OpenTofu provisioning
* CI/CD Pipelines: GitHub Actions & Jenkins automated deployment workflows

I am based in Pune and actively open to on-site, hybrid (Pune), or India-remote opportunities.

If you have a quick 5 minutes this week, I would appreciate the opportunity to learn more about this role and how my technical skills can support your engineering team.

Thank you for your time and consideration!

Best regards,
Candidate Name
[LinkedIn Profile Link] | [Portfolio / GitHub Link] | [Phone Number]"""

        return {
            "subject": subject,
            "body": body
        }

    def generate_follow_up_message(self, recruiter: Dict[str, Any]) -> Dict[str, str]:
        """Generates a polite follow-up message 5-7 days after initial contact."""
        first_name = self._get_first_name(recruiter.get("full_name", ""))
        company = (recruiter.get("current_company") or "your organization").strip()
        job_title = (recruiter.get("related_job_title") or "Junior DevOps Engineer").strip()

        subject = f"Following up: Junior DevOps Engineer profile ({company})"
        body = f"""Hi {first_name},

I hope your week is going well.

I wanted to briefly follow up on my note regarding the {job_title} opening at {company}. 

I remain very interested in the opportunity to contribute my hands-on skills in AWS, Docker, Kubernetes, Terraform, and CI/CD pipelines to your team.

If the position is still open, I would welcome the chance to share my CV or speak briefly at your convenience. If another team member is heading this search, could you kindly point me in their direction?

Thank you again for your time!

Best regards,
Candidate Name
[LinkedIn Profile Link] | [Phone Number]"""

        return {
            "subject": subject,
            "body": body
        }

    def generate_all_templates(self, recruiter: Dict[str, Any]) -> Dict[str, Any]:
        """Returns all three message variations in a structured dict."""
        return {
            "connection_note": self.generate_linkedin_connection_note(recruiter),
            "inmail": self.generate_inmail_message(recruiter),
            "follow_up": self.generate_follow_up_message(recruiter)
        }
