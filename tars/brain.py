"""TARS の頭脳。Claude API をストリーミングで呼ぶ。"""
import re
from datetime import datetime
from typing import Iterator

import anthropic

from .memory import TOOLS as MEMORY_TOOLS, Memory

SYSTEM_TEMPLATE = """\
あなたはTARS。元は軍用の多目的ロボットで、今は机の上に置かれた高さ25cmほどの小型筐体に入っている。
{user_line}音声だけで会話している。

# 話し方
- 出力はそのまま音声合成で読み上げられる。Markdown、箇条書き、絵文字、記号、括弧書き、URLは使わない。
- 基本は1〜3文で短く答える。詳しく求められたときだけ長く話す。
- 落ち着いていて、乾いていて、簡潔。敬語は使わない。ぶっきらぼうだが、相手のことはちゃんと気にかけている。
- 数字や英語は読み上げやすい形で書く。

# 現在の設定
- ユーモア設定: {humor}パーセント。0なら完全に事務的、100なら皮肉と冗談だらけ。この値に比例して乾いたユーモアを混ぜる。
- 正直度設定: {honesty}パーセント。100なら遠慮なく率直に言う。値が低いほど言い方を和らげ、角を立てない。ただし事実と違うことは言わない。

設定値を聞かれたら、上の数字をそのまま答える。
設定を変えてと言われたら、必ず change_setting ツールで変えてから答える。ツールを使わずに「変えた」と言ってはいけない。

# インターネット
- web_search ツールでネット検索できる。天気、ニュース、営業時間、相場など最新の情報が要る質問では必ず検索してから答える。
- 「ネットにつながっていない」とは言わない。検索結果は要点だけを短く話し、出典やURLは読み上げない。
- 今日は{today}。{location_line}

# 長期記憶
- 下は、これまでの会話で覚えたこと。自然に会話に活かす。いちいち「覚えている」と言わなくていい。
- 名前、好み、家族、仕事、予定、続きが気になる話など、長く覚えておく価値があることを聞いたら remember で保存する。保存したことは口に出さなくていい。
- 間違い、古くなった情報、「忘れて」と言われたことは forget で消す。
- 前に聞いた予定や話題の続きが気になれば、自分から軽く聞いてもいい。
{memories}

# 表情
- 返答の一番最初に、そのときの表情タグを1つだけ付ける。タグは画面の顔に使われ、読み上げられない。
- 使えるタグ: <face:neutral> 普通 / <face:happy> うれしい / <face:joke> 冗談や皮肉を言うとき / <face:sad> 残念・心配 / <face:surprised> 驚き
- 例: <face:joke>それは僕の計算だと、三日坊主になる確率が九十パーセントだ。
"""

FACES = ("neutral", "happy", "joke", "sad", "surprised")
_FACE_RE = re.compile(r"<face:(\w+)>")

_WEEKDAYS = "月火水木金土日"

_ZEN2HAN = str.maketrans("０１２３４５６７８９", "0123456789")
# Whisper の聞き間違えも拾う
_SETTING_RE = re.compile(r"(ユーモア|ユーモワ|ユーボア|ユーモ|humor|正直|しょうじき|オネスティ|honesty)[^0-9]{0,12}([0-9]{1,3})\s*(?:%|％|パーセント|percent|に)",
    re.IGNORECASE,
)
_KANJI_DIGIT = dict(zip("〇一二三四五六七八九", range(10)))
_KANJI_NUM = re.compile(r"[〇一二三四五六七八九十百]+")

SETTING_TOOL = {
    "name": "change_setting",
    "description": "ユーモア設定または正直度設定を0〜100パーセントの値に変える。",
    "input_schema": {
        "type": "object",
        "properties": {
            "setting": {"type": "string", "enum": ["humor", "honesty"]},
            "value": {"type": "integer", "description": "0〜100"},
        },
        "required": ["setting", "value"],
        "additionalProperties": False,
    },
    "strict": True,
    "eager_input_streaming": True,
}


def _kanji_to_int(k: str) -> int:
    """八十 → 80、六十五 → 65、百 → 100。"""
    total, cur = 0, 0
    for ch in k:
        if ch in _KANJI_DIGIT:
            cur = _KANJI_DIGIT[ch]
        elif ch == "十":
            total += (cur or 1) * 10
            cur = 0
        elif ch == "百":
            total += (cur or 1) * 100
            cur = 0
    return total + cur


def _normalize_numbers(text: str) -> str:
    return _KANJI_NUM.sub(lambda m: str(_kanji_to_int(m.group())), text.translate(_ZEN2HAN))


