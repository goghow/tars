"""TARS のコンソール画面。Linux のターミナル風で、色を抑えた無機質な見た目。"""
import os
import threading
import time

import pygame

from .face import Face

# 無彩色の灰色系。色は状態を示すところ(OK、録音中)だけに、くすませて使う
BG = (10, 11, 12)
FG = (192, 196, 201)       # 自分の発言・通常の文字
BRIGHT = (232, 234, 236)   # TARS の発言
DIM = (104, 109, 116)      # プロンプトや補足
FAINT = (52, 56, 61)       # 罫線
ACCENT = (126, 168, 186)   # OK などの印(くすんだ青緑)
WARN = (196, 164, 92)      # 注意
RED = (198, 82, 72)        # 録音中
BAR_BG = (22, 24, 27)

FONT_DIR = os.path.join(os.path.dirname(__file__), "fonts")
ASCII_FONTS = [
    os.path.join(FONT_DIR, "MPLUS1Code.ttf"),  # 日本語も等幅の M PLUS 1 Code(同梱、SIL OFL)
    "/System/Library/Fonts/Menlo.ttc",  # macOS
    "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",  # Raspberry Pi OS
]
CJK_FONTS = [
    os.path.join(FONT_DIR, "MPLUS1Code.ttf"),
    "/System/Library/Fonts/ヒラギノ角ゴシック W3.ttc",  # macOS
    "/System/Library/Fonts/Hiragino Sans GB.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",  # Raspberry Pi OS
    "/usr/share/fonts/truetype/fonts-japanese-gothic.ttf",
]

FALLBACK_FONTS = [
    "/System/Library/Fonts/ヒラギノ角ゴシック W3.ttc",
    "/System/Library/Fonts/Menlo.ttc",
    "/System/Library/Fonts/Apple Symbols.ttf",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
]

BOOT_UNITS = [
    "Mounted /dev/mmcblk0p2 (memory).",
    "Started Audio I/O service.",
    "Reached target Network.",
    "Started Uplink to Anthropic API.",
]
SPINNER = "|/-\\"
NO_LINE_START = "、。,.!?！？」』）)ー…"


class UIState:
    """ワーカースレッドと画面スレッドで共有する状態。"""

    def __init__(self):
        self.lock = threading.Lock()
        self.log: list[tuple[str, str]] = []  # (話者, テキスト)
        self.listening = False
        self.stage: str | None = "BOOTING"  # TRANSCRIBING / THINKING など
        self.speaking = False
        self.humor = 0
        self.honesty = 0
        self.face = "neutral"  # Claude の表情タグ
        self.hearing = False  # 常時リスナーが話し声を検出中
        self.mic_off = False  # M キーで常時リスナーのマイクを切っている

    def add(self, who: str, text: str) -> None:
        with self.lock:
            self.log.append((who, text))
            self.log = self.log[-60:]

    def append_last(self, text: str) -> None:
        with self.lock:
            if self.log:
                who, old = self.log[-1]
                self.log[-1] = (who, old + text)

    def set_face(self, face: str) -> None:
        with self.lock:
            self.face = face

    def snapshot(self):
        with self.lock:
            return (list(self.log), self.listening or self.hearing, self.stage, self.speaking,
                    self.humor, self.honesty, self.face)


def _load(paths: list[str], size: int) -> pygame.font.Font:
    for p in paths:
        if p and os.path.exists(p):
            return pygame.font.Font(p, size)
    return pygame.font.SysFont("menlo,dejavusansmono,hiraginosans,notosanscjkjp", size)


def _has_glyph(font: pygame.font.Font, ch: str) -> bool:
    """フォントにない文字は豆腐(□)になるので、私用領域の文字と同じ形かどうかで見分ける。"""
    if ch.isspace():
        return True
    tofu = pygame.image.tobytes(font.render("\ue000", False, (255, 255, 255)), "RGB")
    return pygame.image.tobytes(font.render(ch, False, (255, 255, 255)), "RGB") != tofu


