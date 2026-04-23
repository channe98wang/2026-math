#!/usr/bin/env python3
"""
Extract AMC problem directly from an open Chrome browser.

Usage:
1. Open Chrome
2. Navigate to the AMC problem page (e.g., https://artofproblemsolving.com/wiki/index.php/2002_AMC_12A_Problems/Problem_1)
3. Wait for the page to fully load
4. Run: python extract_from_open_chrome.py -o ./problems

The script automatically detects the version, year, and problem number from the page!
"""

import argparse
import json
import logging
import re
from pathlib import Path

# Import the original downloader to reuse its extract and save methods
import sys
sys.path.insert(0, str(Path(__file__).parent))
from download_amc12_problems import AMC12Downloader
from bs4 import BeautifulSoup

logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
logger = logging.getLogger(__name__)


def get_chrome_page_content():
    """
    Connect to already-open Chrome browser and extract the page content.
    Tries common debugging ports.
    """
    try:
        import socket

        # Try common ports that Chrome uses for remote debugging
        for port in [9222, 9223, 9224, 9225]:
            try:
                sock = socket.create_connection(("localhost", port), timeout=1)
                sock.close()
                logger.info(f"Found Chrome debugging port at {port}")
                return extract_via_cdp(port)
            except (socket.timeout, ConnectionRefusedError):
                continue

        logger.error("Could not find Chrome remote debugging port.")
        logger.info("Make sure Chrome is open with the AoPS problem page loaded.")
        return None

    except Exception as e:
        logger.error(f"Error connecting to Chrome: {e}")
        return None


def extract_via_cdp(port):
    """Extract page content via Chrome DevTools Protocol."""
    try:
        import urllib.request
        import urllib.error

        logger.info(f"Connecting to Chrome on port {port}...")

        try:
            response = urllib.request.urlopen(f"http://localhost:{port}/json/list", timeout=5)
            pages = json.loads(response.read().decode())

            if not pages:
                logger.error("No open pages found in Chrome")
                return None

            # Find the first page that's not the DevTools page
            target_page = None
            for page in pages:
                if "artofproblemsolving" in page.get("url", "").lower():
                    target_page = page
                    break

            if not target_page:
                # Just use the first page if we can't find AoPS
                target_page = pages[0]

            logger.info(f"Found page: {target_page.get('url', 'unknown')}")

            # Extract the WebSocket URL for this page
            ws_url = target_page.get("webSocketDebuggerUrl")
            if not ws_url:
                logger.error("Could not get WebSocket URL from Chrome")
                return None

            # Use websocket to get the page content
            import websocket

            ws = websocket.create_connection(ws_url, timeout=10)
            ws.settimeout(5)  # Set timeout for recv calls

            # Send command to get outer HTML
            cmd = {
                "id": 2,
                "method": "Runtime.evaluate",
                "params": {
                    "expression": "document.documentElement.outerHTML"
                }
            }
            logger.info("Sending Runtime.evaluate command...")
            ws.send(json.dumps(cmd))

            # Receive responses
            html = None
            attempt = 0
            while attempt < 20:
                attempt += 1
                try:
                    response = ws.recv()
                    if response:
                        logger.info(f"Received response {attempt}: {response[:100]}...")
                        data = json.loads(response)

                        # Handle nested result structure: {"result": {"result": {"value": "..."}}}
                        if "result" in data:
                            result = data["result"]
                            # Check for nested result with value
                            if isinstance(result, dict) and "result" in result:
                                if "value" in result["result"]:
                                    html = result["result"]["value"]
                                    logger.info("Found HTML in response!")
                                    break
                            # Check for direct value
                            elif "value" in result:
                                html = result["value"]
                                logger.info("Found HTML in response!")
                                break

                        if "error" in data:
                            logger.error(f"Error in response: {data['error']}")
                            break
                except websocket.WebSocketTimeoutException:
                    logger.warning(f"WebSocket timeout on attempt {attempt}, continuing...")
                    continue
                except json.JSONDecodeError as e:
                    logger.warning(f"JSON decode error: {e}")
                    continue
                except Exception as e:
                    logger.warning(f"Error receiving: {e}")
                    continue

            ws.close()

            if html:
                logger.info("Successfully extracted page content from Chrome")
                return html
            else:
                logger.error("Could not extract HTML from Chrome after multiple attempts")
                return None

        except urllib.error.URLError as e:
            logger.error(f"Could not connect to Chrome on port {port}: {e}")
            return None

    except ImportError:
        logger.error("websocket-client not installed. Install with: pip install websocket-client")
        return None
    except Exception as e:
        logger.error(f"Error extracting via CDP: {e}")
        return None


def extract_problem_info(html):
    """
    Extract version, year, and problem number from the HTML page.
    Looks for patterns like "2002 AMC 12A Problems/Problem 1"
    """
    # Pattern: "YYYY AMC 12[A/B] Problems/Problem N"
    pattern = r'(\d{4})\s+AMC\s+12([AB])\s+Problems.*?Problem\s+(\d+)'

    match = re.search(pattern, html, re.IGNORECASE)
    if match:
        year = int(match.group(1))
        version = f"12{match.group(2).upper()}"
        problem = int(match.group(3))
        return version, year, problem

    # Also check the page title
    soup = BeautifulSoup(html, 'html.parser')
    title = soup.find('title')
    if title:
        match = re.search(pattern, title.text, re.IGNORECASE)
        if match:
            year = int(match.group(1))
            version = f"12{match.group(2).upper()}"
            problem = int(match.group(3))
            return version, year, problem

    return None, None, None


def main():
    # Determine default output directory following project structure
    script_dir = Path(__file__).parent

    parser = argparse.ArgumentParser(
        description="Extract AMC problem directly from an open Chrome browser (auto-detects version/year/problem)",
        epilog=f"Example: python extract_from_open_chrome.py\nOutput: {script_dir}/{{year}}-{{version}}_Problems/"
    )
    parser.add_argument('--output', '-o', default=str(script_dir), help=f'Output directory (default: {script_dir})')

    args = parser.parse_args()

    # Extract the page content from Chrome
    logger.info("Connecting to your open Chrome browser...")
    html = get_chrome_page_content()

    if not html:
        logger.error("Could not extract page content from Chrome.")
        logger.info("\nMake sure:")
        logger.info("1. Chrome is open with the AoPS problem page loaded")
        logger.info("2. The page has fully loaded")
        logger.info("3. You can see the problem content on the page")
        return 1

    # Extract problem info from HTML
    version, year, problem = extract_problem_info(html)

    if not version or not year or not problem:
        logger.error("Could not detect AMC version, year, or problem number from HTML")
        logger.info("Make sure the page is a valid AoPS AMC problem page")
        return 1

    logger.info(f"Detected: AMC {version} {year}, Problem {problem}")

    # Create output directory following convention: {year}-AMC12{A/B}_Problems
    output_dir = Path(args.output) / f"{year}-AMC{version}_Problems"
    output_dir.mkdir(parents=True, exist_ok=True)

    # Parse and save the content
    downloader = AMC12Downloader()
    soup = BeautifulSoup(html, 'html.parser')

    try:
        content = downloader.extract_problem_content(soup, version, year, problem)
        if content.strip():
            downloader.save_problem(content, version, year, problem, str(output_dir))
            logger.info(f"Successfully extracted AMC {version} {year} Problem {problem}")
            return 0
        else:
            logger.error(f"No content found for Problem {problem}")
            return 1
    except Exception as e:
        logger.error(f"Error processing Problem {problem}: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    exit(main())
