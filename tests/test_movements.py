"""Movement database, provenance and case-fit compatibility tests.

The database is intentionally small and sourced.  These tests pin the parts
that make it trustworthy: units are millimetres, a missing value is unknown
rather than zero, every published value has a source, corrections keep their
history, the compatibility report distinguishes PASS/WARNING/FAIL/UNKNOWN, and a
proposal is a diff that never mutates the baseline design.
"""

from __future__ import annotations

import copy
import unittest
from pathlib import Path

from watchcad import movements
from watchcad.nlp import parse
from watchcad.parts import build_parts
from watchcad.schema import Spec, new_spec
from watchcad.solver import solve

DIVER = ("A 42 mm steel dive watch, 300 m water resistance, black matte dial "
         "with applied lume indices, baton hands, sapphire crystal, "
         "screw-down caseback and crown, date.")


def solved(text: str = DIVER) -> Spec:
    spec, _ = solve(parse(text, name="MovementTest").spec)
    return spec


class MovementRecordTests(unittest.TestCase):
    def test_every_record_validates(self):
        records = movements.all_records()
        self.assertGreaterEqual(len(records), 4)
        for record in records:
            self.assertEqual(movements.validate_record(record), [], record["id"])
            self.assertTrue(record["sources"])
            self.assertTrue(record["provenance"])
            self.assertTrue(record["verified_at"])

    def test_units_are_millimetres_and_manufacturer_sourced(self):
        eta = movements.get_record("eta-2824-2")
        self.assertEqual(eta["diameter_mm"], 25.60)
        self.assertEqual(eta["thickness_mm"], 4.60)
        self.assertEqual(eta["frequency_bph"], 28800)
        self.assertEqual(eta["jewels"], 25)
        provenance = movements.field_provenance(eta)
        self.assertEqual(provenance["diameter_mm"]["status"], "verified")
        self.assertEqual(provenance["diameter_mm"]["source"], "eta-2824-2-page")

    def test_null_is_unknown_not_zero(self):
        eta = movements.get_record("eta-2824-2")
        self.assertIsNone(eta["hand_fitting"]["hour_mm"])
        provenance = movements.field_provenance(eta)
        self.assertEqual(provenance["hand_fitting.hour_mm"]["status"], "unknown")
        self.assertIn("ETA", provenance["hand_fitting.hour_mm"]["note"])
        missing = {item["path"] for item in movements.missing_fields(eta)}
        self.assertIn("hand_fitting.hour_mm", missing)
        self.assertIn("stem.position_height_mm", missing)

    def test_sellita_publishes_hand_fitting_heights(self):
        sellita = movements.get_record("sellita-sw200-1")
        provenance = movements.field_provenance(sellita)
        for field in ("hand_fitting.hour_mm", "hand_fitting.minute_mm",
                      "hand_fitting.second_mm"):
            self.assertEqual(provenance[field]["status"], "verified", field)
            self.assertIsNotNone(provenance[field]["value"], field)
        self.assertAlmostEqual(sellita["hand_fitting"]["second_mm"], 1.75)

    def test_migration_is_additive_and_never_guesses(self):
        old = copy.deepcopy(movements.MOVEMENTS["eta-2824-2"])
        old["schema_version"] = 0
        old.pop("stem")
        old.pop("hand_fitting")
        migrated = movements.migrate_record(old)
        self.assertEqual(migrated["diameter_mm"], 25.60)
        self.assertIsNone(migrated["stem"].get("position_height_mm"))
        self.assertIn("stem.position_height_mm", migrated["unknowns"])
        self.assertTrue(any(item.get("event") == "schema_migration"
                            for item in migrated["revisions"]))
        self.assertEqual(movements.validate_record(
            movements._attach_sources(migrated)), [])

    def test_corrections_keep_history_and_source(self):
        sellita = movements.get_record("sellita-sw200-1")
        corrected = movements.correct_record(
            sellita, {"power_reserve_h": 38.0}, source="sellita-sw200-1",
            note="The document states a 38 h minimum; store the minimum.",
            at="2026-11-17")
        self.assertEqual(corrected["power_reserve_h"], 38.0)
        self.assertEqual(corrected["verified_at"], "2026-11-17")
        revision = corrected["revisions"][-1]
        self.assertEqual(revision["field"], "power_reserve_h")
        self.assertEqual(revision["from"], 41.0)
        self.assertEqual(revision["to"], 38.0)
        self.assertEqual(revision["source"], "sellita-sw200-1")
        # The original record is untouched and its history is still empty.
        self.assertEqual(sellita["power_reserve_h"], 41.0)
        self.assertEqual(sellita["revisions"], [])

    def test_search_and_filters(self):
        self.assertEqual([item["id"] for item in movements.search("2824")],
                         ["eta-2824-2"])
        sellita = movements.search(maker="Sellita")
        self.assertEqual([item["id"] for item in sellita], ["sellita-sw200-1"])
        thin = movements.search(max_thickness=4.0)
        self.assertEqual([item["id"] for item in thin], ["miyota-9015"])
        # A dimension filter excludes a record with no published dimension;
        # include_unknown makes that exclusion explicit and optional.
        no_fitting = movements.search(max_thickness=4.0, include_unknown=True)
        self.assertGreaterEqual(len(no_fitting), 1)


