"""Offline control-to-display scroll speed checks."""
import io
import json
import os
import tempfile
import unittest
from unittest.mock import patch
import board
import control

class ScrollControlTests(unittest.TestCase):
    def test_save_and_display_read(self):
        with tempfile.TemporaryDirectory() as path, patch.object(control, 'DATA_DIR', path), patch.object(control, 'STATE', os.path.join(path, 'screens.json')), patch.object(board, 'DATA_DIR', path):
            handler = object.__new__(control.Handler)
            handler.path = '/'
            for value, expected in [('8', 8), ('99', 12), ('0', 2), ('bad', 2)]:
                body = ('podium=on&brightness=auto&race_scroll_speed=' + value).encode()
                handler.headers = {'Content-Length': str(len(body))}
                handler.rfile = io.BytesIO(body)
                with patch.object(handler, '_page'):
                    handler.do_POST()
                self.assertEqual(control.load()['race_scroll_speed'], expected)
                self.assertEqual(board.settings()['race_scroll_speed'], expected)

    def test_existing_settings_and_page(self):
        with tempfile.TemporaryDirectory() as path, patch.object(control, 'STATE', os.path.join(path, 'screens.json')), patch.object(board, 'DATA_DIR', path):
            with open(control.STATE, 'w') as f:
                json.dump({'pin': 'podium', 'dwell': {'podium': 10}}, f)
            self.assertEqual(board.settings()['race_scroll_speed'], 5)
            handler = object.__new__(control.Handler)
            with patch.object(handler, '_send') as send, patch.object(control, 'health_rows', return_value=''), patch.object(control, 'gallery', return_value=''):
                handler._page('Saved')
                page = send.call_args[0][0].decode()
            self.assertEqual(page.count('name="race_scroll_speed"'), 4)
            self.assertIn('Fastest', page)
            self.assertNotIn('{{SPEED}}', page)

if __name__ == '__main__': unittest.main()
