"""Offline regression checks: python -m unittest test_race_scroll."""
import unittest
from unittest.mock import patch
import tempfile
import board
import nascar

class RaceScrollTests(unittest.TestCase):
    def data(self, state, count=40):
        doc = {'events': [{'name': 'Race at IOWA', 'status': {'type': {'state': state}},
                'competitions': [{'competitors': [
                    {'order': i, 'athlete': {'shortName': f'D. DRIVER{i}'}}
                    for i in range(count, 0, -1)]}]}]}
        return {'nascar': {'field': nascar.field(doc), 'podium': nascar.podium(doc)}}

    def test_live_and_finished_full_field(self):
        for state in ('in', 'post', 'pre'):
            wx = self.data(state)
            rows, info = board.race_order(wx['nascar'])
            self.assertEqual(len(rows), 40)
            self.assertEqual([r[0] for r in rows], [str(i) for i in range(1, 41)])
            self.assertTrue(board.is_animated('podium', {}, {}, wx))
            first = board.screen_podium({}, {}, wx, {})
            later = board.screen_podium({}, {}, wx, {}, phase=54)
            self.assertEqual(first.size, (64, 64))
            self.assertEqual(first.crop((0, 0, 64, 20)).tobytes(), later.crop((0, 0, 64, 20)).tobytes())
            self.assertNotEqual(first.crop((0, 20, 64, 64)).tobytes(), later.crop((0, 20, 64, 64)).tobytes())
            end = board.order_span(rows) * board.ORDER_STEP * board.MARQUEE_PX
            self.assertEqual(first.tobytes(), board.screen_podium({}, {}, wx, {}, phase=end).tobytes())
            # Every rank reaches the first visible row during a full cycle.
            for i in range(40):
                phase = i * board.ORDER_ROW_H * board.ORDER_STEP * board.MARQUEE_PX
                frame = board.screen_podium({}, {}, wx, {}, phase=phase)
                expected = board.Image.new('RGB', (12, 5), board.LOAM)
                board.draw_text(board.ImageDraw.Draw(expected), str(i + 1), 1, 0, board.SODIUM, 1)
                self.assertEqual(frame.crop((0, 20, 12, 25)).tobytes(), expected.tobytes())

    def test_legacy_and_empty(self):
        wx = {'nascar': {'podium': {'top': [('1', 'BELL'), ('2', 'GIBBS'), ('3', 'BLANEY')]}}}
        self.assertFalse(board.is_animated('podium', {}, {}, wx))
        board.screen_podium({}, {}, wx, {})
        board.screen_podium({}, {}, {}, {})

    def test_race_night_includes_order(self):
        with patch.object(board, "is_race_night", return_value=True):
            self.assertIn("podium", dict(board.rotation()))

    def test_saved_full_results(self):
        nc = self.data('post')['nascar']
        with tempfile.TemporaryDirectory() as path, patch.object(board, 'DATA_DIR', path):
            board.keep_podium(nc)
            restored = board.keep_podium({})
            self.assertEqual(restored['field'], nc['field'])

if __name__ == '__main__':
    unittest.main()
