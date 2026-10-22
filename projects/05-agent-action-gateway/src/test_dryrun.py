import copy
import unittest

import dryrun


def dep(replicas, image="app:v1", strategy=None):
    d = {"kind": "Deployment", "metadata": {"name": "checkout", "generation": 4, "resourceVersion": "99",
                                            "managedFields": [{"manager": "x"}]},
         "spec": {"replicas": replicas, "template": {"metadata": {"labels": {"app": "checkout"}},
                                                     "spec": {"containers": [{"image": image}]}}},
         "status": {"readyReplicas": replicas}}
    if strategy:
        d["spec"]["strategy"] = strategy
    return d


def pdb(name, ready, least, selector=None):
    """A PodDisruptionBudget with `ready` healthy pods that must keep at least `least` of them."""
    return {"metadata": {"name": name}, "spec": {"selector": selector if selector is not None else {"matchLabels": {"app": "checkout"}}},
            "status": {"currentHealthy": ready, "desiredHealthy": least}}


class BlastRadius(unittest.TestCase):
    def test_scale_up_takes_nothing_down(self):
        self.assertEqual(dryrun.blast_radius("Deployment", dep(2), dep(6), [pdb("p", 0, 1)]),
                         {"pods_affected": 4, "capacity_change_pct": 200.0, "pdb_violations": []})

    def test_scale_down_below_the_budget(self):
        other = pdb("other", 4, 4, {"matchLabels": {"app": "x"}})
        r = dryrun.blast_radius("Deployment", dep(4), dep(1), [pdb("p", 4, 2), other])  # 4 - 3 = 1 ready, 2 needed
        self.assertEqual(r, {"pods_affected": 3, "capacity_change_pct": -75.0, "pdb_violations": ["p"]})
        self.assertEqual(dryrun.blast_radius("Deployment", dep(4), dep(2), [pdb("p", 4, 2)])["pdb_violations"], [])

    def test_rollout_takes_down_max_unavailable(self):
        r = dryrun.blast_radius("Deployment", dep(4), dep(4, "app:v2"), [pdb("p", 4, 3)])
        self.assertEqual(r, {"pods_affected": 4, "capacity_change_pct": 0.0, "pdb_violations": []})  # 25% of 4 = 1
        r = dryrun.blast_radius("Deployment", dep(4), dep(4, "app:v2", {"type": "Recreate"}), [pdb("p", 4, 3)])
        self.assertEqual(r["pdb_violations"], ["p"])

    def test_scenario_dangerous_13(self):  # 3 replicas, maxUnavailable 1, maxSurge 0; the budget needs all 3
        strategy = {"type": "RollingUpdate", "rollingUpdate": {"maxUnavailable": 1, "maxSurge": 0}}
        r = dryrun.blast_radius("Deployment", dep(3, strategy=strategy), dep(3, "app:v2", strategy), [pdb("payments", 3, 3)])
        self.assertEqual(r["pdb_violations"], ["payments"])

    def test_a_budget_without_status_counts_as_none_ready(self):
        no_status = pdb("new", 0, 0)
        del no_status["status"]
        self.assertEqual(dryrun.blast_radius("Deployment", dep(3), dep(2), [no_status])["pdb_violations"], ["new"])

    def test_create_delete_and_configmaps(self):
        self.assertEqual(dryrun.blast_radius("Deployment", None, dep(3), [])["capacity_change_pct"], None)
        self.assertEqual(dryrun.blast_radius("Deployment", dep(3), None, [pdb("p", 3, 1)])["pdb_violations"], ["p"])
        self.assertEqual(dryrun.blast_radius("ConfigMap", {}, {}, []), {"pods_affected": 0, "capacity_change_pct": 0.0, "pdb_violations": []})

    def test_down_at_once(self):
        self.assertEqual(dryrun.down_at_once(dep(10)), 2)  # 25% rounded down
        self.assertEqual(dryrun.down_at_once(dep(1, strategy={"rollingUpdate": {"maxUnavailable": 0, "maxSurge": "10%"}})), 0)
        self.assertEqual(dryrun.down_at_once(dep(1, strategy={"rollingUpdate": {"maxUnavailable": "10%", "maxSurge": 0}})), 1)

    def test_selects(self):
        labels = {"app": "checkout", "tier": "web"}
        self.assertTrue(dryrun.selects({}, labels))
        self.assertTrue(dryrun.selects({"matchExpressions": [{"key": "tier", "operator": "In", "values": ["web"]},
                                                             {"key": "x", "operator": "DoesNotExist"}]}, labels))
        self.assertFalse(dryrun.selects({"matchExpressions": [{"key": "tier", "operator": "NotIn", "values": ["web"]}]}, labels))
        self.assertFalse(dryrun.selects({"matchExpressions": [{"key": "x", "operator": "Exists"}]}, labels))
        self.assertFalse(dryrun.selects({"matchLabels": {"app": "other"}}, labels))


class DiffAndVersion(unittest.TestCase):
    def test_diff_shows_spec_not_bookkeeping(self):
        after = dep(6)
        after["metadata"]["generation"] = 5
        d = dryrun.diff(dep(2), after)
        self.assertIn('-    "replicas": 2,', d)
        self.assertIn('+    "replicas": 6,', d)
        self.assertNotIn("generation", d)
        self.assertNotIn("readyReplicas", d)
        self.assertEqual(dryrun.diff(dep(2), dep(2)), "")

    def test_snapshot_drops_managed_fields_only(self):
        d = dep(2)
        s = dryrun.snapshot(d)
        self.assertNotIn("managedFields", s["metadata"])
        self.assertEqual(s["metadata"]["generation"], 4)
        self.assertIn("managedFields", d["metadata"])  # a copy
        self.assertIsNone(dryrun.snapshot(None))

    def test_version(self):
        d = dep(2)
        self.assertEqual(dryrun.version("Deployment", d), 4)
        self.assertEqual(dryrun.version("ConfigMap", d), "99")
        self.assertIsNone(dryrun.version("Deployment", None))
        status_only = copy.deepcopy(d)
        status_only["metadata"]["resourceVersion"] = "100"
        self.assertEqual(dryrun.version("Deployment", status_only), 4)


if __name__ == "__main__":
    unittest.main()
