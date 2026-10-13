"""ATS Platform Detector and Metadata Extractor."""
import re
from typing import Dict, Any, Tuple
from urllib.parse import urlparse


class ATSDetector:
    """Detects ATS type and extracts company/job identifiers from application URLs and page content."""

    @staticmethod
    def identify(url: str, page_content: str = "") -> Tuple[str, Dict[str, Any]]:
        """Returns the detected ATS platform name and extracted metadata."""
        parsed = urlparse(url)
        domain = parsed.netloc.lower()
        path = parsed.path.lower()
        meta = {"url": url, "platform": "generic", "company": "", "title": ""}

        # 1. Workday (e.g. *.myworkdayjobs.com, wd1.myworkdaysite.com, etc.)
        if "myworkdayjobs.com" in domain or "myworkdaysite.com" in domain or "workday" in domain:
            meta["platform"] = "workday"
            subparts = domain.split(".")
            if len(subparts) > 1:
                meta["company"] = subparts[0].replace("-", " ").capitalize()
            return "workday", meta

        # 2. iCIMS (e.g. *.icims.com, jobs-*.icims.com)
        if "icims.com" in domain or "icims" in path:
            meta["platform"] = "icims"
            subparts = domain.split(".")
            for part in subparts:
                if "icims" not in part and part not in ["jobs", "careers", "www"]:
                    meta["company"] = part.capitalize()
                    break
            return "icims", meta

        # 3. Greenhouse (boards.greenhouse.io, gh_jid, embedded greenhouse)
        if "boards.greenhouse.io" in domain or "gh_jid" in url or "greenhouse" in path:
            meta["platform"] = "greenhouse"
            parts = [p for p in path.split("/") if p]
            if parts:
                meta["company"] = parts[0].capitalize()
            return "greenhouse", meta

        # 4. Lever (jobs.lever.co)
        if "jobs.lever.co" in domain or "lever" in domain:
            meta["platform"] = "lever"
            parts = [p for p in path.split("/") if p]
            if parts:
                meta["company"] = parts[0].capitalize()
            return "lever", meta

        # 5. Ashby (jobs.ashbyhq.com)
        if "jobs.ashbyhq.com" in domain or "ashbyhq" in domain:
            meta["platform"] = "ashby"
            parts = [p for p in path.split("/") if p]
            if parts:
                meta["company"] = parts[0].capitalize()
            return "ashby", meta

        # 6. SmartRecruiters (jobs.smartrecruiters.com)
        if "smartrecruiters.com" in domain or "smartrecruiters" in path:
            meta["platform"] = "smartrecruiters"
            parts = [p for p in path.split("/") if p]
            if parts:
                meta["company"] = parts[0].capitalize()
            return "smartrecruiters", meta

        # 7. Taleo (*.taleo.net)
        if "taleo.net" in domain:
            meta["platform"] = "taleo"
            subparts = domain.split(".")
            if len(subparts) > 1:
                meta["company"] = subparts[0].capitalize()
            return "taleo", meta

        # 8. Page HTML heuristics if URL is a custom company subdomain (e.g. careers.netflix.com)
        if page_content:
            content_lower = page_content.lower()
            if "myworkdayjobs" in content_lower or "data-automation-id" in content_lower:
                meta["platform"] = "workday"
                return "workday", meta
            if "icims_content_iframe" in content_lower or "icims.com" in content_lower:
                meta["platform"] = "icims"
                return "icims", meta
            if "greenhouse.io" in content_lower or "gh-form" in content_lower:
                meta["platform"] = "greenhouse"
                return "greenhouse", meta
            if "lever.co" in content_lower or "lever-form" in content_lower:
                meta["platform"] = "lever"
                return "lever", meta
            if "ashbyhq" in content_lower:
                meta["platform"] = "ashby"
                return "ashby", meta

        return "generic", meta
