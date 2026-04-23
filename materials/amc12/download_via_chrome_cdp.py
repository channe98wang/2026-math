#!/usr/bin/env python3
"""
Download AMC 12 problems via Chrome DevTools Protocol (CDP).

This script solves Cloudflare bot detection by leveraging a real browser session.
User manually opens the first problem (passes Cloudflare), then the script
automatically downloads the rest via CDP navigation.

Usage:
1. Open Chrome with debugging port:
   chrome.exe --remote-debugging-port=9222 --remote-allow-origins=*

2. Manually navigate to the first problem page (e.g., 2002 AMC 12A Problem 1)
   This passes the Cloudflare check.

3. Run the script to download the range:
   python download_via_chrome_cdp.py --version 12A --year 2002 --start 1 --end 25

The script will then automatically navigate to each problem and extract content.
"""

import argparse
import json
import logging
import re
import time
import sys
import os
from pathlib import Path
from bs4 import BeautifulSoup

# Add parent directory to path to import from download_amc12_problems
sys.path.insert(0, str(Path(__file__).parent))
from download_amc12_problems import AMC12Downloader

logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
logger = logging.getLogger(__name__)


class ChromeCDPDownloader:
    """Download AMC problems using Chrome DevTools Protocol."""

    def __init__(self, port=9222):
        self.port = port
        self.base_url = "https://artofproblemsolving.com/wiki/index.php"
        self.downloader = AMC12Downloader()  # Reuse extraction logic
        self.ws = None
        self.cmd_id = 1

    def find_chrome_port(self):
        """Find an available Chrome debugging port."""
        import socket
        for port in [9222, 9223, 9224, 9225]:
            try:
                sock = socket.create_connection(("localhost", port), timeout=1)
                sock.close()
                logger.info(f"Found Chrome debugging port at {port}")
                self.port = port
                return port
            except (socket.timeout, ConnectionRefusedError):
                continue
        return None

    def connect_to_chrome(self):
        """Connect to Chrome via CDP."""
        try:
            import urllib.request
            import websocket

            if not self.port:
                logger.error("No Chrome debugging port found")
                return False

            logger.info(f"Connecting to Chrome on port {self.port}...")

            # Get list of pages
            try:
                response = urllib.request.urlopen(f"http://localhost:{self.port}/json/list", timeout=5)
                pages = json.loads(response.read().decode())

                if not pages:
                    logger.error("No open pages found in Chrome")
                    return False

                # Use the first page
                target_page = pages[0]
                ws_url = target_page.get("webSocketDebuggerUrl")

                if not ws_url:
                    logger.error("Could not get WebSocket URL from Chrome")
                    return False

                logger.info(f"Connecting via WebSocket: {ws_url}")
                self.ws = websocket.create_connection(ws_url, timeout=10)
                self.ws.settimeout(5)

                logger.info("Successfully connected to Chrome")
                return True

            except Exception as e:
                logger.error(f"Failed to connect: {e}")
                return False

        except ImportError:
            logger.error("websocket-client not installed. Install with: pip install websocket-client")
            return False

    def send_cdp_command(self, method, params=None, wait_for_response=True, timeout=10):
        """Send a command via Chrome DevTools Protocol."""
        if not self.ws:
            logger.error("WebSocket not connected")
            return None

        cmd = {
            "id": self.cmd_id,
            "method": method,
        }
        if params:
            cmd["params"] = params

        response_id = self.cmd_id
        self.cmd_id += 1

        try:
            logger.info(f"Sending: {method}")
            self.ws.send(json.dumps(cmd))

            if not wait_for_response:
                return True

            # Wait for response with configurable timeout
            start_time = time.time()
            response_buffer = []

            while time.time() - start_time < timeout:
                try:
                    response = self.ws.recv()
                    if response:
                        data = json.loads(response)

                        # Check if this is our response
                        if data.get("id") == response_id:
                            if "error" in data:
                                logger.error(f"CDP error: {data['error']}")
                                return None
                            logger.info(f"Got response for {method}")
                            return data.get("result")
                        else:
                            # Buffer other responses (might be events)
                            response_buffer.append(data)

                except json.JSONDecodeError:
                    pass
                except Exception as e:
                    logger.warning(f"Error receiving: {e}")
                    continue

            logger.warning(f"No response received for {method} after {timeout}s")
            return None

        except Exception as e:
            logger.error(f"Error sending command: {e}")
            return False

    def navigate_to_url(self, url, wait_seconds=5):
        """Navigate to a URL and wait for the page to load."""
        logger.info(f"Navigating to: {url}")

        # Send navigation command
        self.send_cdp_command("Page.navigate", {"url": url}, wait_for_response=True, timeout=10)

        # Wait for page to fully load - use longer wait time and check multiple times
        logger.info(f"Waiting for page to load (up to {wait_seconds} seconds)...")
        time.sleep(wait_seconds)

        return True

    def get_page_html(self, retries=3):
        """Get the current page's HTML via Runtime.evaluate with retry logic."""
        logger.info("Extracting page HTML...")

        for attempt in range(retries):
            logger.info(f"Attempt {attempt + 1}/{retries} to get HTML...")

            result = self.send_cdp_command(
                "Runtime.evaluate",
                {"expression": "document.documentElement.outerHTML"},
                wait_for_response=True,
                timeout=15
            )

            if result:
                # The result might be nested
                html = None
                if isinstance(result, dict):
                    if "value" in result:
                        html = result["value"]
                    elif "result" in result and isinstance(result["result"], dict):
                        if "value" in result["result"]:
                            html = result["result"]["value"]

                if html:
                    logger.info(f"Successfully got HTML ({len(html)} characters)")
                    return html

            # Wait before retrying
            if attempt < retries - 1:
                logger.warning(f"Failed to get HTML, waiting 2 seconds before retry...")
                time.sleep(2)

        logger.error(f"Failed to get page HTML after {retries} attempts")
        return None

    def download_problem(self, version, year, problem_number):
        """Download a single problem via CDP."""
        # Build URL
        url = f"{self.base_url}/{year}_AMC_{version}_Problems/Problem_{problem_number}"

        # Navigate to the problem
        if not self.navigate_to_url(url, wait_seconds=2):
            return None

        # Get page HTML
        html = self.get_page_html()
        if not html:
            logger.error(f"Failed to get HTML for Problem {problem_number}")
            return None

        # Check for Cloudflare challenge
        if self.downloader._looks_like_cloudflare_challenge(html):
            logger.error(f"Cloudflare challenge detected for Problem {problem_number}")
            logger.error("Please complete the challenge in your browser and try again")
            return None

        # Extract problem content
        soup = BeautifulSoup(html, 'html.parser')
        content = self.downloader.extract_problem_content(soup, version, year, problem_number)

        if not content or not content.strip():
            logger.warning(f"No content found for Problem {problem_number}")
            return None

        logger.info(f"Successfully extracted AMC {version} {year} Problem {problem_number}")
        return content

    def download_range(self, version, year, start_problem, end_problem, output_dir=".", delay=3.0):
        """Download a range of problems."""
        successful = 0
        failed = 0
        total = end_problem - start_problem + 1

        logger.info(f"\n{'='*60}")
        logger.info(f"Starting download of AMC {version} {year} Problems {start_problem}-{end_problem}")
        logger.info(f"Total problems to download: {total}")
        logger.info(f"Delay between problems: {delay} seconds")
        logger.info(f"{'='*60}\n")

        for problem_num in range(start_problem, end_problem + 1):
            logger.info(f"\n{'='*60}")
            logger.info(f"Problem {problem_num}/{end_problem}")
            logger.info(f"Progress: {successful} successful, {failed} failed")
            logger.info(f"{'='*60}")

            content = self.download_problem(version, year, problem_num)

            if content:
                if self.downloader.save_problem(content, version, year, problem_num, output_dir):
                    successful += 1
                    logger.info(f"✓ Problem {problem_num} saved successfully")
                else:
                    failed += 1
                    logger.error(f"✗ Problem {problem_num} failed to save")
            else:
                failed += 1
                logger.error(f"✗ Problem {problem_num} failed to extract")

            # Be respectful to the server
            if problem_num < end_problem:
                logger.info(f"\nWaiting {delay} seconds before next problem...")
                time.sleep(max(0.0, float(delay)))

        logger.info(f"\n{'='*60}")
        logger.info(f"Download Complete")
        logger.info(f"Downloaded: {successful}/{total} problems successfully")
        logger.info(f"Failed: {failed}/{total}")
        logger.info(f"Success rate: {successful*100/total:.1f}%")
        logger.info(f"{'='*60}\n")
        return successful

    def close(self):
        """Close the WebSocket connection."""
        if self.ws:
            try:
                self.ws.close()
                logger.info("WebSocket closed")
            except Exception:
                pass


