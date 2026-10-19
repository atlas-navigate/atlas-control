"""
The update transaction: verify → stage → install → switch → health → commit,
or roll back. Every step is idempotent and preceded by a durable journal
write, so a crash, kill or power cut at any point leaves a state from which
boot-time recovery can either finish the step or undo it.

Journal states (in order):

  ACCEPTED        handed-over files copied into root-owned space
  VERIFIED        signature, device compatibility, bundle hash, free space
  STAGING         bundle unpacked into releases/.staging-<txid>
  ENGINE_SWITCH   a newer engine from the bundle installed and re-executed
  VENV            virtual environment ready (reused or built from wheels)
  STAGED          release complete at releases/<version> (.complete written)
  APPLYING_SYSTEM app stopped, DB state recorded, system files installed
  SWITCHED        current -> new release, services restarted
  HEALTH_CHECK    waiting for the new release to prove itself
  COMMITTING      new release adopted; previous kept for rollback
  ROLLING_BACK    restoring the previous release, its system files and,
                  if the new one migrated the database past what the old
                  one understands, the pre-migration database backup
  terminal: IDLE (with last_result success / rolled_back / failed /
  interrupted) or RECOVERY_FAILED.

Invariant: whenever the app can start, `current` points at a release with a
`.complete` marker whose system files are installed.
"""
import fcntl
import json
import os
import platform
import shutil
import sys
import time
import uuid

from . import ENGINE_VERSION, extract, fsutil, health, paths, perf, sysfiles, venvs, verify
from .runner import DEFAULT as DEFAULT_RUNNER

ORDER = ["ACCEPTED", "VERIFIED", "STAGING", "ENGINE_SWITCH", "VENV", "STAGED",
         "APPLYING_SYSTEM", "SWITCHED", "HEALTH_CHECK", "COMMITTING"]
CANCELLABLE = {"ACCEPTED", "VERIFIED", "STAGING", "ENGINE_SWITCH", "VENV", "STAGED"}
PRE_SWITCH = {"ACCEPTED", "VERIFIED", "STAGING", "ENGINE_SWITCH", "VENV", "STAGED"}

# What the app and the customer see for each state.
PUBLIC_STAGE = {
    "ACCEPTED": ("verifying", "Verifying download"),
    "VERIFIED": ("preparing", "Preparing installation"),
    "STAGING": ("preparing", "Preparing installation"),
    "ENGINE_SWITCH": ("preparing", "Preparing installation"),
    "VENV": ("preparing", "Preparing installation"),
    "STAGED": ("installing", "Installing"),
    "APPLYING_SYSTEM": ("installing", "Installing"),
    "SWITCHED": ("restarting", "Restarting Atlas"),
    "HEALTH_CHECK": ("validating", "Validating installation"),
    "COMMITTING": ("complete", "Update complete"),
    "ROLLING_BACK": ("rolling_back", "Restoring the previous version"),
    "RECOVERY_FAILED": ("failed", "Atlas needs attention"),
}

MSG_NOT_VERIFIED = "Update could not be verified."
MSG_ROLLED_BACK = "The update could not be completed. Atlas has restored the previous version."
MSG_INTERRUPTED = "The update was interrupted. Your Atlas is unchanged; you can try again."
MSG_NO_SPACE = "There is not enough free storage to install this update."

MANIFEST_NAME = "atlas-release.json"
SIG_NAME = "atlas-release.json.sig"
DIAG_KEPT = 20
VENV_ESTIMATE = 600 * 1024 * 1024
SPACE_MARGIN = 512 * 1024 * 1024


class EngineError(Exception):
    def __init__(self, message, public=None):
        super().__init__(message)
        self.public = public or message


class Cancelled(Exception):
    pass


class SimulatedCrash(BaseException):
    """Test-only: abandons the engine mid-step exactly like a power cut would,
    bypassing every exception handler."""