class MovementFitTests(unittest.TestCase):
    def setUp(self):
        self.spec = solved()

    def test_sellita_hand_clearance_fails_on_a_42_mm_diver(self):
        record = movements.get_record("sellita-sw200-1")
        report = movements.compatibility(self.spec, record)
        checks = {check["id"]: check for check in report["checks"]}
        self.assertEqual(checks["hands.clearance"]["status"], "FAIL")
        self.assertIsNotNone(checks["hands.clearance"]["fix"])
        self.assertEqual(report["overall"], "FAIL")
        self.assertFalse(report["provenance"]["physically_verified"])
        # The fix is a proposal only; the design is unchanged.
        self.assertEqual(self.spec.get("hands.stack_height_mm"), 0.9)

    def test_unknown_movement_data_is_not_a_pass(self):
        report = movements.compatibility(
            self.spec, movements.get_record("eta-2824-2"))
        checks = {check["id"]: check for check in report["checks"]}
        self.assertEqual(checks["hands.clearance"]["status"], "UNKNOWN")
        self.assertEqual(checks["stem.crown_alignment"]["status"], "UNKNOWN")
        self.assertEqual(report["overall"], "WARNING")
        self.assertGreaterEqual(report["counts"]["UNKNOWN"], 2)
        self.assertEqual(report["counts"]["FAIL"], 0)

    def test_case_and_dial_checks_have_explicit_statuses(self):
        record = movements.get_record("eta-2824-2")
        checks = {check["id"]: check
                  for check in movements.compatibility(self.spec, record)["checks"]}
        self.assertIn(checks["case.internal_diameter"]["status"],
                      {"PASS", "WARNING", "FAIL"})
        self.assertIn(checks["dial.fit"]["status"], {"PASS", "WARNING", "FAIL"})
        self.assertIn("case.mounting", checks)
        self.assertIn("caseback.clearance", checks)
        self.assertIn("complication.alignment", checks)

    def test_stem_alignment_uses_a_real_height_when_published(self):
        record = movements.get_record("eta-2824-2")
        record["stem"]["position_height_mm"] = 2.30
        for group in record["provenance"]:
            group["fields"] = [field for field in group["fields"]
                               if field != "stem.position_height_mm"]
        record["provenance"].append({
            "fields": ["stem.position_height_mm"],
            "source": "eta-2824-2-page", "status": "secondary"})
        record["unknowns"].pop("stem.position_height_mm", None)

        caseback_top = float(self.spec.get("derived.stack")["z_caseback_top"])
        aligned = self.spec.copy()
        aligned.set("case.crown_height_mm", round(caseback_top + 2.30, 2))
        solve(aligned)
        report = movements.compatibility(aligned, record)
        check = next(item for item in report["checks"]
                     if item["id"] == "stem.crown_alignment")
        self.assertEqual(check["status"], "PASS", check["detail"])

        misplaced = self.spec.copy()
        misplaced.set("case.crown_height_mm", round(caseback_top + 3.30, 2))
        solve(misplaced)
        report = movements.compatibility(misplaced, record)
        check = next(item for item in report["checks"]
                     if item["id"] == "stem.crown_alignment")
        self.assertEqual(check["status"], "FAIL", check["detail"])

    def test_crown_height_is_millimetres_and_does_not_change_checks(self):
        spec = solved()
        default_axis = spec.get("derived.stack")["z_crown_axis"]
        spec.set("case.crown_height_mm", 5.20)
        spec, _ = solve(spec)
        self.assertEqual(spec.get("derived.stack")["z_crown_axis"], 5.20)
        self.assertNotEqual(default_axis, 5.20)
        app = next(part for part in build_parts(spec, "low")
                   if part.name == "crown")
        low = next(part for part in build_parts(spec, "low")
                   if part.name == "crown")
        high = next(part for part in build_parts(spec, "high")
                    if part.name == "crown")
        self.assertEqual(app.bounds(), low.bounds())
        self.assertEqual(low.bounds(), high.bounds())

    def test_mounting_is_an_explicit_unknown_state(self):
        spec = solved()
        self.assertEqual(spec.get("case.movement_mounting"), "unknown")
        record = movements.get_record("seiko-nh35a")
        report = movements.compatibility(spec, record)
        mounting = next(item for item in report["checks"]
                        if item["id"] == "case.mounting")
        self.assertEqual(mounting["status"], "UNKNOWN")
        # The NH35A document explicitly requires a screw-type caseback.
        snap = spec.copy()
        snap.set("case.caseback", "snap")
        mounting = next(item for item in movements.compatibility(snap, record)["checks"]
                        if item["id"] == "case.mounting")
        self.assertEqual(mounting["status"], "FAIL")


