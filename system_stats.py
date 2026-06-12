"""
Atlas Control — Jetson Orin Nano System Stats
Collects CPU, RAM, GPU, temps, power, disk via psutil + tegrastats.
"""
import re
import json
import glob
import time
import threading
import subprocess
import logging

logger = logging.getLogger("system_stats")

_cache = {"data": None, "ts": 0}
_lock = threading.Lock()
CACHE_TTL = 5  # seconds

_disk_io_prev = {"data": None, "ts": 0.0}
_smart_cache = {"data": {}, "ts": 0.0}
SMART_TTL = 60  # seconds


def parse_tegrastats(line):
    """Parse a single tegrastats output line into a dict."""
    result = {}

    # RAM: RAM 5157/7607MB
    m = re.search(r'RAM (\d+)/(\d+)MB', line)
    if m:
        used, total = int(m.group(1)), int(m.group(2))
        result['ram_used_mb'] = used
        result['ram_total_mb'] = total
        result['ram_pct'] = round(used / total * 100, 1) if total else 0

    # SWAP: SWAP 612/3804MB
    m = re.search(r'SWAP (\d+)/(\d+)MB', line)
    if m:
        used, total = int(m.group(1)), int(m.group(2))
        result['swap_used_mb'] = used
        result['swap_total_mb'] = total
        result['swap_pct'] = round(used / total * 100, 1) if total else 0

    # CPU: CPU [16%@1728,14%@1728,...]
    m = re.search(r'CPU \[([^\]]+)\]', line)
    if m:
        cores = []
        for part in m.group(1).split(','):
            cm = re.match(r'(\d+)%@(\d+)', part.strip())
            if cm:
                cores.append({'pct': int(cm.group(1)), 'freq_mhz': int(cm.group(2))})
        result['cpu_cores'] = cores
        if cores:
            result['cpu_pct'] = round(sum(c['pct'] for c in cores) / len(cores), 1)

    # GPU: GR3D_FREQ 31%
    m = re.search(r'GR3D_FREQ (\d+)%', line)
    if m:
        result['gpu_pct'] = int(m.group(1))

    # Temperatures
    for label in ['cpu', 'gpu', 'tj', 'soc0', 'soc1', 'soc2']:
        m = re.search(rf'{label}@([\d.]+)C', line)
        if m:
            result[f'temp_{label}'] = round(float(m.group(1)), 1)

    # Power (mW)
    for tegra_key, out_key in [
        ('VDD_IN', 'power_in_mw'),
        ('VDD_CPU_GPU_CV', 'power_cpu_gpu_mw'),
        ('VDD_SOC', 'power_soc_mw'),
    ]:
        m = re.search(rf'{tegra_key} (\d+)mW', line)
        if m:
            result[out_key] = int(m.group(1))

    return result


def _run_tegrastats():
    """Run tegrastats, capture first line, kill process."""
    try:
        proc = subprocess.Popen(
            ['tegrastats'],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True
        )
        line = proc.stdout.readline()
        proc.terminate()
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            proc.kill()
        return parse_tegrastats(line)
    except Exception as e:
        logger.debug(f"tegrastats error: {e}")
        return {}