class TermFont:
    """英数字は等幅フォント、日本語は日本語フォントで描く(端末と同じく混在させる)。"""

    def __init__(self, cjk_override: str, size: int):
        self.ascii = _load(ASCII_FONTS, size)
        self.cjk = _load(([cjk_override] if cjk_override else []) + CJK_FONTS, size)
        # どちらのフォントにもない文字(全角？や記号)を描くための予備
        self.fallbacks = [_load([p], size) for p in FALLBACK_FONTS if os.path.exists(p)]
        self.ascent = max(self.ascii.get_ascent(), self.cjk.get_ascent())
        self.linesize = max(self.ascii.get_linesize(), self.cjk.get_linesize()) + 2
        self.cell = self.ascii.size("M")[0]
        self._w: dict[str, int] = {}
        self._fonts: dict[str, pygame.font.Font] = {}

    def _font(self, ch: str) -> pygame.font.Font:
        if ch not in self._fonts:
            first = self.ascii if ord(ch) < 128 else self.cjk
            chosen = first
            if ch != "\0" and not _has_glyph(first, ch):
                chosen = next((f for f in self.fallbacks if _has_glyph(f, ch)), first)
            self._fonts[ch] = chosen
        return self._fonts[ch]

    def char_w(self, ch: str) -> int:
        if ch not in self._w:
            self._w[ch] = self._font(ch).size(ch)[0]
        return self._w[ch]

    def width(self, text: str) -> int:
        return sum(self.char_w(c) for c in text)

    def draw(self, scr, segs, x: int, y: int) -> int:
        """(文字列, 色) の並びを1行に描き、右端の x を返す。"""
        for text, color in segs:
            run, run_font = "", None
            for ch in text + "\0":
                f = self._font(ch) if ch != "\0" else None
                if run and f is not run_font:
                    surf = run_font.render(run, True, color)
                    scr.blit(surf, (x, y + self.ascent - run_font.get_ascent()))
                    x += surf.get_width()
                    run = ""
                run_font = f
                run += ch if ch != "\0" else ""
        return x

    def wrap(self, segs, width: int) -> list[list]:
        """色付きの文字列を幅で折り返す。句読点は行頭に来ないようにする。"""
        lines, cur, cur_w = [], [], 0
        for text, color in segs:
            for ch in text:
                if ch == "\n":
                    lines.append(cur)
                    cur, cur_w = [], 0
                    continue
                cw = self.char_w(ch)
                if cur_w + cw > width and cur and ch not in NO_LINE_START:
                    lines.append(cur)
                    cur, cur_w = [], 0
                if cur and cur[-1][1] == color:
                    cur[-1] = (cur[-1][0] + ch, color)
                else:
                    cur.append((ch, color))
                cur_w += cw
        lines.append(cur)
        return lines


