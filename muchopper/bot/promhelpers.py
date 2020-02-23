import contextlib


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


def set_optional(metric, value, *, labels=[]):
    if metric is None:
        return
    if labels:
        metric.labels(*labels).set(value)
    else:
        metric.set(value)
