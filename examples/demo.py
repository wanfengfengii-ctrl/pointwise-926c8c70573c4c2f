import json
from pathlib import Path
from tempfile import TemporaryDirectory

from station.audit.readback import readback
from station.setup.bootstrap import open_archive, open_station


def main():
    sample = json.loads(Path(__file__).with_name('shift.json').read_text(encoding='utf-8'))
    with TemporaryDirectory() as directory:
        path = Path(directory) / 'station.sqlite3'
        station = open_station(path)
        device = sample['device_id']
        calibration = sample['calibration']
        station.configure(device, calibration['revision'], calibration['microliters_per_pulse'])
        shift = station.begin(sample['shift_id'], device, calibration['revision'], sample['limit_microliters'])
        later = sample['later_calibration']
        station.configure(device, later['revision'], later['microliters_per_pulse'])
        station.count(shift.recording_id, sample['pulses_before_first_inspection'])
        originals = [station.inspect(shift.recording_id, action) for action in sample['inspection_actions']]
        originals.append(station.stop(shift.recording_id, sample['stop_action']))
        by_sequence = {item.sequence: item for item in originals}
        publication_states = []
        receipts = {}
        for sequence in sample['delivery_order']:
            receipts[sequence] = station.accept(by_sequence[sequence])
            outcome = station.publish(shift.shift_id)
            publication_states.append({
                'status': outcome.status,
                'missing_sequences': list(outcome.missing_sequences),
                'report_available': outcome.report is not None,
            })
        expected_states = [
            {'status': 'missing', 'missing_sequences': [1, 2], 'report_available': False},
            {'status': 'missing', 'missing_sequences': [2], 'report_available': False},
            {'status': 'signed', 'missing_sequences': [], 'report_available': True},
        ]
        actual = {'source_pulses': [item.pulses for item in originals], 'publication': publication_states}
        expected = {'source_pulses': [7, 0, 0], 'publication': expected_states}
        if actual != expected:
            raise RuntimeError(f'unexpected capture or publication: {actual}')
        signed = outcome.report
        del station
        result = readback(open_archive(path), signed.identity.number)
        downstream_actual = {
            'pulses': result.report.totals.pulses,
            'microliters': result.report.totals.microliters,
            'frozen_coefficient': result.report.shift.microliters_per_pulse,
            'over_limit': result.report.decision.over_limit,
            'evidence_sequences': [item.evidence.sequence for item in result.provenance.entries],
            'same_report': result.report == signed,
            'same_receipts': [item.receipt for item in result.provenance.entries] ==
                             [receipts[1], receipts[2], receipts[3]],
        }
        downstream_expected = {
            'pulses': 7, 'microliters': 14, 'frozen_coefficient': 2, 'over_limit': False,
            'evidence_sequences': [1, 2, 3], 'same_report': True, 'same_receipts': True,
        }
        if downstream_actual != downstream_expected:
            raise RuntimeError(f'unexpected offline readback: {downstream_actual}')
        print(f'报告 {signed.identity.number}，{result.report.totals.pulses} 脉冲，'
              f'{result.report.totals.microliters} 微升，超限 {result.report.decision.over_limit}')
        print('POINTWISE_SCENARIO ' + json.dumps({
            'case_id': 'late-first.missing_zero', 'input': sample,
            'expected': expected, 'actual': actual,
            'downstream_expected': downstream_expected, 'downstream_actual': downstream_actual,
            'code_paths': [
                'station/meter/inspection.py', 'station/intake/receive.py', 'station/intake/completeness.py',
                'station/report/measure.py', 'station/report/publish.py', 'station/audit/readback.py',
            ],
            'oracle': '手算 7 + 0 + 0 = 7，乘冻结系数 2 得 14。按 3、1、2 列缺，等于阈值不超限。',
        }, ensure_ascii=False, sort_keys=True))


if __name__ == '__main__':
    main()
