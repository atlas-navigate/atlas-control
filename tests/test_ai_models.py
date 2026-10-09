"""Ray's models: the catalog, per-hardware profiles, expert overrides, the
settings migration, and switching models (with a fake Ollama)."""
import io
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
from unittest import mock

import atlas_paths
import database
import model_profiles

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ORIN_NANO = {"ram_mb": 7620, "cpus": 6, "board": "NVIDIA Jetson Orin Nano Engineering Reference Developer Kit Super"}
ORIN_NX_16 = {"ram_mb": 15600, "cpus": 8, "board": "NVIDIA Jetson Orin NX 16GB"}


class CatalogTests(unittest.TestCase):
    def test_catalog_is_complete(self):
        ids = [m["id"] for m in model_profiles.models()]
        self.assertIn("qwen2.5:3b", ids)
        self.assertIn("qwen3.5:4b", ids)
        self.assertEqual(model_profiles.default_id(), "qwen2.5:3b")
        for m in model_profiles.models():
            with self.subTest(model=m["id"]):
                self.assertTrue(m["label"] and m["variants"] and m["sampling"])
                self.assertFalse(m.get("think", False))            # answers start at once on a Jetson
                rams = [v.get("min_ram_mb", 0) for v in m["variants"]]
                self.assertEqual(rams, sorted(rams, reverse=True), "variants are listed best first")
                self.assertEqual(m["variants"][-1].get("min_ram_mb", 0), 0, "something always fits")

    def test_qwen35_uses_its_official_non_thinking_sampling(self):
        s = model_profiles.find("qwen3.5:4b")["sampling"]
        self.assertEqual((s["temperature"], s["top_p"], s["top_k"], s["presence_penalty"]), (0.7, 0.8, 20, 1.5))

    def test_installer_facts(self):
        self.assertEqual(model_profiles.min_ollama(), "0.17.1")
        tags = model_profiles.preinstall_tags(ORIN_NANO)
        self.assertEqual(tags, ["qwen2.5:3b", "qwen3.5:4b", "qwen3-embedding:0.6b"])
        # install.sh runs it with the system python3, no venv: standard library only.
        out = subprocess.run([sys.executable if not os.path.exists("/usr/bin/python3") else "/usr/bin/python3",
                              "-I", os.path.join(REPO, "model_profiles.py"), "--min-ollama"],
                             capture_output=True, text=True, check=True).stdout.strip()
        self.assertEqual(out, "0.17.1")


class ProfileTests(unittest.TestCase):
    def test_build_and_context_follow_the_hardware(self):
        nano = model_profiles.resolve({}, "qwen3.5:4b", ORIN_NANO)
        self.assertEqual((nano["tag"], nano["quant"]), ("qwen3.5:4b", "Q4_K_M"))
        self.assertEqual(nano["options"]["num_thread"], 6)
        self.assertEqual(nano["options"]["num_gpu"], -1)
        nx = model_profiles.resolve({}, "qwen3.5:4b", ORIN_NX_16)
        self.assertEqual((nx["tag"], nx["quant"]), ("qwen3.5:4b-q8_0", "Q8_0"))
        self.assertEqual(nx["options"]["num_thread"], 8)

    def test_default_model_keeps_todays_tuning(self):
        p = model_profiles.resolve({}, None, ORIN_NANO)
        self.assertEqual(p["id"], "qwen2.5:3b")
        o = p["options"]
        self.assertEqual((o["num_ctx"], o["num_batch"], o["num_thread"], o["num_predict"]), (4096, 512, 6, 1024))
        self.assertEqual((o["temperature"], o["top_p"], o["top_k"], o["repeat_penalty"]), (0.7, 0.8, 20, 1.05))

    def test_a_model_no_longer_offered_falls_back_to_the_default(self):
        p = model_profiles.resolve({"model": "qwen3.5:2b"}, hw=ORIN_NANO)
        self.assertEqual((p["id"], p["fallback_from"]), ("qwen2.5:3b", "qwen3.5:2b"))
        self.assertIsNone(model_profiles.resolve({"model": "qwen3.5:4b"}, hw=ORIN_NANO)["fallback_from"])

    def test_overrides_are_per_model_cleaned_and_clamped(self):
        settings = {"model": "qwen3.5:4b",
                    "overrides:qwen3.5:4b": json.dumps({"temperature": "0.4", "num_ctx": 999999,
                                                        "num_thread": 64, "bogus": 1, "top_k": "x"}),
                    "overrides:qwen2.5:3b": json.dumps({"temperature": 1.9})}
        p = model_profiles.resolve(settings, hw=ORIN_NANO)
        self.assertEqual(p["options"]["temperature"], 0.4)
        self.assertEqual(p["options"]["num_ctx"], model_profiles.max_context(model_profiles.find("qwen3.5:4b"), ORIN_NANO))
        self.assertEqual(p["options"]["num_thread"], 6)
        self.assertNotIn("bogus", p["overrides"])
        self.assertNotIn("top_k", p["overrides"])
        self.assertEqual(p["options"]["top_k"], 20)
        self.assertIn("num_ctx", p["clamped"])
        self.assertEqual(p["optimized"]["temperature"], 0.7)
        other = model_profiles.resolve(settings, "qwen2.5:3b", ORIN_NANO)
        self.assertEqual(other["options"]["temperature"], 1.9)             # its own, not qwen3.5's

    def test_keep_alive_is_bounded(self):
        self.assertEqual(model_profiles.resolve({"keep_alive_hours": "99"}, hw=ORIN_NANO)["keep_alive"], "24h")
        self.assertEqual(model_profiles.resolve({"keep_alive_hours": "x"}, hw=ORIN_NANO)["keep_alive"], "10h")

    def test_ray_stays_loaded_by_default(self):
        # Negative = no idle timeout in Ollama; navigation still unloads Ray when it needs the memory.
        for value in (None, "always", "ALWAYS"):
            prof = model_profiles.resolve({"keep_alive_hours": value} if value else {}, hw=ORIN_NANO)
            self.assertEqual((prof["keep_alive"], prof["keep_alive_hours"]), ("-1m", "always"))
        self.assertEqual(model_profiles.resolve({"keep_alive_hours": "4"}, hw=ORIN_NANO)["keep_alive_hours"], "4")


class SettingsMigrationTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.mkdtemp()
        self.db = os.path.join(tmp, "t.db")
        for p in (mock.patch.object(database, "DB_PATH", self.db),
                  mock.patch.object(atlas_paths, "BACKUP_DIR", os.path.join(tmp, "backups"))):
            p.start(); self.addCleanup(p.stop)
        database._local.conn = None
        self.addCleanup(setattr, database._local, "conn", None)

    def _old_database(self, rows):
        database.init_db()
        c = sqlite3.connect(self.db)
        c.execute("PRAGMA user_version = 3")
        c.executemany("INSERT OR REPLACE INTO ai_settings (key, value) VALUES (?, ?)", rows.items())
        c.commit(); c.close()
        database._local.conn = None
        database.apply_migrations()

    def test_hand_tuned_values_become_overrides_for_their_model(self):
        self._old_database({"model": "qwen2.5:3b", "temperature": "0.3", "top_k": "40", "num_ctx": "4096",
                            "num_gpu": "99", "num_predict": "1024"})
        s = database.ai_get_settings()
        for key in database.AI_RUNTIME_KEYS:
            self.assertNotIn(key, s)
        self.assertEqual(json.loads(s["overrides:qwen2.5:3b"]), {"temperature": 0.3, "top_k": 40})
        self.assertNotIn("overrides:qwen3.5:4b", s)

    def test_untouched_defaults_leave_no_overrides(self):
        self._old_database({"model": "qwen2.5:3b", "temperature": "0.7", "num_ctx": "4096", "num_gpu": "-1"})
        s = database.ai_get_settings()
        self.assertFalse([k for k in s if k.startswith("overrides:")])
        self.assertEqual(s["model"], "qwen2.5:3b")


# ── switching, against a fake Ollama ─────────────────────────────────────
class _Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()


