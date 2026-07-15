import json
import os
import tempfile
import unittest

from paperhub.json_io import read_json, write_json_atomic


class JsonIoTest(unittest.TestCase):
    def test_atomic_write_round_trips_unicode_without_temp_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "nested", "index.json")

            write_json_atomic(path, {"title": "中文", "papers": [1, 2]})

            self.assertEqual(read_json(path), {"title": "中文", "papers": [1, 2]})
            self.assertEqual(os.listdir(os.path.dirname(path)), ["index.json"])
            self.assertEqual(os.stat(path).st_mode & 0o777, 0o644)
            with open(path, encoding="utf-8") as handle:
                self.assertEqual(json.load(handle)["title"], "中文")

    def test_read_json_returns_default_for_invalid_content(self):
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8") as handle:
            handle.write("not json")
            handle.flush()
            self.assertEqual(read_json(handle.name, {"safe": True}), {"safe": True})


if __name__ == "__main__":
    unittest.main()
