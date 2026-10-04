"""python -m tars で起動。「TARS」と呼ぶかスペース長押しで話す / M でマイクを切る / F で全画面 / ESC で終了。"""
import queue
import random
import re
import threading
import time
import traceback

import pygame

from .audio import Recorder
from .brain import Brain
from .config import Config
from .console import Console, UIState
from .stt import make_stt
from .tts import make_tts
from .wake import WakeListener, split_wake

SENTENCE_END = re.compile(r"[。！？!?\n]")
MIN_AUDIO_SEC = 0.3
TAIL_SEC = 0.3  # キーを離してから少しだけ録り続け、語尾の切れを防ぐ
ECHO_SEC = 0.5  # 話し終えた直後は自分の声の残響を拾わないよう聞かない
ACKS = ["なんだ？", "聞いてる。", "どうした？"]
# 雑音を文字起こしすると Whisper が出しがちな文。ウェイクワードなしの続きの会話では無視する
HALLUCINATION = re.compile(r"ご視聴|字幕|チャンネル登録|ユーモア設定、正直度設定")


class Tars:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.state = UIState()
        self.recorder = Recorder(cfg.sample_rate)
        self.brain = Brain(cfg)
        self.brain.on_face = self.state.set_face
        self.brain.on_settings = self._sync_params
        self.tts = make_tts(cfg)
        self.stt = None  # Whisper の読み込みは重いので裏で行う
        self.jobs: queue.Queue = queue.Queue()
        self.speech: queue.Queue = queue.Queue()
        self.gen = 0  # 割り込み(話している途中にスペース)で増える世代番号
        self.quiet_until = 0.0   # この時刻までは常時リスナーが聞かない
        self.follow_until = 0.0  # この時刻までは「TARS」と呼ばなくても話しかけられる
        self.wake = None
        if cfg.wake_word:
            self.wake = WakeListener(
                cfg.sample_rate, cfg.vad_silence_sec,
                can_listen=self._can_hear, on_utterance=self._on_utterance, on_hearing=self._on_hearing,
                can_barge=self._can_barge, on_barge=self._on_barge,
            )
            self.tts.on_play = self.wake.echo.feed_reference
        self.barge: queue.Queue = queue.Queue(maxsize=2)  # 話し中に切り出した発話
        self.stt_lock = threading.Lock()  # 文字起こしは同時に1つだけ
        self.speaking_text = ""  # いま読み上げている文(自分の声に反応しないため)
        self._sync_params()

    # ---------- 共通 ----------
    def _sync_params(self):
        with self.state.lock:
            self.state.humor, self.state.honesty = self.brain.humor, self.brain.honesty

    def _set_stage(self, stage):
        with self.state.lock:
            self.state.stage = stage

    def _say(self, text: str, gen: int):
        self.speech.put((gen, text))

    # ---------- 入力(メインスレッドから呼ばれる) ----------
    def start_listening(self):
        if self.stt is None:
            return
        self.interrupt()  # 喋っている途中なら黙らせる
        self.recorder.start()
        with self.state.lock:
            self.state.listening = True

    def stop_listening(self):
        with self.state.lock:
            self.state.listening = False
        gen = self.gen

        def finish():
            time.sleep(TAIL_SEC)
            if gen != self.gen:  # その間に次の録音が始まっていたら何もしない
                return
            self.jobs.put((gen, self.recorder.stop(), "key"))

        threading.Thread(target=finish, daemon=True).start()

    def interrupt(self):
        """読み上げを止め、これから読む予定だった文も捨てる。"""
        self.gen += 1
        self._drain(self.speech)
        self.tts.stop()

    # ---------- 常時リスナー(ウェイクワード) ----------
    def _can_hear(self) -> bool:
        with self.state.lock:
            busy = self.state.listening or self.state.speaking or self.state.stage is not None
        return self.stt is not None and not busy and time.time() > self.quiet_until

    def _can_barge(self) -> bool:
        with self.state.lock:
            return self.state.speaking and not self.state.listening

    def _on_barge(self, audio):
        try:
            self.barge.put_nowait(audio)
        except queue.Full:
            pass

    def barge_worker(self):
        """TARS の話し中に「TARS」と呼ばれたら、すぐ読み上げを止める(返事の生成中でも待たない)。"""
        while True:
            audio = self.barge.get()
            if self.stt is None or len(audio) < self.cfg.sample_rate * MIN_AUDIO_SEC:
                continue
            with self.stt_lock:
                text = self.stt.transcribe(audio, self.cfg.sample_rate)
            # 自分で「TARS」と言っている文の最中なら、自分の声を拾っただけ
            if not split_wake(text)[0] or split_wake(self.speaking_text)[0]:
                continue
            # 止めるだけにする。話し中の録音には消し残した TARS の声が混ざり、続きの言葉は信用できない。
            # 読み上げが止まると speech_worker が「呼ばずに続けて話せる」状態にするので、続きはそこで聞く
            self.interrupt()

    def _on_hearing(self, hearing: bool):
        # 呼びかけ待ちの間は、TARS 宛てかどうか分からないので画面に出さない
        if hearing and time.time() >= self.follow_until:
            return
        with self.state.lock:
            self.state.hearing = hearing

    def _on_utterance(self, audio):
        # 発話が終わった時点で続きの会話の時間内だったかを記録しておく
        self.jobs.put((self.gen, audio, "follow" if time.time() < self.follow_until else "wake"))

    def toggle_mic(self):
        if self.wake.muted:
            self.wake.resume()
        else:
            self.wake.pause()
        with self.state.lock:
            self.state.mic_off = self.wake.muted
            self.state.hearing = False

    @staticmethod
    def _drain(q: queue.Queue):
        try:
            while True:
                q.get_nowait()
        except queue.Empty:
            pass

    # ---------- ワーカー ----------
    def load_worker(self):
        try:
            self.stt = make_stt(self.cfg)
            self._set_stage(None)
        except Exception as e:
            self._set_stage("ERROR")
            self.state.add("SYS", f"音声認識の初期化に失敗: {e}")

    def conversation_worker(self):
        while True:
            gen, audio, source = self.jobs.get()
            try:
                self._handle(gen, audio, source)
            except Exception as e:
                traceback.print_exc()
                self.state.add("SYS", f"エラー: {e}")
            finally:
                self._set_stage(None)

    def _handle(self, gen: int, audio, source: str):
        if len(audio) < self.cfg.sample_rate * MIN_AUDIO_SEC:
            return
        if source != "wake":  # 呼びかけ待ちの文字起こしは裏で黙って行う
            self._set_stage("TRANSCRIBING")
        with self.stt_lock:
            text = self.stt.transcribe(audio, self.cfg.sample_rate)
        if source != "key":
            # 常時リスナーから: 「TARS」と呼ばれたか、続きの会話のときだけ反応する
            called, rest = split_wake(text)
            if not called and (source != "follow" or len(text) < 2 or HALLUCINATION.search(text)):
                return
            self.state.add("YOU", text)
            if called and not rest:
                ack = random.choice(ACKS)
                self.state.add("TARS", ack)
                self._say(ack, gen)
                return
            text = rest if called else text
        elif not text:
            self.state.add("SYS", "音声を認識できませんでした")
            return
        else:
            self.state.add("YOU", text)

        reply = self.brain.try_setting_command(text)
        if reply:
            self._sync_params()
            self.state.add("TARS", reply)
            self._say(reply, gen)
            return

        self._set_stage("THINKING")
        self.state.add("TARS", "")
        buf = ""
        for chunk in self.brain.stream_reply(text):
            if gen != self.gen:  # 割り込まれた
                break
            self.state.append_last(chunk)
            buf += chunk
            # 1文できるごとに読み上げキューへ(全文を待たない)
            while (m := SENTENCE_END.search(buf)):
                sentence, buf = buf[: m.end()].strip(), buf[m.end():]
                if sentence:
                    self._say(sentence, gen)
        if buf.strip() and gen == self.gen:
            self._say(buf.strip(), gen)

    def speech_worker(self):
        while True:
            gen, text = self.speech.get()
            if gen != self.gen:
                continue
            with self.state.lock:
                self.state.speaking = True
            self.speaking_text = text
            try:
                self.tts.speak(text)
            except Exception as e:
                self.state.add("SYS", f"音声合成エラー: {e}")
            finally:
                if self.speech.empty():
                    with self.state.lock:
                        self.state.speaking = False
                    self.quiet_until = time.time() + ECHO_SEC
                    self.follow_until = time.time() + self.cfg.follow_up_sec

    # ---------- メインループ ----------
    def run(self):
        console = Console(self.cfg, self.state,
                          lambda: max(self.recorder.level, self.wake.level if self.wake else 0.0))
        for target in (self.load_worker, self.conversation_worker, self.speech_worker, self.barge_worker):
            threading.Thread(target=target, daemon=True).start()
        if self.wake:
            self.wake.start()

        clock = pygame.time.Clock()
        running = True
        while running:
            for ev in pygame.event.get():
                if ev.type == pygame.QUIT:
                    running = False
                elif ev.type == pygame.KEYDOWN:
                    if ev.key == pygame.K_ESCAPE:
                        running = False
                    elif ev.key == pygame.K_f:
                        console.toggle_fullscreen()
                    elif ev.key == pygame.K_m and self.wake:
                        self.toggle_mic()
                    elif ev.key == pygame.K_SPACE and not self.state.listening:
                        self.start_listening()
                elif ev.type == pygame.KEYUP and ev.key == pygame.K_SPACE:
                    if self.state.listening:
                        self.stop_listening()
            console.draw()
            clock.tick(30)
        self.tts.stop()
        pygame.quit()


def main():
    Tars(Config()).run()


if __name__ == "__main__":
    main()
