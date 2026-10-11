"""All-series race selection, rendering and result persistence."""
import datetime as dt
import json
import os
import tempfile
import sys
import unittest
from unittest.mock import patch
import board
import nascar

class SeriesTests(unittest.TestCase):
    def doc(self, state, count=40):
        return {'events': [{'name': 'Race at IOWA', 'status': {'type': {'state': state}},
                'competitions': [{'competitors': [{'order': i, 'athlete': {'shortName': f'D. DRIVER{i}'}}
                                                  for i in range(count, 0, -1)]}]}]}

    def build(self, states):
        return nascar.build({slug: self.doc(state) for (_, slug, _), state in zip(nascar.SERIES, states)},
                            now=dt.datetime(2026, 10, 11, tzinfo=dt.timezone.utc))

    def test_each_series_takes_live_priority(self):
        for index, series in enumerate(('CUP', 'XFN', 'TRK')):
            states=['post']*3
            states[index]='in'
            nc=self.build(states)
            self.assertEqual(len(nc['race_fields']), 3)
            self.assertEqual([f['series'] for f in board.race_candidates(nc)], [series])
            rows, info=board.race_order(nc)
            self.assertEqual(info['label'], 'Running order')
            self.assertEqual(len(rows), 40)
            self.assertTrue(nc['live'])
            self.assertEqual(board.screen_podium({}, {}, {'nascar': nc}, {}).size, (64,64))
            # Wall-facing fields remain Cup's own, even if Trucks is live.
            self.assertEqual(nc['field']['state'], states[0])

    def test_simultaneous_live_and_non_live_cycle(self):
        for states, expected in [(['post','in','in'], ['XFN','TRK']),
                                 (['post','pre','post'], ['CUP','XFN','TRK'])]:
            nc=self.build(states)
            for i, series in enumerate(expected):
                with patch.object(board, '_race_at', [i]):
                    self.assertEqual(board.race_order(nc)[1]['series'], series)

    def test_missing_cup_and_small_field(self):
        nc=nascar.build({'nascar-truck': self.doc('in', 2)})
        self.assertIsNone(nc['podium'])
        self.assertEqual(board.race_order(nc)[1]['series'], 'TRK')
        self.assertEqual(len(board.race_order(nc)[0]), 2)

    def test_results_survive_restart_per_series(self):
        with tempfile.TemporaryDirectory() as path, patch.object(board, 'DATA_DIR', path):
            board.keep_podium(self.build(['post','post','post']))
            restored=board.keep_podium({'race_fields': []})
            self.assertEqual([f['series'] for f in restored['race_fields']], ['CUP','XFN','TRK'])
            live=board.keep_podium(self.build(['pre','in','pre']))
            self.assertEqual(board.race_order(live)[1]['series'], 'XFN')
            with open(os.path.join(path, board.RACE_FIELDS_FILE)) as f:
                saved=json.load(f)
            self.assertTrue(all(field['state']=='post' for field in saved.values()))
            self.assertEqual(board.race_order(live)[1]['state'], 'in')

    def test_pinned_live_order_refreshes(self):
        class Clock:
            now = 0.0
            def time(self): return self.now
            def monotonic(self): return self.now
            def sleep(self, seconds): self.now += seconds
        class Device:
            def set_brightness(self, value): pass
            def push_image(self, image): pass
        clock = Clock()
        nc = self.build(['post', 'in', 'post'])
        calls = []
        def fetch():
            calls.append(clock.now)
            if len(calls) >= 3:
                raise StopIteration('enough polls')
            return {}, {}, {'nascar': nc}, {}
        cfg = {'screens': None, 'pin': 'podium', 'dwell': {'podium': 4},
               'brightness': 'auto', 'command_id': 0, 'command': None,
               'race_scroll_speed': 2}
        with patch.object(sys, 'argv', ['board.py', '--loop']), \
             patch.object(board, 'time', clock), \
             patch.object(board, 'fetch', side_effect=fetch), \
             patch.object(board, 'settings', return_value=cfg), \
             patch.object(board, 'fetch_jellyfin', return_value={}), \
             patch.object(board, 'connect', return_value=Device()), \
             patch.object(board, 'mirror'), \
             patch.object(board, '_race_at', [0]):
            with self.assertRaises(StopIteration):
                board.main()
        self.assertGreaterEqual(calls[2], 60)
        self.assertLess(calls[2], 61)

if __name__ == '__main__': unittest.main()
