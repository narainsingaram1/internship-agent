"""ATS Platform Detector and Metadata Extractor."""
import re
from typing import Dict, Any, Tuple
from urllib.parse import urlparse


class ATSDetector:
    """Detects ATS type and extracts company/job identifiers from application URLs."""

    @staticmethod
    def identify(url: str, page_content: str = "") -> Tuple[str, Dict[str, Any]]:
        """Returns the detected ATS platform name and extracted metadata."""
        parsed = urlparse(url)
        domain = parsed.netloc.lower()
        path = parsed.path.lower()
        meta = {"url": url, "platform": "generic", "company": "", "title": ""}

        # 1. Greenhouse
        if "boards.greenhouse.io" in domain or "gh_jid" in url or "greenhouse" in path:
            meta["platform"] = "greenhouse"
            # Extract company from path e.g. /stripe/jobs/12345
            parts = [p for p in path.split("/") if p]
            if parts:
                meta["company"] = parts[0].capitalize()
            return "greenhouse", meta

        # 2. Lever
        if "jobs.lever.co" in domain or "lever" in domain:
            meta["platform"] = "lever"
            parts = [p for p in path.split("/") if p]
            if parts:
                meta["company"] = parts[0].capitalize()
            return "lever", meta

        # 3. Ashby
        if "jobs.ashbyhq.com" in domain or "ashby" in domain:
            meta["platform"] = "ashby"
            parts = [p for p in path.split("/") if p]
            if parts:
                meta["company"] = parts[0].capitalize()
            return "ashby", meta

        # 4. Workday
        if "myworkdayjobs.com" in domain or "workday" in domain:
            meta["platform"] = "workday"
            subparts = domain.split(".")
            if len(subparts) > 1:
                meta["company"] = subparts[0].capitalize()
            return "workday", meta

        # 5. Page HTML heuristics if URL is a custom subdomain
        if page_content:
            content_lower = page_content.lower()
            if "greenhouse.io" in content_lower or "gh-form" in content_lower:
                meta["platform"] = "greenhouse"
                return "greenhouse", meta
            if "lever.co" in content_lower or "lever-form" in content_lower:
                meta["platform"] = "lever"
                return "lever", meta
            if "ashby" in content_lower:
                meta["platform"] = "ashby"
                return "ashby", meta

        return "generic", meta