def _get_disk_extended():
    """Get NVMe I/O speeds, SMART health, and drive temperature."""
    result = {}

    # Read/Write speeds for nvme0n1 specifically
    try:
        import psutil
        io_map = psutil.disk_io_counters(perdisk=True)
        io = io_map.get('nvme0n1')
        if io:
            now = time.time()
            prev_data = _disk_io_prev["data"]
            prev_ts = _disk_io_prev["ts"]
            if prev_data is not None:
                dt = now - prev_ts
                if dt > 0:
                    result['disk_read_mbps'] = round(
                        (io.read_bytes - prev_data.read_bytes) / (1024 ** 2) / dt, 2)
                    result['disk_write_mbps'] = round(
                        (io.write_bytes - prev_data.write_bytes) / (1024 ** 2) / dt, 2)
            _disk_io_prev["data"] = io
            _disk_io_prev["ts"] = now
    except Exception as e:
        logger.debug(f"disk I/O speed error: {e}")

    now = time.time()
    cached = _smart_cache["data"]
    if cached and (now - _smart_cache["ts"]) < SMART_TTL:
        result.update(cached)
        return result

    # SMART health + temperature via sudo smartctl on /dev/nvme0n1
    smart_data = {}
    try:
        proc = subprocess.run(
            ['sudo', 'smartctl', '-A', '-H', '--json', '/dev/nvme0n1'],
            capture_output=True, text=True, timeout=2
        )
        if proc.returncode in (0, 4):  # 4 = SMART warnings but data available
            data = json.loads(proc.stdout)

            health = data.get('smart_status', {})
            if 'passed' in health:
                smart_data['disk_health'] = 'PASSED' if health['passed'] else 'FAILED'

            temp = data.get('temperature', {})
            if 'current' in temp:
                smart_data['disk_temp_c'] = temp['current']

            nvme = data.get('nvme_smart_health_information_log', {})
            if nvme:
                pct_used = nvme.get('percentage_used')
                if pct_used is not None:
                    smart_data['disk_life_pct_used'] = pct_used
                poh = nvme.get('power_on_hours')
                if poh is not None:
                    smart_data['disk_power_on_hours'] = poh
                # Each data unit = 1000 * 512 bytes
                du_read = nvme.get('data_units_read')
                if du_read is not None:
                    smart_data['disk_total_read_tb'] = round(du_read * 512000 / (1024 ** 4), 2)
                du_written = nvme.get('data_units_written')
                if du_written is not None:
                    smart_data['disk_total_written_tb'] = round(du_written * 512000 / (1024 ** 4), 2)
    except Exception as e:
        logger.debug(f"smartctl error: {e}")

    if smart_data:
        _smart_cache["data"] = smart_data
        _smart_cache["ts"] = now
        result.update(smart_data)
    elif cached:
        result.update(cached)

    return result


def get_stats():
    """Return system stats dict, cached for CACHE_TTL seconds."""
    with _lock:
        now = time.time()
        if _cache['data'] and (now - _cache['ts']) < CACHE_TTL:
            return _cache['data']

    try:
        import psutil

        tegra = _run_tegrastats()

        # CPU — use psutil per-core if tegrastats didn't get it
        cpu_percents = psutil.cpu_percent(interval=0.1, percpu=True)
        cpu_freq = psutil.cpu_freq(percpu=True) or []

        if 'cpu_cores' not in tegra:
            tegra['cpu_cores'] = [
                {
                    'pct': pct,
                    'freq_mhz': int(freq.current) if i < len(cpu_freq) else 0,
                }
                for i, (pct, freq) in enumerate(zip(cpu_percents, cpu_freq or [None]*len(cpu_percents)))
                if True
            ]
            tegra['cpu_pct'] = round(sum(cpu_percents) / len(cpu_percents), 1)

        # RAM — tegrastats values are more accurate on Jetson
        vm = psutil.virtual_memory()
        if 'ram_used_mb' not in tegra:
            tegra['ram_used_mb'] = vm.used // (1024 * 1024)
            tegra['ram_total_mb'] = vm.total // (1024 * 1024)
            tegra['ram_pct'] = round(vm.percent, 1)

        # Root disk (eMMC at /)
        disk = psutil.disk_usage('/')
        tegra['disk_used_gb'] = round(disk.used / (1024**3), 1)
        tegra['disk_total_gb'] = round(disk.total / (1024**3), 1)
        tegra['disk_pct'] = round(disk.percent, 1)

        # NVMe data drive at /atlas_data
        try:
            nvme_disk = psutil.disk_usage('/atlas_data')
            tegra['nvme_used_gb'] = round(nvme_disk.used / (1024**3), 1)
            tegra['nvme_total_gb'] = round(nvme_disk.total / (1024**3), 1)
            tegra['nvme_pct'] = round(nvme_disk.percent, 1)
        except Exception:
            pass

        # Extended NVMe: I/O speeds, SMART health, temperature
        tegra.update(_get_disk_extended())

        # Network (cumulative bytes — frontend can diff if needed)
        net = psutil.net_io_counters()
        tegra['net_sent_mb'] = round(net.bytes_sent / (1024**2), 1)
        tegra['net_recv_mb'] = round(net.bytes_recv / (1024**2), 1)

        # Process count
        tegra['process_count'] = len(psutil.pids())

        # Uptime
        tegra['uptime_s'] = int(time.time() - psutil.boot_time())

        with _lock:
            _cache['data'] = tegra
            _cache['ts'] = time.time()

        return tegra

    except Exception as e:
        logger.error(f"get_stats error: {e}")
        return _cache['data'] or {}