def read_boot_id():
    try:
        with open("/proc/sys/kernel/random/boot_id", encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return "unknown"


def release_info(release_dir):
    """Version facts of an installed release (or legacy checkout)."""
    info = fsutil.read_json(os.path.join(release_dir, "release.json"), {}) or {}
    return {
        "release": os.path.realpath(release_dir),
        "version": info.get("version", "legacy"),
        "seq": int(info.get("seq", 0) or 0),
        "schema_target": int(info.get("schema_target", 0) or 0),
        "engine_version": int(info.get("engine_version", 0) or 0),
    }


class Engine:
    def __init__(self, runner=None, health_check=None, boot_id=None, arch=None, fault=None,
                 execv=None, log=None):
        self.runner = runner or DEFAULT_RUNNER
        self.health_check = health_check or (
            lambda version, release: health.check(self.runner, version, release))
        self.boot_id = boot_id or read_boot_id()
        self.arch = arch or platform.machine()
        # Fault injection for power-loss testing: "<STATE>:kill" / "<STATE>:raise".
        self.fault = fault if fault is not None else (
            os.environ.get("ATLAS_ENGINE_FAULT", "") if paths.dev_mode() else "")
        self.execv = execv or os.execv
        # Everything the engine creates is root-writable only, whatever the
        # caller's umask was.
        os.umask(0o022)
        self.log = log or (lambda msg: print(f"[atlas-engine] {msg}", file=sys.stderr, flush=True))
        self.j = None

    # ── journal & status ──────────────────────────────────────────────────
    def load(self):
        self.j = fsutil.read_json(paths.JOURNAL)
        return self.j

    def save(self, state=None, **fields):
        if state:
            self.j["state"] = state
        self.j.update(fields)
        self.j["updated"] = time.time()
        fsutil.atomic_write_json(paths.JOURNAL, self.j, mode=0o600)
        self.publish()
        if state:
            self._maybe_fault(state)

    def _maybe_fault(self, state):
        if not self.fault or ":" not in self.fault:
            return
        fstate, action = self.fault.split(":", 1)
        if fstate == state:
            self.log(f"fault injection at {state}: {action}")
            if action == "crash":
                raise SimulatedCrash(state)
            if action == "kill":
                os.kill(os.getpid(), 9)
            if action == "panic":
                with open("/proc/sysrq-trigger", "w") as f:
                    f.write("b")
            raise RuntimeError(f"injected fault at {state}")

    def publish(self, message=None):
        """Write the app-visible status (no secrets, no paths)."""
        state = (self.j or {}).get("state", "IDLE")
        stage, label = PUBLIC_STAGE.get(state, ("idle", ""))
        to = (self.j or {}).get("to") or {}
        status = {
            "engine_state": state,
            "stage": stage,
            "label": label,
            "version": to.get("version"),
            "txid": (self.j or {}).get("txid"),
            "message": message or (self.j or {}).get("message") or label,
            "error": (self.j or {}).get("error"),
            "can_cancel": state in CANCELLABLE,
            "updated": time.time(),
        }
        try:
            os.makedirs(paths.RUN, mode=0o755, exist_ok=True)
            fsutil.atomic_write_json(paths.STATUS, status, mode=0o644)
        except OSError:
            pass

    def finish(self, result, message, error=None):
        """Close the transaction: record the outcome and clear the journal."""
        to = (self.j or {}).get("to") or {}
        frm = (self.j or {}).get("from") or {}
        outcome = {
            "result": result,
            "message": message,
            "error": error,
            "version": to.get("version"),
            "from_version": frm.get("version"),
            "txid": (self.j or {}).get("txid"),
            "finished": time.time(),
        }
        os.makedirs(paths.PUBLIC, mode=0o755, exist_ok=True)
        fsutil.atomic_write_json(paths.LAST_RESULT, outcome, mode=0o644)
        self.j = {"state": "IDLE", "last_txid": outcome["txid"]}
        fsutil.atomic_write_json(paths.JOURNAL, self.j, mode=0o600)
        status = dict(outcome, engine_state="IDLE", stage=result, can_cancel=False,
                      label=message, updated=time.time())
        try:
            os.makedirs(paths.RUN, mode=0o755, exist_ok=True)
            fsutil.atomic_write_json(paths.STATUS, status, mode=0o644)
        except OSError:
            pass
        for p in (paths.REQUEST, self._cancel_flag()):
            try:
                os.unlink(p)
            except FileNotFoundError:
                pass
        self.log(f"transaction finished: {result} — {message}" + (f" ({error})" if error else ""))

    def _cancel_flag(self):
        return os.path.join(paths.UPDATE_STATE, "cancel")

    def _check_cancel(self):
        if os.path.exists(self._cancel_flag()) and self.j.get("state") in CANCELLABLE:
            raise Cancelled()

    def tx_dir(self):
        return os.path.join(paths.TX, self.j["txid"])

    def staging_dir(self):
        return os.path.join(paths.RELEASES, f".staging-{self.j['txid']}")

    def target_dir(self):
        return os.path.join(paths.RELEASES, self.j["to"]["version"])

    def sysbackup_dir(self):
        return os.path.join(paths.SYSBACKUP, self.j["txid"])

    # ── entry points ──────────────────────────────────────────────────────
    def run(self):
        """atlas-update-engine.service: resume an open transaction or start one."""
        os.makedirs(paths.RUN, mode=0o755, exist_ok=True)
        with open(paths.LOCK, "w") as lockf:
            fcntl.flock(lockf, fcntl.LOCK_EX)
            self.load()
            if self.j and self.j.get("state") not in (None, "IDLE"):
                return self._drive()
            if not os.path.exists(paths.REQUEST):
                self.log("nothing to do")
                return "idle"
            self._begin()
            return self._drive()

    def _begin(self):
        current = paths.CURRENT
        frm = release_info(current) if os.path.exists(current) else {"release": None, "version": None,
                                                                         "seq": 0, "schema_target": 0}
        self.j = {
            "schema": 1,
            "txid": time.strftime("%Y%m%d%H%M%S", time.gmtime()) + "-" + uuid.uuid4().hex[:8],
            "state": "ACCEPTED",
            "from": frm,
            "to": {},
            "boots": [self.boot_id],
            "engine_version": ENGINE_VERSION,
            "started": time.time(),
        }
        os.makedirs(paths.UPDATE_STATE, mode=0o711, exist_ok=True)
        self.save("ACCEPTED", message="Verifying download")

    def _drive(self):
        try:
            while True:
                state = self.j["state"]
                if state == "IDLE":
                    return "idle"
                handler = getattr(self, f"_step_{state.lower()}", None)
                if handler is None:
                    raise EngineError(f"unknown state {state}")
                nxt = handler()
                if nxt in ("done", "rolled_back", "failed", "interrupted", "recovery_failed"):
                    return nxt
        except Cancelled:
            self._cleanup_pre_switch()
            self.finish("cancelled", "Update cancelled.")
            return "cancelled"
        except verify.VerifyError as exc:
            self._cleanup_pre_switch()
            self._diag("verify", str(exc))
            self.finish("failed", MSG_NOT_VERIFIED, error=str(exc))
            return "failed"
        except EngineError as exc:
            return self._handle_failure(exc.public, str(exc))
        except Exception as exc:  # pragma: no cover - unexpected, still recover
            return self._handle_failure("The update could not be installed.", repr(exc))

    def _handle_failure(self, public, detail):
        state = self.j.get("state")
        self._diag(state or "?", detail)
        if state in PRE_SWITCH:
            self._cleanup_pre_switch()
            self.finish("failed", public, error=detail)
            return "failed"
        if state in ("APPLYING_SYSTEM", "SWITCHED", "HEALTH_CHECK"):
            self.save("ROLLING_BACK", error=detail, message=PUBLIC_STAGE["ROLLING_BACK"][1])
            return self._drive()
        if state == "ROLLING_BACK":
            self.save("RECOVERY_FAILED", error=detail)
            return self._drive()
        self.finish("failed", public, error=detail)
        return "failed"

    def _diag(self, where, detail):
        try:
            os.makedirs(paths.DIAG, mode=0o700, exist_ok=True)
            txid = (self.j or {}).get("txid", "none")
            with open(os.path.join(paths.DIAG, f"{txid}.log"), "a", encoding="utf-8") as f:
                f.write(f"{time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())} {where}: {detail}\n")
        except OSError:
            pass

    # ── steps ─────────────────────────────────────────────────────────────
    def _step_accepted(self):
        self._check_cancel()
        tx = self.tx_dir()
        os.makedirs(tx, mode=0o700, exist_ok=True)
        req = fsutil.read_json(paths.REQUEST, {}) or {}
        src_dir = req.get("handoff_dir") or paths.HANDOFF
        manifest_src = os.path.join(src_dir, MANIFEST_NAME)
        try:
            fsutil.copy_regular_file(manifest_src, os.path.join(tx, MANIFEST_NAME), verify.MAX_MANIFEST_BYTES)
            fsutil.copy_regular_file(os.path.join(src_dir, SIG_NAME), os.path.join(tx, SIG_NAME), 64 * 1024)
        except (OSError, ValueError) as exc:
            raise verify.VerifyError(f"update files unavailable: {exc}")
        with open(os.path.join(tx, MANIFEST_NAME), "rb") as f:
            manifest_bytes = f.read()
        # Parse only to learn the bundle name/size; trust comes at VERIFIED.
        try:
            unverified = json.loads(manifest_bytes)
            bundle_name = unverified["bundle"]["name"]
            bundle_size = int(unverified["bundle"]["size"])
        except (ValueError, KeyError, TypeError):
            raise verify.VerifyError("manifest is malformed")
        if "/" in bundle_name or bundle_name.startswith("."):
            raise verify.VerifyError("bundle name is invalid")
        self._require_space(paths.UPDATE_STATE, bundle_size + SPACE_MARGIN)
        try:
            fsutil.copy_regular_file(os.path.join(src_dir, bundle_name), os.path.join(tx, "bundle.tar.xz"),
                                     bundle_size)
        except (OSError, ValueError) as exc:
            raise verify.VerifyError(f"update bundle unavailable: {exc}")
        return self._verify(manifest_bytes)

    def _verify(self, manifest_bytes):
        tx = self.tx_dir()
        with open(os.path.join(tx, SIG_NAME), "rb") as f:
            sig = f.read()
        keys = verify.load_keyring(paths.KEYRING, paths.DEV_KEYS, paths.dev_mode())
        installed = fsutil.read_json(paths.INSTALLED, {}) or {}
        installed_seq = max(int(installed.get("seq", 0) or 0), int(self.j["from"].get("seq", 0) or 0))
        manifest, kid = verify.verify_manifest(
            manifest_bytes, sig, keys, arch=self.arch, installed_seq=installed_seq,
            engine_version=ENGINE_VERSION,
            python_version=f"{sys.version_info.major}.{sys.version_info.minor}")
        bad = verify.verify_bundle_file(os.path.join(tx, "bundle.tar.xz"), manifest["bundle"])
        if bad is not None:
            raise verify.VerifyError("bundle does not match the signed manifest")
        need = manifest["bundle"]["unpacked_size"] + VENV_ESTIMATE + SPACE_MARGIN
        self._require_space(paths.OPT if os.path.exists(paths.OPT) else paths.ROOT, need)
        self.save("VERIFIED", to={
            "version": manifest["version"],
            "seq": manifest["seq"],
            "schema_target": manifest.get("db", {}).get("schema_target", 0),
            "compat_floor": manifest.get("db", {}).get("compat_floor", 0),
            "engine_version": manifest.get("engine_version", ENGINE_VERSION),
            "unpacked_size": manifest["bundle"]["unpacked_size"],
            "key_id": kid,
        }, message="Preparing installation")
        return "VERIFIED"

    def _step_verified(self):
        self._check_cancel()
        target = self.target_dir()
        if os.path.exists(os.path.join(target, ".complete")):
            # Reinstalling a release that is already on disk (e.g. after rollback).
            self.save("STAGED")
            return "STAGED"
        staging = self.staging_dir()
        if os.path.lexists(staging):
            shutil.rmtree(staging)
        self.save("STAGING")
        return "STAGING"

    def _step_staging(self):
        self._check_cancel()
        staging = self.staging_dir()
        if os.path.lexists(staging):
            shutil.rmtree(staging)
        os.makedirs(paths.RELEASES, mode=0o755, exist_ok=True)
        try:
            extract.extract_bundle(os.path.join(self.tx_dir(), "bundle.tar.xz"), staging,
                                   self.j["to"]["unpacked_size"])
        except extract.ExtractError as exc:
            raise verify.VerifyError(str(exc))
        info = fsutil.read_json(os.path.join(staging, "release.json"), {}) or {}
        if info.get("version") != self.j["to"]["version"] or int(info.get("seq", -1)) != self.j["to"]["seq"]:
            raise verify.VerifyError("bundle contents do not match the signed manifest")
        self.save("ENGINE_SWITCH")
        return "ENGINE_SWITCH"

    def _step_engine_switch(self):
        self._check_cancel()
        staging = self.staging_dir()
        bundled = self.j["to"].get("engine_version", ENGINE_VERSION)
        if isinstance(bundled, int) and bundled > ENGINE_VERSION:
            install_engine(os.path.join(staging, "system"), bundled, self.runner)
            self.save("VENV")
            self.log(f"re-executing engine {bundled}")
            shim = os.path.join(paths.BIN, "atlas-engine")
            self.execv(sys.executable, [sys.executable, "-I", shim, "run"])
            return "VENV"
        self.save("VENV")
        return "VENV"

    def _step_venv(self):
        self._check_cancel()
        staging = self.staging_dir()
        src = staging if os.path.isdir(staging) else self.target_dir()
        key = venvs.ensure_venv(src, self.runner)
        venvs.link_release(src, key)
        strip_write_bits(os.path.join(paths.VENVS, key))
        self.j["to"]["venv"] = key
        if src == staging:
            self.runner.run(["/usr/bin/python3", "-m", "compileall", "-q", "-x", r"/(tests|mobile)/",
                             staging], timeout=900)
            shutil.rmtree(os.path.join(staging, "wheels"), ignore_errors=True)
            strip_write_bits(staging)
            fsutil.atomic_write_bytes(os.path.join(staging, ".complete"),
                                      (self.j["to"]["version"] + "\n").encode())
            os.sync()
            target = self.target_dir()
            if os.path.lexists(target):
                shutil.rmtree(target)
            os.rename(staging, target)
            fsutil.fsync_dir(paths.RELEASES)
        self.save("STAGED")
        return "STAGED"

    def _step_staged(self):
        self._check_cancel()
        # Point of no cancellation: from here the running system changes.
        self.runner.run(["systemctl", "stop", paths.APP_UNIT], timeout=120)
        self.save("APPLYING_SYSTEM", db_before=probe_db(self.runner))
        return "APPLYING_SYSTEM"

    def _step_applying_system(self):
        target = self.target_dir()
        # Identity first (e.g. a legacy atlas.local unit moves to its own name),
        # so the templates below render the names it will answer to.
        try:
            from . import identity
            if identity.load():
                identity.ensure()
        except Exception as exc:
            self.log(f"identity check skipped: {type(exc).__name__}: {exc}")
        try:
            changed = sysfiles.install(target, self.sysbackup_dir(), self.runner)
        except sysfiles.SystemFilesError as exc:
            raise EngineError(str(exc), public="The update could not be installed.")
        self.j["to"]["changed"] = changed
        self.save("SWITCHED")
        fsutil.atomic_symlink(os.path.relpath(target, paths.OPT), paths.CURRENT)
        spec = sysfiles.load_spec(target)
        sysfiles.reload_changed(changed, spec, self.runner)
        apply_power_mode(spec, self.runner, self.log)
        self.runner.run(["systemctl", "daemon-reload"], timeout=60)
        self._restart_app()
        self.runner.run(["systemctl", "reload-or-restart", "nginx"], timeout=60)
        return "SWITCHED"

    def _step_switched(self):
        # Reached after a reboot during SWITCHED: make sure the app is running.
        if os.path.realpath(paths.CURRENT) != os.path.realpath(self.target_dir()):
            fsutil.atomic_symlink(os.path.relpath(self.target_dir(), paths.OPT), paths.CURRENT)
            self._restart_app()
        self.save("HEALTH_CHECK", message="Validating installation")
        return "HEALTH_CHECK"

    def _step_health_check(self):
        result = self.health_check(self.j["to"]["version"], self.target_dir())
        if not result.ok:
            raise EngineError(f"health check failed: {result.detail}", public=MSG_ROLLED_BACK)
        self.save("COMMITTING", message="Update complete")
        return "COMMITTING"

    def _step_committing(self):
        frm = self.j.get("from") or {}
        if frm.get("release") and os.path.isdir(frm["release"]) and \
                os.path.realpath(frm["release"]) != os.path.realpath(self.target_dir()):
            fsutil.atomic_symlink(os.path.relpath(frm["release"], paths.OPT), paths.PREVIOUS)
        if not os.path.lexists(paths.FACTORY):
            fsutil.atomic_symlink(os.path.relpath(self.target_dir(), paths.OPT), paths.FACTORY)
        fsutil.atomic_write_json(paths.INSTALLED, {
            "version": self.j["to"]["version"],
            "seq": self.j["to"]["seq"],
            "release": os.path.realpath(self.target_dir()),
            "schema_target": self.j["to"].get("schema_target", 0),
            "installed": time.time(),
            "txid": self.j["txid"],
        }, mode=0o644)
        upgrade_keyring(os.path.join(self.target_dir(), "system", "keys", "keyring.json"))
        prune(self.runner)
        shutil.rmtree(self.tx_dir(), ignore_errors=True)
        self._remove_handoff()
        self.finish("success", f"Atlas {self.j['to']['version']} is installed.")
        return "done"

    def _step_rolling_back(self):
        frm = self.j.get("from") or {}
        self.runner.run(["systemctl", "stop", paths.APP_UNIT], timeout=120)
        sysfiles.restore(self.sysbackup_dir())
        release = frm.get("release")
        if release and os.path.isdir(release):
            fsutil.atomic_symlink(os.path.relpath(release, paths.OPT), paths.CURRENT)
        restore_db_if_needed(self.j, self.runner, self.log)
        self.runner.run(["systemctl", "daemon-reload"], timeout=60)
        for kind in (self.j.get("to") or {}).get("changed", []):
            for cmd in sysfiles.KINDS_RELOAD.get(kind, []):
                self.runner.run(cmd, timeout=120)
        self._restart_app()
        self.runner.run(["systemctl", "reload-or-restart", "nginx"], timeout=60)
        if frm.get("version") and release:
            result = self.health_check(frm["version"], release)
            if not result.ok:
                raise EngineError(f"previous release unhealthy after rollback: {result.detail}")
        shutil.rmtree(self.tx_dir(), ignore_errors=True)
        failed_target = self.target_dir() if self.j.get("to", {}).get("version") else None
        if failed_target and os.path.realpath(failed_target) not in (
                os.path.realpath(paths.CURRENT), os.path.realpath(paths.PREVIOUS),
                os.path.realpath(paths.FACTORY)):
            shutil.rmtree(failed_target, ignore_errors=True)
        self.finish("rolled_back", MSG_ROLLED_BACK, error=self.j.get("error"))
        return "rolled_back"

    def _step_recovery_failed(self):
        factory = paths.FACTORY
        attempts = int(self.j.get("recovery_attempts", 0))
        if attempts < 3 and os.path.isdir(factory) and \
                os.path.exists(os.path.join(os.path.realpath(factory), ".complete")):
            self.j["recovery_attempts"] = attempts + 1
            self.save("RECOVERY_FAILED")
            fsutil.atomic_symlink(os.path.relpath(os.path.realpath(factory), paths.OPT), paths.CURRENT)
            self._restart_app()
            info = release_info(factory)
            if self.health_check(info["version"], os.path.realpath(factory)).ok:
                self.finish("rolled_back", MSG_ROLLED_BACK, error=self.j.get("error"))
                return "rolled_back"
        self.publish("Atlas could not restore itself automatically. Restart Atlas to try again.")
        return "recovery_failed"

    # ── helpers ───────────────────────────────────────────────────────────
    def _restart_app(self):
        # A release that crash-looped may have left the unit "failed"; a
        # plain restart would then be refused and the rollback stall.
        self.runner.run(["systemctl", "reset-failed", paths.APP_UNIT], timeout=30)
        self.runner.run(["systemctl", "restart", paths.APP_UNIT], timeout=120)

    def _require_space(self, path, need):
        probe = path
        while not os.path.exists(probe):
            probe = os.path.dirname(probe)
        st = os.statvfs(probe)
        if st.f_bavail * st.f_frsize < need:
            raise EngineError(f"need {need} bytes free at {path}", public=MSG_NO_SPACE)

    def _cleanup_pre_switch(self):
        if not self.j or not self.j.get("txid"):
            return
        shutil.rmtree(self.staging_dir(), ignore_errors=True)
        shutil.rmtree(self.tx_dir(), ignore_errors=True)
        key = (self.j.get("to") or {}).get("venv")
        if key and not venvs.is_complete(key):
            shutil.rmtree(os.path.join(paths.VENVS, key), ignore_errors=True)

    def _remove_handoff(self):
        for name in os.listdir(paths.HANDOFF) if os.path.isdir(paths.HANDOFF) else []:
            try:
                os.unlink(os.path.join(paths.HANDOFF, name))
            except OSError:
                pass

    # ── boot recovery ─────────────────────────────────────────────────────
    def recover(self):
        """atlas-update-recover.service, before atlas-control starts."""
        os.makedirs(paths.RUN, mode=0o755, exist_ok=True)
        with open(paths.LOCK, "w") as lockf:
            fcntl.flock(lockf, fcntl.LOCK_EX)
            self.load()
            ensure_current_is_complete(self.log)
            if not self.j or self.j.get("state") in (None, "IDLE"):
                self.publish()
                return "idle"
            state = self.j["state"]
            self.log(f"recovering interrupted update in state {state}")
            if state in PRE_SWITCH:
                self._cleanup_pre_switch()
                self.finish("interrupted", MSG_INTERRUPTED)
                self.runner.run(["systemctl", "start", "--no-block", paths.APP_UNIT], timeout=30)
                return "interrupted"
            if state == "APPLYING_SYSTEM":
                sysfiles.restore(self.sysbackup_dir())
                self.runner.run(["systemctl", "daemon-reload"], timeout=60)
                shutil.rmtree(self.tx_dir(), ignore_errors=True)
                self.finish("interrupted", MSG_INTERRUPTED)
                self.runner.run(["systemctl", "start", "--no-block", paths.APP_UNIT], timeout=30)
                return "interrupted"
            if state in ("SWITCHED", "HEALTH_CHECK"):
                boots = list(self.j.get("boots", []))
                if self.boot_id not in boots:
                    boots.append(self.boot_id)
                if len(boots) > 2:
                    # The new release has had two boots to prove itself.
                    self.save("ROLLING_BACK", boots=boots, error="new release did not become healthy")
                else:
                    # SWITCHED re-checks that `current` really points at the
                    # new release before health checking resumes.
                    self.save(state, boots=boots)
            self._resume_later()
            return "resume"

    def _resume_later(self):
        """Hand the open transaction back to the engine unit, which is ordered
        after atlas-control, and make sure the app itself gets started."""
        self.runner.run(["systemctl", "start", "--no-block", paths.APP_UNIT], timeout=30)
        self.runner.run(["systemctl", "start", "--no-block", "atlas-update-engine.service"], timeout=30)


def probe_db(runner):
    """Schema facts of the live database, read as the service user so no
    root-owned WAL/SHM files are ever created next to it."""
    db_path = os.path.join(paths.APP_STATE, "data", "meshtastic.db")
    if not os.path.exists(db_path):
        return {"user_version": 0, "compat_floor": 0}
    code = ("import json,sqlite3,sys;c=sqlite3.connect('file:'+sys.argv[1]+'?mode=ro',uri=True);"
            "uv=c.execute('PRAGMA user_version').fetchone()[0];"
            "t=c.execute(\"SELECT name FROM sqlite_master WHERE name='atlas_meta'\").fetchone();"
            "cf=c.execute(\"SELECT value FROM atlas_meta WHERE key='compat_floor'\").fetchone() if t else None;"
            "lb=c.execute(\"SELECT value FROM atlas_meta WHERE key='last_backup'\").fetchone() if t else None;"
            "print(json.dumps({'user_version':uv,'compat_floor':int(cf[0]) if cf else 0,"
            "'last_backup':lb[0] if lb else None}))")
    cmd = ["/usr/bin/python3", "-I", "-c", code, db_path]
    if os.geteuid() == 0 and paths.ROOT == "/":
        cmd = ["setpriv", f"--reuid={paths.SERVICE_USER}", f"--regid={paths.SERVICE_USER}",
               "--init-groups"] + cmd
    try:
        r = runner.run(cmd, timeout=30)
        return json.loads(r.stdout.strip() or "{}") if r.returncode == 0 else {"error": r.stderr[-200:]}
    except Exception as exc:
        return {"error": str(exc)}


def restore_db_if_needed(journal, runner, log):
    """If the failed release migrated the DB past what the previous release
    can read, put back the backup taken before that migration."""
    frm = journal.get("from") or {}
    before = journal.get("db_before") or {}
    now = probe_db(runner)
    floor = int(now.get("compat_floor", 0) or 0)
    if floor <= int(frm.get("schema_target", 0) or 0):
        return False
    backups = os.path.join(paths.APP_STATE, "data", "backups")
    wanted = f"meshtastic-v{int(before.get('user_version', 0))}-"
    candidates = sorted((f for f in os.listdir(backups) if f.startswith(wanted)),
                        key=lambda f: os.path.getmtime(os.path.join(backups, f))) if os.path.isdir(backups) else []
    if not candidates:
        log("database needs restoring but no pre-migration backup exists")
        return False
    db_path = os.path.join(paths.APP_STATE, "data", "meshtastic.db")
    st = os.stat(db_path)
    aside = os.path.join(backups, f"rolled-back-{int(time.time())}.db")
    os.replace(db_path, aside)
    for ext in ("-wal", "-shm"):
        try:
            os.unlink(db_path + ext)
        except FileNotFoundError:
            pass
    shutil.copy2(os.path.join(backups, candidates[-1]), db_path)
    os.chown(db_path, st.st_uid, st.st_gid)
    os.chmod(db_path, 0o600)
    log(f"restored database from {candidates[-1]}; newer copy kept as {os.path.basename(aside)}")
    return True


def ensure_current_is_complete(log):
    """If `current` is missing or points at an incomplete release, fall back."""
    cur = paths.CURRENT
    if os.path.exists(os.path.join(cur, ".complete")) or os.path.exists(os.path.join(cur, ".dev-link")):
        return
    for fallback in (paths.PREVIOUS, paths.FACTORY):
        if os.path.exists(os.path.join(fallback, ".complete")):
            fsutil.atomic_symlink(os.path.relpath(os.path.realpath(fallback), paths.OPT), cur)
            log(f"current release was unusable; switched to {os.path.realpath(fallback)}")
            return


def strip_write_bits(top):
    """No file in a release or venv may be writable by anyone but its owner."""
    for dirpath, dirnames, filenames in os.walk(top):
        for name in [dirpath] + [os.path.join(dirpath, f) for f in filenames]:
            try:
                st = os.lstat(name)
            except FileNotFoundError:
                continue
            if not os.path.islink(name) and st.st_mode & 0o022:
                os.chmod(name, st.st_mode & ~0o022)


def install_engine(system_dir, version, runner):
    """Install a bundled engine as engine/<version> and make it current."""
    src = os.path.join(system_dir, "atlas_engine")
    dest = os.path.join(paths.ENGINE_DIR, str(version))
    tmp = dest + ".tmp"
    shutil.rmtree(tmp, ignore_errors=True)
    shutil.copytree(src, os.path.join(tmp, "atlas_engine"))
    r = runner.run(["/usr/bin/python3", "-I", "-c",
                    f"import sys; sys.path.insert(0, {tmp!r}); import atlas_engine.engine, "
                    f"atlas_engine.helper; assert atlas_engine.ENGINE_VERSION == {int(version)}"],
                   timeout=60)
    if r.returncode != 0:
        shutil.rmtree(tmp, ignore_errors=True)
        raise EngineError(f"bundled engine failed its self-test: {r.stderr[-300:]}")
    shutil.rmtree(dest, ignore_errors=True)
    os.rename(tmp, dest)
    cur_link = os.path.join(paths.ENGINE_DIR, "current")
    if os.path.lexists(cur_link):
        fsutil.atomic_symlink(os.readlink(cur_link), os.path.join(paths.ENGINE_DIR, "previous"))
    fsutil.atomic_symlink(str(version), cur_link)


def upgrade_keyring(candidate):
    """Adopt a release's keyring only if it is newer (revocations, rotation)."""
    if not os.path.exists(candidate):
        return False
    if verify.keyring_version(candidate) <= verify.keyring_version(paths.KEYRING):
        return False
    verify.load_keyring(candidate)          # must parse
    with open(candidate, "rb") as f:
        fsutil.atomic_write_bytes(paths.KEYRING, f.read(), mode=0o644)
    return True


def prune(runner=None):
    """Keep only current, previous and factory releases and their venvs."""
    keep = {os.path.realpath(p) for p in (paths.CURRENT, paths.PREVIOUS, paths.FACTORY)
            if os.path.lexists(p)}
    keep_venvs = set()
    if os.path.isdir(paths.RELEASES):
        for name in os.listdir(paths.RELEASES):
            full = os.path.join(paths.RELEASES, name)
            if name.startswith(".staging-"):
                shutil.rmtree(full, ignore_errors=True)
                continue
            if os.path.realpath(full) in keep:
                v = os.path.join(full, "venv")
                if os.path.islink(v):
                    keep_venvs.add(os.path.basename(os.readlink(v)))
                continue
            shutil.rmtree(full, ignore_errors=True)
    if os.path.isdir(paths.VENVS):
        for key in os.listdir(paths.VENVS):
            if key not in keep_venvs:
                shutil.rmtree(os.path.join(paths.VENVS, key), ignore_errors=True)
    for d in (paths.TX, paths.SYSBACKUP):
        if os.path.isdir(d):
            # By age, not name: SYSBACKUP holds reapply-<time> next to update ids.
            entries = sorted((os.path.join(d, e) for e in os.listdir(d)), key=os.path.getmtime)
            for old in entries[:-3]:
                shutil.rmtree(old, ignore_errors=True)
    # One diagnostic log per update: keep the most recent ones for support.
    if os.path.isdir(paths.DIAG):
        logs = sorted((os.path.join(paths.DIAG, f) for f in os.listdir(paths.DIAG)), key=os.path.getmtime)
        for old in logs[:-DIAG_KEPT]:
            try:
                os.unlink(old)
            except OSError:
                pass


def apply_power_mode(spec, runner, log):
    """The release's power mode (perf.ensure_power_mode); never fails an install."""
    try:
        result = perf.ensure_power_mode((spec or {}).get("power_mode"), runner)
    except Exception as exc:
        result = {"changed": False, "reason": f"{type(exc).__name__}: {exc}"}
    if (spec or {}).get("power_mode"):
        log(f"power mode {spec['power_mode']}: {result.get('reason')}")
    return result


def reapply(runner=None, log=print):
    """Install the current release's system files again and reload what
    changed — for a unit whose configuration drifted (edited by hand, or set
    up by an older installer) while no newer release is available. Same
    validation and all-or-nothing restore as an update; only while no update
    is in progress."""
    runner = runner or DEFAULT_RUNNER
    os.makedirs(paths.RUN, mode=0o755, exist_ok=True)
    with open(paths.LOCK, "w") as lockf:
        try:
            fcntl.flock(lockf, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise EngineError("an update is in progress")
        journal = fsutil.read_json(paths.JOURNAL, {}) or {}
        if journal.get("state") not in (None, "IDLE"):
            raise EngineError(f"an update is in progress ({journal.get('state')})")
        current = os.path.realpath(paths.CURRENT)
        if not os.path.exists(os.path.join(current, ".complete")) and \
                not os.path.exists(os.path.join(current, ".dev-link")):
            raise EngineError("no complete release is installed")
        backup = os.path.join(paths.SYSBACKUP, f"reapply-{int(time.time())}")
        try:
            changed = sysfiles.install(current, backup, runner)
        except sysfiles.SystemFilesError as exc:
            raise EngineError(str(exc))
        spec = sysfiles.load_spec(current)
        sysfiles.reload_changed(changed, spec, runner)
        power = apply_power_mode(spec, runner, log)
        if changed:
            runner.run(["systemctl", "daemon-reload"], timeout=60)
            log(f"reapplied: {', '.join(changed)}")
        else:
            shutil.rmtree(backup, ignore_errors=True)
            log("system files already match the installed release")
        return {"changed": changed, "power": power}
