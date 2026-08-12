"""
pixoo_client.py — minimal Divoom Pixoo-64 client.

Replaces the `pixoo` package from PyPI, which needs Python 3.10+. The device
API is one HTTP POST, so there's no reason to carry the dependency. Works on
any Python 3.8+ with requests and Pillow.

Endpoints used:
  Draw/ResetHttpGifId    reset the frame counter
  Draw/SendHttpGif       push one 64x64 RGB frame
  Channel/SetBrightness  0-100
"""

import base64

import requests


class Pixoo:
    SIZE = 64

    def __init__(self, ip, timeout=8):
        self.url = f"http://{ip}/post"
        self.timeout = timeout
        self.pic_id = 1
        self.reset()

    # ------------------------------------------------------------ internals

    def _post(self, payload):
        r = requests.post(self.url, json=payload, timeout=self.timeout)
        r.raise_for_status()
        body = r.json()
        if body.get("error_code", 0) != 0:
            raise RuntimeError(f"Pixoo error {body['error_code']}: {payload['Command']}")
        return body

    # ------------------------------------------------------------ public

    def reset(self):
        """The device rejects a PicID it has already seen, and it remembers
        across our restarts. Reset on connect and count from 1."""
        try:
            self._post({"Command": "Draw/ResetHttpGifId"})
            self.pic_id = 1
        except Exception:
            # not fatal — worst case we bump past a stale id below
            pass

    def set_brightness(self, level):
        self._post({"Command": "Channel/SetBrightness",
                    "Brightness": max(0, min(100, int(level)))})

    def push_image(self, img):
        """img is a 64x64 PIL Image. Converted to raw RGB and base64'd."""
        if img.mode != "RGB":
            img = img.convert("RGB")
        if img.size != (self.SIZE, self.SIZE):
            img = img.resize((self.SIZE, self.SIZE))

        data = base64.b64encode(img.tobytes()).decode()

        self._post({
            "Command": "Draw/SendHttpGif",
            "PicNum": 1,
            "PicWidth": self.SIZE,
            "PicOffset": 0,
            "PicID": self.pic_id,
            "PicSpeed": 1000,
            "PicData": data,
        })
        self.pic_id += 1
        if self.pic_id > 100000:
            self.reset()
