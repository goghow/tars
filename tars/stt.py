"""音声認識。mlx(Apple Silicon の GPU) / local(faster-whisper, CPU) / openai を選べる。"""
import io

import numpy as np
import soundfile as sf

# 固有名詞や言い回しを認識しやすくするためのヒント
PROMPT = "TARS、ユーモア設定、正直度設定。"
# 短い発話だと Whisper がヒントの文をそのまま書き足すことがあるので、その部分は消す
_ECHO = "ユーモア設定、正直度設定"


def clean(text: str) -> str:
    return text.replace(_ECHO, "").replace("、。", "。").strip().rstrip("、,").strip()


class MLXWhisperSTT:
    """Apple Silicon の GPU で動く Whisper。large-v3-turbo でも1秒前後で終わる。"""

    def __init__(self, repo: str):
        import mlx_whisper

        self.mlx_whisper = mlx_whisper
        self.repo = repo
        # 初回はモデルを自動ダウンロードする(約1.6GB)。ついでに読み込みも済ませておく
        mlx_whisper.transcribe(np.zeros(16000, dtype=np.float32), path_or_hf_repo=repo, language="ja")

    def transcribe(self, audio: np.ndarray, sr: int) -> str:
        result = self.mlx_whisper.transcribe(
            audio, path_or_hf_repo=self.repo, language="ja",
            initial_prompt=PROMPT, condition_on_previous_text=False,
        )
        return clean(result["text"])


class LocalWhisperSTT:
    def __init__(self, model_size: str):
        from faster_whisper import WhisperModel

        # 初回はモデルを自動ダウンロードする(small で数百MB)
        self.model = WhisperModel(model_size, device="cpu", compute_type="int8")

    def transcribe(self, audio: np.ndarray, sr: int) -> str:
        segments, _ = self.model.transcribe(
            audio, language="ja", beam_size=5, vad_filter=True, initial_prompt=PROMPT
        )
        return clean("".join(s.text for s in segments))


class OpenAISTT:
    def __init__(self, model: str):
        from openai import OpenAI

        self.client = OpenAI()
        self.model = model

    def transcribe(self, audio: np.ndarray, sr: int) -> str:
        buf = io.BytesIO()
        sf.write(buf, audio, sr, format="WAV")
        result = self.client.audio.transcriptions.create(
            model=self.model,
            file=("speech.wav", buf.getvalue(), "audio/wav"),
            language="ja",
            prompt=PROMPT,
        )
        return clean(result.text)


def make_stt(cfg):
    if cfg.stt_backend == "openai":
        return OpenAISTT(cfg.openai_stt_model)
    if cfg.stt_backend == "mlx":
        return MLXWhisperSTT(cfg.mlx_whisper_model)
    return LocalWhisperSTT(cfg.whisper_model)