class FakeOllama:
    """Enough of Ollama's HTTP API for the switch job."""

    def __init__(self):
        self.version = "0.32.1"
        self.installed = {"qwen2.5:3b", "qwen3-embedding:0.6b"}
        self.loaded = {}                      # name -> options
        self.fail_load = set()
        self.pull_lines = None                # None = succeeds
        self.calls = []
        self.order = []                       # ("chat"|"embed", model, options) as loaded
        self.lock = threading.Lock()

    def urlopen(self, req, timeout=None):
        url = req if isinstance(req, str) else req.full_url
        path = url.split("11434", 1)[1]
        body = json.loads(req.data) if not isinstance(req, str) and req.data else {}
        with self.lock:
            self.calls.append((path, body.get("model")))
        if path == "/api/version":
            return _Resp(json.dumps({"version": self.version}).encode())
        if path == "/api/tags":
            return _Resp(json.dumps({"models": [{"name": n} for n in sorted(self.installed)]}).encode())
        if path == "/api/ps":
            return _Resp(json.dumps({"models": [{"name": n, "size": 2 * 2**30, "size_vram": 2 * 2**30}
                                                for n in self.loaded]}).encode())
        if path == "/api/show":
            caps = ["embedding"] if "embedding" in body["model"] else ["completion", "thinking"]
            return _Resp(json.dumps({"capabilities": caps}).encode())
        if path == "/api/generate" and body.get("keep_alive") == 0:
            self.loaded.pop(body["model"], None)
            return _Resp(b"{}")
        if path == "/api/embed":
            self.loaded[body["model"]] = body.get("options")
            self.order.append(("embed", body["model"], body.get("options")))
            return _Resp(json.dumps({"embeddings": [[0.1] * 8]}).encode())
        if path == "/api/chat":
            model = body["model"]
            if model not in self.installed:
                raise urllib.error.HTTPError(url, 404, "model not found", {}, None)
            if model in self.fail_load:
                raise urllib.error.HTTPError(url, 500, "model requires more system memory", {}, None)
            self.loaded[model] = body.get("options")
            self.order.append(("chat", model, body.get("options")))
            return _Resp(json.dumps({"message": {"content": "ready"}, "eval_count": 12,
                                     "eval_duration": 1_000_000_000}).encode())
        if path == "/api/pull":
            lines = self.pull_lines
            if lines is None:
                lines = [{"status": "pulling", "digest": "sha256:a", "total": 100, "completed": 50},
                         {"status": "pulling", "digest": "sha256:a", "total": 100, "completed": 100},
                         {"status": "success"}]
                self.installed.add(body["model"])
            return _Resp(b"\n".join(json.dumps(l).encode() for l in lines) + b"\n")
        raise AssertionError(f"unexpected Ollama call {path}")


