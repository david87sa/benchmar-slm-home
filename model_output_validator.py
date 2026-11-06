"""Validation helpers for model output against the expected prompt semantics."""

from __future__ import annotations

from collections import Counter


def normalize_prompt_type(prompt_type):
    if prompt_type is None:
        return ""
    return str(prompt_type).strip().lower().replace("_", " ").replace("-", " ")


def _to_expected_value(value):
    if value is None:
        return None
    if isinstance(value, str):
        normalized = value.strip()
        if normalized.lower() in ("null", "none"):
            return None
        return normalized
    return value


def _compare_parameter(expected_parameter, actual_parameter):
    expected = _to_expected_value(expected_parameter)
    actual = _to_expected_value(actual_parameter)

    if expected is None:
        return actual is None

    if isinstance(expected, str) and expected.startswith("regex:"):
        import re

        pattern = expected[len("regex:") :]
        if actual is None:
            return False
        try:
            return re.fullmatch(pattern, str(actual)) is not None
        except re.error:
            return False

    if isinstance(expected, (int, float)) and not isinstance(expected, bool):
        try:
            return float(actual) == float(expected)
        except (TypeError, ValueError):
            return str(actual) == str(expected)

    return str(actual).lower() == str(expected).lower()


def _split_csv_like(value):
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return [str(v).strip() for v in value if str(v).strip()]
    text = str(value).strip()
    if not text or text.lower() in ("null", "none"):
        return []
    return [part.strip() for part in text.split(",") if part.strip()]


def _normalized_action_name(value):
    if value is None:
        return ""
    text = str(value).strip().lower()
    return {
        "turn_on_device": "turn_on",
        "turn_off_device": "turn_off",
        "lock_device": "lock",
        "unlock_device": "unlock",
        "set_temperature": "set_value",
        "set_temperature_device": "set_value",
        "set_device_value": "set_value",
        "get_device_state": "get_state",
    }.get(text, text)


def validate_model_output(item, parsed_output):
    """Return (is_valid, details) based on the prompt type and expected values.

    `parsed_output` contains:
      - is_valid
      - model_device
      - model_action
      - model_param
      - model_status
      - tool_calls_list
    """
    prompt_type = item.get("type", "")
    expected_device = item.get("expected_device")
    expected_action = item.get("expected_action")
    expected_parameter = item.get("expected_parameter")
    expected_status = item.get("expected_status")

    is_valid = parsed_output.get("is_valid", False)
    model_device = parsed_output.get("model_device")
    model_action = parsed_output.get("model_action")
    model_param = parsed_output.get("model_param")
    model_status = parsed_output.get("model_status")
    tool_calls_list = parsed_output.get("tool_calls_list") or []

    normalized_type = normalize_prompt_type(prompt_type)

    def matches_device():
        return str(model_device).lower() == str(expected_device).lower()

    def matches_action():
        return str(model_action).lower() == str(expected_action).lower()

    def matches_status():
        return str(model_status).lower() == str(expected_status).lower()

    def classification_error(message):
        return False, {"rule": message}

    if "invalid" in normalized_type or "out of domain" in normalized_type or "ambiguous" in normalized_type or "incomplete" in normalized_type:
        if expected_status in ("failure", "indeterminado"):
            return (
                model_status == "indeterminado"
                and str(model_device).lower() in ("null", "none", "")
                and str(model_action).lower() in ("null", "none", "")
                and str(model_param).lower() in ("null", "none", "", "none")
            ), {"rule": "invalid_or_ambiguous_requires_indeterminado"}

    if "multiple" in normalized_type or "composite" in normalized_type:
        expected_devices = _split_csv_like(expected_device)
        expected_actions = [_normalized_action_name(item) for item in _split_csv_like(expected_action)]

        actual_devices = []
        actual_actions = []

        for tool_call in tool_calls_list:
            function = tool_call.get("function", {}) if isinstance(tool_call, dict) else {}
            func_name = function.get("name") if isinstance(function, dict) else ""
            args = function.get("arguments", {}) if isinstance(function, dict) else {}
            if isinstance(args, str):
                try:
                    import json

                    args = json.loads(args)
                except json.JSONDecodeError:
                    args = {}
            if isinstance(args, dict):
                device_id = args.get("device_id")
                if device_id not in (None, "null", "multiple"):
                    actual_devices.append(str(device_id).lower())
                actual_actions.append(_normalized_action_name(function.get("name", "")))

        if model_device not in (None, "null", "multiple"):
            actual_devices.extend(str(device).lower() for device in _split_csv_like(model_device))
        if model_action not in (None, "null", "multiple"):
            actual_actions.extend(_normalized_action_name(action) for action in _split_csv_like(model_action))

        expected_device_norm = [str(device).lower() for device in expected_devices]
        expected_action_norm = [str(action).lower() for action in expected_actions]

        device_match = Counter(actual_devices) == Counter(expected_device_norm) if expected_device_norm else len(actual_devices) > 1
        action_match = Counter(actual_actions) == Counter(expected_action_norm) if expected_action_norm else True
        multi_tool_present = len(tool_calls_list) > 1 or str(model_device).lower() == "multiple"

        if expected_status == "success":
            return multi_tool_present and device_match and action_match, {"rule": "multi_device_composite_validation"}
        return (
            multi_tool_present and model_status == "indeterminado" and str(model_device).lower() in ("null", "none", "")
        ), {"rule": "multi_device_ambiguous_requires_indeterminado"}

    if "parameter extraction" in normalized_type:
        if expected_parameter in (None, "null", "none"):
            return is_valid and matches_device() and matches_action() and matches_status(), {"rule": "parameter_extraction_basic"}
        return (
            is_valid
            and matches_device()
            and matches_action()
            and _compare_parameter(expected_parameter, model_param)
            and matches_status()
        ), {"rule": "parameter_extraction_full"}

    if "simple direct" in normalized_type or "implicit" in normalized_type:
        return is_valid and matches_device() and matches_action() and matches_status(), {"rule": "device_action_validation"}

    if "informal" in normalized_type:
        if str(expected_status).lower() == "indeterminado":
            return model_status == "indeterminado", {"rule": "informal_invalid_requires_indeterminado"}
        return is_valid and matches_device() and matches_action() and matches_status(), {"rule": "informal_success_requires_device_action"}

    if "state query" in normalized_type:
        return is_valid and matches_device() and matches_action() and matches_status(), {"rule": "state_query_validation"}

    return is_valid and matches_device() and matches_action() and matches_status(), {"rule": "default_validation"}