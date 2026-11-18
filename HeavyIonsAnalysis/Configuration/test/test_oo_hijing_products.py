"""Exercise the OO recovery product checks without opening a remote dataset."""

from contextlib import nullcontext
from io import StringIO
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import build_oo_hijing_truth as truth


GEN = ("genParticles", "", "SIM")
HEPMC = ("generatorSmeared", "", "SIM")
KEY = (1, 7, 83)
LFN = "/store/test/hijing.root"
MISSING = object()


class Vector(list):
    def empty(self):
        return len(self) == 0


class TestHijingProducts(unittest.TestCase):
    def table(self, particles, hepmc=MISSING):
        def lookup(label, handle):
            if label == GEN:
                if particles is MISSING:
                    return False
                handle.product.return_value = particles
            elif label == HEPMC:
                if hepmc is MISSING:
                    return False
                handle.product.return_value.GetEvent.return_value = hepmc
            else:
                self.fail(f"Unexpected EDM input {label}")
            return True

        events = Mock()
        events.size.return_value = 1
        events.getByLabel.side_effect = lookup
        fwlite = SimpleNamespace(Handle=lambda typename: Mock())
        output = StringIO()
        with (patch.dict(sys.modules, {"DataFormats.FWLite": fwlite}),
              patch.object(truth, "open_events", return_value=nullcontext(events)),
              patch.object(truth, "entry_id", return_value=KEY)):
            truth.write_events(output, {KEY}, {LFN: {KEY}}, Mock())
        return output.getvalue()

    def test_verified_empty_event_is_recorded(self):
        empty = Mock()
        empty.particles_size.return_value = 0
        self.assertEqual(self.table(Vector(), empty), f"OOHIJING_V1\nE 1 7 83 0 {LFN}\n")

    def test_rejected_particles_can_yield_zero_rows(self):
        unstable = Mock()
        unstable.status.return_value = 2
        self.assertEqual(self.table(Vector([unstable])), f"OOHIJING_V1\nE 1 7 83 0 {LFN}\n")

    def test_missing_genparticles_is_fatal(self):
        with self.assertRaisesRegex(ValueError, "genParticles::SIM"):
            self.table(MISSING)

    def test_empty_collection_requires_empty_hepmc(self):
        nonempty = Mock()
        nonempty.particles_size.return_value = 1
        for source, reason in ((MISSING, "missing"), (None, "null"), (nonempty, "contains 1 particle")):
            with self.subTest(source=source), self.assertRaisesRegex(ValueError, reason) as error:
                self.table(Vector(), source)
            self.assertIn(f"{LFN}, entry 0", str(error.exception))


if __name__ == "__main__":
    unittest.main()
