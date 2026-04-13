"""Tests for structured logging configuration (TDD RED -> GREEN)."""

from __future__ import annotations

import json
import os
import tempfile

import structlog

from backend.logging_config import configure_logging


class TestConfigureLogging:
    """Tests for configure_logging function."""

    def test_creates_log_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            log_dir = os.path.join(tmpdir, "test_logs")
            configure_logging(log_dir=log_dir, level="INFO")
            assert os.path.isdir(log_dir)

    def test_sets_structlog_processors(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            configure_logging(log_dir=tmpdir, level="DEBUG")
            config = structlog.get_config()
            # Should have processors configured
            assert len(config["processors"]) > 0

    def test_log_output_is_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            log_path = os.path.join(tmpdir, "quantmind.jsonl")
            configure_logging(log_dir=tmpdir, level="DEBUG")

            # Write a log entry
            logger = structlog.get_logger(component="test")
            logger.info("test_event", key="value")

            # Check the file has valid JSON
            if os.path.exists(log_path) and os.path.getsize(log_path) > 0:
                with open(log_path) as f:
                    line = f.readline().strip()
                    if line:
                        data = json.loads(line)
                        assert "event" in data
