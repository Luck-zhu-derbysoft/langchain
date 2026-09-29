"""Check that business logs are kept in a single current-day file."""

import json
import logging
import os
import tempfile
import time
from pathlib import Path
from unittest import TestCase

from app.observability.logging_config import CurrentDayFileHandler, JsonFormatter


class TestCurrentDayFileHandler(TestCase):
    def test_replaces_previous_day_log_and_appends_on_restart(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            log_root = Path(directory)
            log_file = log_root / "app.log"
            log_file.write_text("previous day\n", encoding="utf-8")
            previous_day = time.time() - 2 * 86400
            os.utime(log_file, (previous_day, previous_day))

            logger = logging.getLogger("app.test.current_day")
            logger.setLevel(logging.INFO)
            logger.propagate = False
            try:
                for message in ("first", "second"):
                    handler = CurrentDayFileHandler(log_root)
                    handler.setFormatter(JsonFormatter())
                    logger.addHandler(handler)
                    logger.info(message)
                    logger.removeHandler(handler)
                    handler.close()

                self.assertEqual([path.name for path in log_root.iterdir()], ["app.log"])
                self.assertEqual(
                    [
                        json.loads(line)["message"]
                        for line in log_file.read_text(encoding="utf-8").splitlines()
                    ],
                    ["first", "second"],
                )
            finally:
                for handler in logger.handlers[:]:
                    logger.removeHandler(handler)
                    handler.close()
