"""
Software updates, as the owner sees them: check → download → install.

This module is the unprivileged half. It finds out whether a newer signed
release exists, downloads it (resumably — a multi-gigabyte bundle must not
restart from zero because Wi-Fi dropped), checks it against the signed
manifest, and hands it to the root-owned update engine through atlas-helper.
The engine verifies everything again, installs, health-checks and rolls back
on its own; while it works (including across the app restart it causes) the
app relays the engine's public status.

Progress goes to every connected client as the Socket.IO event
"update_progress" and is always available from GET /api/update/state, so a
page refresh, a closed phone app or an app restart never loses track of an
update — and only one update can run at a time.

Privacy: checking sends nothing but an HTTP request for a public file, with a
generic User-Agent. No device id, location, usage data or anything else.
"""
import hashlib
import json
import os
import shutil
import sys
import threading
import time
import urllib.error
import urllib.request

import atlas_blocking
import atlas_paths
import atlas_version

DEFAULT_RELEASES_URL = "https://github.com/atlas-navigate/atlas-control/releases"
MANIFEST = "atlas-release.json"
SIGNATURE = "atlas-release.json.sig"
USER_AGENT = "Atlas-Updater/1"
AUTO_CHECK_INTERVAL_S = 6 * 3600
CHUNK = 256 * 1024
SPACE_MARGIN = 1024 * 1024 * 1024
ENGINE_STATUS = "/run/atlas-update/status.json"
ENGINE_RESULT = "/var/lib/atlas-update/public/last_result.json"
ENGINE_INSTALLED = "/var/lib/atlas-update/installed.json"
KEYRING = "/usr/local/lib/atlas/keys/keyring.json"
DEV_KEYS = "/etc/atlas/dev-keys"
DEV_MODE = "/etc/atlas/dev-mode"
# An offline Atlas can boot with its clock in 1970; TLS to the release host
# cannot work until the clock is plausible again.
PLAUSIBLE_EPOCH = 1735689600      # 2025-01-01

MSG_OFFLINE = "Atlas is offline. Connect to the Internet to check for updates."
MSG_NO_TIME = "Waiting for Atlas's clock to synchronise before checking for updates."
MSG_PAUSED = "Download paused — waiting for Internet connection."
MSG_NOT_VERIFIED = "Update could not be verified."
MSG_NO_SPACE = "There is not enough free storage for this update."
# Shown after the outcome: what is running now and what the owner can do.
MSG_NEXT_STEP = ("Your maps, conversations and settings are unchanged. Try again later; "
                 "if it happens again, free some storage first.")

# Customer-facing names for every stage (the UI shows these as a checklist).
STAGES = [
    ("checking", "Checking for updates"),
    ("preparing_download", "Preparing download"),
    ("downloading", "Downloading"),
    ("verifying", "Verifying download"),
    ("preparing", "Preparing installation"),
    ("installing", "Installing"),
    ("validating", "Validating installation"),
    ("restarting", "Restarting Atlas"),
    ("reconnecting", "Reconnecting"),
    ("complete", "Update complete"),
]
STAGE_LABEL = dict(STAGES)


def _verify_module():
    """The engine's verifier ships inside every release (system/atlas_engine)."""
    path = atlas_paths.code("system")
    if path not in sys.path:
        sys.path.insert(0, path)
    from atlas_engine import verify
    return verify


def _read_json(path, default=None):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


