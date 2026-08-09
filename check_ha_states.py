#!/usr/bin/env python3
"""
Script for querying and verifying Home Assistant entity states based on test-prompts.json.
Fetches expected_device IDs from data/test-prompts.json and evaluates whether the actual
live state in Home Assistant matches the expected state.

Can also be imported as a module: use `validate_single_element()` to verify a single
test element against a live Home Assistant instance.
"""

import os
import re
import sys
import json
import argparse
from pathlib import Path

try:
    import requests
except ImportError:
    print("[ERROR] 'requests' library is required. Install it using 'pip install requests'", file=sys.stderr)
    sys.exit(1)

from logger import configure_logging, get_logger

logger = get_logger(__name__)

# Default configuration
BASE_DIR = Path(__file__).parent.resolve()
TEST_PROMPTS_FILE = BASE_DIR / "data" / "test-prompts.json"
TOKEN_FILE = BASE_DIR / "data" / "ha-token.txt"
CONFIG_PATH = BASE_DIR / "data" / "config.json"
DEFAULT_API_PASSWORD = "testing-key"


def _load_config_ha_url():
    """Load Home Assistant URL from data/config.json if available."""
    try:
        if CONFIG_PATH.exists():
            with open(CONFIG_PATH, "r", encoding="utf-8") as fh:
                config = json.load(fh)
            return config.get("ha_url")
    except (json.JSONDecodeError, OSError):
        pass
    return None


DEFAULT_HA_URL = os.getenv("HA_URL", _load_config_ha_url() or "http://localhost:8123")

def get_bearer_token(cmd_token=None):
    """Returns Bearer Token from CLI arg or data/ha-token.txt."""
    if cmd_token:
        return cmd_token.strip()
    if TOKEN_FILE.exists():
        token = TOKEN_FILE.read_text(encoding="utf-8").strip()
        if token:
            return token
    return None

def load_test_prompts(prompts_path):
    """Loads test-prompts.json."""
    if not prompts_path.exists():
        logger.error("Prompts file not found at: %s", prompts_path)
        sys.exit(1)

    with open(prompts_path, "r", encoding="utf-8") as f:
        return json.load(f)

def get_ha_headers(api_password=None, bearer_token=None):
    """Constructs HTTP request headers for Home Assistant API authentication."""
    headers = {
        "Content-Type": "application/json"
    }
    token = get_bearer_token(bearer_token)
    if token:
        headers["Authorization"] = f"Bearer {token}"
    else:
        headers["X-HA-Provider"] = "trusted_networks"
        if api_password:
            headers["x-ha-access"] = api_password
    return headers

def check_ha_connection(ha_url, headers):
    """Verifies connection to Home Assistant API."""
    try:
        res = requests.get(f"{ha_url}/api/", headers=headers, timeout=3)
        if res.status_code in (200, 201):
            return True, res.json().get("message", "API operational")
        else:
            return False, f"HTTP status {res.status_code}"
    except requests.exceptions.RequestException as e:
        return False, str(e)

def fetch_entity_state(ha_url, entity_id, headers, cache={}):
    """Fetches the state of a single entity from Home Assistant (cached per execution)."""
    if entity_id in cache:
        return cache[entity_id]

    url = f"{ha_url}/api/states/{entity_id}"
    try:
        res = requests.get(url, headers=headers, timeout=5)
        logger.debug("HA respuesta para %s:\n%s", entity_id, json.dumps(res.json(), indent=2, ensure_ascii=False) if res.status_code == 200 else res.text)
        if res.status_code == 200:
            result = (True, res.json())
        elif res.status_code == 404:
            result = (False, "Entity not found")
        else:
            result = (False, f"HTTP Error {res.status_code}")
    except requests.exceptions.RequestException as e:
        result = (False, f"Connection error: {e}")

    cache[entity_id] = result
    return result

def is_regex_pattern(parameter):
    """Detects if a parameter is a regex pattern (prefixed with 'regex:')."""
    return isinstance(parameter, str) and parameter.startswith("regex:")


def get_regex_pattern(parameter):
    """Extracts the regex pattern from a 'regex:...' parameter."""
    if is_regex_pattern(parameter):
        return parameter[len("regex:"):]
    return None


def get_expected_states(action, parameter, device_id):
    """
    Returns a list of acceptable state strings for a given action and parameter.
    If parameter is a regex pattern (prefixed with 'regex:'), returns a list
    containing the regex pattern string (without the prefix).
    """
    if action == "turn_on":
        return ["on"]
    elif action == "turn_off":
        return ["off"]
    elif action == "lock":
        # Supports lock entities ('locked') and boolean helpers ('on')
        return ["locked", "on"]
    elif action == "unlock":
        # Supports lock entities ('unlocked') and boolean helpers ('off')
        return ["unlocked", "off"]
    elif action == "set_value":
        if parameter is not None and str(parameter) != "null":
            if is_regex_pattern(parameter):
                return [get_regex_pattern(parameter)]
            try:
                val_float = float(parameter)
                return [str(parameter), f"{val_float:.1f}", f"{val_float:.0f}"]
            except (ValueError, TypeError):
                return [str(parameter)]
        return []
    elif action == "get_state":
        return None
    return []

