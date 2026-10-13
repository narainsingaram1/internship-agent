"""Workable ATS Form Parser and Navigator."""
import time
import logging
from typing import List, Dict, Any
from playwright.sync_api import Page
from .base import BaseParser

logger = logging.getLogger(__name__)


class WorkableParser(BaseParser):
    """Specialized parser for Workable (apply.workable.com) forms."""

    def __init__(self):
        super().__init__("workable")

    def dismiss_overlays(self, page: Page):
        """Dismiss Workable cookie consent banner if present."""
        cookie_selectors = [
            "[data-ui='cookie-consent-accept']",
            "button:has-text('Accept all')",
            "button:has-text('Accept All')",
            "button:has-text('Decline all')",
        ]
        for sel in cookie_selectors:
            try:
                btn = page.locator(sel).first
                if btn.count() > 0 and btn.is_visible():
                    logger.info("Dismissing Workable cookie banner...")
                    btn.click(timeout=1000)
                    page.wait_for_timeout(400)
                    break
            except Exception:
                pass

    def ensure_application_view(self, page: Page):
        """Navigate or normalize Workable job page to the active /apply/ form."""
        self.dismiss_overlays(page)

        # If on job overview /j/<id>, normalize directly to /j/<id>/apply/
        current_url = page.url
        if "/j/" in current_url and not current_url.rstrip("/").endswith("/apply"):
            apply_url = current_url.rstrip("/") + "/apply/"
            logger.info(f"Normalizing Workable job URL to application view: {apply_url}")
            page.goto(apply_url, wait_until="domcontentloaded")
            page.wait_for_timeout(2000)

        self.dismiss_overlays(page)

        # Wait for interactive inputs to mount
        try:
            page.wait_for_selector("input#firstname, input[name='firstname'], input", timeout=6000)
            page.wait_for_timeout(1000)
        except Exception:
            pass

    def extract_job_metadata(self, page: Page) -> Dict[str, Any]:
        """Extract job title and company from Workable headers."""
        title = ""
        company = ""

        for sel in ["h1", "[data-ui='job-title']", ".job-title", "h2"]:
            if page.locator(sel).count() > 0:
                title = page.locator(sel).first.inner_text().strip()
                if title:
                    break

        for sel in ["[data-ui='company-logo']", "[data-ui='company-url']", ".company-name"]:
            if page.locator(sel).count() > 0:
                company = page.locator(sel).first.inner_text().strip()
                if company:
                    break

        return {"title": title or page.title(), "company": company, "platform": "workable"}

    def extract_fields(self, page: Page) -> List[Dict[str, Any]]:
        """Extract all interactive form fields from Workable application form."""
        self.ensure_application_view(page)
        self.dismiss_overlays(page)

        # Ensure scroll to load lazy-rendered custom questions
        try:
            page.evaluate("window.scrollTo(0, document.body.scrollHeight / 2)")
            page.wait_for_timeout(400)
            page.evaluate("window.scrollTo(0, 0)")
        except Exception:
            pass

        raw_fields = page.evaluate("""() => {
            const results = [];
            const inputs = document.querySelectorAll('input, select, textarea');

            inputs.forEach((el, index) => {
                if (el.type === 'hidden' || el.type === 'submit') return;

                const name = el.name || '';
                const id = el.id || '';
                const dataUi = el.getAttribute('data-ui') || '';

                // Label resolution
                let labelText = '';
                if (id) {
                    const lbl = document.querySelector(`label[for="${id}"]`);
                    if (lbl) labelText = lbl.innerText;
                }
                if (!labelText) {
                    const parentSection = el.closest('[data-ui*="section"], [data-ui*="question"], div[class*="styles--"], .form-group');
                    if (parentSection) {
                        const lbl = parentSection.querySelector('label, h3, h4, span[class*="label"], span[class*="styles--"]');
                        if (lbl) labelText = lbl.innerText;
                    }
                }
                if (!labelText) {
                    const parentLabel = el.closest('label');
                    if (parentLabel) labelText = parentLabel.innerText;
                }
                if (!labelText) {
                    labelText = el.placeholder || dataUi || name || id || '';
                }

                labelText = labelText.replace(/\\*|\\n/g, ' ').trim();

                let selector = '';
                if (id) {
                    selector = `#${id}`;
                } else if (name) {
                    selector = `${el.tagName.toLowerCase()}[name="${name}"]`;
                } else if (dataUi) {
                    selector = `[data-ui="${dataUi}"]`;
                } else {
                    selector = `${el.tagName.toLowerCase()}:nth-of-type(${index + 1})`;
                }

                let options = [];
                if (el.tagName.toLowerCase() === 'select') {
                    options = Array.from(el.querySelectorAll('option')).map(o => ({
                        value: o.value,
                        text: o.innerText.trim()
                    })).filter(o => o.value !== '');
                }

                results.push({
                    selector: selector,
                    id: id,
                    name: name,
                    type: el.type || el.tagName.toLowerCase(),
                    tag: el.tagName.toLowerCase(),
                    label: labelText,
                    data_ui: dataUi,
                    placeholder: el.placeholder || '',
                    required: el.required || false,
                    options: options
                });
            });

            return results;
        }""")

        logger.info(f"Extracted {len(raw_fields)} fields from Workable form.")
        return raw_fields
