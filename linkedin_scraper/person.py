import os

from selenium import webdriver
from selenium.common.exceptions import NoSuchElementException
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from .objects import Accomplishment, Contact, Education, Experience, Interest, Scraper


class Person(Scraper):
    __TOP_CARD = "main"
    __WAIT_FOR_ELEMENT_TIMEOUT = 5

    def __init__(
        self,
        linkedin_url=None,
        name=None,
        about=None,
        experiences=None,
        educations=None,
        interests=None,
        accomplishments=None,
        company=None,
        job_title=None,
        contacts=None,
        driver=None,
        get=True,
        scrape=True,
        close_on_complete=True,
        time_to_wait_after_login=0,
    ):
        self.linkedin_url = linkedin_url
        self.name = name
        self.about = about or []
        self.experiences = experiences or []
        self.educations = educations or []
        self.interests = interests or []
        self.accomplishments = accomplishments or []
        self.also_viewed_urls = []
        self.contacts = contacts or []

        if driver is None:
            try:
                if os.getenv("CHROMEDRIVER") is None:
                    driver_path = os.path.join(
                        os.path.dirname(__file__), "drivers/chromedriver"
                    )
                else:
                    driver_path = os.getenv("CHROMEDRIVER")

                driver = webdriver.Chrome(driver_path)
            except:
                driver = webdriver.Chrome()

        if get:
            driver.get(linkedin_url)

        self.driver = driver

        if scrape:
            self.scrape(close_on_complete)

    def add_about(self, about):
        self.about.append(about)

    def add_experience(self, experience):
        self.experiences.append(experience)

    def add_education(self, education):
        self.educations.append(education)

    def add_interest(self, interest):
        self.interests.append(interest)

    def add_accomplishment(self, accomplishment):
        self.accomplishments.append(accomplishment)

    def add_location(self, location):
        self.location = location

    def add_contact(self, contact):
        self.contacts.append(contact)

    def scrape(self, close_on_complete=True):
        if self.is_signed_in():
            self.scrape_logged_in(close_on_complete=close_on_complete)
        else:
            print("you are not logged in!")

    def _click_see_more_by_class_name(self, class_name):
        try:
            _ = WebDriverWait(self.driver, self.__WAIT_FOR_ELEMENT_TIMEOUT).until(
                EC.presence_of_element_located((By.CLASS_NAME, class_name))
            )
            div = self.driver.find_element(By.CLASS_NAME, class_name)
            div.find_element(By.TAG_NAME, "button").click()
        except Exception:
            pass

    def is_open_to_work(self):
        try:
            return "#OPEN_TO_WORK" in self.driver.find_element(
                By.CLASS_NAME, "pv-top-card-profile-picture"
            ).find_element(By.TAG_NAME, "img").get_attribute("title")
        except:
            return False

    def get_experiences(self):
        url = os.path.join(self.linkedin_url, "details/experience")
        self.driver.get(url)
        self.focus()
        main = self.wait_for_element_to_load(by=By.TAG_NAME, name="main")
        self.scroll_to_half()
        self.scroll_to_bottom()
        main_list = self.wait_for_element_to_load(name="pvs-list__container", base=main)
        for position in main_list.find_elements(
            By.CLASS_NAME, "pvs-list__paged-list-item"
        ):
            position = position.find_element(
                By.CSS_SELECTOR, "div[data-view-name='profile-component-entity']"
            )

            # Fix: Handle case where more than 2 elements are returned
            elements = position.find_elements(By.XPATH, "*")
            if len(elements) < 2:
                continue  # Skip if we don't have enough elements

            company_logo_elem = elements[0]
            position_details = elements[1]

            # company elem
            try:
                company_linkedin_url = company_logo_elem.find_element(
                    By.XPATH, "*"
                ).get_attribute("href")
                if not company_linkedin_url:
                    continue
            except NoSuchElementException:
                continue

            # position details
            position_details_list = position_details.find_elements(By.XPATH, "*")
            position_summary_details = (
                position_details_list[0] if len(position_details_list) > 0 else None
            )
            position_summary_text = (
                position_details_list[1] if len(position_details_list) > 1 else None
            )

            if not position_summary_details:
                continue

            outer_positions = position_summary_details.find_element(
                By.XPATH, "*"
            ).find_elements(By.XPATH, "*")

            if len(outer_positions) == 4:
                position_title = (
                    outer_positions[0].find_element(By.TAG_NAME, "span").text
                )
                company = outer_positions[1].find_element(By.TAG_NAME, "span").text
                work_times = outer_positions[2].find_element(By.TAG_NAME, "span").text
                location = outer_positions[3].find_element(By.TAG_NAME, "span").text
            elif len(outer_positions) == 3:
                if "·" in outer_positions[2].text:
                    position_title = (
                        outer_positions[0].find_element(By.TAG_NAME, "span").text
                    )
                    company = outer_positions[1].find_element(By.TAG_NAME, "span").text
                    work_times = (
                        outer_positions[2].find_element(By.TAG_NAME, "span").text
                    )
                    location = ""
                else:
                    position_title = ""
                    company = outer_positions[0].find_element(By.TAG_NAME, "span").text
                    work_times = (
                        outer_positions[1].find_element(By.TAG_NAME, "span").text
                    )
                    location = outer_positions[2].find_element(By.TAG_NAME, "span").text
            else:
                position_title = ""
                company = (
                    outer_positions[0].find_element(By.TAG_NAME, "span").text
                    if outer_positions
                    else ""
                )
                work_times = (
                    outer_positions[1].find_element(By.TAG_NAME, "span").text
                    if len(outer_positions) > 1
                    else ""
                )
                location = ""

            # Safely extract times and duration
            if work_times:
                parts = work_times.split("·")
                times = parts[0].strip() if parts else ""
                duration = parts[1].strip() if len(parts) > 1 else None
            else:
                times = ""
                duration = None

            from_date = " ".join(times.split(" ")[:2]) if times else ""
            to_date = (
                " ".join(times.split(" ")[3:])
                if times and len(times.split(" ")) > 3
                else ""
            )

            if position_summary_text and any(
                element.get_attribute("class") == "pvs-list__container"
                for element in position_summary_text.find_elements(By.XPATH, "*")
            ):
                try:
                    inner_positions = (
                        position_summary_text.find_element(
                            By.CLASS_NAME, "pvs-list__container"
                        )
                        .find_element(By.XPATH, "*")
                        .find_element(By.XPATH, "*")
                        .find_element(By.XPATH, "*")
                        .find_elements(By.CLASS_NAME, "pvs-list__paged-list-item")
                    )
                except NoSuchElementException:
                    inner_positions = []
            else:
                inner_positions = []

            if len(inner_positions) > 1:
                descriptions = inner_positions
                for description in descriptions:
                    try:
                        res = description.find_element(By.TAG_NAME, "a").find_elements(
                            By.XPATH, "*"
                        )
                        position_title_elem = res[0] if len(res) > 0 else None
                        work_times_elem = res[1] if len(res) > 1 else None
                        location_elem = res[2] if len(res) > 2 else None

                        location = (
                            location_elem.find_element(By.XPATH, "*").text
                            if location_elem
                            else None
                        )
                        position_title = (
                            position_title_elem.find_element(By.XPATH, "*")
                            .find_element(By.TAG_NAME, "*")
                            .text
                            if position_title_elem
                            else ""
                        )
                        work_times = (
                            work_times_elem.find_element(By.XPATH, "*").text
                            if work_times_elem
                            else ""
                        )

                        # Safely extract times and duration
                        if work_times:
                            parts = work_times.split("·")
                            times = parts[0].strip() if parts else ""
                            duration = parts[1].strip() if len(parts) > 1 else None
                        else:
                            times = ""
                            duration = None

                        from_date = " ".join(times.split(" ")[:2]) if times else ""
                        to_date = (
                            " ".join(times.split(" ")[3:])
                            if times and len(times.split(" ")) > 3
                            else ""
                        )

                        experience = Experience(
                            position_title=position_title,
                            from_date=from_date,
                            to_date=to_date,
                            duration=duration,
                            location=location,
                            description=description,
                            institution_name=company,
                            linkedin_url=company_linkedin_url,
                        )
                        self.add_experience(experience)
                    except (NoSuchElementException, IndexError):
                        # Skip this description if elements are missing
                        continue
            else:
                description = (
                    position_summary_text.text if position_summary_text else ""
                )

                experience = Experience(
                    position_title=position_title,
                    from_date=from_date,
                    to_date=to_date,
                    duration=duration,
                    location=location,
                    description=description,
                    institution_name=company,
                    linkedin_url=company_linkedin_url,
                )
                self.add_experience(experience)

    def get_educations(self):
        url = os.path.join(self.linkedin_url, "details/education")
        self.driver.get(url)
        self.focus()
        main = self.wait_for_element_to_load(by=By.TAG_NAME, name="main")
        self.scroll_to_half()
        self.scroll_to_bottom()
        main_list = self.wait_for_element_to_load(name="pvs-list__container", base=main)
        for position in main_list.find_elements(
            By.CLASS_NAME, "pvs-list__paged-list-item"
        ):
            try:
                position = position.find_element(
                    By.CSS_SELECTOR, "div[data-view-name='profile-component-entity']"
                )

                # Fix: Handle case where more than 2 elements are returned
                elements = position.find_elements(By.XPATH, "*")
                if len(elements) < 2:
                    continue  # Skip if we don't have enough elements

                institution_logo_elem = elements[0]
                position_details = elements[1]

                # institution elem
                try:
                    institution_linkedin_url = institution_logo_elem.find_element(
                        By.XPATH, "*"
                    ).get_attribute("href")
                except NoSuchElementException:
                    institution_linkedin_url = None

                # position details
                position_details_list = position_details.find_elements(By.XPATH, "*")
                position_summary_details = (
                    position_details_list[0] if len(position_details_list) > 0 else None
                )
                position_summary_text = (
                    position_details_list[1] if len(position_details_list) > 1 else None
                )

                if not position_summary_details:
                    continue

                outer_positions = position_summary_details.find_element(
                    By.XPATH, "*"
                ).find_elements(By.XPATH, "*")

                institution_name = (
                    outer_positions[0].find_element(By.TAG_NAME, "span").text
                    if outer_positions
                    else ""
                )
                degree = (
                    outer_positions[1].find_element(By.TAG_NAME, "span").text
                    if len(outer_positions) > 1
                    else None
                )

                from_date = None
                to_date = None

                if len(outer_positions) > 2:
                    try:
                        times = (
                            outer_positions[2].find_element(By.TAG_NAME, "span").text
                        )

                        if times and "-" in times:
                            split_times = times.split(" ")
                            dash_index = (
                                split_times.index("-") if "-" in split_times else -1
                            )

                            if dash_index > 0:
                                from_date = split_times[dash_index - 1]
                            if dash_index < len(split_times) - 1:
                                to_date = split_times[-1]
                    except (NoSuchElementException, ValueError):
                        from_date = None
                        to_date = None

                description = (
                    position_summary_text.text if position_summary_text else ""
                )

                education = Education(
                    from_date=from_date,
                    to_date=to_date,
                    description=description,
                    degree=degree,
                    institution_name=institution_name,
                    linkedin_url=institution_linkedin_url,
                )
                self.add_education(education)
            except (NoSuchElementException, IndexError):
                # Skip this education entry if elements are missing
                continue

    def get_name_and_location(self):
        top_panel = self.driver.find_element(By.XPATH, "//*[@class='mt2 relative']")
        self.name = top_panel.find_element(By.TAG_NAME, "h1").text
        self.location = top_panel.find_element(
            By.XPATH, "//*[@class='text-body-small inline t-black--light break-words']"
        ).text

    def get_about(self):
        try:
            about = (
                self.driver.find_element(By.ID, "about")
                .find_element(By.XPATH, "..")
                .find_element(By.CLASS_NAME, "display-flex")
                .text
            )
        except NoSuchElementException:
            about = None
        self.about = about

    def scrape_logged_in(self, close_on_complete=True):
        driver = self.driver
        WebDriverWait(driver, self.__WAIT_FOR_ELEMENT_TIMEOUT).until(
            EC.presence_of_element_located(
                (
                    By.TAG_NAME,
                    self.__TOP_CARD,
                )
            )
        )
        self.focus()
        self.wait(5)

        # get name and location
        self.get_name_and_location()

        self.open_to_work = self.is_open_to_work()

        # get about
        self.get_about()
        driver.execute_script(
            "window.scrollTo(0, Math.ceil(document.body.scrollHeight/2));"
        )
        driver.execute_script(
            "window.scrollTo(0, Math.ceil(document.body.scrollHeight/1.5));"
        )

        # get experience
        self.get_experiences()

        # get education
        self.get_educations()

        driver.get(self.linkedin_url)

        # get interest
        try:
            _ = WebDriverWait(driver, self.__WAIT_FOR_ELEMENT_TIMEOUT).until(
                EC.presence_of_element_located(
                    (
                        By.XPATH,
                        "//*[@class='pv-profile-section pv-interests-section artdeco-container-card artdeco-card ember-view']",
                    )
                )
            )
            interestContainer = driver.find_element(
                By.XPATH,
                "//*[@class='pv-profile-section pv-interests-section artdeco-container-card artdeco-card ember-view']",
            )
            for interestElement in interestContainer.find_elements(
                By.XPATH,
                "//*[@class='pv-interest-entity pv-profile-section__card-item ember-view']",
            ):
                interest = Interest(
                    interestElement.find_element(By.TAG_NAME, "h3").text.strip()
                )
                self.add_interest(interest)
        except:
            pass

        # get accomplishment
        try:
            _ = WebDriverWait(driver, self.__WAIT_FOR_ELEMENT_TIMEOUT).until(
                EC.presence_of_element_located(
                    (
                        By.XPATH,
                        "//*[@class='pv-profile-section pv-accomplishments-section artdeco-container-card artdeco-card ember-view']",
                    )
                )
            )
            acc = driver.find_element(
                By.XPATH,
                "//*[@class='pv-profile-section pv-accomplishments-section artdeco-container-card artdeco-card ember-view']",
            )
            for block in acc.find_elements(
                By.XPATH,
                "//div[@class='pv-accomplishments-block__content break-words']",
            ):
                category = block.find_element(By.TAG_NAME, "h3")
                for title in block.find_element(By.TAG_NAME, "ul").find_elements(
                    By.TAG_NAME, "li"
                ):
                    accomplishment = Accomplishment(category.text, title.text)
                    self.add_accomplishment(accomplishment)
        except:
            pass

        # get connections
        try:
            driver.get("https://www.linkedin.com/mynetwork/invite-connect/connections/")
            _ = WebDriverWait(driver, self.__WAIT_FOR_ELEMENT_TIMEOUT).until(
                EC.presence_of_element_located((By.CLASS_NAME, "mn-connections"))
            )
            connections = driver.find_element(By.CLASS_NAME, "mn-connections")
            if connections is not None:
                for conn in connections.find_elements(
                    By.CLASS_NAME, "mn-connection-card"
                ):
                    anchor = conn.find_element(
                        By.CLASS_NAME, "mn-connection-card__link"
                    )
                    url = anchor.get_attribute("href")
                    name = (
                        conn.find_element(By.CLASS_NAME, "mn-connection-card__details")
                        .find_element(By.CLASS_NAME, "mn-connection-card__name")
                        .text.strip()
                    )
                    occupation = (
                        conn.find_element(By.CLASS_NAME, "mn-connection-card__details")
                        .find_element(By.CLASS_NAME, "mn-connection-card__occupation")
                        .text.strip()
                    )

                    contact = Contact(name=name, occupation=occupation, url=url)
                    self.add_contact(contact)
        except:
            connections = None

        if close_on_complete:
            driver.quit()

    @property
    def company(self):
        if self.experiences:
            return (
                self.experiences[0].institution_name
                if self.experiences[0].institution_name
                else None
            )
        else:
            return None

    @property
    def job_title(self):
        if self.experiences:
            return (
                self.experiences[0].position_title
                if self.experiences[0].position_title
                else None
            )
        else:
            return None

    def __repr__(self):
        return f"<Person {self.name}\n\nAbout\n{self.about}\n\nExperience\n{self.experiences}\n\nEducation\n{self.educations}\n\nInterest\n{self.interests}\n\nAccomplishments\n{self.accomplishments}\n\nContacts\n{self.contacts}>"
