import sqlite3

from database import applied_migration_versions, get_connection, initialize_database


def test_connection_enables_foreign_keys_busy_timeout_and_wal(tmp_path):
    connection = get_connection(tmp_path / "connection.db")
    try:
        assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert connection.execute("PRAGMA busy_timeout").fetchone()[0] == 5000
        assert connection.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
    finally:
        connection.close()


def test_migration_is_repeatable_and_preserves_existing_data(tmp_path):
    db = tmp_path / "legacy.db"
    initialize_database(db)
    with get_connection(db) as connection:
        connection.execute("INSERT INTO users(full_name,email,password_hash,created_at,updated_at) VALUES (?,?,?,?,?)", ("Legacy", "legacy@example.test", "hash", "now", "now"))
    initialize_database(db)
    initialize_database(db)
    assert applied_migration_versions(db) == [1]
    with sqlite3.connect(db) as connection:
        assert connection.execute("SELECT email FROM users").fetchone()[0] == "legacy@example.test"


def test_custom_database_location_is_supported(tmp_path):
    db = tmp_path / "custom" / "startup.db"
    initialize_database(db)
    assert db.exists()
    assert applied_migration_versions(db) == [1]
