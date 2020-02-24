import contextlib
import time


@contextlib.contextmanager
def time_optional(metric, *labels):
    if metric is None:
        yield
        return
    if labels:
        m = metric.labels(*labels)
    else:
        m = metric
    with m.time():
        yield


@contextlib.contextmanager
def time_optional_late(metric):
    if metric is None:
        yield
        return
    t0 = time.monotonic()
    info = {"labels": None}
    try:
        yield info
    finally:
        t1 = time.monotonic()
        if info["labels"]:
            m = metric.labels(*info["labels"])
        else:
            m = metric
        m.observe(t1-t0)


def set_optional(metric, value, *, labels=[]):
    if metric is None:
        return
    if labels:
        metric.labels(*labels).set(value)
    else:
        metric.set(value)


def inc_optional(metric, *, labels=[]):
    if metric is None:
        return
    if labels:
        metric.labels(*labels).inc()
    else:
        metric.inc()
