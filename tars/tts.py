"""音声合成と再生。say(macOS標準・キー不要)か openai を選べる。

sox が入っていればピッチを下げて金属っぽい響きを足す(TARS_ROBOT_FX=1)。
"""
import io
import os
import shutil
import subprocess
import sys
import tempfile
import threading

import sounddevice as sd
import soundfile as sf

# 小さいスピーカーで音割れしないよう、鳴らせない低音を削り、ピークを -6dB に抑える。
# 再生側でのリサンプリングによるノイズを避けるため 48kHz に変換し、両端をフェードしてクリックを防ぐ。
# エコーは声がこもって汚くなるので使わない。
SOX_FX = ["highpass", "150", "lowpass", "8000", "gain", "-n", "-6",
          "rate", "-v", "48000", "fade", "0.01", "-0", "0.02"]

# 指定した声が未インストールだと say は黙って既定の声(女性)に落ちるので、
# 男性の声を順に探して使う。Otoya(要ダウンロード)が一番自然。
MALE_VOICES = ["Otoya", "Reed", "Rocko", "Eddy"]


def _installed_voices() -> set[str]:
    out = subprocess.run(["say", "-v", "?"], capture_output=True, text=True).stdout
    # 例: "Reed (日本語（日本）)      ja_JP    # ..." → "Reed"
    return {line.split("  ")[0].split(" (")[0].strip() for line in out.splitlines() if line}


class BaseTTS:
    def __init__(self, robot_fx: bool):
        self.sox = shutil.which("sox") if robot_fx else None
        self.tmp = tempfile.mkdtemp(prefix="tars_")
        self._stop = threading.Event()
        self.on_play = lambda samples, sr: None  # 流した音をエコーキャンセラーに渡す

    def synth(self, text: str) -> str:
        """テキストを音声ファイルにしてパスを返す。"""
        raise NotImplementedError

    def _apply_fx(self, path: str) -> str:
        if not self.sox:
            return path
        out = os.path.join(self.tmp, "fx.wav")
        subprocess.run([self.sox, path, out, *SOX_FX], check=True, capture_output=True)
        return out

    def speak(self, text: str) -> None:
        path = self._apply_fx(self.synth(text))
        data, sr = sf.read(path, dtype="float32")
        self._stop.clear()
        # 別スレッドから sd.stop() で止めると PortAudio がエラー(-50)を出すので、
        # 再生は自分のストリームに少しずつ書き込み、止めるときはフラグで抜ける。
        # latency="high" はバッファを大きくしてプツプツ音を防ぐため
        block = sr // 20
        with sd.OutputStream(samplerate=sr, channels=1 if data.ndim == 1 else data.shape[1],
                             dtype="float32", latency="high") as stream:
            for i in range(0, len(data), block):
                if self._stop.is_set():
                    stream.abort()
                    return
                stream.write(data[i:i + block])
                self.on_play(data[i:i + block], sr)

    def stop(self) -> None:
        self._stop.set()


class SayTTS(BaseTTS):
    def __init__(self, voice: str, rate: int, robot_fx: bool):
        if sys.platform != "darwin":
            raise RuntimeError("say は macOS 専用です。TARS_TTS=openai を使ってください。")
        super().__init__(robot_fx)
        installed = _installed_voices()
        if voice not in installed:
            fallback = next((v for v in MALE_VOICES if v in installed), None)
            print(f"[tts] 声 {voice!r} が見つからないので {fallback!r} を使います", file=sys.stderr)
            voice = fallback or voice
        self.voice, self.rate = voice, rate

    def synth(self, text: str) -> str:
        out = os.path.join(self.tmp, "say.aiff")
        subprocess.run(
            ["say", "-v", self.voice, "-r", str(self.rate), "-o", out, text], check=True
        )
        return out


class OpenAITTS(BaseTTS):
    def __init__(self, model: str, voice: str, robot_fx: bool):
        from openai import OpenAI

        super().__init__(robot_fx)
        self.client = OpenAI()
        self.model, self.voice = model, voice

    def synth(self, text: str) -> str:
        resp = self.client.audio.speech.create(
            model=self.model, voice=self.voice, input=text, response_format="wav"
        )
        out = os.path.join(self.tmp, "openai.wav")
        data, sr = sf.read(io.BytesIO(resp.content), dtype="float32")
        sf.write(out, data, sr)
        return out


def make_tts(cfg) -> BaseTTS:
    if cfg.tts_backend == "openai":
        voice = cfg.tts_voice if cfg.tts_voice not in ("Kyoko", "Otoya") else "onyx"
        return OpenAITTS(cfg.openai_tts_model, voice, cfg.robot_fx)
    return SayTTS(cfg.tts_voice, cfg.tts_rate, cfg.robot_fx)
