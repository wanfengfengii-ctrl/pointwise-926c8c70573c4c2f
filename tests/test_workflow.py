from station.audit.readback import readback
from station.setup.bootstrap import open_archive, open_station
from station.setup.shifts import ShiftRegistry
from tests.support import StationCase


class TestWorkflow(StationCase):
    def test_day_to_night_handover(self):
        shift = self.begin()
        self.station.configure('bench', 'r2', 3)
        self.station.count(shift.recording_id, 7)
        first = self.station.inspect(shift.recording_id, 'first')
        zero = self.station.inspect(shift.recording_id, 'zero')
        terminal = self.station.stop(shift.recording_id, 'stop')
        receipts = {}
        for item, gaps in ((terminal, (1, 2)), (first, (2,)), (zero, ())):
            receipts[item.sequence] = self.station.accept(item)
            outcome = self.station.publish(shift.shift_id)
            self.assertEqual(outcome.missing_sequences, gaps)
            self.assertEqual(outcome.status, 'missing' if gaps else 'signed')
            if gaps:
                self.assertIsNone(outcome.report)
        signed = outcome.report
        self.station = None
        self.sessions = None
        restarted = open_station(self.path)
        self.assertEqual(restarted.configure('bench', 'r2', 3).microliters_per_pulse, 3)
        self.assertEqual(restarted.begin('day', 'bench', 'r1', 14), shift)
        with restarted.database.read_snapshot() as conn:
            self.assertEqual(ShiftRegistry(conn).find('day').microliters_per_pulse, 2)
        self.assertEqual(restarted.publish('day').report, signed)
        result = readback(open_archive(self.path), signed.identity.number)
        self.assertEqual(result.report, signed)
        self.assertEqual([entry.evidence.pulses for entry in result.provenance.entries], [7, 0, 0])
        self.assertEqual([entry.receipt for entry in result.provenance.entries],
                         [receipts[1], receipts[2], receipts[3]])
        self.assertEqual((result.report.totals.pulses, result.report.totals.microliters), (7, 14))
        self.assertEqual((result.report.decision.limit_microliters, result.report.decision.over_limit), (14, False))