class SwitchTests(unittest.TestCase):
    def setUp(self):
        import ai_manager
        self.ai = ai_manager
        database._local.conn = None
        database.init_db()
        conn = database.get_db()
        conn.execute("DELETE FROM ai_settings WHERE key='model' OR key LIKE 'overrides:%' OR key LIKE 'measured:%'")
        conn.commit()
        self.ollama = FakeOllama()
        for p in (mock.patch.object(ai_manager.urllib.request, "urlopen", side_effect=self.ollama.urlopen),
                  mock.patch.object(model_profiles, "hardware", return_value=dict(ORIN_NANO)),
                  mock.patch.object(model_profiles, "available_mb", return_value=3000)):
            p.start(); self.addCleanup(p.stop)
        self.socketio = mock.Mock()
        self.mgr = ai_manager.AIManager(socketio=self.socketio)
        self.mgr._startup_warmup_done.set()
        self.mgr._set_startup_state("ready", ready=True)
        self.mgr._warmup_chat_model(self.mgr.runtime_settings())          # qwen2.5 is running

    def wait(self):
        deadline = time.time() + 10
        while time.time() < deadline:
            job = self.mgr.switch_state()
            if job and job.get("finished"):
                return job
            time.sleep(0.02)
        self.fail("switch did not finish")

    def test_switch_downloads_loads_tests_and_then_saves(self):
        self.mgr.switch_model("qwen3.5:4b")
        job = self.wait()
        self.assertEqual(job["result"], "success", job)
        self.assertEqual(database.ai_get_settings()["model"], "qwen3.5:4b")
        self.assertIn("qwen3.5:4b", self.ollama.loaded)
        self.assertNotIn("qwen2.5:3b", self.ollama.loaded)                   # unloaded first
        opts = self.ollama.loaded["qwen3.5:4b"]
        self.assertEqual((opts["num_ctx"], opts["presence_penalty"]), (4096, 1.5))
        stages = [c.args[1]["stage"] for c in self.socketio.emit.call_args_list if c.args[0] == "ai_model"]
        for stage in ("checking", "downloading", "waiting", "unloading", "loading", "testing", "done"):
            self.assertIn(stage, stages)
        self.assertEqual(json.loads(database.ai_get_settings()["measured:qwen3.5:4b"])["tok_s"], 12.0)
        self.assertTrue(self.mgr.is_ready())
        # On an 8 GB unit Qwen3.5 4B loads before the embedder, which moves to the CPU.
        loads = [(kind, model) for kind, model, _ in self.ollama.order[1:]]
        self.assertLess(loads.index(("chat", "qwen3.5:4b")), loads.index(("embed", "qwen3-embedding:0.6b")))
        self.assertEqual(self.ollama.loaded["qwen3-embedding:0.6b"], {"num_ctx": 1024, "num_gpu": 0})

    def test_switching_back_puts_the_embedder_on_the_gpu_first(self):
        self.ollama.installed.add("qwen3.5:4b")
        self.mgr.switch_model("qwen3.5:4b")
        self.assertEqual(self.wait()["result"], "success")
        self.ollama.order.clear()
        self.mgr.switch_model("qwen2.5:3b")
        self.assertEqual(self.wait()["result"], "success")
        self.assertEqual([(k, m) for k, m, _ in self.ollama.order][:2],
                         [("embed", "qwen3-embedding:0.6b"), ("chat", "qwen2.5:3b")])
        self.assertEqual(self.ollama.loaded["qwen3-embedding:0.6b"], {"num_ctx": 1024})
        self.assertNotIn("qwen3.5:4b", self.ollama.loaded)

    def test_the_choice_persists_and_is_what_boot_loads(self):
        self.ollama.installed.add("qwen3.5:4b")
        self.mgr.switch_model("qwen3.5:4b")
        self.assertEqual(self.wait()["result"], "success")
        # A reboot or an Atlas update: a new process with the same data/.
        self.ollama.loaded.clear()
        database._local.conn = None
        fresh = self.ai.AIManager(socketio=mock.Mock())
        self.ollama.order.clear()
        fresh._warmup()
        self.assertIn("qwen3.5:4b", self.ollama.loaded)
        self.assertEqual([k for k, _, _ in self.ollama.order][:2], ["chat", "embed"])
        self.assertEqual(fresh.runtime_settings()["model"], "qwen3.5:4b")

    def test_a_model_that_wont_load_puts_the_old_one_back(self):
        self.ollama.installed.add("qwen3.5:4b")
        self.ollama.fail_load.add("qwen3.5:4b")
        self.mgr.switch_model("qwen3.5:4b")
        job = self.wait()
        self.assertEqual(job["result"], "failed")
        self.assertIn("still using Qwen2.5 3B", job["message"])
        self.assertEqual(database.ai_get_settings()["model"], "qwen2.5:3b")
        self.assertIn("qwen2.5:3b", self.ollama.loaded)
        self.assertTrue(self.mgr.is_ready())

    def test_offline_download_fails_cleanly_without_touching_the_running_model(self):
        self.ollama.pull_lines = [{"error": "pull model manifest: dial tcp: lookup registry.ollama.ai: no such host"}]
        self.mgr.switch_model("qwen3.5:4b")
        job = self.wait()
        self.assertEqual(job["result"], "failed")
        self.assertIn("Couldn't download Qwen3.5 4B", job["error"])
        self.assertIn("qwen2.5:3b", self.ollama.loaded)
        self.assertFalse(any(p == "/api/generate" for p, _ in self.ollama.calls))   # nothing unloaded
        self.assertEqual(database.ai_get_settings().get("model", "qwen2.5:3b"), "qwen2.5:3b")

    def test_not_enough_memory_is_refused_before_unloading(self):
        self.ollama.installed.add("qwen3.5:4b")
        with mock.patch.object(model_profiles, "available_mb", return_value=200):
            self.mgr.switch_model("qwen3.5:4b")
            job = self.wait()
        self.assertEqual(job["result"], "failed")
        self.assertIn("enough free memory", job["error"])
        self.assertIn("qwen2.5:3b", self.ollama.loaded)

    def test_an_older_ai_engine_is_explained(self):
        self.ollama.version = "0.16.0"
        with self.assertRaises(self.ai.ModelSwitchError) as ctx:
            self.mgr.switch_model("qwen3.5:4b")
        self.assertIn("newer AI engine", str(ctx.exception))

    def test_one_switch_at_a_time_and_waits_for_the_answer_in_progress(self):
        self.ollama.installed.add("qwen3.5:4b")
        self.mgr._ollama_lock.acquire()                                    # an answer is streaming
        try:
            self.mgr.switch_model("qwen3.5:4b")
            with self.assertRaises(self.ai.ModelSwitchError) as ctx:
                self.mgr.switch_model("qwen2.5:3b")
            self.assertTrue(ctx.exception.busy)
            time.sleep(0.2)
            self.assertEqual(self.mgr.switch_state()["stage"], "waiting")
            self.assertIn("qwen2.5:3b", self.ollama.loaded)                # untouched while it answers
        finally:
            self.mgr._ollama_lock.release()
        self.assertEqual(self.wait()["result"], "success")

    def test_overrides_are_applied_by_reloading_and_saved_on_success(self):
        self.mgr.switch_model("qwen2.5:3b", overrides={"num_ctx": 2048, "temperature": 0.5})
        self.assertEqual(self.wait()["result"], "success")
        self.assertEqual(self.ollama.loaded["qwen2.5:3b"]["num_ctx"], 2048)
        self.assertEqual(json.loads(database.ai_get_settings()["overrides:qwen2.5:3b"]),
                         {"num_ctx": 2048, "temperature": 0.5})

    def test_a_new_embedding_model_rebuilds_the_index(self):
        self.ollama.installed.add("nomic-embedding-text")
        conn = database.get_db()
        conn.execute("UPDATE ai_documents SET embedding='[1]'")
        conn.commit()
        with mock.patch.object(self.mgr, "_embed_unembedded_docs") as reembed:
            self.mgr.switch_model("qwen2.5:3b", embed_model="nomic-embedding-text")
            self.assertEqual(self.wait()["result"], "success")
        reembed.assert_called_once()
        self.assertEqual(database.ai_get_settings()["embed_model"], "nomic-embedding-text")
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM ai_documents WHERE embedding IS NOT NULL").fetchone()[0], 0)
        database.ai_set_setting("embed_model", "qwen3-embedding:0.6b")

    def test_a_chat_model_is_not_accepted_as_the_embedder(self):
        self.ollama.installed.add("qwen3.5:4b")
        self.mgr.switch_model("qwen2.5:3b", embed_model="qwen3.5:4b")
        job = self.wait()
        self.assertEqual(job["result"], "failed")
        self.assertIn("not an embedding model", job["error"])


