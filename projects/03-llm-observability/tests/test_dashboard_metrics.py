"""Every metric name the dashboards use, among those starting with DASHBOARD_METRICS (for example "vllm:"
or "DCGM_"), has at least one series in Prometheus. CI has neither vLLM nor a GPU, so sessions prove these
names: DCGM_ in session 1's GPU check, vllm: in session 2.
Run against a port-forwarded Prometheus:
  PROMETHEUS_URL=http://127.0.0.1:19090 DASHBOARD_METRICS=DCGM_ python3 -m unittest test_dashboard_metrics
Skipped unless PROMETHEUS_URL and DASHBOARD_METRICS are set.
"""
import os
import re
import unittest

from test_dashboards import DASHBOARDS, load, targets
from test_metrics import PROMETHEUS, query, until

PREFIX = os.environ.get("DASHBOARD_METRICS", "")


def metric_names(prefix):
    """Metric names in the dashboards' queries that start with prefix (histograms by their _bucket name)."""
    exprs = [t["expr"] for p in sorted(DASHBOARDS.glob("*.json")) for t in targets(load(p.name))]
    return sorted({m for e in exprs for m in re.findall(r"[A-Za-z_:][A-Za-z0-9_:]*", e) if m.startswith(prefix)})


@unittest.skipUnless(PROMETHEUS and PREFIX, "needs PROMETHEUS_URL and DASHBOARD_METRICS")
class DashboardMetricsTest(unittest.TestCase):
    def test_every_metric_has_a_series(self):
        names = metric_names(PREFIX)
        self.assertTrue(names, f"no dashboard metric starts with {PREFIX}")
        for name in names:
            with self.subTest(metric=name):
                self.assertTrue(until(lambda n=name: query(f'{{__name__="{n}"}}'), seconds=30), "no series")


if __name__ == "__main__":
    unittest.main()
