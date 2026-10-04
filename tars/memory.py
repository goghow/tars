"""長期記憶。覚えたことを JSON ファイルに保存し、次に起動しても忘れない。"""
import json
import os
import threading
from datetime import date

MAX_ITEMS = 200

TOOLS = [
    {
        "name": "remember",
        "description": (
            "相手について長く覚えておく価値のあることを1件保存する。"
            "名前、好み、家族やペット、仕事、予定、続きが気になる話題など。"
            "いま相談されている内容そのもの、一時的な雑談、すでに覚えていることは保存しない。"
            "「来週の土曜」などは今日の日付から計算して「10月10日(土)」のような具体的な日付で書く。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "fact": {"type": "string", "description": "覚えること。主語を入れた短い一文。例: ユーザーは辛い食べ物が苦手"},
            },
            "required": ["fact"],
            "additionalProperties": False,
        },
        "strict": True,
        "eager_input_streaming": True,
    },
    {
        "name": "forget",
        "description": "覚えていることが間違っていた、古くなった、忘れてと言われたときに、その番号の記憶を消す。",
        "input_schema": {
            "type": "object",
            "properties": {"id": {"type": "integer", "description": "記憶一覧の番号"}},
            "required": ["id"],
            "additionalProperties": False,
        },
        "strict": True,
        "eager_input_streaming": True,
    },
]


class Memory:
    def __init__(self, path: str):
        self.path = path
        self.lock = threading.Lock()
        self.items: list[dict] = []
        if os.path.exists(path):
            try:
                with open(path, encoding="utf-8") as f:
                    self.items = json.load(f)
            except (OSError, json.JSONDecodeError) as e:
                print(f"[memory] {path} を読めませんでした: {e}")

    def _save(self) -> None:
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.items, f, ensure_ascii=False, indent=1)
        os.replace(tmp, self.path)  # 書き込み途中で落ちても壊れないように

    def prompt_text(self) -> str:
        with self.lock:
            if not self.items:
                return "まだ何も覚えていない。"
            return "\n".join(f"[{i}] {m['date']} {m['fact']}" for i, m in enumerate(self.items, 1))

    def run_tool(self, name: str, args) -> tuple[str, bool]:
        """ツールを実行して (結果の文, エラーかどうか) を返す。"""
        if name == "remember" and isinstance(args, dict) and isinstance(args.get("fact"), str) and args["fact"].strip():
            with self.lock:
                self.items.append({"date": date.today().isoformat(), "fact": args["fact"].strip()})
                self.items = self.items[-MAX_ITEMS:]
                self._save()
            return "保存した。", False
        if name == "forget" and isinstance(args, dict) and isinstance(args.get("id"), int):
            with self.lock:
                i = args["id"] - 1
                if not 0 <= i < len(self.items):
                    return f"番号 {args['id']} の記憶はない。", True
                gone = self.items.pop(i)
                self._save()
            return f"忘れた: {gone['fact']}", False
        return f"入力が不正: {name} {args!r}", True
