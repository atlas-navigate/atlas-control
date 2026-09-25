"""Shared test setup.

Every test run gets a throwaway ATLAS_STATE_DIR so nothing a test does can
touch a real device's database, secrets or logs. This must happen before any
Atlas module is imported, which is why it runs at conftest import time.

Read-only reference datasets (the geocoder's static/data/*.db) are linked in
from ATLAS_TEST_DATASETS, or from the default install location, when present,
so the geocoder tests still exercise real data on a provisioned box.
"""
import os
import shutil
import sys
import tempfile

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO not in sys.path:
    sys.path.insert(0, _REPO)

_STATE = tempfile.mkdtemp(prefix="atlas-test-state-")
os.environ["ATLAS_STATE_DIR"] = _STATE
os.environ.setdefault("ATLAS_TESTING", "1")
# Never pick up the host's own device identity, proxy secret or helper:
# tests that need them create their own.
os.environ["ATLAS_IDENTITY_DIR"] = os.path.join(_STATE, "no-identity")
os.environ["ATLAS_PROXY_SECRET_FILE"] = os.path.join(_STATE, "no-identity", "proxy_secret")
os.environ["ATLAS_HELPER_SOCKET"] = os.path.join(_STATE, "no-helper.sock")
os.makedirs(os.path.join(_STATE, "data"), exist_ok=True)
os.makedirs(os.path.join(_STATE, "logs"), exist_ok=True)
os.makedirs(os.path.join(_STATE, "static"), exist_ok=True)

_datasets = os.environ.get("ATLAS_TEST_DATASETS", "/atlas_data/atlas-control/static/data")
if os.path.isdir(_datasets):
    os.symlink(_datasets, os.path.join(_STATE, "static", "data"))


def pytest_sessionfinish(session, exitstatus):
    shutil.rmtree(_STATE, ignore_errors=True)