class UpdateManager:
    def __init__(self, emit=None, helper_run=None, releases_url=None, clock=time.time,
                 opener=None, sleep=None, audit=None):
        self.emit = emit or (lambda event, data: None)
        # audit(action, **detail): the local security log (audit_log.record).
        self.audit = audit or (lambda action, **detail: None)
        self.helper_run = helper_run
        self.releases_url = (releases_url or os.environ.get("ATLAS_RELEASES_URL")
                             or DEFAULT_RELEASES_URL).rstrip("/")
        self.clock = clock
        self.sleep = sleep or atlas_blocking.sleep
        self.opener = opener or urllib.request.build_opener()
        self.dir = os.path.join(atlas_paths.UPDATES_DIR, "ready")
        self.state_path = os.path.join(atlas_paths.UPDATES_DIR, "state.json")
        self._lock = threading.RLock()
        self._job = None
        self._cancel = threading.Event()
        self._last_emit = 0.0
        self.state = self._load()

    # ── persisted state ──────────────────────────────────────────────────
    def _load(self):
        st = _read_json(self.state_path, {}) or {}
        st.setdefault("state", "idle")
        # A download interrupted by a restart resumes as paused, not "running".
        if st["state"] in ("checking", "downloading", "verifying"):
            st["state"] = "paused" if st.get("manifest") else "idle"
            st["message"] = "Download paused — it will continue shortly." if st.get("manifest") else ""
        return st

    def _save(self):
        os.makedirs(atlas_paths.UPDATES_DIR, exist_ok=True)
        tmp = self.state_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.state, f)
        os.replace(tmp, self.state_path)

    def _set(self, force_emit=True, **fields):
        with self._lock:
            self.state.update(fields)
            self.state["updated"] = self.clock()
            self._save()
        self._publish(force=force_emit)

    # ── public view ──────────────────────────────────────────────────────
    def snapshot(self):
        """Everything a client needs to draw the Software Update page."""
        with self._lock:
            st = dict(self.state)
        info = atlas_version.info()
        manifest = st.get("manifest") or {}
        total = st.get("total_bytes") or (manifest.get("bundle") or {}).get("size")
        done = st.get("downloaded_bytes") or 0
        state = st.get("state", "idle")
        stage = st.get("stage") or {
            "checking": "checking", "downloading": "downloading", "paused": "downloading",
            "verifying": "verifying", "ready": "verifying", "installing": "installing",
        }.get(state)
        percent = None
        if state in ("downloading", "paused") and total:
            percent = round(min(100.0, 100.0 * done / total), 1)
        snap = {
            "state": state,
            "stage": stage,
            "stage_label": STAGE_LABEL.get(stage, ""),
            "stages": [{"id": k, "label": v} for k, v in STAGES],
            "current_version": info["version"],
            "version": manifest.get("version"),
            "notes": manifest.get("notes", []),
            "progress_percent": percent,
            "downloaded_bytes": done if state in ("downloading", "paused", "verifying", "ready") else None,
            "total_bytes": total,
            "bytes_per_second": st.get("bytes_per_second") if state == "downloading" else None,
            "message": st.get("message") or "",
            "error": st.get("error"),
            "can_cancel": state in ("checking", "downloading", "paused", "verifying", "ready"),
            "last_check": st.get("last_check"),
            "result": st.get("result"),
            # The update just installed (version and notes), until another
            # release is found or installed.
            "updated": st.get("installed_update") if st.get("result") == "success" else None,
        }
        if state == "installing":
            engine = _read_json(ENGINE_STATUS, {}) or {}
            snap["stage"] = engine.get("stage") or "installing"
            if snap["stage"] in ("complete", "success"):
                snap["stage"] = "restarting"
            snap["stage_label"] = STAGE_LABEL.get(snap["stage"], engine.get("label") or "Installing")
            snap["message"] = engine.get("message") or "Installing Atlas. Do not power off Atlas."
            snap["can_cancel"] = bool(engine.get("can_cancel"))
        return snap

    def _publish(self, force=False):
        now = time.monotonic()
        if not force and now - self._last_emit < 0.25:
            return
        self._last_emit = now
        try:
            self.emit("update_progress", self.snapshot())
        except Exception:
            pass

    # ── checking ─────────────────────────────────────────────────────────
    def _allowed_url(self, url):
        return url.startswith("https://") or (os.path.exists(DEV_MODE) and url.startswith("http://"))

    def _get(self, url, limit, headers=None):
        if not self._allowed_url(url):
            raise urllib.error.URLError("insecure update URL")
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, **(headers or {})})
        with self.opener.open(req, timeout=30) as r:
            data = r.read(limit + 1)
        if len(data) > limit:
            raise ValueError("response too large")
        return data

    def _release_dir_url(self):
        """URL of the directory holding the newest release's files.

        GitHub: resolve .../releases/latest to its tag once, then fetch every
        file from that tag, so a release being published mid-check can never
        mix one release's manifest with another's signature.
        """
        base = self.releases_url
        if "github.com/" not in base:
            return base
        req = urllib.request.Request(f"{base}/latest", method="HEAD", headers={"User-Agent": USER_AGENT})

        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *a, **k):
                return None

        opener = urllib.request.build_opener(NoRedirect)
        try:
            opener.open(req, timeout=20)
            raise urllib.error.URLError("no release has been published yet")
        except urllib.error.HTTPError as exc:
            location = exc.headers.get("Location", "")
            if exc.code not in (301, 302, 303, 307, 308) or "/tag/" not in location:
                raise urllib.error.URLError("no release has been published yet")
            tag = location.rsplit("/tag/", 1)[1].split("?")[0].strip("/")
        return f"{base}/download/{tag}"

    def _trusted_keys(self, verify):
        return verify.load_keyring(KEYRING, DEV_KEYS, os.path.exists(DEV_MODE))

    def _installed_seq(self):
        installed = _read_json(ENGINE_INSTALLED, {}) or {}
        return max(int(installed.get("seq", 0) or 0), atlas_version.seq())

    def check(self, user_initiated=False):
        """Look for a newer release. Returns the snapshot."""
        with self._lock:
            if self._job is not None and self._job.is_alive():
                return self.snapshot()
            prev = self.state.get("state")
            if prev in ("downloading", "paused", "verifying", "ready", "installing"):
                return self.snapshot()
        self._set(state="checking", stage="checking", message="Checking for updates…", error=None)
        if self.clock() < PLAUSIBLE_EPOCH:
            self._set(state="idle", stage=None, message=MSG_NO_TIME)
            return self.snapshot()
        verify = _verify_module()
        try:
            rel = self._release_dir_url()
            mbytes = self._get(f"{rel}/{MANIFEST}", verify.MAX_MANIFEST_BYTES)
            sig = self._get(f"{rel}/{SIGNATURE}", 64 * 1024)
        except (urllib.error.URLError, OSError, ValueError) as exc:
            reason = str(getattr(exc, "reason", exc))
            offline = "no release has been published" not in reason
            self._set(state="idle", stage=None, last_check=self.clock(),
                      message=MSG_OFFLINE if offline else "No updates are available yet.",
                      error=None if not user_initiated or offline else reason)
            return self.snapshot()
        try:
            manifest, _kid = verify.verify_manifest(mbytes, sig, self._trusted_keys(verify), arch="aarch64")
        except verify.VerifyError as exc:
            self._set(state="idle", stage=None, message=MSG_NOT_VERIFIED, error=str(exc),
                      last_check=self.clock())
            return self.snapshot()
        if manifest["seq"] <= self._installed_seq():
            self._set(state="up_to_date", stage=None, manifest=None, last_check=self.clock(),
                      message="Atlas is up to date.")
            return self.snapshot()
        bundle_url = manifest["bundle"].get("url") or f"{rel}/{manifest['bundle']['name']}"
        os.makedirs(self.dir, exist_ok=True)
        self._write(MANIFEST, mbytes)
        self._write(SIGNATURE, sig)
        self._set(state="available", stage=None, manifest=manifest, bundle_url=bundle_url,
                  release_dir=rel, last_check=self.clock(), result=None, installed_update=None,
                  message=f"Atlas {manifest['version']} is available.")
        return self.snapshot()

    def _write(self, name, data):
        tmp = os.path.join(self.dir, name + ".tmp")
        with open(tmp, "wb") as f:
            f.write(data)
        os.replace(tmp, os.path.join(self.dir, name))

    # ── downloading ──────────────────────────────────────────────────────
    def start(self, install=True):
        """Download (resuming if possible) and, when install=True, install."""
        with self._lock:
            if self._job is not None and self._job.is_alive():
                return self.snapshot()          # one update at a time
            if self.state.get("state") == "installing":
                return self.snapshot()
            if not self.state.get("manifest"):
                return dict(self.snapshot(), error="No update is available to install.")
            self._cancel.clear()
            self.state["install_when_ready"] = bool(install)
            self._job = self._spawn(self._run_job)
        return self.snapshot()

    def _spawn(self, fn):
        try:
            import gevent
            return _GreenletJob(gevent.spawn(fn))
        except Exception:                     # tests without gevent
            t = threading.Thread(target=fn, daemon=True)
            t.start()
            return t

    def cancel(self):
        with self._lock:
            state = self.state.get("state")
        if state == "installing":
            if self.helper_run:
                self.helper_run("update.cancel")
            return self.snapshot()
        self._cancel.set()
        if not (self._job is not None and self._job.is_alive()):
            self._discard_download()
            self._set(state="available" if self.state.get("manifest") else "idle",
                      stage=None, message="Update cancelled.", downloaded_bytes=0)
        return self.snapshot()

    def forget(self):
        """Factory reset: stop any download, delete downloaded updates and
        forget update state. Refused (False) while the engine is installing."""
        with self._lock:
            if self.state.get("state") == "installing":
                return False
        self._cancel.set()
        job = self._job
        if job is not None and job.is_alive():
            job.join(10)
        shutil.rmtree(self.dir, ignore_errors=True)
        with self._lock:
            self.state = {"state": "idle"}
        self._set(message="")
        return True

    def _bundle_paths(self):
        name = self.state["manifest"]["bundle"]["name"]
        final = os.path.join(self.dir, name)
        return final, final + ".part", final + ".part.json"

    def _discard_download(self):
        if not self.state.get("manifest"):
            return
        for p in self._bundle_paths():
            try:
                os.unlink(p)
            except FileNotFoundError:
                pass

    def _free_space_ok(self, size):
        st = os.statvfs(atlas_paths.UPDATES_DIR)
        return st.f_bavail * st.f_frsize >= 3 * size + SPACE_MARGIN

    def _run_job(self):
        try:
            manifest = self.state["manifest"]
            bundle = manifest["bundle"]
            final, part, sidecar = self._bundle_paths()
            if not os.path.exists(final):
                if not self._free_space_ok(bundle["size"]):
                    self._set(state="available", stage=None, message=MSG_NO_SPACE, error=MSG_NO_SPACE)
                    return
                if not self._download(bundle, part, sidecar):
                    return
                self._set(state="verifying", stage="verifying", message="Verifying download…")
                bad = atlas_blocking.call(_verify_module().verify_bundle_file, part, bundle)
                if bad is not None:
                    self._handle_bad_download(bad, bundle, part, sidecar)
                    return
                os.replace(part, final)
                try:
                    os.unlink(sidecar)
                except FileNotFoundError:
                    pass
            self._set(state="ready", stage="verifying", message=f"Atlas {manifest['version']} is ready to install.",
                      downloaded_bytes=bundle["size"], total_bytes=bundle["size"])
            self.audit("update_verified", version=str(manifest["version"]))
            if self.state.get("install_when_ready"):
                self.install()
        except _Cancelled:
            self._discard_download()
            self._set(state="available", stage=None, message="Update cancelled.", downloaded_bytes=0)
        except Exception as exc:
            self._set(state="available", stage=None, message="The update could not be downloaded.",
                      error=str(exc)[:300])

    def _handle_bad_download(self, bad, bundle, part, sidecar):
        attempts = int(self.state.get("repair_attempts", 0)) + 1
        if attempts > 3 or bad < 0:
            self._discard_download()
            self._set(state="available", stage=None, message=MSG_NOT_VERIFIED, error=MSG_NOT_VERIFIED,
                      repair_attempts=0, downloaded_bytes=0)
            return
        # Keep the good prefix and fetch again from the first damaged chunk.
        keep = bad * bundle["chunk_size"]
        with open(part, "r+b") as f:
            f.truncate(keep)
        self._set(repair_attempts=attempts, downloaded_bytes=keep)
        self._run_job()

    def _download(self, bundle, part, sidecar):
        """Fetch into part, resuming across failures. True when complete."""
        url = self.state.get("bundle_url")
        meta = _read_json(sidecar, {}) or {}
        if meta.get("sha256") != bundle["sha256"] or meta.get("size") != bundle["size"]:
            for p in (part, sidecar):
                try:
                    os.unlink(p)
                except FileNotFoundError:
                    pass
            with open(sidecar, "w") as f:
                json.dump({"sha256": bundle["sha256"], "size": bundle["size"], "url": url}, f)
        backoff = 5
        while True:
            if self._cancel.is_set():
                raise _Cancelled()
            have = os.path.getsize(part) if os.path.exists(part) else 0
            if have >= bundle["size"]:
                return True
            self._set(state="downloading", stage="downloading" if have else "preparing_download",
                      message=f"Downloading Atlas {self.state['manifest']['version']}",
                      downloaded_bytes=have, total_bytes=bundle["size"], error=None)
            try:
                self._fetch_range(url, part, have, bundle["size"])
                backoff = 5
            except (urllib.error.URLError, OSError, TimeoutError, ConnectionError) as exc:
                if isinstance(exc, urllib.error.HTTPError) and exc.code in (403, 404, 410):
                    self._set(state="available", stage=None, message="The update is no longer available.",
                              error=f"HTTP {exc.code}")
                    return False
                self._set(state="paused", stage="downloading", message=MSG_PAUSED, bytes_per_second=0)
                waited = 0
                while waited < backoff:
                    if self._cancel.is_set():
                        raise _Cancelled()
                    self.sleep(1)
                    waited += 1
                backoff = min(backoff * 2, 60)

    def _fetch_range(self, url, part, offset, size):
        headers = {"User-Agent": USER_AGENT, "Accept-Encoding": "identity"}
        if offset:
            headers["Range"] = f"bytes={offset}-"
        if not self._allowed_url(url):
            raise urllib.error.URLError("insecure update URL")
        req = urllib.request.Request(url, headers=headers)
        with self.opener.open(req, timeout=30) as r:
            status = getattr(r, "status", 200)
            if offset and status == 206:
                crange = r.headers.get("Content-Range", "")
                expected = f"bytes {offset}-{size - 1}/{size}"
                if crange.replace(" ", "") != expected.replace(" ", ""):
                    raise urllib.error.URLError(f"unexpected Content-Range {crange!r}")
                mode = "ab"
            elif status == 200:
                offset = 0                     # server ignored Range: start over
                mode = "wb"
            else:
                raise urllib.error.URLError(f"unexpected HTTP status {status}")
            done = offset
            started = time.monotonic()
            window_bytes, window_start = 0, started
            speed = self.state.get("bytes_per_second") or 0
            with open(part, mode) as f:
                while True:
                    if self._cancel.is_set():
                        raise _Cancelled()
                    block = r.read(CHUNK)
                    if not block:
                        break
                    if done + len(block) > size:
                        raise urllib.error.URLError("download is larger than the signed size")
                    f.write(block)
                    done += len(block)
                    window_bytes += len(block)
                    now = time.monotonic()
                    if now - window_start >= 1.0:
                        inst = window_bytes / (now - window_start)
                        speed = inst if not speed else 0.7 * speed + 0.3 * inst
                        window_bytes, window_start = 0, now
                    with self._lock:
                        self.state.update(downloaded_bytes=done, bytes_per_second=int(speed),
                                          state="downloading", stage="downloading",
                                          message=f"Downloading Atlas {self.state['manifest']['version']}")
                    self._publish()
                f.flush()
                os.fsync(f.fileno())
            self._save()
            if done < size:
                raise ConnectionError("connection closed early")

    # ── installing ───────────────────────────────────────────────────────
    def install(self):
        """Hand a verified download to the root engine."""
        if not self.helper_run:
            self._set(message="Updates can only be installed on an Atlas device.", error="no helper")
            return self.snapshot()
        with self._lock:
            if self.state.get("state") not in ("ready",):
                return self.snapshot()
        self._set(state="installing", stage="preparing", message="Preparing installation…",
                  handed_off_at=self.clock(), result=None)
        out, rc = self.helper_run("update.apply")
        self.audit("update_install_started" if rc == 0 else "update_install_refused",
                   version=str((self.state.get("manifest") or {}).get("version", "")))
        if rc != 0:
            self._set(state="ready", stage="verifying", message="The update could not be started.",
                      error=out[:300])
        return self.snapshot()

    def reconcile_after_restart(self):
        """On app start: finish the story of an install that restarted us."""
        with self._lock:
            if self.state.get("state") != "installing":
                return
        engine = _read_json(ENGINE_STATUS, {}) or {}
        result = _read_json(ENGINE_RESULT, {}) or {}
        if engine.get("engine_state") not in (None, "IDLE") and engine.get("stage") not in (
                "success", "failed", "rolled_back", "cancelled", "interrupted"):
            return                             # engine still working; keep relaying
        outcome = result.get("result")
        version = (self.state.get("manifest") or {}).get("version")
        running = atlas_version.version()
        if outcome == "success":
            self._finish("success", f"Updated to Atlas {running}.")
        elif outcome == "rolled_back":
            self._finish("rolled_back", f"The update to Atlas {version or 'the new version'} could not be "
                                        f"completed, so Atlas went back to {running}. {MSG_NEXT_STEP}")
        elif outcome == "cancelled":
            self._finish(outcome, f"The update was cancelled. Atlas {running} is still installed.")
        elif outcome in ("failed", "interrupted"):
            self._finish(outcome, f"{result.get('message') or 'The update was not installed.'} "
                                  f"Atlas {running} is still installed. {MSG_NEXT_STEP}")
        else:
            self._finish("unknown", f"Atlas {version or ''} installation status is unknown.")

    def _finish(self, result, message):
        self.audit({"success": "update_installed", "rolled_back": "update_rolled_back"}.get(result, "update_failed"),
                   version=str((self.state.get("manifest") or {}).get("version", "")), result=str(result))
        keep_manifest = result != "success"
        manifest = self.state.get("manifest") or {}
        installed = {"version": atlas_version.version(), "notes": manifest.get("notes", []),
                     "at": self.clock()} if result == "success" else None
        self._set(state="up_to_date" if result == "success" else ("available" if keep_manifest and
                                                                    self.state.get("manifest") else "idle"),
                  stage="complete" if result == "success" else None,
                  result=result, message=message, downloaded_bytes=0, installed_update=installed,
                  manifest=None if result == "success" else self.state.get("manifest"))
        if result == "success":
            shutil.rmtree(self.dir, ignore_errors=True)

    def poll_engine(self):
        """Relay engine progress while installing; returns True to keep polling."""
        with self._lock:
            installing = self.state.get("state") == "installing"
        if not installing:
            return False
        self.reconcile_after_restart()
        self._publish(force=True)
        return self.state.get("state") == "installing"


class _Cancelled(Exception):
    pass


class _GreenletJob:
    def __init__(self, g):
        self.g = g

    def is_alive(self):
        return not self.g.dead

    def join(self, timeout=None):
        self.g.join(timeout)


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()
