"""
Hardware sampler for Ray benchmarks. Read-only, no root.

Samples every `interval` seconds in a background thread:
  cpu_pct        all cores, from /proc/stat
  gpu_load_pct   /sys/.../17000000.gpu/load (per mille → %)
  gpu_mhz        GPU devfreq cur_freq
  emc_mhz        memory controller (bwmgr devfreq) cur_freq
  mem_avail_mb   MemAvailable
  llama_rss_mb   sum of llama-server RSS (every loaded Ollama runner)
  app_rss_mb     Atlas app RSS (python processes of the service user)
  tj_c           hottest thermal zone
  vdd_in_mw      board input power, from tegrastats (works without root)

`window(t0, t1)` summarises the samples taken between two monotonic times.
"""
import os
import re
import subprocess
import threading
import time

GPU_LOAD = "/sys/devices/platform/bus@0/17000000.gpu/load"
GPU_FREQ = "/sys/class/devfreq/17000000.gpu/cur_freq"
EMC_FREQ = "/sys/class/devfreq/bwmgr/cur_freq"


def _read(path, default=None):
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return default


def _cpu_times():
    with open("/proc/stat") as f:
        parts = f.readline().split()[1:]
    vals = [int(v) for v in parts]
    idle = vals[3] + (vals[4] if len(vals) > 4 else 0)
    return sum(vals), idle


def _mem_avail_mb():
    with open("/proc/meminfo") as f:
        for line in f:
            if line.startswith("MemAvailable:"):
                return int(line.split()[1]) // 1024
    return None


def _rss_mb(match):
    total = 0
    for pid in os.listdir("/proc"):
        if not pid.isdigit():
            continue
        try:
            with open(f"/proc/{pid}/cmdline", "rb") as f:
                cmd = f.read().replace(b"\0", b" ").decode(errors="replace")
            if not match(cmd):
                continue
            with open(f"/proc/{pid}/status") as f:
                for line in f:
                    if line.startswith("VmRSS:"):
                        total += int(line.split()[1])
                        break
        except OSError:
            continue
    return total // 1024


def _is_llama(cmd):
    return "llama-server" in cmd


def _is_app(cmd):
    return "/opt/atlas/releases/" in cmd and "python" in cmd


def _max_temp_c():
    best = None
    base = "/sys/class/thermal"
    for zone in os.listdir(base):
        v = _read(os.path.join(base, zone, "temp"))
        if v and v.lstrip("-").isdigit():
            c = int(v) / 1000
            best = c if best is None else max(best, c)
    return best


class Sampler:
    def __init__(self, interval=0.2):
        self.interval = interval
        self.samples = []
        self._stop = threading.Event()
        self._vdd = None
        self._tegra = None

    def start(self):
        try:
            self._tegra = subprocess.Popen(["tegrastats", "--interval", str(int(self.interval * 1000))],
                                           stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
            threading.Thread(target=self._tegra_reader, daemon=True).start()
        except OSError:
            self._tegra = None
        threading.Thread(target=self._loop, daemon=True).start()
        return self

    def stop(self):
        self._stop.set()
        if self._tegra:
            self._tegra.terminate()
            try:
                self._tegra.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self._tegra.kill()

    def _tegra_reader(self):
        for line in self._tegra.stdout:
            m = re.search(r"VDD_IN (\d+)mW", line)
            if m:
                self._vdd = int(m.group(1))

    def _loop(self):
        last = _cpu_times()
        slow_every = max(1, int(1.0 / self.interval))  # RSS/temp once a second
        n = 0
        rss_llama = rss_app = temp = None
        while not self._stop.is_set():
            time.sleep(self.interval)
            cur = _cpu_times()
            dt, di = cur[0] - last[0], cur[1] - last[1]
            last = cur
            if n % slow_every == 0:
                rss_llama = _rss_mb(_is_llama)
                rss_app = _rss_mb(_is_app)
                temp = _max_temp_c()
            n += 1
            load = _read(GPU_LOAD)
            gpu = _read(GPU_FREQ)
            emc = _read(EMC_FREQ)
            self.samples.append({
                "t": time.monotonic(),
                "cpu_pct": round(100 * (dt - di) / dt, 1) if dt else None,
                "gpu_load_pct": int(load) / 10 if load and load.isdigit() else None,
                "gpu_mhz": int(gpu) // 1_000_000 if gpu and gpu.isdigit() else None,
                "emc_mhz": int(emc) // 1_000_000 if emc and emc.isdigit() else None,
                "mem_avail_mb": _mem_avail_mb(),
                "llama_rss_mb": rss_llama,
                "app_rss_mb": rss_app,
                "tj_c": temp,
                "vdd_in_mw": self._vdd,
            })

    def window(self, t0, t1):
        rows = [s for s in self.samples if t0 <= s["t"] <= t1]
        if not rows:
            return {}

        def col(k):
            return [r[k] for r in rows if r.get(k) is not None]

        def avg(k):
            v = col(k)
            return round(sum(v) / len(v), 1) if v else None

        def mx(k):
            v = col(k)
            return max(v) if v else None

        def mn(k):
            v = col(k)
            return min(v) if v else None

        return {
            "samples": len(rows),
            "cpu_pct_avg": avg("cpu_pct"), "gpu_load_pct_avg": avg("gpu_load_pct"),
            "gpu_mhz_avg": avg("gpu_mhz"), "gpu_mhz_max": mx("gpu_mhz"),
            "emc_mhz_avg": avg("emc_mhz"), "emc_mhz_max": mx("emc_mhz"),
            "mem_avail_mb_min": mn("mem_avail_mb"), "llama_rss_mb_max": mx("llama_rss_mb"),
            "app_rss_mb_max": mx("app_rss_mb"), "tj_c_max": mx("tj_c"), "vdd_in_mw_avg": avg("vdd_in_mw"),
        }


def current_temp_c():
    return _max_temp_c()