# ==========================================
# REUSABLE VALIDATION FUNCTION (importable)
# ==========================================
def validate_single_element(element, ha_url=DEFAULT_HA_URL, headers=None, cache=None):
    """
    Validates a single test element against the live Home Assistant state.

    Args:
        element (dict): A test element with keys: id, prompt, expected_device,
                        expected_action, expected_parameter, expected_status.
        ha_url (str): Home Assistant base URL.
        headers (dict): HTTP headers for HA API authentication. If None, default
                        headers are generated using the token file / default password.
        cache (dict): Optional cache dict for entity state fetches.

    Returns:
        dict with keys:
            - id: element id
            - result: one of "match", "mismatch", "query", "skipped", "not_found"
            - live_state: the live state string (or None if not applicable)
            - expected_device: the expected device id
            - expected_action: the expected action
            - message: human-readable verification message
    """
    if headers is None:
        headers = get_ha_headers()
    if cache is None:
        cache = {}

    pid = element.get("id", "N/A")
    expected_device = element.get("expected_device")
    expected_action = element.get("expected_action", "null")
    expected_param = element.get("expected_parameter")
    expected_status = element.get("expected_status")

    result_obj = {
        "id": pid,
        "result": "skipped",
        "live_state": None,
        "expected_device": expected_device,
        "expected_action": expected_action,
        "message": "SKIPPED ⚪"
    }

    if expected_status in ("failure", "indeterminado") or not expected_device or expected_device in ("null", "multiple"):
        result_obj["message"] = "SKIPPED ⚪ (Invalid/Ambiguous)"
        return result_obj

    success, data = fetch_entity_state(ha_url, expected_device, headers, cache)

    if not success:
        result_obj["result"] = "not_found"
        result_obj["message"] = "MISMATCH ✗ (Entity not found)"
        return result_obj

    live_state = str(data.get("state", "unknown"))
    if expected_action == "set_value" and expected_device and str(expected_device).startswith("climate."):
        attributes = data.get("attributes", {}) or {}
        temperature_value = attributes.get("temperature")
        if temperature_value is not None:
            live_state = str(temperature_value)

    if is_regex_pattern(expected_param):
        # Regex-based validation: expected_parameter is prefixed with 'regex:'
        # Evaluated first so it also works for state queries (get_state)
        pattern = get_regex_pattern(expected_param)
        try:
            if re.fullmatch(pattern, live_state):
                result_obj["result"] = "match"
                result_obj["message"] = f"MATCH ✓ (regex '{pattern}' matches live state: {live_state})"
            else:
                result_obj["result"] = "mismatch"
                result_obj["message"] = f"MISMATCH ✗ (regex '{pattern}' does not match live state: {live_state})"
        except re.error as exc:
            result_obj["result"] = "mismatch"
            result_obj["message"] = f"MISMATCH ✗ (invalid regex '{pattern}': {exc})"
        return result_obj

    result_obj["live_state"] = live_state
    acceptable_states = get_expected_states(expected_action, expected_param, expected_device)

    if acceptable_states is None:  # State query (get_state)
        result_obj["result"] = "query"
        result_obj["message"] = f"QUERY ℹ (live state: {live_state})"
    elif live_state in acceptable_states:
        result_obj["result"] = "match"
        result_obj["message"] = f"MATCH ✓ (live state: {live_state})"
    else:
        result_obj["result"] = "mismatch"
        result_obj["message"] = f"MISMATCH ✗ (live state: {live_state})"

    return result_obj

