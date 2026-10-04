"""TARS の設定。値はすべて .env か環境変数で上書きできる。"""
import os
import platform
import sys
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


# Apple Silicon の Mac なら GPU で動く mlx を既定にする(Raspberry Pi では local)
_APPLE_SILICON = sys.platform == "darwin" and platform.machine() == "arm64"


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default)


@dataclass
class Config:
    # --- Claude ---
    model: str = _env("TARS_MODEL", "claude-haiku-4-5-20251001")
    max_tokens: int = int(_env("TARS_MAX_TOKENS", "400"))
    history_turns: int = int(_env("TARS_HISTORY_TURNS", "10"))
    user_name: str = _env("TARS_USER_NAME", "")
    web_search: bool = _env("TARS_WEB_SEARCH", "1") == "1"  # ネット検索(天気・ニュースなど)
    location: str = _env("TARS_LOCATION", "東京")
    memory_file: str = _env("TARS_MEMORY_FILE", "memory.json")  # 長期記憶の保存先  # 天気などで場所を言わなかったときの既定

    # --- 性格パラメータ(初期値) ---
    humor: int = int(_env("TARS_HUMOR", "75"))
    honesty: int = int(_env("TARS_HONESTY", "90"))

    # --- 音声認識 ---
    stt_backend: str = _env("TARS_STT", "mlx" if _APPLE_SILICON else "local")  # mlx | local | openai
    mlx_whisper_model: str = _env("TARS_MLX_WHISPER_MODEL", "mlx-community/whisper-large-v3-turbo")
    whisper_model: str = _env("TARS_WHISPER_MODEL", "small")  # tiny/base/small/medium
    openai_stt_model: str = _env("TARS_OPENAI_STT_MODEL", "gpt-4o-mini-transcribe")
    sample_rate: int = 16000
    wake_word: bool = _env("TARS_WAKE", "1") == "1"  # 「TARS」と呼んで話しかける
    follow_up_sec: float = float(_env("TARS_FOLLOW_UP_SEC", "8"))  # 返事のあと呼ばずに続けて話せる秒数
    vad_silence_sec: float = float(_env("TARS_VAD_SILENCE_SEC", "0.8"))  # これだけ黙ったら話し終わり

    # --- 音声合成 ---
    tts_backend: str = _env("TARS_TTS", "say")  # say | openai
    tts_voice: str = _env("TARS_TTS_VOICE", "Otoya")  # say: Kyoko/Otoya, openai: onyx など
    tts_rate: int = int(_env("TARS_TTS_RATE", "280"))
    openai_tts_model: str = _env("TARS_OPENAI_TTS_MODEL", "gpt-4o-mini-tts")
    robot_fx: bool = _env("TARS_ROBOT_FX", "1") == "1"  # sox があればロボ声加工

    # --- 画面(V3の5インチ液晶に合わせて 800x480 / 1024x600) ---
    screen_w: int = int(_env("TARS_SCREEN_W", "800"))
    screen_h: int = int(_env("TARS_SCREEN_H", "480"))
    fullscreen: bool = _env("TARS_FULLSCREEN", "0") == "1"
    font_path: str = _env("TARS_FONT", "")
