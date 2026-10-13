"""Workday ATS Multi-Step Form Parser and Navigator."""
import time
import logging
from typing import List, Dict, Any, Optional
from playwright.sync_api import Page
from .base import BaseParser

logger = logging.getLogger(__name__)


class WorkdayParser(BaseParser):
    """Specialized parser for Workday's data-automation-id architecture and multi-step wizards."""

    def __init__(self):
        super().__init__("workday")

    def extract_job_metadata(self, page: Page) -> Dict[str, Any]:
        """Extract job title and company from Workday pages."""
        title = ""
        company = ""

        # Title extraction from Workday headers
        for selector in [
            "h2[data-automation-id='jobPostingHeader']",
            "[data-automation-id='jobPostingHeader']",
            "h1",
            "h2",
            ".css-12nw507",
        ]:
            if page.locator(selector).count() > 0:
                title = page.locator(selector).first.inner_text().strip()
                break

        # Company extraction
        for selector in ["[data-automation-id='companyName']", ".header-company-name", "meta[property='og:site_name']"]:
            if page.locator(selector).count() > 0:
                el = page.locator(selector).first
                company = el.get_attribute("content") or el.inner_text().strip()
                break

        if not title:
            title = page.title()

        return {"title": title, "company": company, "platform": "workday"}

    def prepare_application_view(self, page: Page) -> bool:
        """Handle Workday 'Apply' landing page and 'Apply Manually' prompt if present."""
        try:
            # Check for initial "Apply" button on job posting overview
            apply_btn = page.locator("a[data-automation-id='adventureButton'], button:has-text('Apply'), a:has-text('Apply')").first
            if apply_btn.count() > 0 and apply_btn.is_visible():
                logger.info("Found initial Workday 'Apply' button. Clicking...")
                apply_btn.click()
                page.wait_for_timeout(2000)

            # Check for "Apply Manually" option
            apply_manually = page.locator("a[data-automation-id='applyManually'], button:has-text('Apply Manually')").first
            if apply_manually.count() > 0 and apply_manually.is_visible():
                logger.info("Found 'Apply Manually' button. Clicking...")
                apply_manually.click()
                page.wait_for_timeout(3000)

            # Check if sign-in or create account is blocking
            signin_modal = page.locator("[data-automation-id='signInContainer'], input[data-automation-id='email']").first
            if signin_modal.count() > 0 and page.locator("button:has-text('Sign In')").count() > 0:
                logger.info("Workday account login screen detected.")

            return True
        except Exception as e:
            logger.warning(f"Error preparing Workday application view: {e}")
            return False

    def extract_fields(self, page: Page, prepare: bool = True) -> List[Dict[str, Any]]:
        """Extract interactive fields from the current active Workday page step."""
        if prepare:
            self.prepare_application_view(page)

        raw_fields = page.evaluate("""() => {
            const results = [];
            
            // Workday standard input controls
            const inputs = document.querySelectorAll(
                'input, textarea, select, [data-automation-id="select-box"], button[aria-haspopup="listbox"]'
            );

            inputs.forEach((el, index) => {
                if (el.type === 'hidden' || el.type === 'submit') return;

                const autoId = el.getAttribute('data-automation-id') || '';
                const ariaLabel = el.getAttribute('aria-label') || '';
                
                // Find label
                let labelText = '';
                if (el.id) {
                    const labelEl = document.querySelector(`label[for="${el.id}"]`);
                    if (labelEl) labelText = labelEl.innerText;
                }
                if (!labelText) {
                    const parentGroup = el.closest('[data-automation-id*="formField"], .css-1q209eg, fieldset');
                    if (parentGroup) {
                        const lbl = parentGroup.querySelector('label, legend');
                        if (lbl) labelText = lbl.innerText;
                    }
                }
                if (!labelText) {
                    labelText = ariaLabel || autoId || el.placeholder || el.name || '';
                }

                // Clean label
                labelText = labelText.replace(/\\*|\\n/g, ' ').trim();

                // Build unique selector
                let selector = '';
                if (autoId) {
                    selector = `[data-automation-id="${autoId}"]`;
                } else if (el.id) {
                    selector = `#${el.id}`;
                } else if (el.name) {
                    selector = `${el.tagName.toLowerCase()}[name="${el.name}"]`;
                } else {
                    selector = `${el.tagName.toLowerCase()}:nth-of-type(${index + 1})`;
                }

                // Extract options if select or Workday combobox
                let options = [];
                if (el.tagName.toLowerCase() === 'select') {
                    options = Array.from(el.querySelectorAll('option')).map(o => ({
                        value: o.value,
                        text: o.innerText.trim()
                    })).filter(o => o.value !== '');
                }

                results.push({
                    selector: selector,
                    id: el.id || '',
                    name: el.name || autoId,
                    type: el.type || el.tagName.toLowerCase(),
                    tag: el.tagName.toLowerCase(),
                    label: labelText,
                    automation_id: autoId,
                    placeholder: el.placeholder || '',
                    required: el.required || el.getAttribute('aria-required') === 'true',
                    options: options
                });
            });

            return results;
        }""")

        logger.info(f"Extracted {len(raw_fields)} fields from active Workday step.")
        return raw_fields

    def has_next_step(self, page: Page) -> bool:
        """Check if Workday has a 'Next' / 'Save and Continue' button to advance the wizard."""
        next_selectors = [
            "button[data-automation-id='bottom-navigation-next-button']",
            "button:has-text('Save and Continue')",
            "button:has-text('Next')",
        ]
        for sel in next_selectors:
            loc = page.locator(sel).first
            if loc.count() > 0 and loc.is_visible():
                return True
        return False

    def click_next_step(self, page: Page) -> bool:
        """Click next step in Workday multi-step wizard and wait for page hydration."""
        next_selectors = [
            "button[data-automation-id='bottom-navigation-next-button']",
            "button:has-text('Save and Continue')",
            "button:has-text('Next')",
        ]
        for sel in next_selectors:
            loc = page.locator(sel).first
            if loc.count() > 0 and loc.is_visible():
                logger.info(f"Advancing Workday wizard via {sel}...")
                loc.click()
                page.wait_for_timeout(3000)
                return True
        return False