def validate_elements(elements, ha_url=DEFAULT_HA_URL, headers=None, cache=None, verbose=True):
    """
    Validates a list of test elements against the live Home Assistant state.

    Args:
        elements (list): List of test element dicts.
        ha_url (str): Home Assistant base URL.
        headers (dict): HTTP headers for HA API authentication. If None, default
                        headers are generated.
        cache (dict): Optional cache dict for entity state fetches.
        verbose (bool): If True, prints a table of results.

    Returns:
        dict with keys: passed, failed, queries, skipped, results (list of result dicts)
    """
    if headers is None:
        headers = get_ha_headers()
    if cache is None:
        cache = {}

    passed_count = 0
    failed_count = 0
    query_count = 0
    skipped_count = 0
    results = []

    if verbose:
        print(f"{'ID':<7} | {'PROMPT':<40} | {'EXPECTED ENTITY':<38} | {'ACTION':<10} | {'LIVE STATE':<10} | {'VERIFICATION'}")
        print("=" * 132)

    for item in elements:
        ptext = item.get("prompt", "")
        ptext_disp = ptext[:38] + ".." if len(ptext) > 40 else ptext

        res = validate_single_element(item, ha_url, headers, cache)
        results.append(res)

        live_display = res["live_state"] if res["live_state"] else "N/A"
        if res["result"] == "not_found":
            live_display = "NOT FOUND"

        if verbose:
            print(f"{res['id']:<7} | {ptext_disp:<40} | {str(res['expected_device']):<38} | {str(res['expected_action']):<10} | {live_display:<10} | {res['message']}")

        if res["result"] == "match":
            passed_count += 1
        elif res["result"] == "mismatch" or res["result"] == "not_found":
            failed_count += 1
        elif res["result"] == "query":
            query_count += 1
        else:
            skipped_count += 1

    if verbose:
        print("=" * 132)
        print(f"[SUMMARY] Results: {passed_count} Matches (✓) | {failed_count} Mismatches (✗) | {query_count} State Queries (ℹ) | {skipped_count} Skipped (⚪)")

    return {
        "passed": passed_count,
        "failed": failed_count,
        "queries": query_count,
        "skipped": skipped_count,
        "results": results
    }

def main():
    parser = argparse.ArgumentParser(
        description="Query and verify Home Assistant entity states against test-prompts.json"
    )
    parser.add_argument("--url", default=DEFAULT_HA_URL, help="Home Assistant base URL (default: http://localhost:8123)")
    parser.add_argument("--password", default=DEFAULT_API_PASSWORD, help="Home Assistant API password (default: testing-key)")
    parser.add_argument("--token", help="Home Assistant Bearer Token (optional)")
    parser.add_argument("--all", action="store_true", help="Fetch ALL entities from Home Assistant instead of test-prompt evaluation")
    parser.add_argument("--json", action="store_true", help="Output results in raw JSON format")
    parser.add_argument("--json-input", help="JSON string of a single test element or array of elements to validate (bypasses test-prompts.json)")
    parser.add_argument(
        "--log-level",
        default=None,
        choices=["debug", "info", "warning", "error", "critical"],
        help="Logging level (default: env LOG_LEVEL, config.json, or INFO)",
    )

    args = parser.parse_args()
    configure_logging(cli_level=args.log_level)

    headers = get_ha_headers(api_password=args.password, bearer_token=args.token)

    logger.info("====================================================================================================")
    logger.info(" Home Assistant Test Prompt State Verifier")
    logger.info("====================================================================================================")
    logger.info("Target URL: %s", args.url)

    is_connected, msg = check_ha_connection(args.url, headers)
    if not is_connected:
        logger.error("Could not connect to Home Assistant at %s: %s", args.url, msg)
        if "401" in str(msg):
            logger.info("Authentication required. To authorize API access in Home Assistant:")
            logger.info("  1. Open Home Assistant in your browser: http://localhost:8123")
            logger.info("  2. Go to Profile (bottom left) -> 'Long-Lived Access Tokens' -> Create Token")
            logger.info("  3. Save the token string into file: data/ha-token.txt (or pass --token <TOKEN>)")
        else:
            logger.info("Hint: make sure Home Assistant is running: python ha-demo/run_demo.py start")
        sys.exit(1)

    logger.info("Connected to Home Assistant API (%s)", msg)

    if args.all:
        res = requests.get(f"{args.url}/api/states", headers=headers, timeout=5)
        if res.status_code == 200:
            all_states = res.json()
            if args.json:
                print(json.dumps(all_states, indent=2, ensure_ascii=False))
            else:
                print(f"{'ENTITY ID':<45} | {'STATE':<15} | {'FRIENDLY NAME'}")
                print("-" * 85)
                for item in all_states:
                    eid = item.get("entity_id", "")
                    state = item.get("state", "unknown")
                    fname = item.get("attributes", {}).get("friendly_name", "")
                    print(f"{eid:<45} | {state:<15} | {fname}")
            return
        else:
            logger.error("Failed to fetch all states: HTTP %s", res.status_code)
            sys.exit(1)

    if args.json_input:
        try:
            parsed = json.loads(args.json_input)
            prompts = parsed if isinstance(parsed, list) else [parsed]
            logger.info("Validating %s element(s) from --json-input argument", len(prompts))
        except json.JSONDecodeError as e:
            logger.error("Invalid JSON provided to --json-input: %s", e)
            sys.exit(1)
    else:
        prompts = load_test_prompts(TEST_PROMPTS_FILE)

    if args.json:
        results = validate_elements(prompts, ha_url=args.url, headers=headers, verbose=False)
        print(json.dumps(results, indent=2, ensure_ascii=False))
    else:
        validate_elements(prompts, ha_url=args.url, headers=headers, verbose=True)

if __name__ == "__main__":
    main()