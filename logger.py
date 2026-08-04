#!/usr/bin/env python3
"""
Centralized logging configuration for the benchmark-slm-home project.

Supports INFO and DEBUG levels (plus standard WARNING, ERROR, CRITICAL).
Log level can be configured via (highest priority first):
  1. CLI argument --log-level
  2. Environment variable LOG_LEVEL
  3. data/config.json "log_level" key
  4. Default: INFO

Usage:
    from logger import get_logger

    logger = get_logger(__name__)
    logger.info("Starting benchmark")
    logger.debug("Detailed value: %s", value)
"""

import json
import logging
import os
import sys
from pathlib import Path

BASE_DIR = Path(__file__).parent.resolve()
CONFIG_PATH = BASE_DIR / "data" / "config.json"

# Map string level names to logging constants
LOG_LEVELS = {
    "debug": logging.DEBUG,
    "info": logging.INFO,
    "warning": logging.WARNING,
    "warn": logging.WARNING,
    "error": logging.ERROR,
    "critical": logging.CRITICAL,
}

DEFAULT_LEVEL = "info"

_configured = False
_level = logging.INFO


def _load_config_level():
    """Load log level from data/config.json if available."""
    try:
        if CONFIG_PATH.exists():
            with open(CONFIG_PATH, "r", encoding="utf-8") as fh:
                config = json.load(fh)
            return config.get("log_level")
    except (json.JSONDecodeError, OSError):
        pass
    return None


def _resolve_level(cli_level=None):
    """Determine the effective log level from CLI arg, env var, or config.

    Priority: CLI arg > env var LOG_LEVEL > config.json > default (INFO).
    """
    for source in (cli_level, os.getenv("LOG_LEVEL"), _load_config_level(), DEFAULT_LEVEL):
        if source:
            level_str = str(source).strip().lower()
            if level_str in LOG_LEVELS:
                return LOG_LEVELS[level_str]
    return logging.INFO


def configure_logging(cli_level=None):
    """Configure the root logger with the specified level.

    Args:
        cli_level: Log level from CLI argument (e.g., "debug", "info").
                   If None, falls back to env var LOG_LEVEL, then config.json, then INFO.

    Returns:
        The resolved logging level (int).
    """
    global _configured, _level

    _level = _resolve_level(cli_level)

    root_logger = logging.getLogger()
    root_logger.setLevel(_level)

    if _configured:
        # Update existing handlers' level on reconfiguration
        for handler in root_logger.handlers:
            handler.setLevel(_level)
        return _level

    # Remove any existing handlers to avoid duplicates
    root_logger.handlers.clear()

    # Create console handler that writes to stderr (keeps stdout clean for structured output)
    console_handler = logging.StreamHandler(sys.stderr)
    console_handler.setLevel(_level)

    # Create formatter
    formatter = logging.Formatter(
        fmt="%(asctime)s [%(levelname)s] [%(name)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    console_handler.setFormatter(formatter)
    root_logger.addHandler(console_handler)

    _configured = True
    return _level


def get_logger(name=None):
    """Get a logger with the given name.

    Automatically configures logging on first call if not already configured.

    Args:
        name: Logger name (typically __name__ or module name).

    Returns:
        logging.Logger instance.
    """
    if not _configured:
        configure_logging()
    return logging.getLogger(name)


def get_level():
    """Return the current effective log level (int)."""
    return _level


def get_level_name():
    """Return the current effective log level name (uppercase string)."""
    return logging.getLevelName(_level)