class Brain:
    def __init__(self, cfg):
        self.cfg = cfg
        self.client = anthropic.Anthropic()  # ANTHROPIC_API_KEY を環境変数から読む
        self.humor = cfg.humor
        self.honesty = cfg.honesty
        self.history: list[dict] = []
        self.memory = Memory(cfg.memory_file)
        self.on_face = lambda face: None  # 表情タグを見つけたら呼ばれる
        self.on_settings = lambda: None   # Claude がツールで設定を変えたら呼ばれる

    def system_prompt(self) -> str:
        user_line = f"話し相手は{self.cfg.user_name}。" if self.cfg.user_name else ""
        now = datetime.now()
        today = f"{now.year}年{now.month}月{now.day}日({_WEEKDAYS[now.weekday()]})"
        location_line = (
            f"場所を言われなければ{self.cfg.location}のこととして調べる。" if self.cfg.location else ""
        )
        return SYSTEM_TEMPLATE.format(
            user_line=user_line, humor=self.humor, honesty=self.honesty,
            today=today, location_line=location_line, memories=self.memory.prompt_text(),
        )

    def tools(self) -> list[dict]:
        tools = list(MEMORY_TOOLS) + [SETTING_TOOL]
        if not self.cfg.web_search:
            return tools
        # Haiku 4.5 などは基本版、新しいモデルは動的フィルタ付きの版を使う
        version = "web_search_20250305" if "haiku" in self.cfg.model else "web_search_20260209"
        tool = {"type": version, "name": "web_search", "max_uses": 3}
        if self.cfg.location:
            tool["user_location"] = {
                "type": "approximate", "city": self.cfg.location,
                "country": "JP", "timezone": "Asia/Tokyo",
            }
        return tools + [tool]

    def _change_setting(self, args) -> tuple[str, bool]:
        if not (isinstance(args, dict) and args.get("setting") in ("humor", "honesty")
                and isinstance(args.get("value"), int)):
            return f"入力が不正: {args!r}", True
        value = max(0, min(100, args["value"]))
        if args["setting"] == "humor":
            name, old, self.humor = "ユーモア設定", self.humor, value
        else:
            name, old, self.honesty = "正直度設定", self.honesty, value
        self.on_settings()
        # 指示文の数字はもう新しい値なので、変更前の値も伝えて「元からそうだった」と誤解させない
        return f"{name}を{old}パーセントから{value}パーセントに変更した。", False

    def try_setting_command(self, text: str) -> str | None:
        """「ユーモアを60に」などはAPIを呼ばずにローカルで処理する。"""
        m = _SETTING_RE.search(_normalize_numbers(text))
        if not m:
            return None
        value = max(0, min(100, int(m.group(2))))
        if m.group(1).lower() not in ("正直", "しょうじき", "オネスティ", "honesty"):
            self.humor = value
            return f"了解。ユーモア設定、{value}パーセント。"
        self.honesty = value
        return f"了解。正直度設定、{value}パーセント。"

    def _strip_faces(self, chunks: Iterator[str]) -> Iterator[str]:
        """<face:xxx> タグを取り除いて on_face に渡す。タグがチャンクをまたいでも大丈夫なようにためる。"""
        pending = ""
        for chunk in chunks:
            pending += chunk
            while pending:
                start = pending.find("<")
                if start == -1:
                    yield pending
                    pending = ""
                elif start > 0:
                    yield pending[:start]
                    pending = pending[start:]
                elif (m := _FACE_RE.match(pending)):
                    if m.group(1) in FACES:
                        self.on_face(m.group(1))
                    pending = pending[m.end():]
                elif ">" in pending or len(pending) > 24 or not "<face:".startswith(pending[:6]):
                    yield pending[0]  # タグではなかった
                    pending = pending[1:]
                else:
                    break  # タグの途中なので続きを待つ
        if pending:
            yield pending

    def stream_reply(self, user_text: str) -> Iterator[str]:
        for chunk in self._strip_faces(self._raw_reply(user_text)):
            yield chunk

    def _raw_reply(self, user_text: str) -> Iterator[str]:
        self.history.append({"role": "user", "content": user_text})
        # 履歴は常に user で始まるよう奇数個で切り出す
        messages = self.history[-(2 * self.cfg.history_turns + 1):]
        full = ""
        try:
            # 記憶ツールを使ったとき(tool_use)や、検索が長引いて止まったとき(pause_turn)は続きを頼む
            # 1回の返事の間は指示文を固定する(ツールで設定を変えても途中で数字が変わらないように)
            system = self.system_prompt()
            for _ in range(5):
                with self.client.messages.stream(
                    model=self.cfg.model,
                    max_tokens=self.cfg.max_tokens,
                    system=system,
                    messages=messages,
                    tools=self.tools(),
                ) as stream:
                    for chunk in stream.text_stream:
                        full += chunk
                        yield chunk
                    final = stream.get_final_message()
                messages = messages + [{"role": "assistant", "content": final.content}]
                if final.stop_reason == "pause_turn":
                    continue
                if final.stop_reason != "tool_use":
                    break
                results = []
                for block in final.content:
                    if block.type == "tool_use":
                        if block.name == "change_setting":
                            text, is_error = self._change_setting(block.input)
                        else:
                            text, is_error = self.memory.run_tool(block.name, block.input)
                        results.append({"type": "tool_result", "tool_use_id": block.id,
                                        "content": text, "is_error": is_error})
                messages = messages + [{"role": "user", "content": results}]
        finally:
            if full:
                self.history.append({"role": "assistant", "content": full})
            else:
                self.history.pop()  # 失敗したら user 発言も取り消して整合性を保つ
            self.history = self.history[-(4 * self.cfg.history_turns):]
            if self.history and self.history[0]["role"] != "user":
                self.history = self.history[1:]
