"""Decrypt the launch list for the launch email (coming_soon.py).

1. In the Supabase SQL editor run:
       select email, language, created_at from public.launch_list order by created_at;
   and download the result as CSV.
2. Run, with the same DATA_ENCRYPTION_KEYS the app uses set in your shell
   (never commit it, never paste it into a chat):
       python -m tools.export_launch_list launch_list.csv > addresses.csv
3. Send the email, delete addresses.csv, then empty the table:
       delete from public.launch_list;
"""

import csv
import sys

import crypto_service


def main(path: str) -> int:
    if not crypto_service.is_enabled():
        print("Set DATA_ENCRYPTION_KEYS first.", file=sys.stderr)
        return 1
    writer = csv.writer(sys.stdout)
    writer.writerow(["email", "language"])
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            writer.writerow([crypto_service.decrypt(row["email"]), row.get("language", "en")])
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]) if len(sys.argv) == 2 else 2)
