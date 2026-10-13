"""Lever ATS Form Parser."""
import logging
from typing import List, Dict, Any
from playwright.sync_api import Page
from .base import BaseParser

logger = logging.getLogger(__name__)


class LeverParser(BaseParser):
    def __init__(self):
        super().__init__("lever")

    def extract_job_metadata(self, page: Page) -> Dict[str, Any]:
        """Extract company name and job title from Lever pages."""
        title = ""
        company = ""

        # Lever headline
        for selector in [".posting-headline h2", "h2", ".posting-title"]:
            if page.locator(selector).count() > 0:
                title = page.locator(selector).first.inner_text().strip()
                break

        # Company extraction from logo / link
        for selector in [".main-header-logo img", ".posting-headline .posting-category", ".application-page-logo"]:
            if page.locator(selector).count() > 0:
                el = page.locator(selector).first
                company = el.get_attribute("alt") or el.inner_text().strip()
                break

        if not title:
            title = page.title()

        return {"title": title, "company": company, "platform": "lever"}

    def extract_fields(self, page: Page) -> List[Dict[str, Any]]:
        """Extract interactive fields from Lever application form."""
        raw_fields = page.evaluate("""() => {
            const results = [];
            const form = document.querySelector('form#application-form') || document.querySelector('form');
            if (!form) return results;

            // Handle primary Lever inputs
            const inputs = form.querySelectorAll('input, select, textarea');
            inputs.forEach((el, index) => {
                if (el.type === 'hidden' || el.type === 'submit' || el.type === 'button') return;

                let labelText = '';
                // Lever wraps questions in .application-question
                const questionContainer = el.closest('.application-question');
                if (questionContainer) {
                    const textEl = questionContainer.querySelector('.text');
                    if (textEl) labelText = textEl.innerText;
                }

                if (!labelText && el.id) {
                    const labelEl = document.querySelector(`label[for="${el.id}"]`);
                    if (labelEl) labelText = labelEl.innerText;
                }

                if (!labelText) {
                    const parentLabel = el.closest('label');
                    if (parentLabel) labelText = parentLabel.innerText;
                }

                if (!labelText) {
                    labelText = el.getAttribute('placeholder') || el.getAttribute('aria-label') || el.name || '';
                }

                let options = [];
                if (el.tagName.toLowerCase() === 'select') {
                    options = Array.from(el.querySelectorAll('option')).map(opt => ({
                        value: opt.value,
                        text: opt.innerText.trim()
                    })).filter(o => o.value !== '');
                }

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
                    required: el.required || false,
                    options: options
                });
            });

            return results;
        }""")

        logger.info(f"Extracted {len(raw_fields)} fields from Lever form.")
        return raw_fields