class RouteTests(unittest.TestCase):
    def setUp(self):
        import app
        self.app = app
        database._local.conn = None
        database.init_db()
        self.client = app.app.test_client()

    def put(self, path, body):
        return self.client.put(path, json=body, base_url="https://127.0.0.1",
                                environ_base={"REMOTE_ADDR": "127.0.0.1"})

    def test_settings_accept_behaviour_only(self):
        r = self.put("/api/ai/settings", {"rag_top_k": "99", "num_ctx": "65536", "temperature": "3",
                                          "overrides:qwen2.5:3b": "{}", "system_prompt": "Be brief."})
        body = r.get_json()
        self.assertEqual(r.status_code, 200)
        self.assertEqual(body["settings"]["rag_top_k"], "10")
        self.assertNotIn("num_ctx", body["settings"])
        self.assertEqual(sorted(body["ignored"]), ["num_ctx", "overrides:qwen2.5:3b", "temperature"])

    def test_models_list_describes_each_choice(self):
        with mock.patch.object(model_profiles, "hardware", return_value=dict(ORIN_NANO)):
            data = self.client.get("/api/ai/models", base_url="https://127.0.0.1",
                                   environ_base={"REMOTE_ADDR": "127.0.0.1"}).get_json()
        by_id = {m["id"]: m for m in data["models"]}
        self.assertEqual(set(by_id), {"qwen2.5:3b", "qwen3.5:4b"})
        q = by_id["qwen3.5:4b"]
        self.assertEqual((q["quant"], q["options"]["num_ctx"]), ("Q4_K_M", 4096))
        self.assertIn(q["status"], ("installed", "download", "needs_engine"))
        self.assertIn("num_ctx", data["override_keys"])
        self.assertEqual(sum(m["active"] for m in data["models"]), 1)

    def test_overrides_for_an_inactive_model_are_saved_cleaned(self):
        r = self.put("/api/ai/models/qwen3.5:4b/overrides", {"overrides": {"temperature": 5, "nope": 1}})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.get_json()["overrides"], {"temperature": 2.0})
        self.assertEqual(json.loads(database.ai_get_settings()["overrides:qwen3.5:4b"]), {"temperature": 2.0})
        r = self.client.delete("/api/ai/models/qwen3.5:4b/overrides", base_url="https://127.0.0.1",
                               environ_base={"REMOTE_ADDR": "127.0.0.1"})
        self.assertEqual(json.loads(database.ai_get_settings()["overrides:qwen3.5:4b"]), {})
        self.assertEqual(self.put("/api/ai/models/nope/overrides", {"overrides": {}}).status_code, 404)


if __name__ == "__main__":
    unittest.main()
