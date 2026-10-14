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

    def extract_fields(self, page: Page, prepare: bool = True) -> List[Dict[str, Any]]:
        """Extract all interactive form fields from Workable application form."""
        if prepare:
            self.ensure_application_view(page)
        if prepare:
            self.dismiss_overlays(page)

        # Ensure scroll to load lazy-rendered custom questions
        try:
            page.evaluate("window.scrollTo(0, document.body.scrollHeight / 2)")
            page.wait_for_timeout(400)
            page.evaluate("window.scrollTo(0, 0)")
        except Exception:
            pass

        raw_fields = page.evaluate(r"""() => {
            const results = [];
            const inputs = document.querySelectorAll('input, select, textarea');

            inputs.forEach((el, index) => {
                if (el.type === 'hidden' || el.type === 'submit') return;

                const name = el.name || '';
                const id = el.id || '';
                const dataUi = el.getAttribute('data-ui') || '';
                const ariaLabel = el.getAttribute('aria-label') || '';
                const ariaLabelledBy = el.getAttribute('aria-labelledby') || '';

                // Ignore redundant non-id inputs that duplicate interactive comboboxes
                if (!id && name && document.querySelector(`input[id*="${name}"]`)) {
                    return;
                }

                let labelText = '';

                // 1. Check aria-labelledby
                if (ariaLabelledBy) {
                    const lblEl = document.getElementById(ariaLabelledBy);
                    if (lblEl && lblEl.innerText.trim()) {
                        labelText = lblEl.innerText.trim();
                    }
                }

                // 2. Check aria-label
                if (!labelText && ariaLabel) {
                    labelText = ariaLabel.trim();
                }

                // 3. Check label[for="id"]
                if (!labelText && id) {
                    const lbl = document.querySelector(`label[for="${id}"]`);
                    if (lbl && lbl.innerText.trim()) {
                        labelText = lbl.innerText.trim();
                    }
                }

                // 4. Check closest label
                if (!labelText) {
                    const parentLabel = el.closest('label');
                    if (parentLabel && parentLabel.innerText.trim()) {
                        labelText = parentLabel.innerText.trim();
                    }
                }

                // 5. Check parent question/field container
                let groupTitle = '';
                let curr = el.parentElement;
                while (curr && curr.tagName !== 'FORM' && curr.tagName !== 'BODY') {
                    const strongs = curr.querySelectorAll('strong, h3, h4, legend, [data-ui*="label"], [class*="label"], [data-ui*="title"]');
                    for (let s of strongs) {
                        let txt = s.innerText.replace(/[\n\r]+/g, ' ').trim();
                        if (txt && txt !== '*' && txt !== '•' && txt.length > 2) {
                            groupTitle = txt;
                            break;
                        }
                    }
                    if (groupTitle) break;
                    curr = curr.parentElement;
                }

                // If this is a radio or checkbox, distinguish between group question and option
                let optionText = '';
                if (el.type === 'radio' || el.type === 'checkbox') {
                    const lbl = (id ? document.querySelector(`label[for="${id}"]`) : null) || el.closest('label');
                    if (lbl) {
                        optionText = lbl.innerText.replace(/[\n\r]+/g, ' ').trim();
                    }
                }

                // Standard field name overrides (avoid parent personal info section overwriting city/country/zip)
                const standardKey = (id || name || el.placeholder || '').toLowerCase();
                let finalLabel = labelText;
                if (['city', 'postcode', 'postal', 'zip', 'zipcode', 'country', 'phone'].includes(standardKey)) {
                    finalLabel = standardKey.charAt(0).toUpperCase() + standardKey.slice(1);
                } else if (el.type === 'radio' || el.type === 'checkbox') {
                    if (groupTitle && optionText) {
                        finalLabel = `${groupTitle} -> Option: ${optionText}`;
                    } else {
                        finalLabel = optionText || groupTitle || finalLabel;
                    }
                } else if (!finalLabel || finalLabel === 'Select an option…') {
                    finalLabel = groupTitle || el.placeholder || name || id || '';
                }

                finalLabel = finalLabel.replace(/[\n\r]+/g, ' ').replace(/^\*\s*/, '').trim();

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
                    label: finalLabel,
                    group_title: groupTitle,
                    option_text: optionText,
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
