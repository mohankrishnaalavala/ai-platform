"""P3's Grafana dashboards (dashboards/*.json), which deploy.sh loads from Git as ConfigMaps.

Always (lint job): every file parses, each uid is one of P3's and unique, and every data source a panel
names is one Grafana has (the chart's Prometheus, uid "prometheus", and Tempo). tests/charts.sh checks
that every query parses (promtool).
In ci.yml's gateway job (after test_metrics.py): every gateway panel's query returns data from the span
metrics. Skipped there unless SPANS_FILE, TEMPO_URL and PROMETHEUS_URL are set.
"""
import json
import pathlib
import unittest

from test_metrics import PROMETHEUS, SPANS_FILE, TEMPO, query, until

DASHBOARDS = pathlib.Path(__file__).resolve().parent.parent / "dashboards"
UIDS = {"model-serving.json": "p3-model-serving", "gateway.json": "p3-gateway"}
DATASOURCES = {"prometheus", "tempo"}


def load(name):
    return json.loads((DASHBOARDS / name).read_text())


def targets(dashboard):
    return [t for p in dashboard["panels"] for t in p["targets"]]


class DashboardFilesTest(unittest.TestCase):
    def test_each_file_has_its_uid(self):
        self.assertEqual(sorted(p.name for p in DASHBOARDS.glob("*.json")), sorted(UIDS))
        self.assertEqual({name: load(name)["uid"] for name in UIDS}, UIDS)

    def test_every_data_source_exists(self):
        for name in UIDS:
            d = load(name)
            used = {p["datasource"]["uid"] for p in d["panels"]} | {t["datasource"]["uid"] for t in targets(d)}
            self.assertLessEqual(used, DATASOURCES, name)


@unittest.skipUnless(SPANS_FILE and TEMPO and PROMETHEUS, "needs the CI gateway stack (SPANS_FILE, TEMPO_URL, PROMETHEUS_URL)")
class GatewayQueriesTest(unittest.TestCase):
    def test_every_gateway_query_returns_data(self):
        for t in targets(load("gateway.json")):
            with self.subTest(expr=t["expr"]):
                self.assertTrue(until(lambda expr=t["expr"]: query(expr)), "no data")


if __name__ == "__main__":
    unittest.main()
