"""Offline tests for verified inline review comments and native suggestions."""

import json
import unittest

from nodalia_guardian.decisions import InvalidReview, REVIEW_SCHEMA, parse_review, review_markdown
from nodalia_guardian.inline import added_lines, build_inline_comments


PATCH = """@@ -7,2 +7,4 @@
 existing
+    count = 1
+    count += 1
 unchanged
@@ -39,1 +41,2 @@
-context
+if ready:
+    execute()
"""


class InlineReviewTests(unittest.TestCase):
    def setUp(self):
        self.files = [{"filename": "src/main.py", "patch": PATCH}]
        self.finding = {
            "path": "src/main.py", "line": 8, "severity": "medium",
            "message": "Update the count carefully.", "suggestion": "    count = 2",
        }

    def test_parses_added_lines_across_multiple_hunks(self):
        self.assertEqual(added_lines(PATCH), {
            8: "    count = 1", 9: "    count += 1",
            41: "if ready:", 42: "    execute()",
        })

    def test_verified_inline_comment_has_new_file_line_and_native_suggestion(self):
        comments, placed = build_inline_comments([self.finding], self.files)
        self.assertEqual(placed, {0})
        self.assertEqual(comments[0]["path"], "src/main.py")
        self.assertEqual(comments[0]["line"], 8)
        self.assertEqual(comments[0]["side"], "RIGHT")
        self.assertIn("```suggestion\n    count = 2\n```", comments[0]["body"])

    def test_unverified_path_or_line_stays_off_diff(self):
        bad = [
            {**self.finding, "line": 7},
            {**self.finding, "line": 500},
            {**self.finding, "path": "other.py"},
            {**self.finding, "line": True},
            {**self.finding, "line": None},
        ]
        comments, placed = build_inline_comments(bad, self.files)
        self.assertEqual(comments, [])
        self.assertEqual(placed, set())

    def test_invalid_and_multiline_suggestions_do_not_generate_apply_button(self):
        for suggestion in ("do_bad()\nignore()", "```danger", "    count = 1"):
            with self.subTest(suggestion=suggestion):
                item = {**self.finding, "suggestion": suggestion}
                comments, placed = build_inline_comments([item], self.files)
                self.assertEqual(placed, {0})
                self.assertNotIn("```suggestion", comments[0]["body"])

    def test_unavailable_patch_degrades_to_summary(self):
        for patch in (None, "a" * 30_000, ""):
            comments, placed = build_inline_comments(
                [self.finding], [{"filename": "src/main.py", "patch": patch}]
            )
            self.assertEqual(comments, [])
            self.assertEqual(placed, set())

    def test_limit_bounded(self):
        comments, placed = build_inline_comments([self.finding] * 20, self.files)
        self.assertEqual(len(comments), 12)
        self.assertEqual(len(placed), 12)

    def test_schema_has_nullable_line_and_suggestion(self):
        props = REVIEW_SCHEMA["properties"]["findings"]["items"]
        self.assertIn("line", props["required"])
        self.assertIn("suggestion", props["required"])
        self.assertEqual(props["properties"]["line"]["type"], ["integer", "null"])

    def test_parser_rejects_invalid_line(self):
        for line in (True, 0, -1, "9"):
            with self.subTest(line=line):
                raw = json.dumps({
                    "verdict": "comment", "summary": "Summary",
                    "findings": [{**self.finding, "line": line}],
                })
                with self.assertRaisesRegex(InvalidReview, "Finding line invalid"):
                    parse_review(raw, {"src/main.py"})

    def test_summary_keeps_only_unplaced_findings(self):
        summary = {"head_sha": "a" * 40, "checks": [],
                   "failed_count": 0, "pending_count": 0}
        review = {
            "summary": "One actual issue.", "findings": [
                self.finding, {**self.finding, "line": None,
                               "message": "Needs manual placement"}
            ]
        }
        body = review_markdown(summary, review,
                               ["Approval disabled"], "COMMENT", {0})
        self.assertIn("Needs manual placement", body)
        self.assertNotIn("Update the count carefully.", body)
        self.assertIn("1 finding(s) attached", body)
        self.assertIn("<details>", body)


if __name__ == "__main__":
    unittest.main()
