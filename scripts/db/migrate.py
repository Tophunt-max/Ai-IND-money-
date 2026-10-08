#!/usr/bin/env python3
"""Apply supabase/migrations/*.sql to the database, each file once, in order.

    SUPABASE_DB_URL=postgresql://... python scripts/db/migrate.py [--dry-run]

Run by .github/workflows/db-migrate.yml before every EC2 deploy (and by hand from the
Actions tab). Applied files are recorded in ``public.schema_migrations`` (version, file
name, sha256); each new file runs in its own transaction together with its record, so a
failing file changes nothing and stops the run. A recorded file whose content changed
is reported, not re-run: add a new numbered file instead.

The migrations are written to be safe to re-run (IF NOT EXISTS, DROP ... IF EXISTS), so
the first automatic run on a database set up by hand in the SQL Editor is harmless.

Exit codes: 0 ok, 1 a migration failed, 2 bad configuration.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import sys
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS = ROOT / "supabase" / "migrations"
FILE_RE = re.compile(r"^(\d+)_([A-Za-z0-9_\-]+)\.sql$")
LOCK_ID = 7_352_118_404  # pg_advisory_lock key: one migrator at a time

TRACKING_SQL = """
CREATE TABLE IF NOT EXISTS public.schema_migrations (
    version TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    checksum TEXT NOT NULL,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
ALTER TABLE public.schema_migrations ENABLE ROW LEVEL SECURITY;
"""


def migration_files(directory: Path = MIGRATIONS) -> list[tuple[str, Path]]:
    """[(version, path)] sorted by number. Raises ValueError on a bad or duplicate name."""
    found: dict[str, Path] = {}
    for path in directory.glob("*.sql"):
        m = FILE_RE.match(path.name)
        if not m:
            raise ValueError(f"Migration file name must look like 005_name.sql: {path.name}")
        version = str(int(m.group(1)))
        if version in found:
            raise ValueError(f"Two migrations with number {version}: "
                             f"{found[version].name}, {path.name}")
        found[version] = path
    return sorted(found.items(), key=lambda item: int(item[0]))


def checksum(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def safe_url(url: str) -> str:
    """The URL without its password, for logs."""
    p = urlparse(url)
    host = f"{p.hostname}:{p.port}" if p.port else (p.hostname or "?")
    return f"{p.scheme}://{p.username or '?'}:***@{host}{p.path}"


def connect(url: str):
    import psycopg2

    if "sslmode=" not in url:
        url += ("&" if "?" in url else "?") + "sslmode=require"
    conn = psycopg2.connect(url, connect_timeout=20)
    conn.autocommit = False
    return conn


def run(url: str, dry_run: bool = False, directory: Path = MIGRATIONS, out=print) -> int:
    try:
        files = migration_files(directory)
    except ValueError as exc:
        out(f"ERROR {exc}")
        return 2
    out(f"Database: {safe_url(url)}")
    where = directory.relative_to(ROOT) if directory.is_relative_to(ROOT) else directory
    out(f"Migrations: {len(files)} file(s) in {where}")

    conn = connect(url)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT pg_advisory_lock(%s)", (LOCK_ID,))
            cur.execute(TRACKING_SQL)
            conn.commit()
            cur.execute("SELECT version, name, checksum FROM public.schema_migrations")
            applied = {row[0]: (row[1], row[2]) for row in cur.fetchall()}

        pending = []
        for version, path in files:
            digest = checksum(path)
            if version in applied:
                if applied[version][1] != digest:
                    out(f"WARN  {path.name}: changed since it was applied; not re-run "
                        "(put the change in a new numbered file)")
                else:
                    out(f"ok    {path.name}")
                continue
            pending.append((version, path, digest))

        if not pending:
            out("Up to date: nothing to apply.")
            return 0
        if dry_run:
            for _, path, _ in pending:
                out(f"TODO  {path.name}")
            out(f"Dry run: {len(pending)} migration(s) would be applied.")
            return 0

        for version, path, digest in pending:
            try:
                with conn.cursor() as cur:
                    cur.execute(path.read_text(encoding="utf-8"))
                    cur.execute(
                        "INSERT INTO public.schema_migrations (version, name, checksum) "
                        "VALUES (%s, %s, %s)", (version, path.name, digest))
                conn.commit()
                out(f"APPLY {path.name}")
            except Exception as exc:
                conn.rollback()
                out(f"FAIL  {path.name}: {str(exc).strip()}")
                out("Nothing from this file was applied. Fix it and run again.")
                return 1
        out(f"Done: {len(pending)} migration(s) applied.")
        return 0
    finally:
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT pg_advisory_unlock(%s)", (LOCK_ID,))
            conn.commit()
        except Exception:
            pass
        conn.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--dry-run", action="store_true", help="list pending files only")
    args = parser.parse_args(argv)
    url = os.environ.get("SUPABASE_DB_URL", "").strip()
    if not url:
        print("ERROR SUPABASE_DB_URL is not set (Supabase → Connect → Session pooler URI)")
        return 2
    if not url.startswith(("postgres://", "postgresql://")):
        print("ERROR SUPABASE_DB_URL must start with postgresql://")
        return 2
    try:
        return run(url, dry_run=args.dry_run)
    except Exception as exc:
        print(f"ERROR could not connect to {safe_url(url)}: {str(exc).strip()}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