class MovementProposalTests(unittest.TestCase):
    def test_proposal_is_a_diff_and_baseline_stays_unchanged(self):
        spec = solved()
        before = copy.deepcopy(spec.data)
        checksum = spec.get("derived.checksum")
        proposal = movements.propose(spec, "sellita-sw200-1")
        self.assertEqual(spec.data, before)
        self.assertEqual(spec.get("derived.checksum"), checksum)
        self.assertEqual(proposal["movement"]["id"], "sellita-sw200-1")
        self.assertEqual(proposal["compatibility"]["overall"], "FAIL")
        paths = {change["path"] for change in proposal["variant"]["changes"]}
        self.assertIn("movement.calibre_id", paths)
        self.assertIn("movement.width_mm", paths)
        self.assertTrue(proposal["suggested_changes"])
        self.assertTrue(any(change["path"] == "hands.stack_height_mm"
                            for change in proposal["suggested_changes"]))
        self.assertTrue(proposal["spec_diff"])
        self.assertIn("nothing is applied", proposal["provenance_note"].lower())

    def test_proposal_respects_locked_fields(self):
        spec = solved()
        spec.lock("movement.width_mm", True)
        proposal = movements.propose(spec, "miyota-9015")
        changed = {change["path"] for change in proposal["variant"]["changes"]}
        self.assertNotIn("movement.width_mm", changed)
        self.assertIn("movement.width_mm",
                      {item["path"] for item in proposal["locked"]})

    def test_unknown_movement_is_rejected(self):
        with self.assertRaises(KeyError):
            movements.propose(solved(), "not-a-calibre")


class MovementApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import json
        import tempfile
        import threading
        import urllib.request
        from http.server import ThreadingHTTPServer
        from pathlib import Path as _Path

        from watchcad import api

        cls.tmp = tempfile.TemporaryDirectory()
        api.BUILD_ROOT = _Path(cls.tmp.name)
        api.STUDIO = api.Studio(solved())
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), api.Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f"http://127.0.0.1:{cls.server.server_address[1]}"
        cls.urllib = urllib.request
        cls.json = json

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.tmp.cleanup()

    def get(self, path):
        with self.urllib.urlopen(self.base + path) as response:
            return response.status, self.json.loads(response.read())

    def post(self, path, body):
        request = self.urllib.Request(
            self.base + path, data=self.json.dumps(body).encode(),
            headers={"Content-Type": "application/json"})
        with self.urllib.urlopen(request) as response:
            return response.status, self.json.loads(response.read())

    def test_catalog_detail_and_proposal_routes(self):
        status, catalog = self.get("/api/movements")
        self.assertEqual(status, 200)
        self.assertGreaterEqual(catalog["count"], 4)
        self.assertIn("ETA", catalog["makers"])
        self.assertTrue(catalog["sources"])
        status, filtered = self.get("/api/movements?q=9015&type=automatic")
        self.assertEqual([item["id"] for item in filtered["movements"]],
                         ["miyota-9015"])

        status, detail = self.get("/api/movements/detail?id=sellita-sw200-1")
        self.assertEqual(status, 200)
        self.assertEqual(detail["record"]["calibre"], "SW 200-1")
        self.assertEqual(detail["compatibility"]["overall"], "FAIL")
        self.assertIn("hands.clearance", {check["id"]
                                          for check in detail["compatibility"]["checks"]})
        self.assertTrue(detail["provenance"]["hand_fitting.second_mm"])
        self.assertTrue(detail["sources"])

        status, proposal = self.post("/api/movements/propose",
                                     {"id": "sellita-sw200-1"})
        self.assertEqual(status, 200)
        self.assertTrue(proposal["variant"]["changes"])
        self.assertTrue(proposal["spec_diff"])
        # The proposal route must not mutate the design.
        status, state = self.get("/api/state")
        self.assertEqual(state["spec"]["data"]["movement"]["calibre_id"], "")

        try:
            self.get("/api/movements/detail?id=not-a-calibre")
            self.fail("unknown calibre should be a 404")
        except self.urllib.HTTPError as error:
            self.assertEqual(error.code, 404)
            error.close()


class FrontendDataTests(unittest.TestCase):
    def test_manufacturer_records_are_not_embedded_in_the_frontend(self):
        root = Path(__file__).resolve().parent.parent / "web"
        text = "\n".join(path.read_text(encoding="utf-8")
                         for path in root.glob("*") if path.is_file())
        for needle in ("sellita", "2824-2", "nh35", "9015",
                       "portal.eta.ch", "miyotamovement.com"):
            self.assertNotIn(needle.lower(), text.lower(),
                             f"{needle} must come from the API, not the frontend")


if __name__ == "__main__":
    unittest.main()