def main():
    parser = argparse.ArgumentParser(
        description="Download AMC 12 problems via Chrome DevTools Protocol",
        epilog="""
WORKFLOW:
1. Open Chrome with debugging port:
   chrome.exe --remote-debugging-port=9222 --remote-allow-origins=*

2. Manually navigate to the first AMC problem page
   (This passes the Cloudflare check)

3. Run this script to download the range:
   python download_via_chrome_cdp.py --version 12A --year 2002 --start 1 --end 25

The script will automatically navigate to each problem and extract content.
"""
    )

    parser.add_argument('--version', '-v', required=True, choices=['12A', '12B'],
                        help='AMC version (12A or 12B)')
    parser.add_argument('--year', '-y', type=int, required=True,
                        help='Year (e.g., 2002, 2003, 2010)')
    parser.add_argument('--start', '-s', type=int, default=1,
                        help='Start problem number (default: 1)')
    parser.add_argument('--end', '-e', type=int, default=25,
                        help='End problem number (default: 25)')
    parser.add_argument('--output', '-o', default='.',
                        help='Output directory (default: current directory)')
    parser.add_argument('--delay', '-d', type=float, default=3.0,
                        help='Delay between problems in seconds (default: 3.0)')
    parser.add_argument('--port', '-p', type=int, default=9222,
                        help='Chrome debugging port (default: 9222)')

    args = parser.parse_args()

    # Validate arguments
    if args.start < 1 or args.end < 1 or args.start > args.end:
        logger.error("Invalid problem range")
        return 1

    if args.end > 25:
        logger.warning("AMC 12 typically has 25 problems, but allowing up to end number")

    # Create downloader
    downloader = ChromeCDPDownloader(port=args.port)

    # Try to find Chrome
    if not downloader.find_chrome_port():
        logger.error("\nCould not find Chrome debugging port")
        logger.error("\nMake sure Chrome is running with:")
        logger.error("  chrome.exe --remote-debugging-port=9222 --remote-allow-origins=*")
        return 1

    # Connect to Chrome
    if not downloader.connect_to_chrome():
        logger.error("Failed to connect to Chrome")
        return 1

    try:
        # Download the range
        output_dir = Path(args.output) / f"{args.year}-AMC{args.version}_Problems"
        output_dir.mkdir(parents=True, exist_ok=True)

        successful = downloader.download_range(
            args.version,
            args.year,
            args.start,
            args.end,
            str(output_dir),
            args.delay
        )

        return 0 if successful > 0 else 1

    except KeyboardInterrupt:
        logger.info("\nDownload interrupted by user")
        return 1
    except Exception as e:
        logger.error(f"Unexpected error: {e}")
        import traceback
        traceback.print_exc()
        return 1
    finally:
        downloader.close()


if __name__ == "__main__":
    exit(main())
