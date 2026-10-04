"""ウェイクワード用の常時リスナー。

マイクを聞き続け、話し声のかたまり(発話)を VAD で切り出して渡す。
「TARS」と言ったかどうかの判定は、文字起こししたあとに split_wake で行う。
TARS が話している間は、スピーカーから流した音をエコーキャンセラーでマイクの音から差し引き、
残った発話を on_barge に渡す(話し中の割り込み用)。
"""
import collections
import re
import threading

import numpy as np
import sounddevice as sd
import webrtcvad

FRAME_MS = 30
PRE_ROLL_MS = 300     # 話し始めの取りこぼしを防ぐため、直前の音も含める
START_FRAMES = 6      # 直近10フレーム中これだけ声なら話し始め
MAX_SEC = 15
BARGE_SILENCE_SEC = 0.4  # 話し中は早く区切って早く止める
BARGE_MAX_SEC = 3.0  # 消し残しの声で区切れなくても、これで打ち切って判定する
REF_MAX_SEC = 5         # エコーキャンセラーの参照音(スピーカーに流した音)をためておく上限

# Whisper は TARS をいろいろな書き方にするので、まとめて拾う
WAKE_RE = re.compile(r"(tars|ターズ|タース|たーず|たーす|タアズ|ダーズ)", re.IGNORECASE)
_TRIM = "、。,.!?！？ 　ー〜"


class EchoCanceller:
    """WebRTC のエコーキャンセラー(livekit パッケージ)。使えなければ素通しにする。"""

    def __init__(self, sr: int):
        self.sr = sr
        self.n = sr // 100  # 10ms ずつ処理する
        self._ref: collections.deque = collections.deque()
        self._lock = threading.Lock()
        try:
            from livekit import rtc
            self.rtc = rtc
            self.apm = rtc.AudioProcessingModule(echo_cancellation=True, high_pass_filter=True)
        except Exception as e:
            print(f"[wake] エコーキャンセラーを使えません({e})。話し中の割り込みは精度が落ちます")
            self.apm = None

    def feed_reference(self, samples: np.ndarray, sr: int) -> None:
        """スピーカーに流した音を受け取る(TTS のスレッドから呼ばれる)。"""
        if self.apm is None:
            return
        mono = samples if samples.ndim == 1 else samples.mean(axis=1)
        if sr != self.sr:
            t = np.arange(0, len(mono), sr / self.sr)
            mono = np.interp(t, np.arange(len(mono)), mono)
        pcm = (np.clip(mono, -1, 1) * 32767).astype(np.int16)
        with self._lock:
            self._ref.extend(pcm.tolist())
            while len(self._ref) > REF_MAX_SEC * self.sr:
                self._ref.popleft()

    def _frame(self, data: np.ndarray):
        f = self.rtc.AudioFrame.create(self.sr, 1, self.n)
        np.frombuffer(f.data, dtype=np.int16)[:] = data
        return f

    def process(self, frame: np.ndarray) -> np.ndarray:
        if self.apm is None:
            return frame
        out = []
        for i in range(0, len(frame) - self.n + 1, self.n):
            with self._lock:
                k = min(self.n, len(self._ref))
                ref = np.array([self._ref.popleft() for _ in range(k)] + [0] * (self.n - k), dtype=np.int16)
            self.apm.process_reverse_stream(self._frame(ref))
            mic = self._frame(frame[i:i + self.n])
            self.apm.process_stream(mic)
            out.append(np.frombuffer(mic.data, dtype=np.int16).copy())
        return np.concatenate(out) if out else frame


def split_wake(text: str) -> tuple[bool, str]:
    """(ウェイクワードがあったか, それより後ろの言葉) を返す。"""
    m = WAKE_RE.search(text)
    if not m:
        return False, text
    return True, text[m.end():].strip(_TRIM)


class WakeListener:
    def __init__(self, sample_rate: int, silence_sec: float, can_listen, on_utterance, on_hearing,
                 can_barge=lambda: False, on_barge=lambda audio: None):
        self.sr = sample_rate
        self.frame = sample_rate * FRAME_MS // 1000
        self.end_frames = int(silence_sec * 1000 / FRAME_MS)
        self.can_listen = can_listen      # () -> bool: 今聞いてよいか
        self.on_utterance = on_utterance  # (np.ndarray float32) -> None
        self.on_hearing = on_hearing      # (bool) -> None: 話し声を検出中か(画面用)
        self.can_barge = can_barge        # () -> bool: TARS が話し中で、割り込みを聞くか
        self.on_barge = on_barge          # (np.ndarray float32) -> None: 話し中に切り出した発話
        self.echo = EchoCanceller(sample_rate)
        self.vad = webrtcvad.Vad(2)       # 0〜3。大きいほど雑音を声と見なしにくい
        self.level = 0.0
        self._q: collections.deque = collections.deque()
        self._event = threading.Event()
        self._stream: sd.InputStream | None = None

    def start(self) -> None:
        self.resume()
        threading.Thread(target=self._loop, daemon=True).start()

    @property
    def muted(self) -> bool:
        return self._stream is None

    def pause(self) -> None:
        """マイクを閉じる(macOS のマイク使用中ランプも消える)。"""
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None
            self.level = 0.0

    def resume(self) -> None:
        if self._stream is None:
            self._stream = sd.InputStream(
                samplerate=self.sr, channels=1, dtype="int16", blocksize=self.frame,
                callback=self._callback,
            )
            self._stream.start()

    def _callback(self, indata, frames, time_info, status):
        self._q.append(indata[:, 0].copy())
        self._event.set()

    def _frames(self):
        while True:
            self._event.wait()
            self._event.clear()
            while self._q:
                yield self._q.popleft()

    def _loop(self) -> None:
        pre = collections.deque(maxlen=PRE_ROLL_MS // FRAME_MS)
        recent = collections.deque(maxlen=10)
        voiced: list[np.ndarray] = []
        silent = 0
        barge = False  # いま切り出している発話が TARS の話し中に始まったか
        for frame in self._frames():
            # 話していないときも常に通す(エコーキャンセラーは流れ続けている方が早く慣れる)
            frame = self.echo.process(frame)
            normal, barging = self.can_listen(), self.can_barge()
            if not (normal or barging) or (voiced and barge != barging and not normal):
                if voiced and not barge:
                    self.on_hearing(False)
                voiced, silent = [], 0
                pre.clear()
                recent.clear()
                continue
            is_speech = len(frame) == self.frame and self.vad.is_speech(frame.tobytes(), self.sr)
            self.level = float(np.sqrt(np.mean((frame / 32768.0) ** 2)))
            if not voiced:
                pre.append(frame)
                recent.append(is_speech)
                if sum(recent) >= START_FRAMES:
                    voiced, silent, barge = list(pre), 0, barging
                    if not barge:
                        self.on_hearing(True)
                continue
            voiced.append(frame)
            silent = 0 if is_speech else silent + 1
            end_frames = int(BARGE_SILENCE_SEC * 1000 / FRAME_MS) if barge else self.end_frames
            max_ms = (BARGE_MAX_SEC if barge else MAX_SEC) * 1000
            if silent >= end_frames or len(voiced) * FRAME_MS > max_ms:
                audio = np.concatenate(voiced).astype(np.float32) / 32768.0
                voiced = []
                recent.clear()
                pre.clear()
                if barge:
                    self.on_barge(audio)
                else:
                    self.on_hearing(False)
                    self.on_utterance(audio)
