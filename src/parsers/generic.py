"""Universal Generic Form Parser with iframe and shadow DOM support."""
import logging
from typing import List, Dict, Any
from playwright.sync_api import Page
from .base import BaseParser

logger = logging.getLogger(__name__)


class GenericParser(BaseParser):
    def __init__(self):
        super().__init__("generic")

    def extract_job_metadata(self, page: Page) -> Dict[str, Any]:
        """Extract generic page title, company, and headings."""
        title = ""
        company = ""

        for selector in ["h1", "title", ".job-title", "#job-title", "h1.title"]:
            if page.locator(selector).count() > 0:
                title = page.locator(selector).first.inner_text().strip()
                break

        for selector in [".company-name", ".employer", "meta[property='og:site_name']", ".header-company"]:
            if page.locator(selector).count() > 0:
                el = page.locator(selector).first
                company = el.get_attribute("content") or el.inner_text().strip()
                break

        return {
            "title": title or page.title(),
            "company": company,
            "platform": "generic",
        }

    def extract_fields(self, page: Page) -> List[Dict[str, Any]]:
        """Walk all visible interactive form controls across main document and accessible iframes."""
        raw_fields = page.evaluate("""() => {
            const results = [];
            
            function processDocument(doc, prefix = '') {
                if (!doc) return;
                const inputs = doc.querySelectorAll('input, select, textarea, [role="combobox"]');

                inputs.forEach((el, index) => {
                    if (el.type === 'hidden' || el.type === 'submit' || el.type === 'button') return;

                    // Visibility check
                    try {
                        const style = window.getComputedStyle(el);
                        if (style.display === 'none' && el.type !== 'file') return;
                        if (style.visibility === 'hidden' && el.type !== 'file') return;
                    } catch (e) {}

                    // Find label
                    let labelText = '';
                    if (el.id) {
                        const labelEl = doc.querySelector(`label[for="${el.id}"]`);
                        if (labelEl) labelText = labelEl.innerText;
                    }
                    if (!labelText) {
                        const parentLabel = el.closest('label');
                        if (parentLabel) labelText = parentLabel.innerText;
                    }
                    if (!labelText) {
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
                        selector: prefix + selector,
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
            }

            // 1. Process Main Document
            processDocument(document);

            // 2. Process Accessible iframes
            const iframes = document.querySelectorAll('iframe');
            iframes.forEach((iframe, i) => {
                try {
                    if (iframe.contentDocument) {
                        const framePrefix = iframe.id ? `#${iframe.id} ` : `iframe:nth-of-type(${i + 1}) `;
                        processDocument(iframe.contentDocument, framePrefix);
                    }
                } catch (e) {
                    // Cross-origin iframe
                }
            });

            return results;
        }""")

        logger.info(f"Extracted {len(raw_fields)} fields via Universal Generic Parser.")
        return raw_fields
