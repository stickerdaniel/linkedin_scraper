"""Person/Profile scraper for LinkedIn."""

import logging
from typing import Optional
from urllib.parse import urljoin
from patchright.async_api import Page, TimeoutError as PlaywrightTimeoutError

from .base import BaseScraper
from ..models import Person, Experience, Education, Accomplishment, Interest, Contact
from ..callbacks import ProgressCallback
from ..core.exceptions import AuthenticationError, ScrapingError

logger = logging.getLogger(__name__)


class PersonScraper(BaseScraper):
    """Async scraper for LinkedIn person profiles."""

    def __init__(self, page: Page, callback: Optional[ProgressCallback] = None):
        """
        Initialize person scraper.

        Args:
            page: Patchright page object
            callback: Progress callback
        """
        super().__init__(page, callback)

    async def scrape(self, linkedin_url: str) -> Person:
        """
        Scrape a LinkedIn person profile.

        Args:
            linkedin_url: LinkedIn profile URL

        Returns:
            Person object with all scraped data

        Raises:
            AuthenticationError: If not logged in
            ScrapingError: If scraping fails
        """
        await self.callback.on_start("person", linkedin_url)

        # Normalize URL to ensure trailing slash for correct urljoin behavior
        linkedin_url = linkedin_url.rstrip("/") + "/"

        try:
            # Navigate to profile first (this loads the page with our session)
            await self.navigate_and_wait(linkedin_url)
            await self.callback.on_progress("Navigated to profile", 10)

            # Now check if logged in
            await self.ensure_logged_in()

            # Wait for main content
            await self.page.wait_for_selector("main", timeout=10000)
            await self.wait_and_focus(1)

            # Get name and location
            name, location = await self._get_name_and_location()
            await self.callback.on_progress(f"Got name: {name}", 20)

            # Check open to work
            open_to_work = await self._check_open_to_work()

            # Get about
            about = await self._get_about()
            await self.callback.on_progress("Got about section", 30)

            # Scroll to load content
            await self.scroll_page_to_half()
            await self.scroll_page_to_bottom(pause_time=0.5, max_scrolls=3)

            # Get experiences
            experiences = await self._get_experiences(linkedin_url)
            await self.callback.on_progress(f"Got {len(experiences)} experiences", 60)

            educations = await self._get_educations(linkedin_url)
            await self.callback.on_progress(f"Got {len(educations)} educations", 50)

            interests = await self._get_interests(linkedin_url)
            await self.callback.on_progress(f"Got {len(interests)} interests", 65)

            accomplishments = await self._get_accomplishments(linkedin_url)
            await self.callback.on_progress(
                f"Got {len(accomplishments)} accomplishments", 85
            )

            contacts = await self._get_contacts(linkedin_url)
            await self.callback.on_progress(f"Got {len(contacts)} contacts", 95)

            person = Person(
                linkedin_url=linkedin_url,
                name=name,
                location=location,
                about=about,
                open_to_work=open_to_work,
                experiences=experiences,
                educations=educations,
                interests=interests,
                accomplishments=accomplishments,
                contacts=contacts,
            )

            await self.callback.on_progress("Scraping complete", 100)
            await self.callback.on_complete("person", person)

            return person

        except (AuthenticationError, ScrapingError):
            raise
        except Exception as e:
            await self.callback.on_error(e)
            raise ScrapingError(f"Failed to scrape person profile: {e}") from e

    async def _get_name_and_location(self) -> tuple[str, Optional[str]]:
        """Extract name and location from profile."""
        try:
            # Try h1 first (old layout)
            name = await self.safe_extract_text("h1", default="")
            if not name:
                # New layout: extract from page title ("Name | LinkedIn")
                title = await self.page.title()
                name = title.replace(" | LinkedIn", "").strip() if title else "Unknown"

            # Try old selector first
            location = await self.safe_extract_text(
                ".text-body-small.inline.t-black--light.break-words", default=""
            )
            if not location:
                # New layout: find location <p> in top card section via JS
                location = await self.page.evaluate("""() => {
                    const main = document.querySelector(
                        '[data-view-name="profile-main-level"]'
                    );
                    const section = main ? main.querySelector('section') : null;
                    if (!section) return '';
                    const ps = section.querySelectorAll('p');
                    // Collect candidate p-tags up to "Contact info" boundary
                    const candidates = [];
                    for (const p of ps) {
                        const t = p.textContent.trim();
                        if (!t || t.length > 80 || t.length < 3) continue;
                        if (t.includes('Contact')) break;
                        if (t.includes('·') || t.includes('followers') ||
                            t.includes('http') || t.includes('Premium') ||
                            t.includes('Followed by'))
                            continue;
                        candidates.push(t);
                    }
                    // Last candidate = location (first = headline)
                    return candidates.length >= 2
                        ? candidates[candidates.length - 1] : '';
                }""")
            return name or "Unknown", location if location else None
        except Exception as e:
            logger.warning(f"Error getting name/location: {e}")
            return "Unknown", None

    async def _check_open_to_work(self) -> Optional[bool]:
        """Check if profile has open to work badge."""
        try:
            # Look for open to work indicator
            img_title = await self.get_attribute_safe(
                ".pv-top-card-profile-picture img", "title", default=""
            )
            return "#OPEN_TO_WORK" in img_title.upper()
        except PlaywrightTimeoutError:
            return None
        except Exception as e:
            logger.debug(f"Error checking open to work status: {e}")
            return None

    async def _get_about(self) -> Optional[str]:
        """Extract about section."""
        try:
            about = await self.page.evaluate("""() => {
                const h2s = document.querySelectorAll('h2');
                for (const h2 of h2s) {
                    if (h2.textContent.trim() !== 'About') continue;
                    const section = h2.closest('section');
                    if (!section) continue;
                    // New layout: data-testid="expandable-text-box"
                    const textBox = section.querySelector(
                        '[data-testid="expandable-text-box"]'
                    );
                    if (textBox) return textBox.textContent.trim();
                    // Old layout: span[aria-hidden="true"]
                    const spans = section.querySelectorAll(
                        'span[aria-hidden="true"]'
                    );
                    for (const span of spans) {
                        const t = span.textContent.trim();
                        if (t.length > 20 && t !== 'About') return t;
                    }
                }
                return null;
            }""")
            return about
        except Exception as e:
            logger.debug(f"Error getting about section: {e}")
            return None

    async def _get_experiences(self, base_url: str) -> list[Experience]:
        """Extract experiences from the main profile page Experience section."""
        experiences = []

        try:
            # JS-based extraction: works with both old and new LinkedIn layouts
            # Links come in pairs (logo + detail) sharing the same href.
            # Detail links contain <p> tags with position, company, dates.
            # Nested positions (multiple roles at one company) are grouped
            # by href: the first entry is a company summary, rest are roles.
            exp_data = await self.page.evaluate("""() => {
                const h2s = document.querySelectorAll('h2');
                for (const h2 of h2s) {
                    if (h2.textContent.trim() !== 'Experience') continue;
                    const section = h2.closest('section');
                    if (!section) continue;

                    const links = section.querySelectorAll('a');
                    const seenHrefs = new Map();
                    const grouped = new Map();

                    for (const link of links) {
                        const href = link.getAttribute('href') || '';
                        const text = link.textContent.trim();
                        if (!text) {
                            seenHrefs.set(href, true);
                            continue;
                        }
                        if (!seenHrefs.has(href)) continue;
                        // Skip media/see-more links
                        const dv = link.getAttribute('data-view-name') || '';
                        if (dv && dv !== 'experience-company-logo-click')
                            continue;

                        let parts = Array.from(link.querySelectorAll('p'))
                            .map(p => p.textContent.trim())
                            .filter(t => t);
                        if (parts.length < 1) {
                            parts = link.innerText.split('\\n')
                                .map(p => p.trim()).filter(p => p);
                        }
                        if (parts.length < 2) continue;

                        if (!grouped.has(href)) grouped.set(href, []);
                        grouped.get(href).push(parts);
                    }

                    const results = [];
                    const durOnly = /^\\d+\\s+(yr|yrs|mo|mos)(\\s+\\d+\\s+(yr|yrs|mo|mos))?$/;

                    for (const [href, entries] of grouped) {
                        if (entries.length > 1 && entries[0].length >= 2
                            && durOnly.test(entries[0][1])) {
                            // Nested: first = company summary, rest = roles
                            const companyName = entries[0][0];
                            for (let i = 1; i < entries.length; i++) {
                                const p = entries[i];
                                results.push({
                                    position: p[0],
                                    company: companyName,
                                    dates: p[1] || '',
                                    location: p.length > 2 ? p[2] : '',
                                    companyUrl: href,
                                });
                            }
                        } else {
                            // Single-position entries
                            for (const p of entries) {
                                results.push({
                                    position: p[0],
                                    company: p.length >= 3 ? p[1] : '',
                                    dates: p.length >= 3 ? p[2] :
                                        (p[1] || ''),
                                    location: p.length > 3 ? p[3] : '',
                                    companyUrl: href,
                                });
                            }
                        }
                    }
                    return results;
                }
                return [];
            }""")

            for data in exp_data:
                from_date, to_date, duration = self._parse_work_times(
                    data.get("dates", "")
                )
                experiences.append(
                    Experience(
                        position_title=data["position"],
                        institution_name=data["company"],
                        linkedin_url=data.get("companyUrl"),
                        from_date=from_date,
                        to_date=to_date,
                        duration=duration,
                        location=data.get("location") or None,
                        description=None,
                    )
                )

            if not experiences:
                # Fallback: navigate to details page
                exp_url = urljoin(base_url, "details/experience")
                await self.navigate_and_wait(exp_url)
                await self.page.wait_for_selector("main", timeout=10000)
                await self.wait_and_focus(1.5)
                await self.scroll_page_to_half()
                await self.scroll_page_to_bottom(pause_time=0.5, max_scrolls=5)

                items = []
                main_element = self.page.locator("main")
                if await main_element.count() > 0:
                    list_items = await main_element.locator(
                        "list > listitem, ul > li"
                    ).all()
                    if list_items:
                        items = list_items

                if not items:
                    old_list = self.page.locator(".pvs-list__container").first
                    if await old_list.count() > 0:
                        items = await old_list.locator(
                            ".pvs-list__paged-list-item"
                        ).all()

                for item in items:
                    try:
                        result = await self._parse_experience_item(item)
                        if result:
                            if isinstance(result, list):
                                experiences.extend(result)
                            else:
                                experiences.append(result)
                    except Exception as e:
                        logger.debug(f"Error parsing experience item: {e}")
                        continue

        except Exception as e:
            logger.warning(
                f"Error getting experiences: {e}. The experience section may not be available or the page structure has changed."
            )

        return experiences
    
    async def _parse_main_page_experience(self, item) -> Optional[Experience]:
        """Parse experience from main profile page list item with [logo_link, details_link] structure."""
        try:
            links = await item.locator('a').all()
            if len(links) < 2:
                return None
            
            company_url = await links[0].get_attribute('href')
            detail_link = links[1]
            
            unique_texts = await self._extract_unique_texts_from_element(detail_link)
            
            if len(unique_texts) < 2:
                return None
            
            position_title = unique_texts[0]
            company_name = unique_texts[1]
            work_times = unique_texts[2] if len(unique_texts) > 2 else ""
            
            from_date, to_date, duration = self._parse_work_times(work_times)
            
            return Experience(
                position_title=position_title,
                institution_name=company_name,
                linkedin_url=company_url,
                from_date=from_date,
                to_date=to_date,
                duration=duration,
                location=None,
                description=None,
            )
            
        except Exception as e:
            logger.debug(f"Error parsing main page experience: {e}")
            return None
    
    async def _extract_unique_texts_from_element(self, element) -> list[str]:
        """Extract unique text content from nested elements, avoiding duplicates from parent/child overlap."""
        text_elements = await element.locator('span[aria-hidden="true"], div > span').all()
        
        if not text_elements:
            text_elements = await element.locator('span, div').all()
        
        seen_texts = set()
        unique_texts = []
        
        for el in text_elements:
            text = await el.text_content()
            if text and text.strip():
                text = text.strip()
                if text not in seen_texts and len(text) < 200 and not any(text in t or t in text for t in seen_texts if len(t) > 3):
                    seen_texts.add(text)
                    unique_texts.append(text)
        
        return unique_texts

    async def _parse_experience_item(self, item):
        """Parse experience item. Returns Experience or list for nested positions."""
        try:
            links = await item.locator('a, link').all()
            if len(links) >= 2:
                company_url = await links[0].get_attribute('href')
                detail_link = links[1]
                
                generics = await detail_link.locator('generic, span, div').all()
                texts = []
                for g in generics:
                    text = await g.text_content()
                    if text and text.strip() and len(text.strip()) < 200:
                        texts.append(text.strip())
                
                unique_texts = list(dict.fromkeys(texts))
                
                if len(unique_texts) >= 2:
                    position_title = unique_texts[0]
                    company_name = unique_texts[1]
                    work_times = unique_texts[2] if len(unique_texts) > 2 else ""
                    location = unique_texts[3] if len(unique_texts) > 3 else ""
                    
                    from_date, to_date, duration = self._parse_work_times(work_times)
                    
                    return Experience(
                        position_title=position_title,
                        institution_name=company_name,
                        linkedin_url=company_url,
                        from_date=from_date,
                        to_date=to_date,
                        duration=duration,
                        location=location,
                        description=None,
                    )
            
            entity = item.locator('div[data-view-name="profile-component-entity"]').first
            if await entity.count() == 0:
                return None

            children = await entity.locator("> *").all()
            if len(children) < 2:
                return None

            company_link = children[0].locator("a").first
            company_url = await company_link.get_attribute("href")

            detail_container = children[1]
            detail_children = await detail_container.locator("> *").all()

            if len(detail_children) == 0:
                return None

            has_nested_positions = False
            if len(detail_children) > 1:
                nested_list = await detail_children[1].locator(".pvs-list__container").count()
                has_nested_positions = nested_list > 0

            if has_nested_positions:
                return await self._parse_nested_experience(item, company_url, detail_children)
            else:
                first_detail = detail_children[0]
                nested_elements = await first_detail.locator("> *").all()

                if len(nested_elements) == 0:
                    return None

                span_container = nested_elements[0]
                outer_spans = await span_container.locator("> *").all()

                position_title = ""
                company_name = ""
                work_times = ""
                location = ""

                if len(outer_spans) >= 1:
                    aria_span = outer_spans[0].locator('span[aria-hidden="true"]').first
                    position_title = await aria_span.text_content()
                if len(outer_spans) >= 2:
                    aria_span = outer_spans[1].locator('span[aria-hidden="true"]').first
                    company_name = await aria_span.text_content()
                if len(outer_spans) >= 3:
                    aria_span = outer_spans[2].locator('span[aria-hidden="true"]').first
                    work_times = await aria_span.text_content()
                if len(outer_spans) >= 4:
                    aria_span = outer_spans[3].locator('span[aria-hidden="true"]').first
                    location = await aria_span.text_content()

                from_date, to_date, duration = self._parse_work_times(work_times)

                description = ""
                if len(detail_children) > 1:
                    description = await detail_children[1].inner_text()

                return Experience(
                    position_title=position_title.strip(),
                    institution_name=company_name.strip(),
                    linkedin_url=company_url,
                    from_date=from_date,
                    to_date=to_date,
                    duration=duration,
                    location=location.strip(),
                    description=description.strip() if description else None,
                )

        except Exception as e:
            logger.debug(f"Error parsing experience: {e}")
            return None

    async def _parse_nested_experience(
        self, item, company_url: str, detail_children
    ) -> list[Experience]:
        """
        Parse nested experience positions (multiple roles at the same company).
        Returns a list of Experience objects.
        """
        experiences = []

        try:
            # Get company name from first detail
            first_detail = detail_children[0]
            nested_elements = await first_detail.locator("> *").all()
            if len(nested_elements) == 0:
                return []

            span_container = nested_elements[0]
            outer_spans = await span_container.locator("> *").all()

            # First span is company name for nested positions
            company_name = ""
            if len(outer_spans) >= 1:
                aria_span = outer_spans[0].locator('span[aria-hidden="true"]').first
                company_name = await aria_span.text_content()

            # Get the nested list from detail_children[1]
            nested_container = detail_children[1].locator(".pvs-list__container").first
            nested_items = await nested_container.locator(
                ".pvs-list__paged-list-item"
            ).all()

            for nested_item in nested_items:
                try:
                    # Each nested item has a link with position details
                    link = nested_item.locator("a").first
                    link_children = await link.locator("> *").all()

                    if len(link_children) == 0:
                        continue

                    # Navigate to get the spans
                    first_child = link_children[0]
                    nested_els = await first_child.locator("> *").all()
                    if len(nested_els) == 0:
                        continue

                    spans_container = nested_els[0]
                    position_spans = await spans_container.locator("> *").all()

                    # Extract position details
                    position_title = ""
                    work_times = ""
                    location = ""

                    if len(position_spans) >= 1:
                        aria_span = (
                            position_spans[0].locator('span[aria-hidden="true"]').first
                        )
                        position_title = await aria_span.text_content()
                    if len(position_spans) >= 2:
                        aria_span = (
                            position_spans[1].locator('span[aria-hidden="true"]').first
                        )
                        work_times = await aria_span.text_content()
                    if len(position_spans) >= 3:
                        aria_span = (
                            position_spans[2].locator('span[aria-hidden="true"]').first
                        )
                        location = await aria_span.text_content()

                    # Parse dates
                    from_date, to_date, duration = self._parse_work_times(work_times)

                    # Get description if available
                    description = ""
                    if len(link_children) > 1:
                        description = await link_children[1].inner_text()

                    experiences.append(
                        Experience(
                            position_title=position_title.strip(),
                            institution_name=company_name.strip(),
                            linkedin_url=company_url,
                            from_date=from_date,
                            to_date=to_date,
                            duration=duration,
                            location=location.strip(),
                            description=description.strip() if description else None,
                        )
                    )

                except Exception as e:
                    logger.debug(f"Error parsing nested position: {e}")
                    continue

        except Exception as e:
            logger.debug(f"Error parsing nested experience: {e}")

        return experiences

    def _parse_work_times(
        self, work_times: str
    ) -> tuple[Optional[str], Optional[str], Optional[str]]:
        """
        Parse work times string into from_date, to_date, duration.

        Examples:
        - "2000 - Present · 26 yrs 1 mo" -> ("2000", "Present", "26 yrs 1 mo")
        - "Jan 2020 - Dec 2022 · 2 yrs" -> ("Jan 2020", "Dec 2022", "2 yrs")
        - "2015 - Present" -> ("2015", "Present", None)
        """
        if not work_times:
            return None, None, None

        try:
            # Split by · to separate date range from duration
            parts = work_times.split("·")
            times = parts[0].strip() if len(parts) > 0 else ""
            duration = parts[1].strip() if len(parts) > 1 else None

            # Parse dates - split by " - " to get from and to
            if " - " in times:
                date_parts = times.split(" - ")
                from_date = date_parts[0].strip()
                to_date = date_parts[1].strip() if len(date_parts) > 1 else ""
            else:
                from_date = times
                to_date = ""

            return from_date, to_date, duration
        except Exception as e:
            logger.debug(f"Error parsing work times '{work_times}': {e}")
            return None, None, None

    async def _get_educations(self, base_url: str) -> list[Education]:
        """Extract educations from the main profile page Education section."""
        educations = []

        try:
            # JS-based extraction: works with both old and new LinkedIn layouts
            # Links come in pairs (logo + detail) sharing the same href.
            edu_data = await self.page.evaluate("""() => {
                const h2s = document.querySelectorAll('h2');
                for (const h2 of h2s) {
                    if (h2.textContent.trim() !== 'Education') continue;
                    const section = h2.closest('section');
                    if (!section) continue;

                    const links = section.querySelectorAll(
                        'a[href*="/school/"]'
                    );
                    const seenHrefs = new Map();
                    const results = [];

                    for (const link of links) {
                        const href = link.getAttribute('href') || '';
                        const text = link.textContent.trim();
                        if (!text) {
                            seenHrefs.set(href, true);
                            continue;
                        }
                        if (!seenHrefs.has(href)) continue;

                        let parts = Array.from(link.querySelectorAll('p'))
                            .map(p => p.textContent.trim())
                            .filter(t => t);
                        if (parts.length < 1) {
                            parts = link.innerText.split('\\n')
                                .map(p => p.trim()).filter(p => p);
                        }
                        if (parts.length >= 1) {
                            results.push({
                                institution: parts[0],
                                degree: parts.length >= 3 ? parts[1] : (
                                    parts.length === 2 &&
                                    !parts[1].match(/\\d{4}/)
                                        ? parts[1] : null
                                ),
                                dates: parts.length >= 3 ? parts[2] : (
                                    parts.length === 2 &&
                                    parts[1].match(/\\d{4}/)
                                        ? parts[1] : ''
                                ),
                                schoolUrl: href,
                            });
                        }
                    }
                    return results;
                }
                return [];
            }""")

            for data in edu_data:
                from_date, to_date = self._parse_education_times(
                    data.get("dates", "")
                )
                educations.append(
                    Education(
                        institution_name=data["institution"],
                        degree=data.get("degree"),
                        linkedin_url=data.get("schoolUrl"),
                        from_date=from_date,
                        to_date=to_date,
                        description=None,
                    )
                )

            if not educations:
                # Fallback: navigate to details page
                edu_url = urljoin(base_url, "details/education")
                await self.navigate_and_wait(edu_url)
                await self.page.wait_for_selector("main", timeout=10000)
                await self.wait_and_focus(2)
                await self.scroll_page_to_half()
                await self.scroll_page_to_bottom(pause_time=0.5, max_scrolls=5)

                items = []
                main_element = self.page.locator("main")
                if await main_element.count() > 0:
                    list_items = await main_element.locator(
                        "ul > li, ol > li"
                    ).all()
                    if list_items:
                        items = list_items

                if not items:
                    old_list = self.page.locator(".pvs-list__container").first
                    if await old_list.count() > 0:
                        items = await old_list.locator(
                            ".pvs-list__paged-list-item"
                        ).all()

                for item in items:
                    try:
                        edu = await self._parse_education_item(item)
                        if edu:
                            educations.append(edu)
                    except Exception as e:
                        logger.debug(f"Error parsing education item: {e}")
                        continue

        except Exception as e:
            logger.warning(
                f"Error getting educations: {e}. The education section may not be publicly visible or the page structure has changed."
            )

        return educations
    
    async def _parse_main_page_education(self, item) -> Optional[Education]:
        """Parse education from main profile page list item with [logo_link, details_link] structure."""
        try:
            links = await item.locator('a').all()
            if not links:
                return None
            
            institution_url = await links[0].get_attribute('href')
            detail_link = links[1] if len(links) > 1 else links[0]
            
            unique_texts = await self._extract_unique_texts_from_element(detail_link)
            
            if not unique_texts:
                return None
            
            institution_name = unique_texts[0]
            degree = None
            times = ""
            
            if len(unique_texts) == 3:
                degree = unique_texts[1]
                times = unique_texts[2]
            elif len(unique_texts) == 2:
                second = unique_texts[1]
                if " - " in second or any(c.isdigit() for c in second):
                    times = second
                else:
                    degree = second
            
            from_date, to_date = self._parse_education_times(times)
            
            return Education(
                institution_name=institution_name,
                degree=degree.strip() if degree else None,
                linkedin_url=institution_url,
                from_date=from_date,
                to_date=to_date,
                description=None,
            )
            
        except Exception as e:
            logger.debug(f"Error parsing main page education: {e}")
            return None

    async def _parse_education_item(self, item) -> Optional[Education]:
        """Parse a single education item."""
        try:
            links = await item.locator('a, link').all()
            if len(links) >= 1:
                institution_url = await links[0].get_attribute('href')
                
                detail_link = links[1] if len(links) >= 2 else links[0]
                generics = await detail_link.locator('generic, span, div').all()
                texts = []
                for g in generics:
                    text = await g.text_content()
                    if text and text.strip() and len(text.strip()) < 200:
                        texts.append(text.strip())
                
                unique_texts = list(dict.fromkeys(texts))
                
                if unique_texts:
                    institution_name = unique_texts[0]
                    degree = None
                    times = ""
                    
                    if len(unique_texts) == 3:
                        degree = unique_texts[1]
                        times = unique_texts[2]
                    elif len(unique_texts) == 2:
                        second = unique_texts[1]
                        if " - " in second or second.isdigit() or any(c.isdigit() for c in second):
                            times = second
                        else:
                            degree = second
                    
                    from_date, to_date = self._parse_education_times(times)
                    
                    return Education(
                        institution_name=institution_name,
                        degree=degree.strip() if degree else None,
                        linkedin_url=institution_url,
                        from_date=from_date,
                        to_date=to_date,
                        description=None,
                    )
            
            entity = item.locator('div[data-view-name="profile-component-entity"]').first
            if await entity.count() == 0:
                return None

            children = await entity.locator("> *").all()
            if len(children) < 2:
                return None

            institution_link = children[0].locator("a").first
            institution_url = await institution_link.get_attribute("href")

            detail_container = children[1]
            detail_children = await detail_container.locator("> *").all()

            if len(detail_children) == 0:
                return None

            first_detail = detail_children[0]
            nested_elements = await first_detail.locator("> *").all()

            if len(nested_elements) == 0:
                return None

            span_container = nested_elements[0]
            outer_spans = await span_container.locator("> *").all()

            institution_name = ""
            degree = None
            times = ""

            if len(outer_spans) >= 1:
                aria_span = outer_spans[0].locator('span[aria-hidden="true"]').first
                institution_name = await aria_span.text_content()

            if len(outer_spans) == 3:
                aria_span = outer_spans[1].locator('span[aria-hidden="true"]').first
                degree = await aria_span.text_content()
                aria_span = outer_spans[2].locator('span[aria-hidden="true"]').first
                times = await aria_span.text_content()
            elif len(outer_spans) == 2:
                aria_span = outer_spans[1].locator('span[aria-hidden="true"]').first
                times = await aria_span.text_content()

            from_date, to_date = self._parse_education_times(times)

            description = ""
            if len(detail_children) > 1:
                description = await detail_children[1].inner_text()

            return Education(
                institution_name=institution_name.strip(),
                degree=degree.strip() if degree else None,
                linkedin_url=institution_url,
                from_date=from_date,
                to_date=to_date,
                description=description.strip() if description else None,
            )

        except Exception as e:
            logger.debug(f"Error parsing education: {e}")
            return None

    def _parse_education_times(self, times: str) -> tuple[Optional[str], Optional[str]]:
        """
        Parse education times string into from_date, to_date.

        Examples:
        - "1973 - 1977" -> ("1973", "1977")
        - "2015" -> ("2015", "2015")
        - "" -> (None, None)
        """
        if not times:
            return None, None

        try:
            # Split by " - " or " – " (en-dash) to get from and to dates
            import re as _re

            date_parts = _re.split(r"\s*[-–]\s*", times)
            if len(date_parts) >= 2:
                from_date = date_parts[0].strip()
                to_date = date_parts[1].strip()
            else:
                # Single year
                from_date = times.strip()
                to_date = times.strip()

            return from_date, to_date
        except Exception as e:
            logger.debug(f"Error parsing education times '{times}': {e}")
            return None, None

    async def _get_interests(self, base_url: str) -> list[Interest]:
        """Extract interests from the main profile page Interests section."""
        interests = []

        try:
            # JS-based extraction: works with new (radio) and old (tab) layouts
            interest_data = await self.page.evaluate("""() => {
                const h2s = document.querySelectorAll('h2');
                for (const h2 of h2s) {
                    if (h2.textContent.trim() !== 'Interests') continue;
                    const section = h2.closest('section');
                    if (!section) continue;

                    const results = [];
                    const links = section.querySelectorAll('a[href]');
                    for (const link of links) {
                        const href = link.getAttribute('href') || '';
                        if (!href || href.includes('#')) continue;
                        // Use first <p> tag for clean name, fallback
                        // to first line of innerText
                        const firstP = link.querySelector('p');
                        let name = firstP
                            ? firstP.textContent.trim()
                            : '';
                        if (!name) {
                            const lines = link.innerText.split('\\n')
                                .map(l => l.trim()).filter(l => l);
                            name = lines[0] || '';
                        }
                        // Strip connection degree suffix (e.g. "· 3rd+")
                        name = name.replace(/\\s*·\\s*\\d+\\w*\\+?$/, '');
                        if (name && name.length > 2 && name.length < 150) {
                            results.push({ name: name, url: href });
                        }
                    }
                    return results;
                }
                return [];
            }""")

            for data in interest_data:
                url = data.get("url", "")
                category = "influencer"
                if "/company/" in url:
                    category = "company"
                elif "/school/" in url:
                    category = "school"
                elif "/groups/" in url:
                    category = "group"
                interests.append(
                    Interest(
                        name=data["name"],
                        category=category,
                        linkedin_url=url,
                    )
                )

            if not interests:
                # Fallback: navigate to details page with tab interaction
                interests_url = urljoin(base_url, "details/interests/")
                await self.navigate_and_wait(interests_url)
                await self.page.wait_for_selector("main", timeout=10000)
                await self.wait_and_focus(1.5)

                tabs = await self.page.locator(
                    '[role="tab"], [role="radio"], tab'
                ).all()

                for tab in tabs:
                    try:
                        tab_name = await tab.text_content()
                        if not tab_name:
                            continue
                        tab_name = tab_name.strip()
                        category = self._map_interest_tab_to_category(tab_name)

                        await tab.click()
                        await self.wait_and_focus(0.8)

                        tabpanel = self.page.locator(
                            '[role="tabpanel"], tabpanel'
                        ).first
                        list_items = await tabpanel.locator(
                            "listitem, li, .pvs-list__paged-list-item"
                        ).all()

                        for item in list_items:
                            try:
                                interest = await self._parse_interest_item(
                                    item, category
                                )
                                if interest:
                                    interests.append(interest)
                            except Exception as e:
                                logger.debug(
                                    f"Error parsing interest item: {e}"
                                )
                                continue

                    except Exception as e:
                        logger.debug(f"Error processing interest tab: {e}")
                        continue

        except Exception as e:
            logger.warning(f"Error getting interests: {e}")

        return interests
    
    async def _parse_interest_item(self, item, category: str) -> Optional[Interest]:
        """Parse a single interest item from profile or details page."""
        try:
            link = item.locator("a, link").first
            if await link.count() == 0:
                return None
            href = await link.get_attribute("href")

            unique_texts = await self._extract_unique_texts_from_element(item)
            name = unique_texts[0] if unique_texts else None

            if name and href:
                return Interest(
                    name=name,
                    category=category,
                    linkedin_url=href,
                )
            return None
        except Exception as e:
            logger.debug(f"Error parsing interest: {e}")
            return None

    def _map_interest_tab_to_category(self, tab_name: str) -> str:
        tab_lower = tab_name.lower()
        if "compan" in tab_lower:
            return "company"
        elif "group" in tab_lower:
            return "group"
        elif "school" in tab_lower:
            return "school"
        elif "newsletter" in tab_lower:
            return "newsletter"
        elif "voice" in tab_lower or "influencer" in tab_lower:
            return "influencer"
        else:
            return tab_lower

    async def _get_accomplishments(self, base_url: str) -> list[Accomplishment]:
        accomplishments = []

        accomplishment_sections = [
            ("certifications", "certification"),
            ("honors", "honor"),
            ("publications", "publication"),
            ("patents", "patent"),
            ("courses", "course"),
            ("projects", "project"),
            ("languages", "language"),
            ("organizations", "organization"),
        ]

        for url_path, category in accomplishment_sections:
            try:
                section_url = urljoin(base_url, f"details/{url_path}/")
                await self.navigate_and_wait(section_url)
                await self.page.wait_for_selector("main", timeout=10000)
                await self.wait_and_focus(1)

                nothing_to_see = await self.page.locator(
                    'text="Nothing to see for now"'
                ).count()
                if nothing_to_see > 0:
                    continue

                main_list = self.page.locator(
                    ".pvs-list__container, main ul, main ol"
                ).first
                if await main_list.count() == 0:
                    continue

                items = await main_list.locator(".pvs-list__paged-list-item").all()
                if not items:
                    items = await main_list.locator("> li").all()

                seen_titles = set()
                for item in items:
                    try:
                        accomplishment = await self._parse_accomplishment_item(
                            item, category
                        )
                        if accomplishment and accomplishment.title not in seen_titles:
                            seen_titles.add(accomplishment.title)
                            accomplishments.append(accomplishment)
                    except Exception as e:
                        logger.debug(f"Error parsing {category} item: {e}")
                        continue

            except Exception as e:
                logger.debug(f"Error getting {category}s: {e}")
                continue

        return accomplishments

    async def _parse_accomplishment_item(
        self, item, category: str
    ) -> Optional[Accomplishment]:
        try:
            entity = item.locator(
                'div[data-view-name="profile-component-entity"]'
            ).first
            if await entity.count() > 0:
                spans = await entity.locator('span[aria-hidden="true"]').all()
            else:
                spans = await item.locator('span[aria-hidden="true"]').all()

            title = ""
            issuer = ""
            issued_date = ""
            credential_id = ""

            for i, span in enumerate(spans[:5]):
                text = await span.text_content()
                if not text:
                    continue
                text = text.strip()

                if len(text) > 500:
                    continue

                if i == 0:
                    title = text
                elif "Issued by" in text:
                    parts = text.split("·")
                    issuer = parts[0].replace("Issued by", "").strip()
                    if len(parts) > 1:
                        issued_date = parts[1].strip()
                elif "Issued " in text and not issued_date:
                    issued_date = text.replace("Issued ", "")
                elif "Credential ID" in text:
                    credential_id = text.replace("Credential ID ", "")
                elif i == 1 and not issuer:
                    issuer = text
                elif (
                    any(
                        month in text
                        for month in [
                            "Jan",
                            "Feb",
                            "Mar",
                            "Apr",
                            "May",
                            "Jun",
                            "Jul",
                            "Aug",
                            "Sep",
                            "Oct",
                            "Nov",
                            "Dec",
                        ]
                    )
                    and not issued_date
                ):
                    if "·" in text:
                        parts = text.split("·")
                        issued_date = parts[0].strip()
                    else:
                        issued_date = text

            link = item.locator('a[href*="credential"], a[href*="verify"]').first
            credential_url = (
                await link.get_attribute("href") if await link.count() > 0 else None
            )

            if not title or len(title) > 200:
                return None

            return Accomplishment(
                category=category,
                title=title,
                issuer=issuer if issuer else None,
                issued_date=issued_date if issued_date else None,
                credential_id=credential_id if credential_id else None,
                credential_url=credential_url,
            )

        except Exception as e:
            logger.debug(f"Error parsing accomplishment: {e}")
            return None

    async def _get_contacts(self, base_url: str) -> list[Contact]:
        """Extract contact info from the contact-info overlay dialog."""
        contacts = []

        try:
            contact_url = urljoin(base_url, "overlay/contact-info/")
            await self.navigate_and_wait(contact_url)
            await self.wait_and_focus(1)

            dialog = self.page.locator('dialog, [role="dialog"]').first
            if await dialog.count() == 0:
                logger.warning("Contact info dialog not found")
                return contacts

            # JS-based extraction using innerText parsing
            # The dialog text follows a pattern: "label\nvalue\nlabel\nvalue..."
            contact_data = await dialog.evaluate("""(el) => {
                const results = [];
                const links = el.querySelectorAll('a[href]');
                for (const link of links) {
                    const href = link.getAttribute('href') || '';
                    const text = link.textContent.trim();
                    if (!text || href.includes('premium')) continue;
                    results.push({ href, text });
                }
                // Parse innerText for non-link contacts (birthday, phone, etc.)
                const fullText = el.innerText;
                const birthdayMatch = fullText.match(
                    /Birthday\\n+([A-Z][a-z]+ \\d{1,2})/
                );
                if (birthdayMatch) {
                    results.push({
                        type: 'birthday', value: birthdayMatch[1]
                    });
                }
                const phoneMatch = fullText.match(
                    /Phone\\n+([\\d\\s\\-+()]+)/
                );
                if (phoneMatch) {
                    results.push({
                        type: 'phone', value: phoneMatch[1].trim()
                    });
                }
                const addressMatch = fullText.match(
                    /Address\\n+(.+?)(?:\\n|$)/
                );
                if (addressMatch) {
                    results.push({
                        type: 'address', value: addressMatch[1].trim()
                    });
                }
                return results;
            }""")

            for data in contact_data:
                if "type" in data:
                    # Pre-typed entries (birthday, phone, address)
                    contacts.append(
                        Contact(type=data["type"], value=data["value"])
                    )
                    continue

                href = data.get("href", "")
                text = data.get("text", "")
                label_match = None
                if "(" in text and ")" in text:
                    import re
                    m = re.search(r"\(([^)]+)\)", text)
                    if m:
                        label_match = m.group(1)
                        text = text.replace(m.group(0), "").strip()

                if "/in/" in href:
                    contacts.append(
                        Contact(
                            type="linkedin", value=href, label=label_match
                        )
                    )
                elif "mailto:" in href:
                    contacts.append(
                        Contact(
                            type="email",
                            value=href.replace("mailto:", ""),
                            label=label_match,
                        )
                    )
                elif href.startswith("http"):
                    contacts.append(
                        Contact(
                            type="website", value=text, label=label_match
                        )
                    )

        except Exception as e:
            logger.warning(f"Error getting contacts: {e}")

        return contacts
    
    def _map_contact_heading_to_type(self, heading: str) -> Optional[str]:
        """Map contact section heading to contact type."""
        heading = heading.lower()
        if "profile" in heading:
            return "linkedin"
        elif "website" in heading:
            return "website"
        elif "email" in heading:
            return "email"
        elif "phone" in heading:
            return "phone"
        elif "twitter" in heading or "x.com" in heading:
            return "twitter"
        elif "birthday" in heading:
            return "birthday"
        elif "address" in heading:
            return "address"
        return None
