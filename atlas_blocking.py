"""
Keep blocking work off the gevent hub.

app.py monkey-patches only sockets and DNS (subprocess, time, select and
threading are deliberately left native — see the comment at the top of
app.py). That means a plain subprocess.run(), time.sleep() or
threading.Event.wait() issued from a request greenlet stalls *every* HTTP
request and Socket.IO client until it returns: a 20-second nmcli scan froze
the whole UI for 20 seconds.

Every such call made from code that can run on the hub goes through here:

* on the hub (main) thread, the work is handed to gevent's native thread pool
  and the calling greenlet yields until it finishes;
* on a native worker thread (GPS reader, Wi-Fi failover loop, AI worker, …)
  it simply runs inline, because those threads have no hub to protect.

Running the unpatched subprocess module inside gevent's pool threads is safe:
it uses real pipes and the real select(), never a greenlet.
"""
import subprocess
import threading
import time


def on_hub_thread():
    """True when called from the thread that runs the gevent hub.

    Flask-SocketIO's gevent server lives on the main thread; every other
    thread in Atlas is a native worker.
    """
    return threading.current_thread() is threading.main_thread()


def call(fn, *args, **kwargs):
    """Run fn(*args, **kwargs) without blocking the hub; return its result."""
    if not on_hub_thread():
        return fn(*args, **kwargs)
    try:
        import gevent
        pool = gevent.get_hub().threadpool
    except Exception:
        return fn(*args, **kwargs)
    return pool.apply(fn, args, kwargs)


def run(cmd, **kwargs):
    """subprocess.run() that is safe to call from a request handler."""
    return call(subprocess.run, cmd, **kwargs)


def check_output(cmd, **kwargs):
    """subprocess.check_output() that is safe to call from a request handler."""
    return call(subprocess.check_output, cmd, **kwargs)


def sleep(seconds):
    """Sleep without freezing other greenlets when called on the hub."""
    if on_hub_thread():
        try:
            import gevent
            gevent.sleep(seconds)
            return
        except Exception:
            pass
    time.sleep(seconds)


def wait_event(event, timeout=None, poll=0.05):
    """threading.Event.wait() that yields to other greenlets on the hub.

    A native Event.wait() on the hub thread blocks the event loop, so the
    greenlet that would set the event can never run. Poll cooperatively
    instead. Returns the event's final state, like Event.wait().
    """
    if not on_hub_thread():
        return event.wait(timeout)
    deadline = None if timeout is None else time.monotonic() + timeout
    while not event.is_set():
        if deadline is not None and time.monotonic() >= deadline:
            return event.is_set()
        sleep(poll)
    return True
