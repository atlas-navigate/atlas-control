"""Schema versioning: migrations run once, back up first, and never go backwards."""
import os
import sqlite3
import tempfile
import unittest
from unittest import mock

import atlas_paths
import database


class MigrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db = os.path.join(self.tmp, "t.db")
        self.backups = os.path.join(self.tmp, "backups")
        p1 = mock.patch.object(database, "DB_PATH", self.db)
        p2 = mock.patch.object(atlas_paths, "BACKUP_DIR", self.backups)
        p1.start(); p2.start()
        self.addCleanup(p1.stop); self.addCleanup(p2.stop)
        database._local.conn = None
        self.addCleanup(setattr, database._local, "conn", None)

    def test_fresh_database_reaches_current_schema(self):
        database.init_db()
        self.assertEqual(database.schema_version(), database.SCHEMA_VERSION)
        conn = sqlite3.connect(self.db)
        floor = conn.execute("SELECT value FROM atlas_meta WHERE key='compat_floor'").fetchone()[0]
        self.assertEqual(int(floor), database.COMPAT_FLOOR)

    def test_migrations_run_once_and_back_up_first(self):
        database.init_db()
        backups = os.listdir(self.backups)
        self.assertEqual(len(backups), 1)
        self.assertEqual(os.stat(os.path.join(self.backups, backups[0])).st_mode & 0o077, 0)
        self.assertEqual(database.apply_migrations(), [])
        self.assertEqual(len(os.listdir(self.backups)), 1)

    def test_failed_migration_rolls_back_and_keeps_version(self):
        database.init_db()
        start = database.schema_version()

        def boom(conn):
            conn.execute("CREATE TABLE half_done (x INTEGER)")
            raise RuntimeError("migration failed")

        with mock.patch.object(database, "MIGRATIONS",
                               database.MIGRATIONS + [(start + 1, "boom", boom)]):
            with self.assertRaises(RuntimeError):
                database.apply_migrations()
        self.assertEqual(database.schema_version(), start)
        conn = sqlite3.connect(self.db)
        self.assertIsNone(conn.execute(
            "SELECT name FROM sqlite_master WHERE name='half_done'").fetchone())

    def test_newer_database_is_left_alone(self):
        database.init_db()
        conn = sqlite3.connect(self.db)
        conn.execute(f"PRAGMA user_version = {database.SCHEMA_VERSION + 5}")
        conn.commit(); conn.close()
        self.assertEqual(database.apply_migrations(), [])
        self.assertEqual(database.schema_version(), database.SCHEMA_VERSION + 5)

    def test_embed_marker_from_a_newer_release_is_not_clobbered(self):
        database.init_db()
        conn = sqlite3.connect(self.db)
        conn.execute("INSERT INTO ai_documents (title, content, embedding, created_at) "
                     "VALUES ('t', 'c', ?, 0)", ('{"v":5}',))
        conn.execute("UPDATE ai_settings SET value='5' WHERE key='embed_format_v'")
        conn.commit(); conn.close()
        database.init_db()
        conn = sqlite3.connect(self.db)
        self.assertEqual(conn.execute("SELECT embedding FROM ai_documents").fetchone()[0], '{"v":5}')
        self.assertEqual(conn.execute(
            "SELECT value FROM ai_settings WHERE key='embed_format_v'").fetchone()[0], "5")
