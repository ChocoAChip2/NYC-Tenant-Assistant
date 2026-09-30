"""The quarterly legal-library workflow keeps the properties the design relies on.

No Actions run is possible from a test, so this pins the workflow text:
the schedule, least privilege, the no-token fallback to a Supabase-free
dry run, the issue on change, and a failed refresh failing the job.
"""

import os
import unittest

WORKFLOW = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        ".github", "workflows", "refresh-legal-library.yml")


class RefreshWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(WORKFLOW, encoding="utf-8") as handle:
            cls.text = handle.read()

    def test_runs_quarterly_and_on_demand(self):
        self.assertIn('cron: "0 9 1 1,4,7,10 *"', self.text)
        self.assertIn("workflow_dispatch:", self.text)

    def test_least_privilege(self):
        self.assertIn("permissions:\n  contents: read\n  issues: write\n", self.text)
        self.assertNotIn("SERVICE_ROLE", self.text)

    def test_no_install_step(self):
        # tools/corpus is standard library only; a pip install would be a
        # supply-chain surface the design deliberately avoids.
        self.assertNotIn("pip install", self.text)

    def test_without_a_token_it_is_a_dry_run_that_leaves_supabase_alone(self):
        self.assertIn('if [ -z "${CORPUS_INGEST_TOKEN:-}" ]; then', self.text)
        self.assertIn("unset SUPABASE_URL SUPABASE_KEY", self.text)
        self.assertIn("args+=(--dry-run)", self.text)

    def test_opens_an_issue_only_when_the_law_changed(self):
        self.assertIn("if: steps.refresh.outputs.changed == 'true'", self.text)
        self.assertIn('--body-file "$RUNNER_TEMP/summary.md"', self.text)

    def test_a_failed_refresh_fails_the_job(self):
        self.assertIn("if: steps.refresh.outputs.exit_code != '0'", self.text)
        self.assertIn('echo "exit_code=$code" >> "$GITHUB_OUTPUT"', self.text)


if __name__ == "__main__":
    unittest.main()
