"""Frame-ordinal video access, independent of timestamp-based backend seeking.

OpenCV can acknowledge a frame seek while returning a different image on VFR
inputs. Only count frames decoded from a freshly opened stream; never seek the
backend. A bounded image cache makes nearby backward/forward stepping cheap.
"""
from collections import OrderedDict
from functools import lru_cache
from pathlib import Path

import cv2


@lru_cache(maxsize=32)
def _count_frames(path, size, mtime_ns):
    cap = cv2.VideoCapture(path)
    try:
        if not cap.isOpened():
            raise ValueError("無法開啟影片或不支援的編碼")
        count = 0
        while cap.grab():
            count += 1
        return count
    finally:
        cap.release()


class ExactVideoCapture:
    """Small VideoCapture-compatible interface using zero-based frame ordinals.

    Cache at most 64 MiB (and at most 16 frames). A backward cache miss reopens
    the file and decodes from the beginning, trading seek speed for correctness.
    CAP_PROP_FRAME_COUNT is a cached count of decodable frames, not an estimate.
    """

    def __init__(self, path):
        self.path = str(Path(path).resolve())
        self._cap = cv2.VideoCapture(self.path)
        self._decoded = 0
        self._position = 0
        self._cache = OrderedDict()
        self._cache_bytes = 0

    def isOpened(self):
        return self._cap.isOpened()

    def get(self, prop):
        if prop == cv2.CAP_PROP_POS_FRAMES:
            return float(self._position)
        if prop == cv2.CAP_PROP_FRAME_COUNT:
            stat = Path(self.path).stat()
            return float(_count_frames(self.path, stat.st_size, stat.st_mtime_ns))
        return self._cap.get(prop)

    def set(self, prop, value):
        if prop != cv2.CAP_PROP_POS_FRAMES or not self.isOpened():
            return False
        if value < 0 or not float(value).is_integer():
            return False
        self._position = int(value)
        return True

    def read(self):
        if not self.isOpened():
            return False, None
        index = self._position
        if index in self._cache:
            self._cache.move_to_end(index)
            self._position += 1
            return True, self._cache[index].copy()
        if index < self._decoded:
            self._cap.release()
            self._cap = cv2.VideoCapture(self.path)
            self._decoded = 0
        while self._decoded < index:
            if not self._cap.grab():
                return False, None
            self._decoded += 1
        ok, frame = self._cap.read()
        if not ok:
            return False, None
        self._decoded += 1
        self._position = index + 1
        self._cache[index] = frame.copy()
        self._cache_bytes += frame.nbytes
        while self._cache and (len(self._cache) > 16 or self._cache_bytes > 64 * 1024 * 1024):
            _, old = self._cache.popitem(last=False)
            self._cache_bytes -= old.nbytes
        return True, frame

    def release(self):
        self._cap.release()
        self._cache.clear()
        self._cache_bytes = 0
