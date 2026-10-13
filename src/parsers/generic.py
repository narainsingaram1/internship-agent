"""Universal Generic Form Parser with auto-transition from job description to application form."""
import time
import logging
from typing import List, Dict, Any
from playwright.sync_api import Page
from .base import BaseParser

logger = logging.getLogger(__name__)


class GenericParser(BaseParser):
    def __init__(self):
        super().__init__("generic")

    def dismiss_overlays(self, page: Page):
        """Dismiss common cookie banners, GDPR prompts, and modal backdrops."""
        overlay_selectors = [
            "[data-ui='cookie-consent-accept']",
            "button:has-text('Accept all')",
            "button:has-text('Accept All')",
            "button:has-text('Accept all cookies')",
            "button:has-text('Accept Cookies')",
            "button:has-text('Accept')",
            "button:has-text('I agree')",
            "button:has-text('Agree')",
            "button:has-text('Allow all')",
            "#onetrust-accept-btn-handler",
            ".cc-accept",
            "button[id*='cookie']",
            "button[class*='cookie']",
        ]
        for sel in overlay_selectors:
            try:
                btn = page.locator(sel).first
                if btn.count() > 0 and btn.is_visible():
                    logger.info(f"Dismissing overlay/cookie banner: {sel}")
                    btn.click(timeout=1000)
                    page.wait_for_timeout(500)
                    break
            except Exception:
                pass

    def ensure_application_view(self, page: Page):
        """If on a job description overview page, automatically find and click the 'Apply' button."""
        self.dismiss_overlays(page)

        # Count visible non-hidden inputs
        input_count = page.evaluate("""() => {
            return document.querySelectorAll('input:not([type="hidden"]), textarea, select').length;
        }""")

        # If already on a form with 3+ fields, we are good
        if input_count >= 3:
            return

        logger.info(f"Page currently has {input_count} interactive fields. Checking for 'Apply' button...")

        apply_selectors = [
            "[data-ui='apply-button']",
            "[data-ui='application-form-tab']",
            "button:has-text('Apply for this job')",
            "a:has-text('Apply for this job')",
            "button:has-text('Apply Now')",
            "a:has-text('Apply Now')",
            "button:has-text('Apply')",
            "a:has-text('Apply')",
            "a[href*='/apply']",
            "button:has-text('Start Application')",
            "a:has-text('Start Application')",
        ]

        for sel in apply_selectors:
            try:
                loc = page.locator(sel).first
                if loc.count() > 0 and loc.is_visible():
                    logger.info(f"Clicking application transition button: {sel}")
                    loc.click(timeout=3000)
                    page.wait_for_timeout(2500)
                    return
            except Exception as e:
                logger.debug(f"Could not click {sel}: {e}")

        # Try scrolling down to trigger lazy loading of form
        try:
            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            page.wait_for_timeout(1000)
            page.evaluate("window.scrollTo(0, 0)")
        except Exception:
            pass

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

    def extract_fields(self, page: Page, prepare: bool = True) -> List[Dict[str, Any]]:
        """Walk all visible interactive form controls across main document and accessible iframes."""
        if prepare:
            self.ensure_application_view(page)
        self.dismiss_overlays(page)

        raw_fields = page.evaluate(r"""() => {
            const results = [];
            
            function processDocument(doc, prefix = '') {
                if (!doc) return;
                const inputs = doc.querySelectorAll('input, select, textarea, [role="combobox"]');

                inputs.forEach((el, index) => {
                    if (el.type === 'hidden' || el.type === 'submit' || el.type === 'button') return;

                    // Visibility check
                    try {
                        const style = window.getComputedStyle(el);
                        if (style.display === 'none' && !['file', 'radio', 'checkbox'].includes(el.type)) return;
                        if (style.visibility === 'hidden' && !['file', 'radio', 'checkbox'].includes(el.type)) return;
                    } catch (e) {}

                    // Find label
                    let labelText = '';
                    const ariaLabelledBy = el.getAttribute('aria-labelledby');
                    if (ariaLabelledBy) {
                        const lblEl = doc.getElementById(ariaLabelledBy);
                        if (lblEl && lblEl.innerText.trim()) {
                            labelText = lblEl.innerText.trim();
                        }
                    }
                    if (!labelText && el.getAttribute('aria-label')) {
                        labelText = el.getAttribute('aria-label').trim();
                    }
                    if (!labelText && el.id) {
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

                    // Check group container for fieldsets / questions
                    let groupTitle = '';
                    let curr = el.parentElement;
                    while (curr && curr.tagName !== 'FORM' && curr.tagName !== 'BODY') {
                        const headings = curr.querySelectorAll('legend, strong, h3, h4, [data-ui*="label"], [class*="label"], [data-ui*="title"]');
                        for (let h of headings) {
                            let txt = h.innerText.replace(/[\n\r]+/g, ' ').trim();
                            if (txt && txt !== '*' && txt !== '•' && txt.length > 2) {
                                groupTitle = txt;
                                break;
                            }
                        }
                        if (groupTitle) break;
                        curr = curr.parentElement;
                    }

                    let optionText = '';
                    if (el.type === 'radio' || el.type === 'checkbox') {
                        const lbl = (el.id ? doc.querySelector(`label[for="${el.id}"]`) : null) || el.closest('label');
                        if (lbl) optionText = lbl.innerText.replace(/[\n\r]+/g, ' ').trim();
                    }

                    let finalLabel = labelText;
                    if (el.type === 'radio' || el.type === 'checkbox') {
                        if (groupTitle && optionText) {
                            finalLabel = `${groupTitle} -> Option: ${optionText}`;
                        } else {
                            finalLabel = optionText || groupTitle || finalLabel;
                        }
                    } else if (!finalLabel || finalLabel.toLowerCase().includes('select an option')) {
                        finalLabel = groupTitle || el.placeholder || el.name || el.id || '';
                    }

                    finalLabel = finalLabel.replace(/[\n\r]+/g, ' ').replace(/^\*\s*/, '').trim();

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
                        label: finalLabel,
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
                } catch (e) {}
            });

            return results;
        }""")

        logger.info(f"Extracted {len(raw_fields)} fields via Universal Generic Parser.")
        return raw_fields
