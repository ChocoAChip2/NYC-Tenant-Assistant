"""Migration files must be named exactly as the live database records them.

The Supabase GitHub integration compares the live migration history with
supabase/migrations/ by version (the digits before the first "_"). When a
migration is applied through the dashboard, the CLI or an assistant tool,
Supabase stamps it with a 14-digit UTC timestamp; a file named with only a
date (20261007_x.sql) never matches, and the "Supabase Preview" check fails
with "Remote migration versions not found in local migrations directory".

After applying a migration, name its file <version>_<name>.sql using the
version from `select version, name from supabase_migrations.schema_migrations`.
"""

import os
import re
import unittest

MIGRATIONS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "supabase", "migrations")


class MigrationNameTests(unittest.TestCase):
    def test_every_file_has_a_full_timestamp_version(self):
        for name in os.listdir(MIGRATIONS):
            with self.subTest(name=name):
                self.assertRegex(name, r"^\d{14}_[a-z0-9_]+\.sql$")

    def test_versions_are_unique(self):
        versions = [name.split("_", 1)[0] for name in os.listdir(MIGRATIONS)]
        self.assertEqual(len(versions), len(set(versions)))


if __name__ == "__main__":
    unittest.main()
