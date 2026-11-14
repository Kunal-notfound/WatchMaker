"""Deterministic tests for the engineering optimisation lab.

The lab is deliberately model-free: candidates are real spec changes, and the
solver, mesh builder and verifier evaluate them.  These tests pin down that
contract — determinism, no mutation of the source spec, honest provenance and
a measurable difference for every ranked candidate.
"""

from __future__ import annotations

import unittest

from watchcad import optimise
from watchcad.nlp import parse
from watchcad.solver import solve

DIVER = ("A 42 mm steel dive watch, 300 m water resistance, black matte dial "
         "with applied lume indices, baton hands, sapphire crystal, "
         "screw-down caseback and crown, date.")


class OptimiseTests(unittest.TestCase):
    def setUp(self):
        self.spec, _ = solve(parse(DIVER, name="OptimiseTest").spec)
        self.checksum = self.spec.get("derived.checksum")

    def test_run_is_deterministic_and_does_not_mutate_the_spec(self):
        first = optimise.run(self.spec, "weight", 6)
        second = optimise.run(self.spec, "weight", 6)
        self.assertEqual(first["winner"], second["winner"])
        self.assertEqual(first["ranked"], second["ranked"])
        self.assertEqual(
            [(item["id"], item["metrics"].get("mass_g"))
             for item in first["candidates"]],
            [(item["id"], item["metrics"].get("mass_g"))
             for item in second["candidates"]],
        )
        self.assertEqual(self.spec.get("derived.checksum"), self.checksum)

    def test_baseline_is_the_current_spec(self):
        result = optimise.run(self.spec, "weight", 4)
        baseline = next(item for item in result["candidates"]
                        if item["id"] == "baseline")
        self.assertEqual(baseline["changes"], [])
        self.assertEqual(baseline["checksum"], self.checksum)
        self.assertEqual(result["baseline_checksum"], self.checksum)
        self.assertFalse(result["provenance"]["model_used"])
        self.assertFalse(result["provenance"]["physically_verified"])

    def test_weight_winner_is_lighter_or_equal_than_baseline(self):
        result = optimise.run(self.spec, "weight", 9)
        baseline = next(item for item in result["candidates"]
                        if item["id"] == "baseline")
        winner = next(item for item in result["candidates"]
                      if item["id"] == result["winner"])
        self.assertLessEqual(winner["metrics"]["mass_g"],
                             baseline["metrics"]["mass_g"])
        self.assertEqual(winner["status"], "ok")
        self.assertEqual(winner["metrics"]["failing_checks"], 0)

    def test_every_objective_returns_ranked_measured_candidates(self):
        for objective in optimise.OBJECTIVES:
            result = optimise.run(self.spec, objective, 6)
            self.assertEqual(result["objective"], objective)
            self.assertEqual(len(result["candidates"]), 6)
            self.assertIn(result["winner"], result["ranked"])
            for item in result["candidates"]:
                self.assertIn("metrics", item)
                self.assertIn("provenance", item["metrics"])
                self.assertEqual(item["metrics"]["provenance"]["mass_g"],
                                 "calculated")
                self.assertEqual(
                    item["metrics"]["provenance"]["estimated_unit_cost_usd"],
                    "estimated")
            changed = [item for item in result["candidates"]
                       if item["changes"] and item["status"] == "ok"]
            self.assertTrue(changed, objective)
            self.assertTrue(any(item["measurable"] for item in changed), objective)

    def test_candidate_changes_stay_inside_the_schema(self):
        result = optimise.run(self.spec, "manufacturability", 12)
        for item in result["candidates"]:
            for change in item["changes"]:
                self.assertIn(change["path"], optimise.FIELDS)
                self.assertNotIn(change["path"], self.spec.locks)


if __name__ == "__main__":
    unittest.main()
