"""Abstract Base Class for Form Parsers."""
from abc import ABC, abstractmethod
from typing import List, Dict, Any
from playwright.sync_api import Page


class BaseParser(ABC):
    """Abstract interface for ATS form parsers."""

    def __init__(self, platform_name: str):
        self.platform_name = platform_name

    @abstractmethod
    def extract_fields(self, page: Page) -> List[Dict[str, Any]]:
        """Extract all interactive form fields (inputs, selects, textareas, file drops)."""
        pass

    @abstractmethod
    def extract_job_metadata(self, page: Page) -> Dict[str, Any]:
        """Extract job title, company name, and location from the job posting page."""
        pass
