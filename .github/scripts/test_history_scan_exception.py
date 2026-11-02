import ast
from pathlib import Path
import unittest
from history_scan_exception import verify_status_literal


class HistoricalExceptionTests(unittest.TestCase):
    def test_different_print_literal_is_not_approved(self):
        self.assertFalse(verify_status_literal('\n' * 390 + 'print(\n    "other status text"\n)\n'))

    def test_assignment_at_same_line_is_not_approved(self):
        self.assertFalse(verify_status_literal('\n' * 391 + 'api_key = "fixture value"\n'))

    def test_print_variable_is_not_approved(self):
        self.assertFalse(verify_status_literal('\n' * 390 + 'print(\n    token\n)\n'))

    def test_adjacent_print_does_not_gain_an_exception(self):
        self.assertFalse(verify_status_literal('\n' * 391 + 'print(\n    "other text"\n)\n'))


if __name__ == '__main__':
    unittest.main()