class Console:
    def __init__(self, cfg, state: UIState, level_fn):
        self.cfg, self.state, self.level_fn = cfg, state, level_fn
        pygame.init()
        flags = pygame.FULLSCREEN if cfg.fullscreen else 0
        self.screen = pygame.display.set_mode((cfg.screen_w, cfg.screen_h), flags)
        pygame.display.set_caption("tars@tars: ~")
        if cfg.fullscreen:
            pygame.mouse.set_visible(False)
        self.font = TermFont(cfg.font_path, max(13, cfg.screen_h // 28))
        name = (cfg.user_name or "").strip()
        self.user = name if name.isascii() and name.isalnum() else "user"
        self.t0 = time.time()
        self.boot_done_at: float | None = None
        self.face = Face()

    def toggle_fullscreen(self) -> None:
        pygame.display.toggle_fullscreen()

    def _prompt(self):
        return [(f"{self.user}@tars:~$ ", DIM)]

    def _boot_lines(self, stage, now):
        elapsed = now - self.t0
        lines = []
        for i, unit in enumerate(BOOT_UNITS):
            if elapsed > 0.25 * (i + 1):
                lines.append([("[  ", DIM), ("OK", ACCENT), ("  ] ", DIM), (unit, DIM)])
        if elapsed > 0.25 * (len(BOOT_UNITS) + 1):
            if stage == "BOOTING":
                spin = SPINNER[int(now * 8) % 4]
                lines.append([(f"[  {spin}   ] ", DIM), ("Starting speech recognition...", DIM)])
            else:
                if self.boot_done_at is None:
                    self.boot_done_at = now
                lines.append([("[  ", DIM), ("OK", ACCENT), ("  ] ", DIM), ("Started speech recognition.", DIM)])
                lines += [
                    [],
                    [("TARS OS 0.1 (GNU/Linux aarch64) tty1", FG)],
                    [],
                    [("呼出    ", DIM), ("「TARS」 / SPACE 長押し", FG)],
                    [("操作    ", DIM), ("M マイク / F 全画面 / ESC 終了", FG)],
                    [],
                ]
        return lines

    def draw(self) -> None:
        log, listening, stage, speaking, humor, honesty, emotion = self.state.snapshot()
        scr, f = self.screen, self.font
        w, h = scr.get_size()
        now = time.time()
        scr.fill(BG)

        # 下: ステータスバー(区切り線と大文字のラベルだけの無機質な帯)
        bar_h = f.linesize + 6
        bar_y = h - bar_h
        pygame.draw.rect(scr, BAR_BG, (0, bar_y, w, bar_h))
        pygame.draw.line(scr, FAINT, (0, bar_y), (w, bar_y), 1)
        if listening:
            mode, mode_c = "REC", RED
        elif stage:
            mode, mode_c = stage, FG
        elif speaking:
            mode, mode_c = "SPEAKING", FG
        else:
            mode, mode_c = "IDLE", DIM
        mic = "MIC OFF" if self.state.mic_off else "MIC ON"
        items = [("TARS", BRIGHT), (mode, mode_c), (mic, WARN if self.state.mic_off else DIM),
                 (f"HUMOR {humor}", DIM), (f"HONESTY {honesty}", DIM), (time.strftime("%H:%M"), DIM)]
        while len(items) > 3 and f.width("  │  ".join(t for t, _ in items)) > w - 2 * f.cell:
            items.pop(-1)  # 狭いときは後ろから省く
        segs = []
        for i, item in enumerate(items):
            if i:
                segs.append(("  │  ", FAINT))
            segs.append(item)
        f.draw(scr, segs, f.cell, bar_y + 3)

        # 左ペイン: 顔 / 右ペイン: 会話(右がアクティブなので境界線は緑)
        face_w = int(w * 0.38)
        pad = f.cell
        self.face.draw(scr, pygame.Rect(pad, pad, face_w - 2 * pad, bar_y - 2 * pad),
                       FG, emotion, listening, stage, speaking, self.level_fn())
        pygame.draw.line(scr, FAINT, (face_w, 0), (face_w, bar_y - 1), 1)

        x0 = face_w + pad
        text_w = w - x0 - pad
        lines = [ln for segs in self._boot_lines(stage, now) for ln in f.wrap(segs, text_w)]
        for who, text in log:
            if who == "YOU":
                segs = self._prompt() + [(text, FG)]
            elif who == "TARS":
                segs = [(text, BRIGHT)]
            else:
                segs = [("tars: ", WARN), (text, DIM)]
            lines += f.wrap(segs, text_w)

        # いま入力中の行
        cursor = int(now * 2) % 2 == 0
        if listening:
            n = int(min(1.0, self.level_fn() * 12) * 10)
            lines.append(self._prompt() + [("● REC  ", RED), ("▮" * n + "▯" * (10 - n), DIM)]
                         + ([(" █", FG)] if cursor else []))
        elif stage and stage != "BOOTING":
            spin = SPINNER[int(now * 8) % 4]
            lines.append([(f"{spin} {stage}", DIM)])
        elif not speaking and stage != "BOOTING":
            lines.append(self._prompt() + ([("█", FG)] if cursor else []))

        # 下から積む(古い行は上に流れて消える)
        y = bar_y - pad // 2 - f.linesize
        for segs in reversed(lines):
            if y < pad // 2:
                break
            f.draw(scr, segs, x0, y)
            y -= f.linesize
        pygame.display.flip()
