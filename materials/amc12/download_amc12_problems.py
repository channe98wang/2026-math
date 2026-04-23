#!/usr/bin/env python3
"""
AMC 12 Problem Downloader

Downloads AMC 12 problems and solutions from Art of Problem Solving Wiki.
Saves each problem in .org format for easy processing.

Usage:
    python download_amc12_problems.py --version 12B --year 2010 --start 1 --end 25
    python download_amc12_problems.py -v 12A -y 2015 -s 1 -e 25
"""

import argparse
import requests
from bs4 import BeautifulSoup, NavigableString
import os
import time
import re
from urllib.parse import urljoin, urlparse
import logging
from typing import Optional

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('amc12_download.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

class AMC12Downloader:
    def __init__(
        self,
        timeout=120,
        fetch_method: str = "auto",
        headless: bool = True,
        playwright_channel: str = "chromium",
        playwright_user_data_dir: Optional[str] = None,
        curl_impersonate: str = "chrome120",
        cookies_from: str = "none",
        cookie: Optional[str] = None,
        user_agent: Optional[str] = None,
    ):
        self.base_url = "https://artofproblemsolving.com/wiki/index.php"
        self.session = requests.Session()
        self.timeout = timeout
        self.fetch_method = fetch_method
        self.headless = headless
        self.playwright_channel = playwright_channel
        self.playwright_user_data_dir = playwright_user_data_dir
        self.curl_impersonate = curl_impersonate
        self.cookies_from = cookies_from

        # Set a modern user agent (AoPS is now protected by bot-detection/Cloudflare).
        # If you use a clearance cookie from your browser, it may help to match the same UA.
        self.user_agent = user_agent or (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/131.0.0.0 Safari/537.36"
        )
        self.session.headers.update(
            {
                "User-Agent": self.user_agent,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9",
                "Cache-Control": "no-cache",
                "Pragma": "no-cache",
                "Upgrade-Insecure-Requests": "1",
            }
        )

        if cookie:
            self._apply_cookie_header(cookie)

        if cookies_from and cookies_from.lower() != "none":
            self._try_load_browser_cookies(cookies_from.lower())

    def _apply_cookie_header(self, cookie: str) -> None:
        """
        Apply a raw Cookie header string like:
          "cf_clearance=...; other=value"
        """
        cookie = (cookie or "").strip()
        if not cookie:
            return
        # requests will merge cookies; but setting header is also fine.
        self.session.headers["Cookie"] = cookie

    def _try_load_browser_cookies(self, cookies_from: str) -> None:
        """
        Attempt to load cookies for artofproblemsolving.com from a local browser profile.
        This can reuse Cloudflare clearance cookies after you have visited AoPS in that browser.
        """
        domain = "artofproblemsolving.com"
        try:
            import browser_cookie3  # type: ignore
        except Exception as e:
            logger.warning(
                "Browser cookie loading requested, but browser-cookie3 is not installed. "
                "Install it with: py -m pip install browser-cookie3. "
                f"Import error: {e}"
            )
            return

        def _load(browser_name: str):
            fn = getattr(browser_cookie3, browser_name, None)
            if not callable(fn):
                raise ValueError(f"Unsupported browser for cookies: {browser_name}")
            return fn(domain_name=domain)

        try:
            if cookies_from == "auto":
                for candidate in ("chrome", "edge", "brave", "chromium"):
                    try:
                        jar = _load(candidate)
                        self.session.cookies.update(jar)
                        logger.info(f"Loaded browser cookies from {candidate} for {domain}")
                        return
                    except Exception:
                        continue
                logger.warning(f"Failed to load cookies from any supported browser for {domain}")
                return

            jar = _load(cookies_from)
            self.session.cookies.update(jar)
            logger.info(f"Loaded browser cookies from {cookies_from} for {domain}")
        except Exception as e:
            logger.warning(f"Failed to load browser cookies ({cookies_from}) for {domain}: {e}")

    @staticmethod
    def _looks_like_cloudflare_challenge(html: str) -> bool:
        """Heuristic: detect Cloudflare 'Just a moment...' bot challenge pages."""
        if not html:
            return False
        lowered = html.lower()
        return (
            "just a moment" in lowered
            or "cf-chl" in lowered
            or "cloudflare" in lowered
            or "attention required" in lowered
        )

    def _fetch_html_with_requests(self, url: str) -> str:
        # Prime cookies (helps with some WAF setups; harmless otherwise).
        try:
            self.session.get("https://artofproblemsolving.com/", timeout=min(30, self.timeout))
        except Exception:
            pass

        response = self.session.get(url, timeout=self.timeout)
        body = response.text or ""
        # Don't raise yet; we want the body to check for Cloudflare challenge.
        if response.status_code >= 400:
            hint = ""
            if self._looks_like_cloudflare_challenge(body):
                hint = (
                    " (Cloudflare challenge detected; try --fetch cloudscraper, curl_cffi, or playwright --headful)"
                )
            raise requests.HTTPError(
                f"{response.status_code} Client Error: {response.reason} for url: {response.url}{hint}",
                response=response,
            )
        return body

    def _fetch_html_with_cloudscraper(self, url: str) -> str:
        """
        Fetch HTML using cloudscraper, which automatically bypasses Cloudflare challenges.
        This is often the easiest method and doesn't require manual cookie extraction.
        """
        try:
            import cloudscraper  # type: ignore
        except Exception as e:
            raise RuntimeError(
                "cloudscraper is required for --fetch cloudscraper.\n"
                "Install it with:\n"
                "  py -m pip install cloudscraper\n"
                f"Import error: {e}"
            )

        scraper = cloudscraper.create_scraper()
        try:
            response = scraper.get(url, timeout=self.timeout)
            response.raise_for_status()
            body = response.text or ""
            if self._looks_like_cloudflare_challenge(body):
                raise RuntimeError(
                    "Still seeing Cloudflare challenge after cloudscraper. "
                    "Try --fetch playwright --headful instead."
                )
            return body
        except Exception as e:
            raise RuntimeError(f"cloudscraper failed to fetch {url}: {e}")

    def _fetch_html_with_curl_cffi(self, url: str) -> str:
        """
        Fetch HTML using curl-cffi (libcurl) with browser TLS impersonation.
        This often succeeds when Cloudflare blocks `requests`, especially when used
        with a valid `cf_clearance` cookie copied from Chrome DevTools.
        """
        try:
            from curl_cffi import requests as curl_requests  # type: ignore
        except Exception as e:
            raise RuntimeError(
                "curl-cffi is required for --fetch curl_cffi.\n"
                "Install it with:\n"
                "  py -m pip install curl-cffi\n"
                f"Import error: {e}"
            )

        headers = dict(self.session.headers)
        # Ensure we don't send a broken Cookie header
        cookie_header = headers.get("Cookie")
        if cookie_header is not None and not str(cookie_header).strip():
            headers.pop("Cookie", None)

        try:
            r = curl_requests.get(
                url,
                headers=headers,
                timeout=self.timeout,
                impersonate=self.curl_impersonate,
                allow_redirects=True,
            )
        except TypeError:
            # Older curl-cffi versions may not accept allow_redirects
            r = curl_requests.get(
                url,
                headers=headers,
                timeout=self.timeout,
                impersonate=self.curl_impersonate,
            )

        if getattr(r, "status_code", 0) >= 400:
            body = getattr(r, "text", "") or ""
            hint = ""
            if self._looks_like_cloudflare_challenge(body):
                hint = (
                    " (Cloudflare challenge detected; copy the FULL `cookie:` header value from Chrome DevTools "
                    "Network for the exact problem page and put it in aops_cookie.txt)"
                )
            # Include a tiny snippet for debugging without dumping full HTML
            snippet = re.sub(r"\s+", " ", body[:160]).strip()
            raise RuntimeError(
                f"{r.status_code} Client Error for url: {getattr(r, 'url', url)}{hint}. "
                f"Response head: {snippet!r}"
            )

        return r.text

    def _fetch_html_with_undetected_chrome(self, url: str) -> str:
        """
        Fetch HTML using undetected-chromedriver (selenium with bot detection bypass).
        This uses Chrome with stealth plugins and is often more successful than Playwright
        at bypassing Cloudflare challenges.
        """
        try:
            import undetected_chromedriver as uc  # type: ignore
        except Exception as e:
            raise RuntimeError(
                "undetected-chromedriver is required for this method.\n"
                "Install it with:\n"
                "  py -m pip install undetected-chromedriver selenium\n"
                f"Import error: {e}"
            )

        logger.info("Launching undetected-chromedriver (stealth Chrome)...")
        driver = None
        try:
            options = uc.ChromeOptions()
            options.add_argument("--disable-automation")
            options.add_argument("--disable-blink-features=AutomationControlled")
            options.add_argument("--disable-dev-shm-usage")
            options.add_argument("--no-sandbox")
            options.add_argument(f"--user-agent={self.user_agent}")

            driver = uc.Chrome(options=options, version_main=None, headless=self.headless)

            logger.info(f"Loading page: {url}")
            driver.get(url)

            # Wait for the page to load and Cloudflare to complete
            logger.info("Waiting for page and Cloudflare challenge to complete...")
            import time
            import selenium.webdriver.support.expected_conditions as EC
            from selenium.webdriver.support.ui import WebDriverWait
            from selenium.webdriver.common.by import By

            try:
                # Wait up to timeout seconds for page to load
                wait = WebDriverWait(driver, min(self.timeout, 60))
                wait.until(EC.presence_of_element_located((By.ID, "mw-content-text")))
                logger.info("Page content loaded!")
            except Exception as e:
                logger.warning(f"Could not confirm page load, but continuing: {e}")
                time.sleep(5)  # Give it a bit more time anyway

            html = driver.page_source

            if self._looks_like_cloudflare_challenge(html):
                raise RuntimeError(
                    "Still seeing Cloudflare challenge. "
                    "Try with a longer timeout: --timeout 300"
                )

            logger.info("Successfully retrieved page content with undetected-chromedriver")
            return html

        except Exception as e:
            raise RuntimeError(f"undetected-chromedriver failed: {e}")
        finally:
            if driver:
                try:
                    driver.quit()
                except Exception:
                    pass

    def _fetch_html_with_playwright(self, url: str) -> str:
        """
        Fetch HTML using a real browser engine (Playwright). This is needed because AoPS
        is frequently protected by Cloudflare challenges that block plain HTTP clients.
        """
        try:
            from playwright.sync_api import sync_playwright  # type: ignore
        except Exception as e:
            raise RuntimeError(
                "Playwright is required to bypass AoPS bot protection.\n"
                "Install it with:\n"
                "  py -m pip install playwright\n"
                "  py -m playwright install chromium\n"
                f"Import error: {e}"
            )

        timeout_ms = int(self.timeout * 1000)

        def _channel_kwargs(channel: str) -> dict:
            c = (channel or "chromium").lower()
            # Playwright supports channels like "chrome" and "msedge" if installed.
            if c in {"chrome", "msedge"}:
                return {"channel": c}
            return {}

        with sync_playwright() as p:
            # Cloudflare is much more likely to pass when we keep a persistent profile
            # (so clearance cookies stick) and when using the installed Chrome channel.
            user_data_dir = self.playwright_user_data_dir or os.path.join(os.getcwd(), ".playwright_aops_profile")
            context = p.chromium.launch_persistent_context(
                user_data_dir=user_data_dir,
                headless=self.headless,
                args=[
                    "--disable-blink-features=AutomationControlled",
                ],
                user_agent=self.user_agent,
                locale="en-US",
                viewport={"width": 1280, "height": 720},
                **_channel_kwargs(self.playwright_channel),
            )
            page = context.new_page()

            try:
                from playwright_stealth import stealth_sync  # type: ignore

                stealth_sync(page)
            except Exception:
                pass

            try:
                page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
            except Exception as e:
                # If the user closes the browser window mid-run, Playwright throws TargetClosedError.
                raise RuntimeError(
                    "The browser window was closed before the page finished loading.\n"
                    "If you are running with --headful, leave the window open until the script saves the file.\n"
                    f"Original error: {e}"
                )

            # Cloudflare can show a brief challenge page before allowing navigation.
            logger.info("Checking for Cloudflare challenge...")
            started = time.time()
            while True:
                elapsed = time.time() - started
                if elapsed > self.timeout:
                    logger.warning(f"Timeout waiting for Cloudflare challenge. Continuing with page content...")
                    break
                try:
                    html = page.content()
                except Exception as e:
                    raise RuntimeError(
                        "The browser window was closed before the Cloudflare check completed.\n"
                        "Leave it open, complete the check, then wait for the AoPS page to load.\n"
                        f"Original error: {e}"
                    )
                if not self._looks_like_cloudflare_challenge(html):
                    logger.info("Cloudflare challenge completed!")
                    break
                logger.info(f"Still showing Cloudflare challenge ({elapsed:.0f}s elapsed)... waiting...")
                time.sleep(2)

            # Wait for wiki content if we made it through.
            logger.info("Waiting for page content to load...")
            try:
                page.wait_for_selector("#mw-content-text", timeout=min(timeout_ms, 30000))
            except Exception as e:
                logger.warning(f"Could not find #mw-content-text selector: {e}")
            try:
                page.wait_for_load_state("networkidle", timeout=min(timeout_ms, 30000))
            except Exception as e:
                logger.warning(f"Page did not reach networkidle state: {e}")

            time.sleep(1)  # Give the page a moment to settle

            try:
                html = page.content()
            except Exception as e:
                raise RuntimeError(
                    "The browser window was closed before we could read the page HTML.\n"
                    f"Original error: {e}"
                )
            logger.info("Successfully retrieved page content")
            page.close()
            context.close()
            return html

    def fetch_html(self, url: str) -> str:
        """Fetch HTML, using requests by default and other methods as fallback when needed."""
        method = (self.fetch_method or "auto").lower()

        if method == "requests":
            return self._fetch_html_with_requests(url)

        if method == "cloudscraper":
            return self._fetch_html_with_cloudscraper(url)

        if method in {"curl_cffi", "curl-cffi", "curl"}:
            return self._fetch_html_with_curl_cffi(url)

        if method in {"undetected", "undetected-chrome", "uc"}:
            return self._fetch_html_with_undetected_chrome(url)

        if method == "playwright":
            return self._fetch_html_with_playwright(url)

        # auto - try in order: requests, cloudscraper, undetected-chrome, curl-cffi, playwright
        try:
            html = self._fetch_html_with_requests(url)
            if self._looks_like_cloudflare_challenge(html):
                raise RuntimeError("Cloudflare challenge detected")
            return html
        except Exception as first_err:
            logger.warning(f"Requests fetch failed or was blocked ({first_err}); retrying with cloudscraper...")
            try:
                html = self._fetch_html_with_cloudscraper(url)
                if self._looks_like_cloudflare_challenge(html):
                    raise RuntimeError("Cloudflare challenge detected (cloudscraper)")
                return html
            except Exception as second_err:
                logger.warning(f"cloudscraper fetch failed ({second_err}); retrying with undetected-chrome...")
                try:
                    html = self._fetch_html_with_undetected_chrome(url)
                    if self._looks_like_cloudflare_challenge(html):
                        raise RuntimeError("Cloudflare challenge detected (undetected-chrome)")
                    return html
                except Exception as third_err:
                    logger.warning(f"undetected-chrome fetch failed ({third_err}); retrying with curl-cffi...")
                    try:
                        html = self._fetch_html_with_curl_cffi(url)
                        if self._looks_like_cloudflare_challenge(html):
                            raise RuntimeError("Cloudflare challenge detected (curl-cffi)")
                        return html
                    except Exception as fourth_err:
                        logger.warning(f"curl-cffi fetch failed ({fourth_err}); retrying with Playwright...")
                        return self._fetch_html_with_playwright(url)
        
    def get_problem_url(self, version, year, problem_number, season=None):
        """Generate the URL for a specific AMC 12 problem."""
        # Handle special case for 2021 Fall AMC 12A/12B
        if year == 2021 and season and season.lower() == 'fall':
            # Special format: 2021_Fall_AMC_12A_Problems/Problem_25
            url = f"{self.base_url}/{year}_Fall_AMC_{version}_Problems/Problem_{problem_number}"
        elif year in (2000, 2001):
            # 2000 and 2001 only had a single AMC 12 (no A/B split).
            # Format: 2000_AMC_12_Problems/Problem_1
            url = f"{self.base_url}/{year}_AMC_12_Problems/Problem_{problem_number}"
        elif year >= 2000:
            # Modern format: 2010_AMC_12B_Problems/Problem_25
            url = f"{self.base_url}/{year}_AMC_{version}_Problems/Problem_{problem_number}"
        else:
            # Older format might be different
            url = f"{self.base_url}/{year}_AMC_{version}_Problems/Problem_{problem_number}"
        
        return url
    
    def download_problem(self, version, year, problem_number, save_raw_html=False, output_dir=".", season=None):
        """Download a single AMC 12 problem and its solutions."""
        url = self.get_problem_url(version, year, problem_number, season)
        
        try:
            logger.info(f"Downloading: {url}")
            html = self.fetch_html(url)
            if self._looks_like_cloudflare_challenge(html):
                logger.error(
                    "Blocked by Cloudflare challenge.\n"
                    "Options:\n"
                    "  1) Try a real browser engine:\n"
                    "     py -m pip install playwright\n"
                    "     py -m playwright install chromium\n"
                    "     python download_amc12_problems.py ... --fetch playwright\n"
                    "     (If it still blocks, try: --headful and complete the challenge in the opened browser.)\n"
                    "  2) Reuse your browser cookies (after visiting AoPS in Chrome/Edge):\n"
                    "     py -m pip install browser-cookie3\n"
                    "     python download_amc12_problems.py ... --fetch requests --cookies-from chrome\n"
                    "     (On Windows you may need to run the terminal as Administrator.)\n"
                    "  3) Paste a clearance cookie manually:\n"
                    "     python download_amc12_problems.py ... --cookie \"cf_clearance=...\""
                )
                return None
            
            if save_raw_html:
                # Save the raw HTML to a file and return
                os.makedirs(output_dir, exist_ok=True)
                filename = f"AMC{version}_{year}_Problem{problem_number:02d}.html"
                filepath = os.path.join(output_dir, filename)
                with open(filepath, 'w', encoding='utf-8') as f:
                    f.write(html)
                logger.info(f"Saved raw HTML: {filepath}")
                return None
            
            soup = BeautifulSoup(html, 'html.parser')
            
            # Extract problem content
            content = self.extract_problem_content(soup, version, year, problem_number)
            
            if not content.strip():
                logger.warning(f"No content found for {version} {year} Problem {problem_number}")
                return None
                
            return content
            
        except requests.RequestException as e:
            logger.error(f"Failed to download {url}: {e}")
            return None
        except Exception as e:
            logger.error(f"Unexpected error downloading {url}: {e}")
            return None
    
    def extract_problem_content(self, soup, version, year, problem_number):
        """Extract the main problem content from the AoPS page."""
        # Find the main content area
        main_content = soup.find('div', {'id': 'mw-content-text'})
        if not main_content:
            logger.warning("Could not find main content area")
            return ""
        
        # Get the parser output div which contains the actual content
        parser_output = main_content.find('div', class_='mw-parser-output')
        if not parser_output:
            parser_output = main_content
        
        # Remove edit sections first to avoid processing them
        for edit_span in parser_output.find_all('span', class_='mw-editsection'):
            edit_span.decompose()
        
        # Start building the output
        output_lines = []
        
        # Add the title (2000 and 2001 had only a single AMC 12, no A/B split)
        if year in (2000, 2001):
            output_lines.append(f"{year} AMC 12 Problems/Problem {problem_number}")
        else:
            output_lines.append(f"{year} AMC {version} Problems/Problem {problem_number}")
        
        # Extract and add table of contents
        toc = parser_output.find('div', {'id': 'toc'})
        if toc:
            output_lines.append("Contents")
            toc_items = toc.find_all('li', class_=re.compile(r'toclevel-'))
            for item in toc_items:
                # Get the toc number and text
                toc_number = item.find('span', class_='tocnumber')
                toc_text = item.find('span', class_='toctext')
                if toc_number and toc_text:
                    number = toc_number.get_text().strip()
                    text = toc_text.get_text().strip()
                    output_lines.append(f"{number} {text}")
        
        # Process the main content sections
        # Skip the TOC div when processing content
        for element in parser_output.children:
            if not hasattr(element, 'name'):
                continue
            
            # Skip TOC
            if hasattr(element, 'get') and element.get('id') == 'toc':
                continue
            
            # Skip navigation elements
            if hasattr(element, 'get'):
                classes = element.get('class', [])
                if any(cls in str(classes) for cls in ['catlinks', 'printfooter', 'nav']):
                    continue
            
            # Handle headings
            if element.name in ['h1', 'h2', 'h3', 'h4', 'h5', 'h6']:
                heading_text = element.get_text().strip()
                if heading_text:
                    output_lines.append(heading_text)
            
            # Handle paragraphs
            elif element.name == 'p':
                text = self.extract_text_with_math(element)
                if text.strip():
                    output_lines.append(text)
            
            # Handle lists
            elif element.name in ['ul', 'ol']:
                for li in element.find_all('li', recursive=False):
                    text = self.extract_text_with_math(li)
                    if text.strip():
                        output_lines.append(f"- {text}")
            
            # Handle tables (like navigation tables)
            elif element.name == 'table':
                table_text = self.extract_table_content(element)
                if table_text:
                    output_lines.append(table_text)
            
            # Handle center tags (often contain LaTeX tables as images)
            elif element.name == 'center':
                center_text = self.extract_text_with_math(element)
                if center_text.strip():
                    output_lines.append(center_text)
        
        # Add footer content (copyright, categories, etc.)
        # Look for specific footer elements that might not be direct children
        copyright_found = False
        for p in parser_output.find_all('p'):
            text = p.get_text().strip()
            if 'copyright' in text.lower() and 'mathematical association' in text.lower():
                if not copyright_found:
                    output_lines.append(text)
                    copyright_found = True
        
        # Add category info if present
        catlinks = parser_output.find('div', class_='catlinks')
        if catlinks:
            cat_text = catlinks.get_text().strip()
            if cat_text and 'Category:' in cat_text:
                output_lines.append('\n' + cat_text)
        
        # Look for the "Art of Problem Solving is an ACS WASC Accredited School" line
        for div in parser_output.find_all('div'):
            text = div.get_text().strip()
            if 'art of problem solving' in text.lower() and 'accredited' in text.lower():
                if text not in '\n'.join(output_lines):
                    output_lines.append('\n' + text)
                break
        
        return '\n'.join(output_lines)
    
    def extract_text_with_math(self, element):
        """Extract text while preserving LaTeX mathematical expressions."""
        if not element:
            return ""
        
        result = []
        
        # Process all children recursively
        for child in element.children:
            if isinstance(child, NavigableString):
                # Plain text
                text = str(child).strip()
                if text:
                    result.append(text)
            elif child.name == 'img':
                # Check if it's a LaTeX image
                classes = child.get('class', [])
                if 'latex' in classes or 'latexcenter' in classes:
                    alt_text = child.get('alt', '')
                    if alt_text:
                        # Check if it's display math or inline math
                        if 'latexcenter' in classes:
                            # Display math - ensure proper formatting
                            if not (alt_text.startswith('\\[') and alt_text.endswith('\\]')):
                                alt_text = alt_text  # Keep as is if already formatted
                            result.append('\n\n' + alt_text + '\n\n')
                        elif alt_text.startswith('\\[') and alt_text.endswith('\\]'):
                            # Display math
                            result.append('\n\n' + alt_text + '\n\n')
                        elif alt_text.startswith('$') and alt_text.endswith('$'):
                            # Inline math
                            result.append(alt_text)
                        else:
                            # Assume it's inline math and wrap it
                            result.append(f'${alt_text}$')
            elif child.name == 'a':
                # Links - just get the text
                link_text = self.extract_text_with_math(child)
                if link_text:
                    result.append(link_text)
            elif child.name in ['span', 'div', 'strong', 'em', 'b', 'i']:
                # Inline elements - recurse
                inner_text = self.extract_text_with_math(child)
                if inner_text:
                    result.append(inner_text)
            elif child.name == 'br':
                # Line break
                result.append('\n')
            elif child.name == 'script':
                # Check for MathJax content
                if child.get('type') in ['math/tex', 'math/tex; mode=display', 'math/tex; mode=inline']:
                    math_content = child.string
                    if math_content:
                        if 'display' in child.get('type', ''):
                            result.append(f'\n\\[{math_content}\\]\n')
                        else:
                            result.append(f'${math_content}$')
        
        # Join the result and clean up spacing
        text = ' '.join(result)
        # Fix multiple spaces
        text = re.sub(r'[ \t]+', ' ', text)
        # Fix spacing around display math
        text = re.sub(r'\s*\n\s*\\\[\s*', '\n\n\\[', text)
        text = re.sub(r'\s*\\\]\s*\n\s*', '\\]\n\n', text)
        # Clean up multiple newlines
        text = re.sub(r'\n{3,}', '\n\n', text)
        
        return text.strip()
    
    def extract_table_content(self, table):
        """Extract content from a table element."""
        rows = table.find_all('tr')
        if not rows:
            return ""
        
        # Check if this is a navigation table
        first_cell_text = rows[0].get_text().strip() if rows[0] else ""
        
        # For navigation tables, format differently
        if any(keyword in first_cell_text.lower() for keyword in ['amc', 'preceded', 'followed', 'problems']):
            lines = []
            for row in rows:
                cells = row.find_all(['td', 'th'])
                if cells:
                    row_text = ' '.join([self.extract_text_with_math(cell) for cell in cells])
                    if row_text.strip():
                        lines.append(row_text.strip())
            return '\n'.join(lines) if lines else ""
        
        # For other tables, use org-mode table format
        lines = []
        for row in rows:
            cells = row.find_all(['td', 'th'])
            if cells:
                row_content = '|'.join([self.extract_text_with_math(cell) for cell in cells])
                lines.append(f"|{row_content}|")
        
        return '\n'.join(lines) if lines else ""
    

    def save_problem(self, content, version, year, problem_number, output_dir=".", season=None):
        """Save the problem content to a .org file."""
        if not content:
            return False
            
        # Create output directory if it doesn't exist
        os.makedirs(output_dir, exist_ok=True)
        
        # Generate filename - include season if provided
        if season:
            filename = f"AMC{version}_{year}_{season}_Problem{problem_number:02d}.org"
        else:
            filename = f"AMC{version}_{year}_Problem{problem_number:02d}.org"
        filepath = os.path.join(output_dir, filename)
        
        try:
            with open(filepath, 'w', encoding='utf-8') as f:
                # The content already includes the title and all formatting
                f.write(content)
            
            logger.info(f"Saved: {filepath}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to save {filepath}: {e}")
            return False
    
    def download_range(self, version, year, start_problem, end_problem, output_dir=".", save_raw_html=False, season=None, delay: float = 1.0):
        """Download a range of problems."""
        successful_downloads = 0
        total_problems = end_problem - start_problem + 1
        
        season_display = f" {season}" if season else ""
        logger.info(f"Starting download of AMC {version} {year}{season_display} Problems {start_problem}-{end_problem}")
        
        for problem_num in range(start_problem, end_problem + 1):
            content = self.download_problem(version, year, problem_num, save_raw_html=save_raw_html, output_dir=output_dir, season=season)
            
            if not save_raw_html and content:
                if self.save_problem(content, version, year, problem_num, output_dir, season):
                    successful_downloads += 1
            elif save_raw_html:
                successful_downloads += 1
            
            # Be respectful to the server
            time.sleep(max(0.0, float(delay)))
        
        logger.info(f"Download completed: {successful_downloads}/{total_problems} problems downloaded successfully")
        return successful_downloads

def main():
    parser = argparse.ArgumentParser(
        description="Download AMC 12 problems and solutions from AoPS Wiki",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python download_amc12_problems.py --version 12B --year 2010 --start 1 --end 25
  python download_amc12_problems.py -v 12A -y 2015 -s 1 -e 25 -o ./problems
  python download_amc12_problems.py --version 12B --year 2020 --start 20 --end 25
  python download_amc12_problems.py --version 12A --year 2011 --start 13 --end 25 --timeout 180
  python download_amc12_problems.py --version 12A --year 2021 --season Fall --start 1 --end 25
  python download_amc12_problems.py --year 2000 --start 1 --end 25
  python download_amc12_problems.py --version 12 --year 2001 --start 1 --end 25

Notes:
  AoPS Wiki is often protected by Cloudflare bot challenges. If you see repeated 403s,
  use Playwright (real browser engine):
    py -m pip install playwright
    py -m playwright install chromium
    python download_amc12_problems.py ... --fetch playwright

  If Playwright still gets blocked, you can reuse cookies from your browser profile (after
  visiting AoPS in Chrome/Edge) or pass a cookie manually:
    py -m pip install browser-cookie3
    python download_amc12_problems.py ... --fetch requests --cookies-from chrome
    python download_amc12_problems.py ... --cookie "cf_clearance=..."

  If `requests` is still blocked even with a valid cookie, try curl-cffi (Chrome TLS fingerprint):
    py -m pip install curl-cffi
    python download_amc12_problems.py ... --fetch curl_cffi --cookie-file aops_cookie.txt
        """
    )
    
    parser.add_argument(
        '--version', '-v',
        choices=['12', '12A', '12B'],
        required=False,
        default=None,
        help='AMC version (12A or 12B; use 12 for 2000 and 2001 which had a single AMC 12). '
             'Defaults to 12 for years 2000/2001 and is required otherwise.'
    )
    
    parser.add_argument(
        '--year', '-y',
        type=int,
        required=True,
        help='Year of the AMC competition'
    )
    
    parser.add_argument(
        '--season', '-se',
        choices=['Fall', 'Spring'],
        help='Season for special years (e.g., Fall for 2021 Fall AMC)'
    )
    
    parser.add_argument(
        '--start', '-s',
        type=int,
        default=1,
        help='Starting problem number (default: 1)'
    )
    
    parser.add_argument(
        '--end', '-e',
        type=int,
        required=True,
        help='Ending problem number'
    )
    
    parser.add_argument(
        '--output', '-o',
        default='.',
        help='Output directory (default: current directory)'
    )
    
    parser.add_argument(
        '--delay',
        type=float,
        default=1.0,
        help='Delay between downloads in seconds (default: 1.0)'
    )

    parser.add_argument(
        '--fetch',
        choices=['auto', 'requests', 'cloudscraper', 'undetected', 'curl_cffi', 'playwright'],
        default='auto',
        help='Fetch method. Try undetected-chrome (best), cloudscraper, curl_cffi, or playwright to bypass Cloudflare (default: auto)'
    )

    parser.add_argument(
        '--headful',
        action='store_true',
        help='Use a visible browser window with Playwright (debugging; default: headless).'
    )

    parser.add_argument(
        '--playwright-channel',
        choices=['chromium', 'chrome', 'msedge'],
        default='chromium',
        help='Playwright browser channel to use (default: chromium). Try "chrome" or "msedge" to reduce Cloudflare loops.',
    )

    parser.add_argument(
        '--playwright-user-data-dir',
        default=None,
        help='Persistent profile directory for Playwright (stores cookies/clearance). Default: .playwright_aops_profile in the current dir.',
    )

    parser.add_argument(
        '--curl-impersonate',
        default='chrome120',
        help=(
            'curl-cffi TLS impersonation profile (default: chrome120). '
            'If you get an error, try chrome119/chrome116/chrome110.'
        ),
    )

    parser.add_argument(
        '--cookies-from',
        choices=['none', 'auto', 'chrome', 'edge', 'firefox', 'brave', 'chromium', 'opera'],
        default='none',
        help=(
            'Load cookies for artofproblemsolving.com from a local browser profile. '
            'Useful to reuse Cloudflare clearance after visiting AoPS in your browser '
            '(default: none).'
        ),
    )

    parser.add_argument(
        '--cookie',
        default=None,
        help='Optional raw Cookie header string (e.g. "cf_clearance=...; other=value").',
    )

    parser.add_argument(
        '--cookie-file',
        default=None,
        help='Path to a text file containing a raw Cookie header string (same format as --cookie).',
    )

    parser.add_argument(
        '--user-agent',
        default=None,
        help='Override User-Agent header. If using --cookie with cf_clearance, match your browser UA if possible.',
    )
    
    parser.add_argument(
        '--timeout',
        type=int,
        default=120,
        help='Timeout for each download in seconds (default: 120)'
    )
    
    parser.add_argument(
        '--save-raw-html',
        action='store_true',
        help='Save the raw HTML of each problem page to a .html file and skip further processing.'
    )
    
    args = parser.parse_args()
    
    # Validate arguments
    if args.start < 1 or args.end < 1:
        logger.error("Problem numbers must be positive integers")
        return 1
    
    if args.start > args.end:
        logger.error("Start problem number must be less than or equal to end problem number")
        return 1
    
    if args.year < 2000 or args.year > 2025:
        logger.warning(f"Year {args.year} seems unusual. Continuing anyway...")

    # 2000 and 2001 did not have AMC 12A/12B; there was only a single AMC 12.
    if args.year in (2000, 2001):
        if args.version is None:
            args.version = '12'
        elif args.version != '12':
            logger.warning(
                f"Year {args.year} only had a single AMC 12 (no 12A/12B). "
                f"Overriding --version {args.version} with '12'."
            )
            args.version = '12'
    else:
        if args.version is None:
            logger.error("--version is required for years other than 2000 and 2001 (choose 12A or 12B).")
            return 1
        if args.version == '12':
            logger.error(
                f"Version '12' (single AMC 12) only applies to years 2000 and 2001. "
                f"For year {args.year}, use 12A or 12B."
            )
            return 1
    
    # Create downloader and start downloading
    def _normalize_cookie_string(raw: Optional[str]) -> Optional[str]:
        if raw is None:
            return None
        # Allow multi-line cookies (we'll join lines with '; ')
        s = "\n".join([line.strip() for line in str(raw).splitlines() if line.strip()]).strip()
        if not s:
            return None
        # If user pasted a bare cf_clearance VALUE (no '='), auto-prefix it.
        if "=" not in s:
            logger.warning(
                "Cookie string has no '='. Assuming this is a bare cf_clearance value and auto-prefixing "
                "to 'cf_clearance=...'. If it still fails, use Chrome DevTools Network -> copy request headers "
                "and paste the full 'cookie:' header value."
            )
            s = f"cf_clearance={s}"
        
        # Heuristic check for incomplete cookies
        if "cf_clearance" in s and "__cf_bm" not in s and len(s) < 300:
             logger.warning(
                "Your cookie string looks very short (only cf_clearance?). "
                "Cloudflare often requires the FULL cookie string (including __cf_bm, PHPSESSID, etc). "
                "Please copy the entire 'Cookie:' header value from DevTools."
             )
        return s

    cookie_value = _normalize_cookie_string(args.cookie)
    if not cookie_value and args.cookie_file:
        try:
            with open(args.cookie_file, "r", encoding="utf-8") as f:
                file_content = f.read()
                # Check if the file still contains the default instructions
                if "PASTE THE FULL COOKIE STRING HERE" in file_content:
                    logger.error("The cookie file contains the default instructions. Please replace it with the actual cookie string.")
                    return 1
                cookie_value = _normalize_cookie_string(file_content)
        except Exception as e:
            logger.error(f"Failed to read --cookie-file {args.cookie_file}: {e}")
            return 1

    downloader = AMC12Downloader(
        timeout=args.timeout,
        fetch_method=args.fetch,
        headless=(not args.headful),
        playwright_channel=args.playwright_channel,
        playwright_user_data_dir=args.playwright_user_data_dir,
        curl_impersonate=args.curl_impersonate,
        cookies_from=args.cookies_from,
        cookie=cookie_value,
        user_agent=args.user_agent,
    )
    
    try:
        successful = downloader.download_range(
            args.version,
            args.year,
            args.start,
            args.end,
            args.output,
            save_raw_html=args.save_raw_html,
            season=args.season,
            delay=args.delay
        )
        
        if successful > 0:
            logger.info(f"Successfully downloaded {successful} problems")
            return 0
        else:
            logger.error("No problems were downloaded successfully")
            return 1
            
    except KeyboardInterrupt:
        logger.info("Download interrupted by user")
        return 1
    except Exception as e:
        logger.error(f"Unexpected error: {e}")
        return 1

if __name__ == "__main__":
    exit(main()) 