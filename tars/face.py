"""TARS の顔。状態(聞く・考える・話す)と感情タグで目と口が変わる。白黒の画面に合わせて四角い目で描く。"""
import math
import random
import time

import pygame

# 感情ごとの目と口の形。値は顔の大きさに対する比率
SHAPES = {
    "neutral":   dict(eye_w=1.0, eye_h=1.4, slant=0.0, look_y=0.0, mouth="line"),
    "happy":     dict(eye_w=1.1, eye_h=1.0, slant=0.0, look_y=0.0, mouth="smile"),
    "joke":      dict(eye_w=1.1, eye_h=0.55, slant=0.0, look_y=0.1, mouth="smirk"),
    "sad":       dict(eye_w=1.0, eye_h=1.1, slant=0.45, look_y=0.25, mouth="frown"),
    "surprised": dict(eye_w=1.2, eye_h=1.7, slant=0.0, look_y=-0.1, mouth="o"),
}
HOLD_SEC = 6.0  # 話し終えてから感情の顔を保つ時間


class Face:
    def __init__(self):
        self.cur = {"eye_w": 1.0, "eye_h": 0.1, "slant": 0.0, "look_x": 0.0, "look_y": 0.0}
        self.next_blink = time.time() + 3
        self.next_glance = time.time() + 5
        self.glance = 0.0
        self.last_speaking = 0.0

    def _target(self, now, emotion, listening, stage, speaking, level):
        if speaking:
            self.last_speaking = now
        if not speaking and now - self.last_speaking > HOLD_SEC:
            emotion = "neutral"
        shape = dict(SHAPES.get(emotion, SHAPES["neutral"]))
        shape["look_x"] = self.glance
        if stage == "BOOTING":
            shape.update(eye_h=0.1, mouth="line")
        elif listening:
            # 声が大きいほど目が少し大きくなる
            shape.update(eye_w=1.1, eye_h=1.5 + min(0.4, level * 6), slant=0.0, look_x=0.0, look_y=0.0, mouth="line")
        elif stage:
            # 考え中: 目を細めて左右に動かす
            shape.update(eye_h=0.8, slant=0.0, look_x=math.sin(now * 3) * 0.6, look_y=-0.35, mouth="line")
        # まばたき(待機中と話し中だけ)
        if not listening and not stage and now > self.next_blink:
            shape["eye_h"] = 0.08
            if now > self.next_blink + 0.12:
                self.next_blink = now + random.uniform(2.5, 6)
        # 待機中はたまに視線をそらす
        if now > self.next_glance:
            self.glance = random.choice([0.0, 0.0, -0.4, 0.4])
            self.next_glance = now + random.uniform(3, 7)
        return shape

    def draw(self, scr, rect, color, emotion, listening, stage, speaking, level):
        now = time.time()
        tgt = self._target(now, emotion, listening, stage, speaking, level)
        for k in self.cur:  # なめらかに近づける
            self.cur[k] += (tgt[k] - self.cur[k]) * 0.3
        c = self.cur
        u = min(rect.width, rect.height) / 5  # 顔の単位サイズ
        cx, cy = rect.centerx, rect.centery - u * 0.6
        gap = u * 1.3
        ew, eh = u * c["eye_w"], u * c["eye_h"]
        ox, oy = c["look_x"] * u * 0.5, c["look_y"] * u * 0.5
        lw = max(3, int(u * 0.22))

        for side in (-1, 1):
            ex = cx + side * gap + ox
            ey = cy + oy
            x0, x1 = ex - ew / 2, ex + ew / 2
            y0, y1 = ey - eh / 2, ey + eh / 2
            if emotion == "happy" and tgt["eye_h"] > 0.5 and not (listening or stage):
                # うれしい: ^ ^ の目
                arc = pygame.Rect(x0, ey - eh * 0.2, ew, eh * 0.9)
                pygame.draw.arc(scr, color, arc, 0, math.pi, lw)
                continue
            slant = c["slant"] * eh
            # 悲しい: 目の外側を下げる
            outer_top = y0 + slant
            if side < 0:
                pts = [(x0, outer_top), (x1, y0), (x1, y1), (x0, y1)]
            else:
                pts = [(x0, y0), (x1, outer_top), (x1, y1), (x0, y1)]
            pygame.draw.polygon(scr, color, pts)

        # 口
        mx, my, mw = cx + ox * 0.5, cy + u * 1.9, u * 1.6
        mouth = tgt["mouth"]
        if speaking and not listening:
            # 話している間はパクパクさせる
            open_h = u * (0.12 + 0.45 * abs(math.sin(now * 13) * math.sin(now * 4.7)))
            pygame.draw.rect(scr, color, (mx - mw / 2, my - open_h / 2, mw, open_h))
        elif mouth == "smile":
            pygame.draw.arc(scr, color, pygame.Rect(mx - mw / 2, my - u * 0.6, mw, u * 0.9), math.pi, 2 * math.pi, lw)
        elif mouth == "frown":
            pygame.draw.arc(scr, color, pygame.Rect(mx - mw / 3, my - u * 0.1, mw * 2 / 3, u * 0.7), 0, math.pi, lw)
        elif mouth == "smirk":
            pygame.draw.line(scr, color, (mx - mw / 2, my + u * 0.1), (mx + mw / 2, my - u * 0.25), lw)
        elif mouth == "o":
            s = u * 0.55
            pygame.draw.rect(scr, color, (mx - s / 2, my - s / 2, s, s), lw)
        else:
            pygame.draw.rect(scr, color, (mx - mw / 2, my - lw / 2, mw, lw))
