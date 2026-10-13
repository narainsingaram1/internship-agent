"""iCIMS ATS Form Parser with iframe handling and multi-step flow."""
import time
import logging
from typing import List, Dict, Any, Optional
from playwright.sync_api import Page, FrameLocator
from .base import BaseParser

logger = logging.getLogger(__name__)


class ICIMSParser(BaseParser):
    """Specialized parser for iCIMS iframe architecture and profile forms."""

    def __init__(self):
        super().__init__("icims")

    def get_frame(self, page: Page):
        """Locate iCIMS content iframe if present, otherwise return standard page."""
        iframe_locators = ["#icims_content_iframe", "iframe[name='icims_content_iframe']", "iframe[src*='icims']"]
        for sel in iframe_locators:
            if page.locator(sel).count() > 0:
                logger.info(f"Targeting iCIMS iframe: {sel}")
                return page.frame_locator(sel)
        return page

    def extract_job_metadata(self, page: Page) -> Dict[str, Any]:
        """Extract job title and company from iCIMS pages or nested iframes."""
        title = ""
        company = ""

        # Check in iframe first, then main page
        frame = self.get_frame(page)
        for selector in [".iCIMS_Header h1", ".iCIMS_JobHeader h1", "h1.title", "h1"]:
            try:
                loc = frame.locator(selector).first
                if loc.count() > 0:
                    title = loc.inner_text().strip()
                    break
            except Exception:
                pass

        if not title:
            title = page.title()

        return {"title": title, "company": company, "platform": "icims"}

    def prepare_application_view(self, page: Page) -> bool:
        """Handle iCIMS 'Apply for this job online' initial click."""
        frame = self.get_frame(page)
        try:
            apply_btn = frame.locator(
                "a:has-text('Apply for this job online'), a.iCIMS_Anchor:has-text('Apply'), a:has-text('Apply')"
            ).first
            if apply_btn.count() > 0 and apply_btn.is_visible():
                logger.info("Found iCIMS 'Apply online' button. Clicking...")
                apply_btn.click()
                page.wait_for_timeout(3000)
                return True
        except Exception as e:
            logger.warning(f"Error clicking iCIMS start button: {e}")
        return False

    def extract_fields(self, page: Page) -> List[Dict[str, Any]]:
        """Extract interactive fields from active iCIMS form inside frame or page."""
        self.prepare_application_view(page)
        frame = self.get_frame(page)

        # JavaScript evaluation across the active document/frame
        raw_fields = page.evaluate("""() => {
            const results = [];
            // Target iframe document if present
            let doc = document;
            const iframe = document.querySelector('#icims_content_iframe, iframe[name="icims_content_iframe"]');
            if (iframe && iframe.contentDocument) {
                doc = iframe.contentDocument;
            }

            const inputs = doc.querySelectorAll('input, select, textarea');
            inputs.forEach((el, index) => {
                if (el.type === 'hidden' || el.type === 'submit' || el.type === 'button') return;

                let labelText = '';
                if (el.id) {
                    const labelEl = doc.querySelector(`label[for="${el.id}"]`);
                    if (labelEl) labelText = labelEl.innerText;
                }
                if (!labelText) {
                    const parent = el.closest('.iCIMS_JobQuestion, .form-group, tr');
                    if (parent) {
                        const lbl = parent.querySelector('label, th, td.iCIMS_FieldLabel');
                        if (lbl) labelText = lbl.innerText;
                    }
                }
                if (!labelText) {
                    labelText = el.getAttribute('aria-label') || el.placeholder || el.name || '';
                }

                labelText = labelText.replace(/\\*|\\n/g, ' ').trim();

                let options = [];
                if (el.tagName.toLowerCase() === 'select') {
                    options = Array.from(el.querySelectorAll('option')).map(o => ({
                        value: o.value,
                        text: o.innerText.trim()
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
                    label: labelText,
                    placeholder: el.placeholder || '',
                    required: el.required || false,
                    options: options
                });
            });

            return results;
        }""")

        logger.info(f"Extracted {len(raw_fields)} fields from iCIMS form.")
        return raw_fields
