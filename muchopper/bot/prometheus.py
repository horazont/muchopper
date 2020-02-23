import contextlib
import functools
import time

import aiohttp
from aiohttp import web

import prometheus_client
import prometheus_client.openmetrics.exposition


class PrometheusMetrics:
    def __init__(self, registry=None):
        super().__init__()
        self.registry = registry or prometheus_client.REGISTRY
        self._response_time_metric = prometheus_client.Summary(
            "muclumbus_http_response_seconds",
            "Monotonic time passed for processing a reqeust",
            ["endpoint", "http_status"]
        )
        self._existence_metric = prometheus_client.Gauge(
            "muclumbus_http_endpoint_flag",
            "Existence of an endpoint in the code",
            ["endpoint"],
        )
        # self.registry.register(self)

        self.handle_metrics = self.observe("metrics", self.handle_metrics)

    def observe(self, endpoint, f):
        self._existence_metric.labels("metrics").set(1)

        @functools.wraps(f)
        async def wrapped(*args, **kwargs):
            t0 = time.monotonic()
            status_code = 500
            try:
                response = await f(*args, **kwargs)
                status_code = response.status
                return response
            finally:
                t1 = time.monotonic()
                self._response_time_metric.labels(
                    endpoint, str(status_code)
                ).observe(t1-t0)

        return wrapped

    def collect(self):
        yield self._existence_metric
        yield self._response_time_metric

    async def handle_metrics(self, request):
        content_type = \
            prometheus_client.openmetrics.exposition.CONTENT_TYPE_LATEST
        encoder = prometheus_client.openmetrics.exposition.generate_latest
        return web.Response(
            body=encoder(self.registry),
            status=200,
            content_type=content_type.replace("; charset=utf-8", ""),
            charset="utf-8",
        )


def make_app(endpoint):
    app = web.Application()
    app.add_routes([web.get("/metrics", endpoint.handle_metrics)])
    return app


async def start_app(app, bind_host, bind_port):
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, bind_host, bind_port)
    await site.start()
    return runner
