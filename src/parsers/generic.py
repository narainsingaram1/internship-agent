"""Universal Generic Form Parser for custom portals and ATSs."""
import logging
from typing import List, Dict, Any
from playwright.sync_api import Page
from .base import BaseParser

logger = logging.getLogger(__name__)


class GenericParser(BaseParser):
    def __init__(self):
        super().__init__("generic")

    def extract_job_metadata(self, page: Page) -> Dict[str, Any]:
        """Extract generic page title and headings."""
        title = ""
        for selector in ["h1", "title", ".job-title", "#job-title"]:
            if page.locator(selector).count() > 0:
                title = page.locator(selector).first.inner_text().strip()
                break

        return {
            "title": title or page.title(),
            "company": "",
            "platform": "generic",
        }

    def extract_fields(self, page: Page) -> List[Dict[str, Any]]:
        """Walk all visible interactive form controls across the page."""
        raw_fields = page.evaluate("""() => {
            const results = [];
            const inputs = document.querySelectorAll('input, select, textarea, [role="combobox"]');

            inputs.forEach((el, index) => {
                if (el.type === 'hidden' || el.type === 'submit' || el.type === 'button') return;

                // Check visibility
                const style = window.getComputedStyle(el);
                if (style.display === 'none' && el.type !== 'file') return;
                if (style.visibility === 'hidden' && el.type !== 'file') return;

                // Find label
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
                    // Preceding text or sibling
                    const prev = el.previousElementSibling;
                    if (prev && (prev.tagName === 'LABEL' || prev.tagName === 'SPAN' || prev.tagName === 'P')) {
                        labelText = prev.innerText;
                    }
                }
                if (!labelText) {
                    labelText = el.getAttribute('aria-label') || el.getAttribute('placeholder') || el.name || '';
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
                    required: el.required || el.getAttribute('aria-required') === 'true',
                    options: options
                });
            });

            return results;
        }""")

        logger.info(f"Extracted {len(raw_fields)} fields via Generic Parser.")
        return raw_fields
