"""scripts/db/migrate.py without a database, and re-run safety of the migration files."""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location("migrate", ROOT / "scripts" / "db" / "migrate.py")
migrate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(migrate)


def test_repo_migrations_are_numbered_in_order():
    files = migrate.migration_files()
    versions = [int(v) for v, _ in files]
    assert versions == sorted(versions) and versions[:4] == [1, 2, 3, 4]


def test_bad_and_duplicate_names(tmp_path):
    (tmp_path / "001_a.sql").write_text("")
    (tmp_path / "01_b.sql").write_text("")
    with pytest.raises(ValueError, match="Two migrations"):
        migrate.migration_files(tmp_path)
    (tmp_path / "01_b.sql").unlink()
    (tmp_path / "notes.sql").write_text("")
    with pytest.raises(ValueError, match="must look like"):
        migrate.migration_files(tmp_path)


def test_safe_url_hides_the_password():
    url = "postgresql://postgres.abc:S3cr3t@aws-0-ap-south-1.pooler.supabase.com:5432/postgres"
    shown = migrate.safe_url(url)
    assert "S3cr3t" not in shown and "pooler.supabase.com:5432" in shown


def test_main_needs_the_url(monkeypatch, capsys):
    monkeypatch.delenv("SUPABASE_DB_URL", raising=False)
    assert migrate.main([]) == 2
    monkeypatch.setenv("SUPABASE_DB_URL", "https://x.supabase.co")
    assert migrate.main([]) == 2
    assert "postgresql://" in capsys.readouterr().out


@pytest.mark.parametrize("path", sorted((ROOT / "supabase" / "migrations").glob("*.sql")),
                         ids=lambda p: p.name)
def test_migrations_are_safe_to_re_run(path):
    """Every CREATE is IF NOT EXISTS / OR REPLACE, or follows a DROP ... IF EXISTS."""
    sql = path.read_text()
    for kind, name in re.findall(r"CREATE (POLICY|TRIGGER)\s+\"?([\w]+)\"?", sql):
        assert re.search(rf"DROP {kind} IF EXISTS \"?{name}\"?", sql), f"{kind} {name}"
    for kind in ("TABLE", "INDEX"):
        for stmt in re.findall(rf"CREATE (?:UNIQUE )?{kind}\s+(?!IF NOT EXISTS)(\w+)", sql):
            pytest.fail(f"CREATE {kind} {stmt} without IF NOT EXISTS")
    assert not re.search(r"CREATE FUNCTION", sql), "use CREATE OR REPLACE FUNCTION"
