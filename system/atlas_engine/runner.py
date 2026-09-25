"""External commands the engine runs, behind one seam so tests can fake them."""
import subprocess


class Runner:
    def run(self, cmd, timeout=120, input=None, check=False, env=None):
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                           input=input, env=env)
        if check and r.returncode != 0:
            raise RuntimeError(f"{cmd[0]} failed ({r.returncode}): {(r.stderr or r.stdout).strip()[:400]}")
        return r


DEFAULT = Runner()
