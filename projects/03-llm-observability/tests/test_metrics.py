"""Traces into Tempo, span metrics into Prometheus: P3's collector pipeline (helm/otel-collector.yaml),
rendered from the chart and run in ci.yml's gateway job after test_spans.py.

Checks:
- a gateway trace can be looked up in Tempo
- Prometheus has the span metrics for both model groups, with failed chat-default attempts (fallbacks)
- the latency histogram has a 2.5 s bucket (the latency SLO's threshold)
- the metrics carry no label beyond the ones P3 chose (a guard against a per-user or per-prompt label)
- SCALE_QUERY, the gateway's chat-default request rate that the scale-from-zero trigger will read, returns
  a number
Skipped unless SPANS_FILE, TEMPO_URL and PROMETHEUS_URL are set.
"""
import json
import os
import time
import unittest
import urllib.error
import urllib.parse
import urllib.request

from test_spans import SPANS_FILE, read_spans

TEMPO, PROMETHEUS = os.environ.get("TEMPO_URL", ""), os.environ.get("PROMETHEUS_URL", "")
CALLS = 'traces_span_metrics_calls_total{span_name="litellm_request"}'
SCALE_QUERY = ('sum(rate(traces_span_metrics_calls_total{span_name="litellm_request",'
               'litellm_model_group="chat-default"}[2m]))')
# The span metrics' own labels (helm/otel-collector.yaml's dimensions plus the connector's defaults).
# job and instance come from the scrape; exported_job is the exporter's own job (from service.name), which
# Prometheus renames; otel_scope_* name the connector. One value each per service. Nothing else may appear.
LABELS = {"__name__", "service_name", "span_name", "span_kind", "status_code", "litellm_model_group",
          "metadata_user_api_key_team_alias", "http_response_status_code", "job", "instance", "exported_job"}


def get(url):
    with urllib.request.urlopen(url, timeout=10) as r:
        return json.loads(r.read())


def query(q):
    return get(PROMETHEUS + "/api/v1/query?" + urllib.parse.urlencode({"query": q}))["data"]["result"]


def until(check, seconds=60):
    """check() until it returns something truthy; the collector flushes every 15 s, Prometheus scrapes every 5 s."""
    found = None
    for _ in range(seconds // 3):
        try:
            if found := check():
                return found
        except (urllib.error.URLError, ValueError):
            pass
        time.sleep(3)
    return found


@unittest.skipUnless(SPANS_FILE and TEMPO and PROMETHEUS, "needs the CI gateway stack (SPANS_FILE, TEMPO_URL, PROMETHEUS_URL)")
class MetricsTest(unittest.TestCase):
    def test_a_gateway_trace_is_in_tempo(self):
        trace = max((s for s in read_spans() if s["name"] == "litellm_request"), key=lambda s: s["start"])["trace"]

        def found():
            try:
                return "litellm_request" in json.dumps(get(f"{TEMPO}/api/v2/traces/{trace}"))
            except urllib.error.HTTPError:  # 404 until Tempo has the trace
                return False
        self.assertTrue(until(found), f"trace {trace} not in Tempo")

    def test_span_metrics_for_both_models_and_fallbacks(self):
        def seen():
            pairs = {(s["metric"].get("litellm_model_group"), s["metric"].get("status_code")) for s in query(CALLS)}
            hosted = {("chat-hosted", "STATUS_CODE_OK"), ("chat-hosted", "STATUS_CODE_UNSET")} & pairs
            return pairs if ("chat-default", "STATUS_CODE_ERROR") in pairs and hosted else None
        pairs = until(seen)
        print(f"\nlitellm_request series (model group, status): {sorted(pairs or [], key=str)}")
        self.assertTrue(pairs, "no failed chat-default attempts (fallbacks) and answered chat-hosted ones")

    def test_latency_histogram_has_the_slo_bucket(self):
        self.assertTrue(until(lambda: query('traces_span_metrics_duration_seconds_bucket{le="2.5"}')))

    def test_the_chosen_labels_and_no_others(self):
        series = until(lambda: query(CALLS))
        labels = {k for s in series for k in s["metric"]}
        self.assertEqual({k for k in labels - LABELS if not k.startswith("otel_scope_")}, set())
        # A misnamed dimension would make its label vanish, not fail: each must be on some series.
        self.assertLessEqual({"litellm_model_group", "metadata_user_api_key_team_alias", "http_response_status_code"}, labels)

    def test_the_scale_from_zero_query_returns_a_number(self):
        result = until(lambda: query(SCALE_QUERY))
        print(f"\n{SCALE_QUERY} = {result[0]['value'][1] if result else 'nothing'}")
        self.assertEqual(len(result), 1)
        float(result[0]["value"][1])


if __name__ == "__main__":
    unittest.main()
