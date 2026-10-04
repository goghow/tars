"""押している間だけ録音するレコーダー。"""
import threading

import numpy as np
import sounddevice as sd


class Recorder:
    def __init__(self, sample_rate: int = 16000):
        self.sr = sample_rate
        self._chunks: list[np.ndarray] = []
        self._stream: sd.InputStream | None = None
        self._lock = threading.Lock()
        self.level = 0.0  # 画面のレベルメーター用(0〜1くらい)

    def _callback(self, indata, frames, time_info, status):
        mono = indata[:, 0].copy()
        with self._lock:
            self._chunks.append(mono)
        self.level = float(np.sqrt(np.mean(mono ** 2)))

    def start(self) -> None:
        self.stop()  # 前の録音の余韻(語尾の録り足し)中に押されたら、それを捨てて録り直す
        with self._lock:
            self._chunks = []
        self._stream = sd.InputStream(
            samplerate=self.sr, channels=1, dtype="float32", callback=self._callback
        )
        self._stream.start()

    def stop(self) -> np.ndarray:
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None
        self.level = 0.0
        with self._lock:
            if not self._chunks:
                return np.zeros(0, dtype=np.float32)
            return np.concatenate(self._chunks)
