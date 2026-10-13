"""Greenhouse ATS Form Parser."""
import logging
from typing import List, Dict, Any
from playwright.sync_api import Page
from .base import BaseParser

logger = logging.getLogger(__name__)


class GreenhouseParser(BaseParser):
    def __init__(self):
        super().__init__("greenhouse")

    def extract_job_metadata(self, page: Page) -> Dict[str, Any]:
        """Extract company name and job title from Greenhouse pages."""
        title = ""
        company = ""

        # Title extraction attempts
        for selector in [".app-title", "h1.app-title", "h1", ".job-title"]:
            if page.locator(selector).count() > 0:
                title = page.locator(selector).first.inner_text().strip()
                break

        # Company extraction attempts
        for selector in [".company-name", ".org-name", "h2.company-name", ".heading-subtitle"]:
            if page.locator(selector).count() > 0:
                company = page.locator(selector).first.inner_text().strip()
                break

        # Fallback to document title
        if not title:
            title = page.title()

        return {"title": title, "company": company, "platform": "greenhouse"}

    def extract_fields(self, page: Page) -> List[Dict[str, Any]]:
        """Extract interactive fields from Greenhouse application form."""
        fields = []

        # Execute in-browser script to collect all interactive form inputs
        raw_fields = page.evaluate("""() => {
            const results = [];
            const form = document.querySelector('form#application_form') || document.querySelector('form');
            if (!form) return results;

            const elements = form.querySelectorAll('input, select, textarea');
            elements.forEach((el, index) => {
                // Ignore hidden inputs and submit buttons
                if (el.type === 'hidden' || el.type === 'submit' || el.type === 'button') return;

                // Find corresponding label
                let labelText = '';
                if (el.id) {
                    const labelEl = document.querySelector(`label[for="${el.id}"]`);
                    if (labelEl) labelText = labelEl.innerText;
                }
                if (!labelText) {
                    const parentLabel = el.closest('label');
                    if (parentLabel) labelText = parentLabel.innerText;
                }
                if (!labelText) {
                    const fieldParent = el.closest('.field');
                    if (fieldParent) {
                        const labelEl = fieldParent.querySelector('label');
                        if (labelEl) labelText = labelEl.innerText;
                    }
                }
                if (!labelText) {
                    labelText = el.getAttribute('aria-label') || el.getAttribute('placeholder') || el.name || '';
                }

                // Options for select elements
                let options = [];
                if (el.tagName.toLowerCase() === 'select') {
                    options = Array.from(el.querySelectorAll('option')).map(opt => ({
                        value: opt.value,
                        text: opt.innerText.trim()
                    })).filter(o => o.value !== '');
                }

                // Generate selector
                let selector = '';
                if (el.id) {
                    selector = `#${el.id}`;
                } else if (el.name) {
                    selector = `${el.tagName.toLowerCase()}[name="${el.name}"]`;
                } else {
                    selector = `${el.tagName.toLowerCase()}:nth-of-type(${index + 1})`;
                }

                results.push({
                    selector: selector,
                    id: el.id || '',
                    name: el.name || '',
                    type: el.type || el.tagName.toLowerCase(),
                    tag: el.tagName.toLowerCase(),
                    label: labelText.replace(/\\*|\\n/g, ' ').trim(),
                    placeholder: el.placeholder || '',
                    required: el.required || el.getAttribute('aria-required') === 'true',
                    options: options
                });
            });

            return results;
        }""")

        logger.info(f"Extracted {len(raw_fields)} fields from Greenhouse form.")
        return raw_fields
