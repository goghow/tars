# TARS プロトタイプ(Mac版)

スペースキーを押している間だけ録音 → 音声認識 → Claude → 1文ずつ読み上げ。
画面は TARS-AI V3 の5インチ液晶(800×480)と同じサイズで作ってあるので、そのまま Raspberry Pi に持っていける。

## セットアップ

```bash
brew install python@3.12 sox        # sox は任意(ロボ声エフェクト用)
cd tars-proto
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env                # ANTHROPIC_API_KEY を書く
python -m tars
```

- 初回起動時に Whisper の small モデル(数百MB)を自動ダウンロードする。画面が BOOTING の間は待つ。
- 初回はターミナルにマイクの使用許可を求められるので許可する。
- 男性の声にしたい場合は「システム設定 → アクセシビリティ → 読み上げコンテンツ → システムの声」から Otoya を追加し、`.env` に `TARS_TTS_VOICE=Otoya` を書く。

## 操作

| キー | 動作 |
|---|---|
| 「TARS(ターズ)」と呼ぶ | 話しかける。「ターズ、明日の天気は？」のように続けて言ってもいい。返事のあと8秒間は呼ばずに続けて話せる |
| スペース(長押し) | 話す。TARS が喋っている途中に押すと割り込める |
| 話し中に「TARS」と呼ぶ | 読み上げを止める。止まったら、そのまま続けて話せる |
| M | マイクを切る/入れる。切っている間は「TARS」と呼んでも反応しない(スペースキーでは話せる) |
| F | 全画面の切り替え |
| ESC | 終了 |

「ユーモアを60に」「正直度80パーセント」のように話すと、API を呼ばずに設定値が変わる。

名前や好みなど覚えておく価値のあることは、TARS が自分で `memory.json` に保存して次回以降も覚えている。「〇〇は忘れて」と言えば消す。

## ファイル構成

| ファイル | 役割 |
|---|---|
| `tars/__main__.py` | 会話パイプラインとメインループ |
| `tars/brain.py` | Claude 連携、人格プロンプト、設定値 |
| `tars/stt.py` | 音声認識(local=faster-whisper / openai) |
| `tars/tts.py` | 音声合成(say / openai)とロボ声加工 |
| `tars/audio.py` | 押している間だけ録音 |
| `tars/console.py` | コンソール画面(左に顔、右に会話ログ) |
| `tars/fonts/` | 画面用フォント M PLUS 1 Code(SIL Open Font License、`OFL.txt`) |
| `tars/face.py` | 顔の表情(聞く・考える・話す、感情タグ) |
| `tars/wake.py` | ウェイクワード(常にマイクを聞き、話し声を切り出す) |
| `tars/memory.py` | 長期記憶(`memory.json` に保存。消すと記憶がリセットされる) |
| `tars/config.py` | 設定(`.env` で上書き) |

## うまくいかないとき

- 呼んでも反応しない/勝手に反応する:`TARS_VAD_SILENCE_SEC` で話し終わりの判定時間を調整する。常時聞くのをやめるなら `TARS_WAKE=0`。

- 反応が遅い:`TARS_WHISPER_MODEL=base` にする。Claude 側は Haiku が既定。
- 認識精度が低い:`TARS_WHISPER_MODEL=medium` にする(その分遅くなる)。
- 日本語が豆腐(□)になる:`TARS_FONT` に日本語フォントのパスを指定する。
