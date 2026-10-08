#!/usr/bin/env python3
"""
LokiMan -- a retro pseudo-3D three-lane runner in the spirit of Pepsiman.

Run:      pip install pygame && python lokiman.py

Controls
    A / D  or  LEFT / RIGHT   shift lanes (leaves a green illusion trail)
    W / UP / SPACE            jump over low barriers
    S / DOWN                  superhero slide under high hazards
    F                         unleash GLORIOUS PURPOSE (when the meter is full)
                              (it also hurts bosses; Tesseracts damage them too)
    F11 or Cmd+F              toggle fullscreen
    M                         mute / unmute
    Q                         cycle graphics quality (auto-lowers if slow)
    F3                        show FPS
    P                         pause          ENTER  start / restart     ESC  quit

Architecture
    LaneManager  lane geometry, perspective projection, road rendering
    Player       Loki: movement, jump, slide, tumble, aura, illusion trail
    Obstacle     every hazard (taxi, baton Minuteman, Alligator Loki, pits...)
    Tesseract    glowing collectible cubes
    Level        per-level theme data, backdrop and roadside scenery
    GameState    rules: spawning, collisions, scoring, progression
    Game         pygame window, input, drawing of world / HUD / overlays
"""
import gc
import math
import os
import random
import sys

import pygame

# --------------------------------------------------------------------------
# Constants
# --------------------------------------------------------------------------
W, H = 960, 640
FPS = 60
FPS_CAP = 120
SIM_DT = 1 / 120.0
HORIZON = 230            # screen y of the vanishing line
PLAYER_Y = 560           # screen y of Loki's feet (scale == 1)
LANE_W = 170             # pixels between lane centres at scale 1
ND = 14.0                # perspective constant: scale = ND / (ND + dist)
FAR = 120.0              # draw distance (world units)
BAND = 4.0               # length of one road stripe
SPAWN_D = 110.0          # things appear this far ahead
ROAD_HALF = 1.5          # road half width in lane units

BASE_SPEED = 25.0
MAX_SPEED = 46.0
LANE_SPEED = 9.0         # lanes per second while shifting
JUMP_V = 650.0
GRAVITY = 1750.0
SLIDE_TIME = 0.75
TUMBLE_TIME = 1.1
FALL_TIME = 1.2
INVULN_TIME = 2.2
AURA_TIME = 6.0
METER_GAIN = 7.0
START_LIVES = 3
MAX_LIVES = 5
APPLES_PER_LIFE = 10
CHASE_LEN = 320.0
REV_D0 = 14.0             # Loki's distance from the camera in the reversed (Alioth) scene
MJ_WARN_D = 46.0          # Mjolnir warning flash appears this far ahead
MJ_LAUNCH = 16.0          # ...and the hammer is thrown at this distance
MJ_VX = 12.0              # hammer speed in lanes per second
BEST_FILE = "lokiman_best.txt"

GOLD = (245, 200, 40)
GREEN = (40, 170, 80)
DKGREEN = (18, 100, 48)
WHITE = (255, 255, 255)
BLACK = (0, 0, 0)
CYAN = (90, 220, 255)


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------
def clamp(v, lo, hi):
    return lo if v < lo else hi if v > hi else v


def mix(c1, c2, t):
    t = clamp(t, 0.0, 1.0)
    return (int(c1[0] + (c2[0] - c1[0]) * t),
            int(c1[1] + (c2[1] - c1[1]) * t),
            int(c1[2] + (c2[2] - c1[2]) * t))


def hrand(a, b=0, salt=0):
    """Deterministic pseudo random number in [0, 1) from integers."""
    v = math.sin(a * 12.9898 + b * 78.233 + salt * 37.719) * 43758.5453
    return v - math.floor(v)


FX = {"glow": True, "tint": True, "decor": 1}   # lowered automatically on slow machines

_fonts = {}


def get_font(size):
    f = _fonts.get(size)
    if f is None:
        names = ("impact,arialblack,dejavusansbold,arial" if size >= 44
                 else "arialrounded,helveticaneue,arial,verdana,dejavusans")
        try:
            f = pygame.font.SysFont(names, size, bold=True)
        except Exception:
            f = pygame.font.Font(None, size)
        _fonts[size] = f
    return f


_text_cache = {}


def text_surf(txt, size, color, outline=BLACK, ow=3):
    key = (txt, size, color, outline, ow)
    img = _text_cache.get(key)
    if img is None:
        if len(_text_cache) > 600:
            _text_cache.clear()
        f = get_font(size)
        base = f.render(txt, True, color)
        edge = f.render(txt, True, outline)
        img = pygame.Surface((base.get_width() + ow * 2, base.get_height() + ow * 2), pygame.SRCALPHA)
        for dx in range(-ow, ow + 1):
            for dy in range(-ow, ow + 1):
                if dx * dx + dy * dy <= ow * ow + 1:
                    img.blit(edge, (ow + dx, ow + dy))
        img.blit(base, (ow, ow))
        _text_cache[key] = img
    return img


def draw_text(surf, txt, size, color, pos, anchor="center", outline=BLACK, ow=3):
    img = text_surf(txt, size, color, outline, ow)
    surf.blit(img, img.get_rect(**{anchor: pos}))
    return img


_fill_cache = {}


def fill_alpha(surf, rect, color, alpha):
    """Blit a cached translucent colour block (avoids per-frame surface allocation)."""
    rect = pygame.Rect(rect)
    if alpha <= 0 or rect.w <= 0 or rect.h <= 0:
        return
    key = (rect.w, rect.h, color)
    img = _fill_cache.get(key)
    if img is None:
        if len(_fill_cache) > 60:
            _fill_cache.clear()
        img = pygame.Surface(rect.size)
        img.fill(color)
        _fill_cache[key] = img
    img.set_alpha(int(clamp(alpha, 0, 255)))
    surf.blit(img, rect.topleft)


_glow_cache = {}


def glow_surf(radius, color, strength=170):
    radius = max(4, int(radius) // 8 * 8 + 8)
    key = (radius, color, strength)
    g = _glow_cache.get(key)
    if g is None:
        g = pygame.Surface((radius * 2, radius * 2), pygame.SRCALPHA)
        for r in range(radius, 0, -2):
            a = int(strength * (1 - r / radius) ** 2)
            pygame.draw.circle(g, color + (a,), (radius, radius), r)
        _glow_cache[key] = g
    return g


def blit_glow(surf, cx, cy, radius, color, strength=170):
    if not FX["glow"]:
        return
    g = glow_surf(radius, color, strength)
    surf.blit(g, g.get_rect(center=(int(cx), int(cy))))


def vgradient(surf, top, bottom, y0=0, y1=None):
    y1 = surf.get_height() if y1 is None else y1
    for y in range(y0, y1):
        pygame.draw.line(surf, mix(top, bottom, (y - y0) / max(1, y1 - y0 - 1)), (0, y), (surf.get_width(), y))


# anchored primitives: shapes defined at scale 1 relative to an anchor point
def prect(surf, col, a, s, x, y, w, h):
    pygame.draw.rect(surf, col, (int(a[0] + x * s), int(a[1] + y * s), max(1, int(w * s) + 1), max(1, int(h * s) + 1)))


def ppoly(surf, col, a, s, pts):
    pygame.draw.polygon(surf, col, [(a[0] + px * s, a[1] + py * s) for px, py in pts])


def pcirc(surf, col, a, s, x, y, r):
    pygame.draw.circle(surf, col, (int(a[0] + x * s), int(a[1] + y * s)), max(1, int(r * s)))


def pline(surf, col, a, s, x0, y0, x1, y1, w=2):
    pygame.draw.line(surf, col, (a[0] + x0 * s, a[1] + y0 * s), (a[0] + x1 * s, a[1] + y1 * s), max(1, int(w * s)))


def pellipse(surf, col, a, s, x, y, w, h, width=0):
    pygame.draw.ellipse(surf, col, (int(a[0] + x * s), int(a[1] + y * s), max(2, int(w * s)), max(2, int(h * s))),
                        max(0, int(width * s)) if width else 0)


def draw_cube(surf, cx, cy, r, spin=0.0, glow=True):
    """A glowing blue Tesseract cube (isometric)."""
    if glow:
        blit_glow(surf, cx, cy, r * 2.6, (60, 160, 255), 150)
    k = 0.55 + 0.45 * abs(math.cos(spin))
    rx = 0.87 * r * k
    top = [(cx, cy - r), (cx + rx, cy - r * 0.5), (cx, cy), (cx - rx, cy - r * 0.5)]
    left = [(cx - rx, cy - r * 0.5), (cx, cy), (cx, cy + r), (cx - rx, cy + r * 0.5)]
    right = [(cx + rx, cy - r * 0.5), (cx, cy), (cx, cy + r), (cx + rx, cy + r * 0.5)]
    pygame.draw.polygon(surf, (190, 245, 255), top)
    pygame.draw.polygon(surf, (40, 140, 255), left)
    pygame.draw.polygon(surf, (20, 85, 215), right)
    w = 1 if r < 14 else 2
    for poly in (top, left, right):
        pygame.draw.polygon(surf, (225, 250, 255), poly, w)
    pygame.draw.circle(surf, WHITE, (int(cx), int(cy)), max(1, int(r * 0.16)))


# --------------------------------------------------------------------------
# LaneManager: lane geometry, perspective, road drawing
# --------------------------------------------------------------------------
class LaneManager:
    COUNT = 3

    def __init__(self):
        self.cam = 0.0           # camera sway (lane units) that follows Loki
        self.rev = False         # reversed camera: Loki runs towards the screen, hazards rise from the bottom

    @property
    def pscale(self):
        return ND / (ND + REV_D0) if self.rev else 1.0

    @property
    def sprite_k(self):
        return self.pscale * 1.45 if self.rev else 1.0

    @property
    def gy(self):
        """Screen y of Loki's feet."""
        return HORIZON + (PLAYER_Y - HORIZON) * self.pscale

    @staticmethod
    def lane_x(lane):
        return lane - 1

    def px(self, lx):
        """Screen x of lane position lx on the player's line (scale 1)."""
        return W / 2 + (lx - self.cam) * LANE_W * self.pscale

    def clamp_lane(self, lane):
        return int(clamp(lane, 0, self.COUNT - 1))

    @staticmethod
    def scale(dist):
        return ND / (ND + max(dist, -ND * 0.8))

    def project(self, lx, dist):
        """lane-unit x and world distance -> (screen x, screen y, scale)."""
        if self.rev:
            dist = REV_D0 - dist
        s = self.scale(dist)
        return W / 2 + (lx - self.cam) * LANE_W * s, HORIZON + (PLAYER_Y - HORIZON) * s, s

    def draw_ground(self, surf, level, travel):
        pal = level.pal
        surf.blit(level.backdrop(), (0, 0))
        pygame.draw.rect(surf, pal["ground"], (0, HORIZON, W, H - HORIZON))
        k0 = int(travel // BAND)
        off = travel - k0 * BAND
        cam = self.cam
        for i in range(int(FAR / BAND) + 1, -1, -1):
            d0 = i * BAND - off
            d1 = d0 + BAND
            k = k0 + i
            fog = clamp(d0 / FAR, 0, 1) ** 1.3 * 0.8
            s0, s1 = self.scale(d0), self.scale(d1)
            y0 = HORIZON + (PLAYER_Y - HORIZON) * s0 + 1
            y1 = HORIZON + (PLAYER_Y - HORIZON) * s1

            def quad(l0, l1, col, s0=s0, s1=s1, y0=y0, y1=y1, fog=fog):
                pygame.draw.polygon(surf, mix(col, pal["fog"], fog),
                                    [(W / 2 + (l0 - cam) * LANE_W * s0, y0), (W / 2 + (l1 - cam) * LANE_W * s0, y0),
                                     (W / 2 + (l1 - cam) * LANE_W * s1, y1), (W / 2 + (l0 - cam) * LANE_W * s1, y1)])

            side = pal["side_a"] if k % 2 == 0 else pal["side_b"]
            quad(-3.8, -ROAD_HALF, side)
            quad(ROAD_HALF, 3.8, side)
            quad(-ROAD_HALF, ROAD_HALF, pal["road_a"] if k % 2 == 0 else pal["road_b"])
            curb = pal["curb_a"] if k % 2 == 0 else pal["curb_b"]
            quad(-ROAD_HALF - 0.09, -ROAD_HALF, curb)
            quad(ROAD_HALF, ROAD_HALF + 0.09, curb)
            if k % 2 == 0:
                for lx in (-0.5, 0.5):
                    quad(lx - 0.025, lx + 0.025, pal["line"])


# --------------------------------------------------------------------------
# Level: theme, backdrop and roadside scenery
# --------------------------------------------------------------------------
class Level:
    DECOR_SPACING = 10.0

    def __init__(self, num, title, year, length, theme, obstacles, pal, scenes=()):
        self.boss = None
        self.scenes = [dict(kind=k, at=a) for k, a in scenes]
        self.num, self.title, self.year, self.length = num, title, year, length
        self.theme, self.obstacles, self.pal = theme, obstacles, pal
        self._bd = None

    # ----- backdrop -----
    def backdrop(self):
        if self._bd is None:
            self._bd = getattr(self, "_bd_" + self.theme)()
        return self._bd

    def _bd_city(self):
        p = self.pal
        surf = pygame.Surface((W, HORIZON))
        vgradient(surf, p["sky_top"], p["sky_bot"])
        rng = random.Random(7)
        blit_glow(surf, 120, HORIZON - 40, 170, (80, 255, 90), 150)        # Hulk smash glow
        for col, hmin, hmax in (((86, 66, 88), 70, 170), ((36, 38, 60), 40, 120)):
            x = -20
            while x < W:
                bw, bh = rng.randint(40, 90), rng.randint(hmin, hmax)
                pygame.draw.rect(surf, col, (x, HORIZON - bh, bw, bh))
                for wy in range(HORIZON - bh + 8, HORIZON - 6, 14):
                    for wx in range(x + 6, x + bw - 8, 12):
                        if rng.random() > 0.62:
                            pygame.draw.rect(surf, (255, 215, 120), (wx, wy, 5, 7))
                x += bw + rng.randint(-8, 4)
        pygame.draw.rect(surf, (28, 30, 48), (210, HORIZON - 200, 34, 200))      # Avengers tower
        draw_text(surf, "A", 30, GOLD, (227, HORIZON - 180), outline=(28, 30, 48), ow=1)
        pygame.draw.polygon(surf, CYAN, [(716, 0), (744, 0), (736, HORIZON - 30), (724, HORIZON - 30)])  # portal beam
        blit_glow(surf, 730, HORIZON - 30, 90, CYAN, 190)
        for gx, gy in ((560, 90), (610, 60), (330, 70), (860, 110)):             # Chitauri gliders
            pygame.draw.polygon(surf, (20, 18, 30), [(gx - 16, gy), (gx + 16, gy), (gx, gy + 8)])
        for sx, sy in ((300, HORIZON - 150), (520, HORIZON - 120), (880, HORIZON - 130)):  # smoke
            for j in range(4):
                blit_glow(surf, sx + j * 14, sy - j * 26, 36 + j * 6, (40, 40, 48), 190)
        return surf

    def _bd_tva(self):
        p = self.pal
        surf = pygame.Surface((W, HORIZON))
        vgradient(surf, p["sky_top"], p["sky_bot"])
        pygame.draw.rect(surf, (225, 130, 50), (W // 2 - 200, 40, 400, HORIZON - 40))
        for i in range(8):
            pygame.draw.rect(surf, (200, 110, 40) if i % 2 else (235, 145, 60),
                             (W // 2 - 200 + i * 50, 40, 50, HORIZON - 40))
        pygame.draw.rect(surf, (90, 50, 25), (W // 2 - 200, 40, 400, HORIZON - 40), 6)
        pygame.draw.circle(surf, (250, 235, 190), (W // 2, 112), 54)
        pygame.draw.circle(surf, (90, 50, 25), (W // 2, 112), 54, 5)
        for a in range(12):
            ang = a * math.pi / 6
            pygame.draw.line(surf, (90, 50, 25), (W // 2 + math.sin(ang) * 44, 112 - math.cos(ang) * 44),
                             (W // 2 + math.sin(ang) * 51, 112 - math.cos(ang) * 51), 3)
        pygame.draw.line(surf, (90, 50, 25), (W // 2, 112), (W // 2, 80), 4)
        pygame.draw.line(surf, (200, 40, 30), (W // 2, 112), (W // 2 + 28, 124), 3)
        draw_text(surf, "T.V.A.", 34, (250, 235, 190), (W // 2, 190), outline=(90, 50, 25), ow=3)
        return surf

    def _bd_void(self):
        p = self.pal
        surf = pygame.Surface((W, HORIZON))
        vgradient(surf, p["sky_top"], p["sky_bot"])
        rng = random.Random(3)
        for _ in range(90):
            c = rng.randint(120, 255)
            surf.set_at((rng.randint(0, W - 1), rng.randint(0, HORIZON - 40)), (c, c, 255))
        glow = pygame.Surface((W, HORIZON), pygame.SRCALPHA)
        for i in range(9):                                   # Alioth, the storm eater
            w, h = 760 - i * 72, 330 - i * 32
            pygame.draw.ellipse(glow, (70 + i * 6, 20, 110 - i * 6, 70), (W // 2 - w // 2, HORIZON - 60 - h // 2, w, h))
        pygame.draw.ellipse(glow, (10, 0, 20, 230), (W // 2 - 90, HORIZON - 120, 180, 120))
        surf.blit(glow, (0, 0))
        for ex in (-34, 34):
            pygame.draw.ellipse(surf, (255, 240, 255), (W // 2 + ex - 14, HORIZON - 96, 28, 9))
        for _ in range(3):                                   # lightning
            x, y = rng.randint(150, W - 150), 0
            pts = [(x, y)]
            while y < HORIZON - 60:
                x += rng.randint(-18, 18)
                y += rng.randint(18, 34)
                pts.append((x, y))
            pygame.draw.lines(surf, (210, 170, 255), False, pts, 2)
        for col, hmin, hmax in (((60, 30, 90), 40, 100), ((34, 14, 52), 20, 70)):
            pts, x = [(0, HORIZON)], 0
            while x < W + 40:
                pts.append((x, HORIZON - rng.randint(hmin, hmax)))
                x += rng.randint(25, 60)
            pts.append((W, HORIZON))
            pygame.draw.polygon(surf, col, pts)
        return surf

    # ----- roadside scenery -----
    def draw_decor(self, surf, lm, travel):
        sp = self.DECOR_SPACING
        self.cam = lm.cam
        k0 = int(travel // sp)
        fn = getattr(self, "_decor_" + self.theme)
        for k in range(k0 + int(FAR / sp) + 1, k0 - 1, -1):
            if FX["decor"] > 1 and k % 2:
                continue
            dist = k * sp - travel
            if dist < -3 or dist > FAR:
                continue
            s = lm.scale(dist)
            sy = HORIZON + (PLAYER_Y - HORIZON) * s
            fog = clamp(dist / FAR, 0, 1) ** 1.3 * 0.8
            for side in (-1, 1):
                fn(surf, k, side, s, sy, fog)

    _facades = {}

    def _facade(self, variant, w, h):
        """Pre-rendered window grid for a city building, scaled to w x h."""
        base = self._facades.get(variant)
        if base is None:
            fw, fh = 340, 600
            base = pygame.Surface((fw, fh))
            base.fill(mix((38, 42, 66), (74, 60, 84), variant / 5.0))
            for wy in range(18, fh - 8, 30):
                for wx in range(12, fw - 16, 26):
                    c = (255, 215, 120) if hrand(wx + variant * 31, wy, variant) > 0.55 else (22, 24, 40)
                    pygame.draw.rect(base, c, (wx, wy, 12, 16))
            self._facades[variant] = base
        return pygame.transform.scale(base, (max(2, w), max(2, h)))

    def _fogc(self, c, fog):
        return mix(c, self.pal["fog"], fog)

    def _decor_city(self, surf, k, side, s, sy, fog):
        cx = W / 2 - self.cam * LANE_W * s
        r1, r2, r3 = hrand(k, side, 1), hrand(k, side, 2), hrand(k, side, 3)
        bw, bh = (1.9 + r2 * 0.6) * LANE_W * s, (230 + r3 * 330) * s
        bx = cx + side * (3.05 + r1 * 0.4) * LANE_W * s
        fac = self._facade(int(r2 * 5.99), int(bw), int(bh))
        pygame.draw.rect(surf, self.pal["fog"], (bx - bw / 2, sy - bh, bw, bh + 2))
        fac.set_alpha(int(255 * (1 - fog)))
        surf.blit(fac, (bx - fac.get_width() / 2, sy - bh))
        if r1 > 0.55:                                          # burning rooftop
            t = pygame.time.get_ticks() / 1000.0
            for j in range(3):
                fx = bx + (j - 1) * 24 * s
                fh = (26 + 12 * math.sin(t * 9 + j + k)) * s
                pygame.draw.circle(surf, (255, 120, 20), (int(fx), int(sy - bh - fh * 0.3)), max(1, int(fh * 0.7)))
                pygame.draw.circle(surf, (255, 220, 60), (int(fx), int(sy - bh - fh * 0.2)), max(1, int(fh * 0.4)))
        if k % 2 == 0:                                         # street lamp
            lx = cx + side * 1.62 * LANE_W * s
            pygame.draw.rect(surf, self._fogc((90, 90, 96), fog), (lx - 3 * s, sy - 150 * s, 6 * s + 1, 150 * s))
            pygame.draw.rect(surf, self._fogc((255, 240, 160), fog), (lx - (side * 22 + 4) * s, sy - 154 * s, 28 * s, 7 * s))

    def _decor_tva(self, surf, k, side, s, sy, fog):
        cx = W / 2 - self.cam * LANE_W * s
        c1, c2 = ((205, 112, 42), (178, 92, 34)) if k % 2 == 0 else ((178, 92, 34), (205, 112, 42))
        x_in = cx + side * 1.75 * LANE_W * s
        x_out = cx + side * 9 * LANE_W * s
        x0, x1 = min(x_in, x_out), max(x_in, x_out)
        ch = 430 * s
        pygame.draw.rect(surf, self._fogc(c1, fog), (x0, sy - ch, x1 - x0, ch + 2))
        pygame.draw.rect(surf, self._fogc((92, 50, 24), fog), (x0, sy - ch, x1 - x0, 10 * s + 1))   # cornice
        pygame.draw.rect(surf, self._fogc((92, 50, 24), fog), (x0, sy - 40 * s, x1 - x0, 40 * s + 2))  # skirting
        dx = cx + side * 2.55 * LANE_W * s
        dw, dh = 0.9 * LANE_W * s, 290 * s
        pygame.draw.rect(surf, self._fogc((110, 62, 30), fog), (dx - dw / 2, sy - 40 * s - dh, dw, dh))
        pygame.draw.rect(surf, self._fogc((240, 200, 120), fog), (dx - dw * 0.3, sy - 40 * s - dh * 0.75, dw * 0.6, dh * 0.2))
        pillar_w = 0.3 * LANE_W * s
        pygame.draw.rect(surf, self._fogc(c2, fog), (x_in - pillar_w / 2 - side * pillar_w * 0.3, sy - ch, pillar_w, ch + 2))
        if k % 2 == 0:                                           # ceiling light panels
            lw = 3.0 * LANE_W * s
            pygame.draw.rect(surf, self._fogc((92, 50, 24), fog), (cx - lw * 0.75, sy - ch - 4 * s, lw * 1.5, 12 * s + 1))
            pygame.draw.rect(surf, self._fogc((255, 245, 200), fog), (cx - lw / 2, sy - ch - 2 * s, lw, 8 * s + 1))

    def _decor_void(self, surf, k, side, s, sy, fog):
        cx = W / 2 - self.cam * LANE_W * s
        r1, r2, r3 = hrand(k, side, 1), hrand(k, side, 2), hrand(k, side, 3)
        bx = cx + side * (2.5 + r1 * 2.2) * LANE_W * s
        w, h = (0.8 + r2 * 0.9) * LANE_W * s, (130 + r3 * 400) * s
        col = self._fogc(mix((54, 30, 82), (96, 52, 140), r2), fog)
        pygame.draw.polygon(surf, col, [(bx - w / 2, sy), (bx + (r2 - 0.5) * w * 0.4, sy - h), (bx + w / 2, sy)])
        pygame.draw.polygon(surf, self._fogc((30, 14, 48), fog), [(bx, sy), (bx + (r2 - 0.5) * w * 0.4, sy - h), (bx + w / 2, sy)])
        if r1 > 0.55:
            blit_glow(surf, bx + (r2 - 0.5) * w * 0.4, sy - h, 26 * s + 4, (200, 100, 255), 160)
            pygame.draw.circle(surf, self._fogc((235, 190, 255), fog), (int(bx + (r2 - 0.5) * w * 0.4), int(sy - h)), max(1, int(6 * s)))
        if r3 > 0.62:                                           # floating rubble
            pygame.draw.rect(surf, self._fogc((70, 40, 100), fog), (bx - 20 * s, sy - h - 60 * s, 40 * s, 16 * s))


def build_levels():
    levels = [
        Level(1, "THE BATTLE OF NEW YORK", "2012", 1500, "city",
              [("taxi", 3), ("glider", 3), ("shock", 3)],
              dict(sky_top=(30, 45, 75), sky_bot=(225, 130, 70), ground=(40, 40, 45),
                   road_a=(66, 66, 74), road_b=(58, 58, 66), line=(240, 200, 40),
                   curb_a=(205, 60, 50), curb_b=(235, 235, 235), side_a=(124, 124, 130),
                   side_b=(112, 112, 118), fog=(200, 120, 80), pit_rim=(255, 150, 40)),
              scenes=(('smash', 0.33), ('chase:hulk', 0.68))),
        Level(2, "THE TIME VARIANCE AUTHORITY", "1973-ish", 1800, "tva",
              [("minute", 3), ("cabinet", 3), ("jetski", 3)],
              dict(sky_top=(70, 35, 20), sky_bot=(235, 140, 50), ground=(90, 45, 20),
                   road_a=(204, 112, 42), road_b=(188, 100, 36), line=(250, 230, 170),
                   curb_a=(110, 60, 25), curb_b=(240, 200, 120), side_a=(124, 72, 36),
                   side_b=(108, 62, 30), fog=(230, 150, 70), pit_rim=(255, 230, 120)),
              scenes=(('jetskis', 0.33), ('chase:clock', 0.68))),
        Level(3, "THE VOID AT THE END OF TIME", "THE END", 2100, "void",
              [("gator", 3), ("ship", 2), ("smoke", 3)],
              dict(sky_top=(25, 10, 45), sky_bot=(150, 60, 170), ground=(30, 15, 45),
                   road_a=(74, 44, 106), road_b=(64, 36, 94), line=(190, 120, 255),
                   curb_a=(130, 60, 200), curb_b=(60, 200, 140), side_a=(52, 30, 72),
                   side_b=(44, 26, 62), fog=(120, 50, 150), pit_rim=(200, 100, 255)),
              scenes=(('stampede', 0.28), ('stampede', 0.6))),
    ]
    for lvl, boss in zip(levels, ('hulk', 'thor', 'alioth')):
        lvl.boss = boss
    return levels


# --------------------------------------------------------------------------
# Tesseract
# --------------------------------------------------------------------------
class Tesseract:
    high = False                 # high collectibles can only be grabbed while jumping

    def __init__(self, lane_x, dist):
        self.lane_x, self.dist, self.prev_dist = lane_x, dist, dist
        self.t = random.random() * 6
        self.collected = False

    def update(self, dt, speed):
        self.prev_dist = self.dist
        self.dist -= speed * dt
        self.t += dt

    @property
    def dead(self):
        return self.collected or self.dist < -6

    def draw(self, surf, lm):
        if self.dist > FAR:
            return
        sx, sy, s = lm.project(self.lane_x, self.dist)
        bob = 46 + math.sin(self.t * 4) * 7
        draw_cube(surf, sx, sy - bob * s, 20 * s, spin=self.t * 3)
        if math.sin(self.t * 7) > 0.8:
            r = 6 * s
            c = (sx + 14 * s, sy - (bob + 14) * s)
            pygame.draw.line(surf, WHITE, (c[0] - r, c[1]), (c[0] + r, c[1]), 1)
            pygame.draw.line(surf, WHITE, (c[0], c[1] - r), (c[0], c[1] + r), 1)


def draw_apple(surf, cx, cy, r, t=0.0):
    """One of Idunn's golden apples."""
    blit_glow(surf, cx, cy, r * 2.4, (255, 210, 60), 140)
    pygame.draw.circle(surf, (205, 140, 20), (int(cx), int(cy + r * 0.08)), max(2, int(r)))
    pygame.draw.circle(surf, (255, 205, 45), (int(cx - r * 0.06), int(cy - r * 0.06)), max(2, int(r * 0.9)))
    pygame.draw.ellipse(surf, (255, 250, 190), (cx - r * 0.55, cy - r * 0.62, r * 0.45 + 1, r * 0.3 + 1))
    pygame.draw.line(surf, (110, 70, 20), (cx, cy - r * 0.8), (cx + r * 0.1, cy - r * 1.3), max(1, int(r * 0.14)))
    pygame.draw.ellipse(surf, (60, 190, 80), (cx + r * 0.1, cy - r * 1.35, r * 0.7 + 1, r * 0.35 + 1))
    if math.sin(t * 6) > 0.7:
        pygame.draw.line(surf, WHITE, (cx + r * 0.9 - 4, cy - r * 0.5), (cx + r * 0.9 + 4, cy - r * 0.5), 2)
        pygame.draw.line(surf, WHITE, (cx + r * 0.9, cy - r * 0.5 - 4), (cx + r * 0.9, cy - r * 0.5 + 4), 2)


class GoldenApple(Tesseract):
    """Floats high above the road: jump to grab it."""
    high = True

    def draw(self, surf, lm):
        if self.dist > FAR:
            return
        sx, sy, s = lm.project(self.lane_x, self.dist)
        pellipse(surf, (0, 0, 0), (sx, sy), s, -16, -5, 32, 10)
        draw_apple(surf, sx, sy - (132 + math.sin(self.t * 4) * 8) * s, 19 * s, self.t)


# --------------------------------------------------------------------------
# Obstacle
# --------------------------------------------------------------------------
#  kind: low   -> jump over it         high  -> slide under it
#        heavy -> change lanes!        pit   -> jump the gap (or fall)
SPECS = {
    "taxi":    dict(kind="heavy", width=0.9, depth=2.4, clear=0),
    "glider":  dict(kind="high",  width=0.9, depth=1.4, clear=0),
    "shock":   dict(kind="low",   width=1.0, depth=1.6, clear=46),
    "minute":  dict(kind="heavy", width=0.9, depth=1.2, clear=0),
    "cabinet": dict(kind="heavy", width=0.8, depth=1.4, clear=0),
    "jetski":  dict(kind="low",   width=1.0, depth=1.4, clear=58),
    "gator":   dict(kind="low",   width=0.9, depth=1.8, clear=54),
    "ship":    dict(kind="heavy", width=1.4, depth=2.8, clear=0),
    "smoke":   dict(kind="high",  width=1.0, depth=1.6, clear=0),
    "fist":    dict(kind="heavy", width=0.95, depth=2.2, clear=0),
    "mjolnir": dict(kind="high",  width=0.9,  depth=1.2, clear=0),
    "pit":     dict(kind="pit",   width=0.9, depth=5.0, clear=26),
}


class Obstacle:
    def __init__(self, key, lane_x, dist, vx=0.0):
        self.key = key
        spec = SPECS[key]
        self.kind, self.width, self.depth, self.clear = spec["kind"], spec["width"], spec["depth"], spec["clear"]
        self.lane_x, self.dist, self.prev_dist, self.vx = lane_x, dist, dist, vx
        self.t = random.random() * 10
        self.hit = False
        self.blasted = False
        self.bt = 0.0
        self.bdir = 1
        self.landed = False
        self.launched = True              # Mjolnir: False while only the red warning shows
        self.target = lane_x
        self.dirn = 1
        self.announced = False

    @classmethod
    def mjolnir(cls, target_lane_x, dist):
        o = cls("mjolnir", 99.0, dist)          # parked off-screen until it is thrown
        o.target, o.launched, o.dirn = target_lane_x, False, random.choice((-1, 1))
        return o

    @property
    def ground(self):
        """Drawn flat on the road (pits and the Mjolnir warning)."""
        return self.kind == "pit" or (self.key == "mjolnir" and not self.launched)

    @property
    def blastable(self):
        return self.kind != "pit" and (self.key != "mjolnir" or self.launched)

    def update(self, dt, speed):
        self.prev_dist = self.dist
        self.dist -= speed * dt
        self.t += dt
        if self.key == "mjolnir" and not self.launched and self.dist <= MJ_LAUNCH:
            self.launched = True                                    # thrown: arrives in the target lane as it reaches Loki
            self.vx = self.dirn * MJ_VX
            self.lane_x = self.target - self.vx * max(self.dist, 0.2) / max(speed, 1.0)
        self.lane_x += self.vx * dt
        if self.blasted:
            self.bt += dt
            self.lane_x += self.bdir * 9 * dt

    @property
    def dead(self):
        return self.dist < -7 or (self.blasted and self.bt > 0.9)

    @property
    def active(self):
        return not (self.blasted or self.hit)

    def blast(self, direction):
        self.blasted = True
        self.bdir = 1 if direction >= 0 else -1
        self.vx = 0

    # -- drawing ----------------------------------------------------------
    def _draw_warning(self, surf, lm):
        """Red flashing lane: Thor is about to throw Mjolnir across it."""
        if self.dist > MJ_WARN_D or self.dist < MJ_LAUNCH - 6:
            return
        pts = []
        for lx, d in ((self.target - 0.5, self.dist - 2.5), (self.target + 0.5, self.dist - 2.5),
                      (self.target + 0.5, self.dist + 2.5), (self.target - 0.5, self.dist + 2.5)):
            sx, sy, _ = lm.project(lx, d)
            pts.append((sx, sy))
        on = int(self.t * 9) % 2 == 0
        pygame.draw.polygon(surf, (255, 40, 40) if on else (150, 10, 20), pts)
        pygame.draw.polygon(surf, WHITE, pts, 2)
        sx, sy, s = lm.project(self.target, self.dist)
        if s > 0.12:
            ppoly(surf, (255, 220, 40), (sx, sy), s, [(0, -112), (-34, -52), (34, -52)])
            pline(surf, BLACK, (sx, sy), s, 0, -98, 0, -76, 7)
            pcirc(surf, BLACK, (sx, sy), s, 0, -64, 4)

    def draw_ground(self, surf, lm, level):
        """Pits are painted flat on the road."""
        if self.key == "mjolnir":
            self._draw_warning(surf, lm)
            return
        near, far = self.dist - self.depth / 2, self.dist + self.depth / 2
        if far < -5 or near > FAR:
            return
        near = max(near, -4.5)
        far = min(far, FAR)
        x0, x1 = self.lane_x - 0.47, self.lane_x + 0.47
        pts = []
        for lx, d in ((x0, near), (x1, near), (x1, far), (x0, far)):
            sx, sy, _ = lm.project(lx, d)
            pts.append((sx, sy))
        rim = level.pal["pit_rim"]
        pygame.draw.polygon(surf, rim, pts)
        inner = []
        for lx, d in ((x0 + 0.06, near + 0.35), (x1 - 0.06, near + 0.35), (x1 - 0.06, far - 0.3), (x0 + 0.06, far - 0.3)):
            sx, sy, _ = lm.project(lx, d)
            inner.append((sx, sy))
        pygame.draw.polygon(surf, (6, 2, 14), inner)
        mid = lm.project(self.lane_x, self.dist)
        if mid[2] > 0.15:
            for j in range(5):
                px = mid[0] + (hrand(j, 1, int(self.t * 3)) - 0.5) * 90 * mid[2]
                py = mid[1] - hrand(j, 2, int(self.t * 3)) * 18 * mid[2]
                pygame.draw.circle(surf, mix(rim, BLACK, 0.4), (int(px), int(py)), max(1, int(2 * mid[2])))

    def draw(self, surf, lm):
        if self.dist > FAR:
            return
        sx, sy, s = lm.project(self.lane_x, self.dist)
        if s < 0.03:
            return
        a = [sx, sy]
        if self.blasted:
            a[1] -= 520 * self.bt * s
        pellipse(surf, (0, 0, 0), (sx, sy), s, -self.width * 55, -7, self.width * 110, 14)   # shadow
        getattr(self, "_draw_" + self.key)(surf, a, s, self.t)

    def _draw_fist(self, surf, a, s, t):
        lift = max(0.0, self.dist - 11) * 38
        if lift > 0:                                                  # flashing warning shadow
            warn = clamp(1 - (self.dist - 11) / 45, 0.15, 1)
            col = (255, 80, 60) if int(t * 10) % 2 else (120, 255, 90)
            pellipse(surf, col, a, s, -70 * warn, -14 * warn, 140 * warn, 28 * warn, 5)
        b = (a[0], a[1] - lift * s)
        prect(surf, (70, 150, 55), b, s, -34, -1500, 68, 1380)           # the arm, from off-screen
        prect(surf, (50, 120, 40), b, s, 12, -1500, 22, 1380)
        ppoly(surf, (110, 60, 150), b, s, [(-44, -150), (44, -150), (52, -118), (36, -126), (20, -116), (0, -126), (-20, -116), (-36, -126), (-52, -118)])
        prect(surf, (90, 185, 70), b, s, -62, -120, 124, 104)
        prect(surf, (60, 140, 50), b, s, -62, -36, 124, 20)
        for i in range(4):                                             # knuckles
            pcirc(surf, (110, 205, 90), b, s, -46 + i * 31, -14, 17)
            pcirc(surf, (70, 150, 55), b, s, -46 + i * 31, -14, 17 * 0.4)
        pline(surf, (50, 110, 40), b, s, -62, -78, 62, -78, 3)
        if lift == 0:                                                  # impact cracks and dust
            for dx, dy in ((-70, -6), (60, -10), (-30, -2), (34, 0)):
                pline(surf, (20, 20, 20), a, s, dx, dy, dx * 2.1, dy - 14, 3)
            for j in range(4):
                pcirc(surf, (170, 170, 160), a, s, -60 + j * 40, -20 - (t * 60 + j * 20) % 40, 12)

    def _draw_mjolnir(self, surf, a, s, t):
        d = 1 if self.vx >= 0 else -1
        c = (a[0], a[1] - 78 * s)
        for j in range(5):                                             # lightning trail
            pline(surf, (170, 220, 255), (c[0], c[1]), s, -d * (60 + j * 26), (j % 2 * 2 - 1) * 10, -d * (90 + j * 26), -(j % 2 * 2 - 1) * 12, 3)
        blit_glow(surf, c[0], c[1], 70 * s + 8, (150, 200, 255), 160)
        ang = t * 16 * d

        def rot(px, py):
            return (c[0] + (px * math.cos(ang) - py * math.sin(ang)) * s, c[1] + (px * math.sin(ang) + py * math.cos(ang)) * s)
        handle = [rot(-6, -2), rot(6, -2), rot(6, 70), rot(-6, 70)]
        head = [rot(-42, -40), rot(42, -40), rot(42, 4), rot(-42, 4)]
        shade = [rot(-42, -18), rot(42, -18), rot(42, 4), rot(-42, 4)]
        pygame.draw.polygon(surf, (110, 70, 40), handle)
        pygame.draw.polygon(surf, (190, 196, 210), head)
        pygame.draw.polygon(surf, (120, 126, 142), shade)
        pygame.draw.polygon(surf, WHITE, head, 2)
        pygame.draw.polygon(surf, (210, 40, 40), [rot(-6, 44), rot(6, 44), rot(18, 74), rot(-18, 74)])   # red cloth

    def _draw_taxi(self, surf, a, s, t):
        prect(surf, (20, 20, 22), a, s, -58, -18, 26, 20)
        prect(surf, (20, 20, 22), a, s, 32, -18, 26, 20)
        prect(surf, (255, 205, 20), a, s, -70, -52, 140, 38)
        prect(surf, (240, 188, 10), a, s, -44, -84, 88, 34)
        prect(surf, (70, 100, 130), a, s, -38, -80, 36, 26)
        prect(surf, (70, 100, 130), a, s, 2, -80, 36, 26)
        pline(surf, WHITE, a, s, -30, -78, -12, -56, 2)
        pline(surf, WHITE, a, s, 10, -78, 28, -60, 2)
        prect(surf, WHITE, a, s, -12, -92, 24, 9)
        for i in range(14):
            if i % 2 == 0:
                prect(surf, BLACK, a, s, -70 + i * 10, -34, 10, 7)
        prect(surf, (255, 250, 170), a, s, -64, -48, 18, 10)
        prect(surf, (255, 250, 170), a, s, 46, -48, 18, 10)
        ppoly(surf, (150, 30, 30), a, s, [(-70, -14), (70, -14), (60, -6), (-60, -6)])   # bent bumper
        for j in range(3):                                                              # smoke
            r = 8 + j * 4
            pcirc(surf, (90 + j * 20,) * 3, a, s, -20 + j * 18, -96 - ((t * 40 + j * 30) % 60), r)

    def _draw_glider(self, surf, a, s, t):
        bob = math.sin(t * 5) * 4
        pellipse(surf, (60, 40, 90), a, s, -50, -12, 100, 12)
        b = (a[0], a[1] + bob * s)
        ppoly(surf, (60, 50, 85), b, s, [(-80, -118), (0, -150), (80, -118), (44, -80), (-44, -80)])
        ppoly(surf, (36, 30, 58), b, s, [(-80, -118), (0, -150), (0, -100), (-44, -80)])
        ppoly(surf, (100, 90, 130), b, s, [(-34, -122), (0, -138), (34, -122), (0, -104)])
        for ex in (-40, 40):
            pcirc(surf, CYAN, b, s, ex, -84, 9)
            pcirc(surf, WHITE, b, s, ex, -84, 4)
        pline(surf, (255, 220, 80), b, s, -60, -110, -76, -92, 2)                        # sparks
        pline(surf, (255, 220, 80), b, s, 62, -108, 78, -126, 2)

    def _draw_shock(self, surf, a, s, t):
        for i in range(3):
            ph = (t * 1.6 + i / 3.0) % 1.0
            r = 30 + ph * 60
            col = mix((120, 255, 100), (20, 90, 30), ph)
            pellipse(surf, col, a, s, -r, -r * 0.34, r * 2, r * 0.68, 8)
        pellipse(surf, (170, 255, 150), a, s, -30, -10, 60, 20)
        for dx in (-34, 8, 30):
            ppoly(surf, (90, 90, 90), a, s, [(dx, 0), (dx + 8, -22), (dx + 16, 0)])        # rubble
        pline(surf, (140, 255, 120), a, s, 0, -4, 0, -40 - 10 * math.sin(t * 8), 3)

    def _draw_minute(self, surf, a, s, t):
        prect(surf, (60, 40, 30), a, s, -18, -26, 16, 26)
        prect(surf, (60, 40, 30), a, s, 2, -26, 16, 26)
        prect(surf, (255, 140, 30), a, s, -26, -86, 52, 64)
        prect(surf, (200, 100, 20), a, s, -26, -48, 52, 6)
        prect(surf, (255, 150, 40), a, s, -15, -116, 30, 32)
        prect(surf, (20, 20, 20), a, s, -11, -106, 22, 11)
        prect(surf, (255, 140, 30), a, s, -40, -84, 14, 38)
        ang = math.sin(t * 4.5) * 1.15
        px, py = 33, -72
        ex, ey = px + math.sin(ang) * 82, py - math.cos(ang) * 82
        pline(surf, (255, 120, 20), a, s, px, py, ex, ey, 11)
        pline(surf, (255, 240, 170), a, s, px, py, ex, ey, 5)
        blit_glow(surf, a[0] + ex * s, a[1] + ey * s, 26 * s + 4, (255, 170, 40), 190)

    def _draw_cabinet(self, surf, a, s, t):
        lift = max(0.0, self.dist - 10) * 26
        b = (a[0], a[1] - lift * s)
        prect(surf, (110, 114, 110), b, s, -34, -118, 68, 118)
        prect(surf, (156, 162, 154), b, s, -30, -114, 60, 110)
        for i in range(3):
            y = -112 + i * 36
            prect(surf, (126, 132, 124), b, s, -26, y, 52, 32)
            prect(surf, (230, 230, 220), b, s, -14, y + 6, 28, 8)
            prect(surf, (60, 60, 60), b, s, -9, y + 18, 18, 5)
        if lift == 0:
            for j in range(4):                                                         # papers
                px = -50 + j * 32 + math.sin(t * 3 + j) * 6
                prect(surf, WHITE, a, s, px, -20 - ((t * 30 + j * 20) % 70), 14, 18)

    def _draw_jetski(self, surf, a, s, t):
        d = 1 if self.vx >= 0 else -1
        for j in range(4):                                                             # speed lines
            pline(surf, (255, 230, 200), a, s, -d * (70 + j * 14), -20 - j * 8, -d * (110 + j * 20), -20 - j * 8, 3)
        ppoly(surf, (240, 240, 245), a, s, [(-d * 66, -20), (d * 66, -20), (d * 82, -36), (d * 40, -48), (-d * 52, -46)])
        ppoly(surf, (30, 110, 200), a, s, [(-d * 66, -20), (d * 66, -20), (d * 74, -28), (-d * 60, -30)])
        pline(surf, (40, 40, 40), a, s, d * 30, -48, d * 44, -78, 5)                     # handlebars
        prect(surf, (176, 130, 80), a, s, -d * 14 - 14, -90, 28, 44)                    # tan jacket
        prect(surf, (230, 190, 150), a, s, -d * 14 - 9, -108, 18, 18)
        prect(surf, (200, 170, 90), a, s, -d * 14 - 10, -112, 20, 8)                    # sandy hair
        for j in range(5):                                                             # sparks / spray
            pcirc(surf, (255, 200, 80), a, s, -d * (60 + hrand(j, int(t * 12)) * 40), -8 - hrand(j, 5, int(t * 12)) * 22, 3)

    def _draw_gator(self, surf, a, s, t):
        gap = abs(math.sin(t * 6)) * 24
        prect(surf, (20, 100, 55), a, s, -50, -40, 100, 40)
        prect(surf, (34, 140, 70), a, s, -44, -44 - gap * 0.5, 88, 18)                   # upper jaw
        prect(surf, (110, 20, 40), a, s, -40, -30 - gap, 80, 20 + gap)                   # mouth
        for i in range(7):                                                             # teeth
            x = -38 + i * 12
            ppoly(surf, WHITE, a, s, [(x, -30 - gap), (x + 10, -30 - gap), (x + 5, -16 - gap)])
            ppoly(surf, WHITE, a, s, [(x, -4), (x + 10, -4), (x + 5, -14)])
        for ex in (-32, 32):
            pcirc(surf, (255, 235, 60), a, s, ex, -56 - gap * 0.5, 11)
            pcirc(surf, BLACK, a, s, ex, -56 - gap * 0.5, 4)
        for hx in (-1, 1):                                                             # tiny golden horns
            ppoly(surf, GOLD, a, s, [(hx * 6, -62 - gap * 0.5), (hx * 20, -88 - gap * 0.5), (hx * 14, -60 - gap * 0.5)])
        for i in range(4):
            ppoly(surf, (14, 70, 40), a, s, [(-40 + i * 24, -40), (-30 + i * 24, -54 - gap * 0.2), (-20 + i * 24, -40)])

    def _draw_ship(self, surf, a, s, t):
        ppoly(surf, (92, 54, 30), a, s, [(-122, -62), (122, -62), (96, 0), (-96, 0)])
        ppoly(surf, (60, 34, 20), a, s, [(-122, -62), (122, -62), (118, -50), (-118, -50)])
        for i in range(5):
            pline(surf, (50, 28, 16), a, s, -90 + i * 45, -50, -80 + i * 42, -4, 2)
        ppoly(surf, (20, 8, 28), a, s, [(-20, -62), (-4, -40), (-12, -20), (16, -48), (30, -62)])  # smashed hole
        pline(surf, (86, 50, 28), a, s, 10, -62, 30, -138, 9)                            # snapped mast
        ppoly(surf, (200, 195, 180), a, s, [(30, -132), (84, -118), (60, -96), (78, -76), (28, -80)])
        pline(surf, (86, 50, 28), a, s, -40, -62, -66, -104, 7)
        pcirc(surf, WHITE, a, s, 52, -110, 7)
        pcirc(surf, BLACK, a, s, 49, -111, 2)
        pcirc(surf, BLACK, a, s, 56, -111, 2)
        blit_glow(surf, a[0], a[1] - 40 * s, 70 * s + 4, (170, 80, 255), 90)

    _smoke_frames = {}

    @classmethod
    def _smoke_frame(cls, i):
        img = cls._smoke_frames.get(i)
        if img is None:
            w, h = 240, 190
            img = pygame.Surface((w, h), pygame.SRCALPHA)
            c = (w // 2, h // 2)
            t = i * 0.35
            for j in range(9):
                ang = j * 0.7 + t * 1.3
                r = 34 + 10 * math.sin(t * 3 + j)
                px = c[0] + math.cos(ang) * 62 * (0.5 + 0.5 * hrand(j, 3))
                py = c[1] + math.sin(ang) * 40 * (0.5 + 0.5 * hrand(j, 4))
                pygame.draw.circle(img, (110 + j * 8, 40, 160, 170), (int(px), int(py)), int(r))
            pygame.draw.circle(img, (30, 6, 50, 220), c, 34)
            for ex in (-12, 12):
                pygame.draw.ellipse(img, (255, 240, 255, 255), (c[0] + ex - 5, c[1] - 5, 11, 7))
            cls._smoke_frames[i] = img
        return img

    def _draw_smoke(self, surf, a, s, t):
        pellipse(surf, (40, 10, 60), a, s, -60, -12, 120, 16)
        img = self._smoke_frame(int(t * 10) % 18)
        w, h = max(8, int(240 * s)), max(8, int(190 * s))
        img = pygame.transform.scale(img, (w, h))
        surf.blit(img, (a[0] - w / 2, a[1] - 130 * s - h / 2))


# --------------------------------------------------------------------------
# Particles and banners
# --------------------------------------------------------------------------
class Particle:
    def __init__(self, x, y, vx, vy, life, color, size=4, gravity=0.0, kind="spark", text=None):
        self.x, self.y, self.vx, self.vy = x, y, vx, vy
        self.life = self.max_life = life
        self.color, self.size, self.gravity, self.kind, self.text = color, size, gravity, kind, text
        self.spin = random.random() * 6

    def update(self, dt):
        self.life -= dt
        self.vy += self.gravity * dt
        self.x += self.vx * dt
        self.y += self.vy * dt
        self.spin += dt * 8

    def draw(self, surf):
        f = clamp(self.life / self.max_life, 0, 1)
        if self.kind == "cube":
            draw_cube(surf, self.x, self.y, self.size, self.spin, glow=False)
        elif self.kind == "text":
            draw_text(surf, self.text, 30, self.color, (self.x, self.y), ow=2)
        elif self.kind == "confetti":
            w = max(1, int(abs(math.cos(self.spin)) * self.size))
            pygame.draw.rect(surf, self.color, (self.x, self.y, w, self.size))
        else:
            r = max(1, int(self.size * f))
            pygame.draw.circle(surf, mix(BLACK, self.color, 0.4 + 0.6 * f), (int(self.x), int(self.y)), r)


class Banner:
    def __init__(self, text, color, dur=2.4):
        self.text, self.color, self.dur, self.t = text, color, dur, 0.0

    @property
    def dead(self):
        return self.t >= self.dur

    def draw(self, surf, y):
        t = self.t
        fade = clamp(min(t / 0.12, (self.dur - t) / 0.3), 0, 1)
        img = text_surf(self.text, 46, self.color, BLACK, 3)
        k = min(1.0, (W - 90) / img.get_width()) * (1 + 0.22 * max(0.0, 1 - t / 0.16))
        if abs(k - 1) > 0.01:
            img = pygame.transform.smoothscale(img, (max(2, int(img.get_width() * k)), max(2, int(img.get_height() * k))))
        rect = pygame.Rect(0, 0, img.get_width() + 40, img.get_height() + 8)
        rect.center = (W // 2, y)
        fill_alpha(surf, rect, (8, 10, 26), 225 * fade)
        if fade > 0.6:
            pygame.draw.rect(surf, self.color, rect, 3)
        img.set_alpha(int(255 * fade))
        surf.blit(img, img.get_rect(center=rect.center))


# --------------------------------------------------------------------------
# Player
# --------------------------------------------------------------------------
class Player:
    SPRITE_K = 0.85

    def __init__(self, lm):
        self.lm = lm
        self._sprites = {}
        self._ghosts_img = {}
        self.reset()

    def reset(self):
        self.lane = 1
        self.x = 0.0
        self.jump_h = 0.0
        self.vy = 0.0
        self.slide_t = 0.0
        self.pending_slide = False
        self.tumble = 0.0
        self.fall = 0.0
        self.invuln = 0.0
        self.aura = 0.0
        self.run_phase = 0.0
        self.ghosts = []
        self.ghost_t = 0.0
        self.time = 0.0
        self.front = False        # True: Loki faces the camera (chase scenes)

    # -- state ----------------------------------------------------------
    @property
    def sliding(self):
        return self.slide_t > 0

    @property
    def can_act(self):
        return self.tumble <= 0 and self.fall <= 0

    def vulnerable(self):
        return self.tumble <= 0 and self.fall <= 0 and self.invuln <= 0 and self.aura <= 0

    def move(self, d):
        if not self.can_act:
            return False
        new = self.lm.clamp_lane(self.lane + d)
        if new != self.lane:
            self.lane = new
            self.ghosts.append([self.x, self.jump_h, 0.4, self.pose(), int(self.run_phase * 2) % 12])
            return True
        return False

    def jump(self):
        if self.can_act and self.jump_h <= 0 and not self.sliding:
            self.vy = JUMP_V
            return True
        return False

    def slide(self):
        if not self.can_act:
            return False
        if self.jump_h > 0:
            self.vy = min(self.vy, -700)       # slam down, slide on landing
            self.pending_slide = True
        elif self.slide_t <= 0:
            self.slide_t = SLIDE_TIME
            return True
        return False

    def start_tumble(self):
        self.tumble = TUMBLE_TIME
        self.invuln = INVULN_TIME
        self.slide_t = 0
        self.vy = 0
        self.jump_h = 0

    def start_fall(self):
        self.fall = FALL_TIME
        self.slide_t = 0
        self.vy = 0
        self.jump_h = 0

    def pose(self):
        if self.sliding:
            return "slide"
        if self.jump_h > 0:
            return "jump"
        return "run"

    # -- update ---------------------------------------------------------
    def update(self, dt, speed):
        self.time += dt
        self.run_phase += dt * (6 + speed * 0.25)
        target = self.lm.lane_x(self.lane)
        if abs(self.x - target) > 0.01:
            self.ghost_t -= dt
            if self.ghost_t <= 0:
                self.ghosts.append([self.x, self.jump_h, 0.35, self.pose(), int(self.run_phase * 2) % 12])
                self.ghost_t = 0.03
            diff = target - self.x
            step = max(LANE_SPEED * 0.35 * dt, abs(diff) * (1 - math.exp(-22 * dt)))
            self.x += clamp(diff, -step, step)
        else:
            self.x = target
        if self.jump_h > 0 or self.vy > 0:
            self.vy -= GRAVITY * dt
            self.jump_h += self.vy * dt
            if self.jump_h <= 0:
                self.jump_h, self.vy = 0.0, 0.0
                if self.pending_slide:
                    self.pending_slide = False
                    self.slide_t = SLIDE_TIME
        self.slide_t = max(0.0, self.slide_t - dt)
        self.tumble = max(0.0, self.tumble - dt)
        self.invuln = max(0.0, self.invuln - dt)
        self.aura = max(0.0, self.aura - dt)
        if self.fall > 0:
            self.fall -= dt
            if self.fall <= 0:
                self.fall = 0.0
                self.invuln = INVULN_TIME
        for g in self.ghosts:
            g[2] -= dt
        self.ghosts = [g for g in self.ghosts if g[2] > 0]

    # -- drawing --------------------------------------------------------
    def _render(self, pose, frame):
        """Back view of Loki (cape, helmet horns), feet at bottom centre."""
        if self.front:
            return self._render_front(pose, frame)
        surf = pygame.Surface((130, 190), pygame.SRCALPHA)
        fx, fy = 65, 186
        ph = frame / 12.0 * math.pi * 2
        legs_up = (0, 0)
        if pose == "run":
            legs_up = (max(0, math.sin(ph)) * 14, max(0, -math.sin(ph)) * 14)
        elif pose == "jump":
            legs_up = (16, 6)
        swing = math.sin(ph) * 8 if pose == "run" else -10
        # legs + boots
        for i, lift in enumerate(legs_up):
            lx = fx - 22 + i * 20
            pygame.draw.rect(surf, DKGREEN, (lx, fy - 38 - lift, 16, 30))
            pygame.draw.rect(surf, (25, 25, 25), (lx - 1, fy - 12 - lift, 18, 12))
            pygame.draw.rect(surf, GOLD, (lx - 1, fy - 18 - lift, 18, 4))
        # cape (back of the armour) and torso
        pygame.draw.polygon(surf, (14, 88, 44), [(fx - 28, fy - 100), (fx + 28, fy - 100), (fx + 38, fy - 24), (fx - 38, fy - 24)])
        pygame.draw.polygon(surf, (28, 130, 62), [(fx - 20, fy - 100), (fx + 20, fy - 100), (fx + 26, fy - 24), (fx - 26, fy - 24)])
        pygame.draw.line(surf, GOLD, (fx - 38, fy - 24), (fx + 38, fy - 24), 4)
        pygame.draw.line(surf, GOLD, (fx - 28, fy - 100), (fx - 38, fy - 24), 3)
        pygame.draw.line(surf, GOLD, (fx + 28, fy - 100), (fx + 38, fy - 24), 3)
        pygame.draw.rect(surf, GREEN, (fx - 24, fy - 98, 48, 20))                  # shoulders
        pygame.draw.rect(surf, GOLD, (fx - 24, fy - 98, 48, 5))
        pygame.draw.circle(surf, GOLD, (fx, fy - 84), 6)                           # clasp
        pygame.draw.circle(surf, (60, 230, 120), (fx, fy - 84), 3)
        # arms
        for sgn in (-1, 1):
            ay = fy - 96 + (swing * sgn if pose == "run" else (-30 if pose == "jump" else 0))
            pygame.draw.rect(surf, GREEN, (fx + sgn * 34 - 6, ay, 12, 38))
            pygame.draw.rect(surf, GOLD, (fx + sgn * 34 - 7, ay + 28, 14, 7))
            pygame.draw.rect(surf, (225, 190, 150), (fx + sgn * 34 - 5, ay + 35, 10, 8))
        # head: skin + black hair, then the great golden horned helmet
        pygame.draw.rect(surf, (30, 28, 34), (fx - 14, fy - 128, 28, 30))
        pygame.draw.rect(surf, (225, 190, 150), (fx - 8, fy - 100, 16, 6))
        pygame.draw.rect(surf, GOLD, (fx - 17, fy - 120, 34, 11))
        pygame.draw.rect(surf, (200, 150, 20), (fx - 17, fy - 112, 34, 3))
        for sgn in (-1, 1):                                                        # V-shaped horns
            pygame.draw.polygon(surf, GOLD, [(fx + sgn * 12, fy - 118), (fx + sgn * 44, fy - 184),
                                             (fx + sgn * 33, fy - 186), (fx + sgn * 5, fy - 128)])
            pygame.draw.polygon(surf, (200, 150, 20), [(fx + sgn * 12, fy - 118), (fx + sgn * 44, fy - 184),
                                                       (fx + sgn * 40, fy - 176), (fx + sgn * 14, fy - 122)])
        pygame.draw.polygon(surf, GOLD, [(fx - 5, fy - 126), (fx + 5, fy - 126), (fx, fy - 146)])
        return surf

    def _render_front(self, pose, frame):
        """Front view: a terrified, screaming Loki running towards the camera."""
        surf = pygame.Surface((130, 190), pygame.SRCALPHA)
        fx, fy = 65, 186
        ph = frame / 12.0 * math.pi * 2
        if pose == "run":
            legs_up = (max(0, math.sin(ph)) * 14, max(0, -math.sin(ph)) * 14)
        elif pose == "jump":
            legs_up = (16, 6)
        else:
            legs_up = (0, 0)
        swing = math.sin(ph) * 12 if pose == "run" else 0
        pygame.draw.polygon(surf, (14, 88, 44), [(fx - 32, fy - 100), (fx + 32, fy - 100), (fx + 44, fy - 24), (fx - 44, fy - 24)])
        for i, lift in enumerate(legs_up):
            lx = fx - 22 + i * 20
            pygame.draw.rect(surf, DKGREEN, (lx, fy - 38 - lift, 16, 30))
            pygame.draw.rect(surf, (25, 25, 25), (lx - 1, fy - 12 - lift, 18, 12))
            pygame.draw.rect(surf, GOLD, (lx - 1, fy - 18 - lift, 18, 4))
        pygame.draw.rect(surf, GREEN, (fx - 24, fy - 100, 48, 66))
        pygame.draw.polygon(surf, GOLD, [(fx - 20, fy - 98), (fx - 7, fy - 98), (fx, fy - 76), (fx + 7, fy - 98), (fx + 20, fy - 98), (fx, fy - 52)])
        pygame.draw.rect(surf, GOLD, (fx - 24, fy - 44, 48, 6))
        pygame.draw.circle(surf, (60, 230, 120), (fx, fy - 41), 4)
        for sgn in (-1, 1):                                            # arms flail
            if pose == "jump":
                ay, ah = fy - 128, 40
            else:
                ay, ah = fy - 98 + swing * sgn, 38
            pygame.draw.rect(surf, GREEN, (fx + sgn * 34 - 6, ay, 12, ah))
            pygame.draw.rect(surf, GOLD, (fx + sgn * 34 - 7, ay + ah - 10, 14, 6))
            pygame.draw.rect(surf, (225, 190, 150), (fx + sgn * 34 - 5, ay - 6 if pose == "jump" else ay + ah - 4, 10, 8))
        pygame.draw.rect(surf, (225, 190, 150), (fx - 14, fy - 128, 28, 30))      # face
        pygame.draw.rect(surf, (30, 28, 34), (fx - 15, fy - 132, 30, 9))
        pygame.draw.rect(surf, (30, 28, 34), (fx - 15, fy - 126, 4, 18))
        pygame.draw.rect(surf, (30, 28, 34), (fx + 11, fy - 126, 4, 18))
        for ex in (-9, 2):                                              # huge panicked eyes
            pygame.draw.rect(surf, WHITE, (fx + ex, fy - 119, 8, 10))
            pygame.draw.rect(surf, (20, 150, 70), (fx + ex + 2, fy - 116, 4, 5))
        pygame.draw.line(surf, (30, 28, 34), (fx - 11, fy - 123), (fx - 3, fy - 121), 2)
        pygame.draw.line(surf, (30, 28, 34), (fx + 11, fy - 123), (fx + 3, fy - 121), 2)
        pygame.draw.ellipse(surf, (90, 20, 30), (fx - 5, fy - 107, 10, 10))      # screaming mouth
        pygame.draw.ellipse(surf, (230, 90, 100), (fx - 3, fy - 102, 6, 4))
        pygame.draw.rect(surf, GOLD, (fx - 17, fy - 130, 34, 8))
        for sgn in (-1, 1):
            pygame.draw.polygon(surf, GOLD, [(fx + sgn * 12, fy - 126), (fx + sgn * 44, fy - 188),
                                             (fx + sgn * 33, fy - 190), (fx + sgn * 5, fy - 134)])
            pygame.draw.polygon(surf, (200, 150, 20), [(fx + sgn * 12, fy - 126), (fx + sgn * 44, fy - 188),
                                                       (fx + sgn * 40, fy - 180), (fx + sgn * 14, fy - 130)])
        pygame.draw.polygon(surf, GOLD, [(fx - 5, fy - 132), (fx + 5, fy - 132), (fx, fy - 150)])
        return surf

    def sprite(self, pose, frame):
        key = (self.front, pose, frame if pose == "run" else 0)
        img = self._sprites.get(key)
        if img is None:
            base = self._render("run" if pose == "slide" else pose, key[2])
            k = self.SPRITE_K
            if pose == "slide":
                base = pygame.transform.rotozoom(pygame.transform.smoothscale(base, (int(130 * 1.35), int(190 * 0.55))), 10, 1.0)
                img = pygame.transform.rotozoom(base, 0, k)
            else:
                img = pygame.transform.rotozoom(base, 0, k)
            self._sprites[key] = img
        return img

    def ghost_img(self, pose, frame):
        key = (self.front, pose, frame if pose == "run" else 0)
        g = self._ghosts_img.get(key)
        if g is None:
            g = pygame.mask.from_surface(self.sprite(pose, frame)).to_surface(
                setcolor=(90, 255, 150, 255), unsetcolor=(0, 0, 0, 0))
            self._ghosts_img[key] = g
        return g

    _scaled = {}

    @classmethod
    def _sc(cls, img, k):
        if abs(k - 1) < 0.01:
            return img
        key = (id(img), int(k * 100))
        out = cls._scaled.get(key)
        if out is None:
            if len(cls._scaled) > 80:
                cls._scaled.clear()
            out = pygame.transform.rotozoom(img, 0, k)
            cls._scaled[key] = out
        return out

    def draw(self, surf, t):
        lm = self.lm
        k = lm.sprite_k                   # 1.0 normally; smaller when the camera is reversed
        sx = lm.px(self.x)
        gy = lm.gy
        jh = self.jump_h * k
        # shadow
        sh = clamp(1 - self.jump_h / 220, 0.35, 1)
        if self.fall <= 0:
            pellipse(surf, (0, 0, 0), (sx, gy), k, -42 * sh, -8, 84 * sh, 16)
        # illusion trail
        for x, h, life, pose, frame in self.ghosts:
            img = self._sc(self.ghost_img(pose, frame), k)
            img.set_alpha(int(170 * life / 0.4))
            surf.blit(img, img.get_rect(midbottom=(lm.px(x), gy - h * k)))
        if self.invuln > 0 and self.tumble <= 0 and self.fall <= 0 and self.aura <= 0 and int(t * 18) % 2:
            return
        cy = gy - jh
        if self.aura > 0:                                               # green magic aura
            pulse = 1 + 0.12 * math.sin(t * 14)
            blit_glow(surf, sx, cy - 80 * k, 150 * k * pulse, (60, 255, 120), 200)
            for i in range(6):
                ang = t * 4 + i * math.pi / 3
                pygame.draw.circle(surf, (190, 255, 210), (int(sx + math.cos(ang) * 78 * k), int(cy - 80 * k + math.sin(ang) * 92 * k)), 5)
        if self.fall > 0:                                               # dropping into the pit
            p = 1 - self.fall / FALL_TIME
            img = pygame.transform.rotozoom(self.sprite("jump", 0), p * 200, (1 - p * 0.35) * k)
            old = surf.get_clip()
            surf.set_clip(pygame.Rect(0, 0, W, int(gy) + 6))
            surf.blit(img, img.get_rect(center=(sx, gy - 70 * k + p * p * 300 * k)))
            surf.set_clip(old)
            return
        if self.tumble > 0:                                             # slapstick somersault
            p = 1 - self.tumble / TUMBLE_TIME
            img = pygame.transform.rotozoom(self.sprite("jump", 0), -p * 720, k)
            hop = abs(math.sin(p * math.pi * 2)) * 70 * k
            surf.blit(img, img.get_rect(center=(sx, gy - 70 * k - hop)))
            for i in range(4):
                ang = t * 9 + i * math.pi / 2
                pygame.draw.circle(surf, GOLD, (int(sx + math.cos(ang) * 44 * k), int(gy - 160 * k - hop + math.sin(ang) * 10)), 5)
            return
        pose = self.pose()
        img = self._sc(self.sprite(pose, int(self.run_phase * 2) % 12), k)
        bob = abs(math.sin(self.run_phase)) * 5 * k if pose == "run" else 0
        surf.blit(img, img.get_rect(midbottom=(sx, cy - bob + (6 * k if pose == "slide" else 0))))
        if pose == "slide":                                             # dramatic sparks
            for i in range(5):
                pygame.draw.line(surf, (255, 220, 120), (sx - 50 * k + i * 24 * k, gy - 4), (sx - 60 * k + i * 24 * k - 12, gy - 4 - i % 3 * 5), 2)


# --------------------------------------------------------------------------
# GameState: rules and progression
# --------------------------------------------------------------------------
def _best_path():
    return os.path.join(os.path.expanduser("~"), "." + BEST_FILE)


def load_best():
    try:
        with open(_best_path()) as f:
            return int(f.read().strip())
    except (OSError, ValueError):
        return 0


def save_best(v):
    try:
        with open(_best_path(), "w") as f:
            f.write(str(int(v)))
    except OSError:
        pass


CHASE_INFO = {
    "hulk":   ("HULK WANTS A WORD! RUN!", (120, 255, 90), "THE HULK GOT BORED. PUNY HULK!"),
    "clock":  ("MISS MINUTES SAYS: TIME'S UP!", (255, 170, 60), "TIME WAITS FOR NO ONE... EXCEPT LOKI!"),
    "alioth": ("ALIOTH IS HUNGRY! RUN!", (205, 130, 255), "ALIOTH CHOKED ON YOUR AWESOMENESS!"),
}
BREAK_QUIPS = (
    ("I AM LOKI OF ASGARD... AND I AM LATE!", "NEW YORK? I'VE SEEN WORSE. BARELY."),
    ("I'M ON A VERY TIGHT SCHEDULE. LITERALLY.", "MOBIUS, I WILL HAVE THAT JET SKI."),
    ("THE VOID? MY FAVOURITE HOLIDAY SPOT!", "KNEEL. OR AT LEAST SIT DOWN."),
)
BOSS_INFO = {
    "hulk":   dict(name="THE HULK", color=(110, 235, 80), intro="BOSS: THE HULK!",
                   defeat="HULK SMASH... HIMSELF! PUNY HULK!"),
    "thor":   dict(name="THOR", color=(120, 190, 255), intro="BOSS: THOR! BROTHER, NO!",
                   defeat="BROTHER... WELL PLAYED."),
    "alioth": dict(name="ALIOTH", color=(205, 130, 255), intro="TURN AROUND! ALIOTH!",
                   defeat="ALIOTH BANISHED! KNEEL!"),
}
TESS_DAMAGE = 4.2
TESS_QUIPS = ("TESSERACT TAX COLLECTED!", "SHINY! MINE!", "THE MULTIVERSE OWES ME!", "IS THIS... GLORIOUS PURPOSE?")
BREAK_ROW_T = 1.0


class GameState:
    TITLE, PLAYING, PAUSED, GAME_OVER, VICTORY, LEVEL_BREAK = range(6)
    LOW_HIT_QUIPS = ("NOT THE FACE!", "I MEANT TO DO THAT!", "A TRICK, OF COURSE!")

    def __init__(self):
        self.lm = LaneManager()
        self.player = Player(self.lm)
        self.levels = build_levels()
        self.mode = self.TITLE
        self.t = 0.0
        self.travel = 0.0
        self.particles = []
        self.banners = []
        self.shake = 0.0
        self.flash = 0.0
        self.flash_color = WHITE
        self.sfx_queue = []
        self.best = load_best()
        self.new_best = False
        self.brk = None
        self.reset_run()

    @property
    def level(self):
        return self.levels[self.level_idx]

    def reset_run(self):
        self.lives = START_LIVES
        self.score = 0.0
        self.tess = 0
        self.tess_total = 0
        self.apples = 0
        self.meter = 0.0
        self.level_idx = 0
        self.total_dist = 0.0
        self.play_time = 0.0
        self.speed = BASE_SPEED
        self.speed_mult = 1.0
        self.card_t = 0.0
        self.meter_ready_said = False
        self.victory_t = 0.0
        self.new_best = False
        self.thor_said = False
        self.brk = None
        self.player.reset()
        self.particles.clear()
        self.banners.clear()
        self.begin_level()

    def begin_level(self):
        """Reset per-level state and schedule this level's goofy set pieces."""
        lvl = self.level
        self.level_dist = 0.0
        self.next_spawn = 55.0 if self.level_idx == 0 else 70.0
        self.obstacles = []
        self.tesseracts = []
        self.level_tess = self.level_apples = self.level_apples_spawned = 0
        self.level_hits = 0
        self.scene_points = 0
        self.chase = None
        self.boss = None
        self.boss_done = False
        self.reverse = False
        self.lm.rev = False
        self.player.front = False
        self.announce = []
        self.scene_watch = []
        self.pending_scenes = [dict(kind=sc["kind"], at_dist=sc["at"] * lvl.length)
                               for sc in lvl.scenes if not sc["kind"].startswith("chase")]
        self.chase_plans = [dict(who=sc["kind"].split(":")[1], start=sc["at"] * lvl.length,
                                 end=min(sc["at"] * lvl.length + CHASE_LEN, lvl.length - 70))
                            for sc in lvl.scenes if sc["kind"].startswith("chase")]

    def start_game(self):
        self.reset_run()
        self.mode = self.PLAYING
        self.sound("start")
        self.add_banner("BURDENED WITH GLORIOUS PURPOSE!", (110, 255, 140), 2.6)
        self.add_banner("LEVEL 1: " + self.level.title, GOLD, 3.0)

    # -- fx -------------------------------------------------------------
    def sound(self, name):
        self.sfx_queue.append(name)

    def add_banner(self, text, color, dur=2.4):
        self.banners.append(Banner(text, color, dur))
        if len(self.banners) > 2:
            self.banners.pop(0)

    def burst(self, x, y, color, n=10, speed=240, size=5, gravity=500):
        for _ in range(n):
            a = random.random() * math.tau
            v = random.uniform(0.3, 1.0) * speed
            self.particles.append(Particle(x, y, math.cos(a) * v, math.sin(a) * v - 80, random.uniform(0.35, 0.8), color, size, gravity))

    def update_fx(self, dt):
        for p in self.particles:
            p.update(dt)
        self.particles = [p for p in self.particles if p.life > 0]
        for b in self.banners:
            b.t += dt
        self.banners = [b for b in self.banners if not b.dead]
        self.shake = max(0.0, self.shake - dt)
        self.flash = max(0.0, self.flash - dt)

    # -- input ----------------------------------------------------------
    def action(self, name):
        if self.mode != self.PLAYING:
            return
        p = self.player
        if name == "left":
            if p.move(-1):
                self.sound("lane")
        elif name == "right":
            if p.move(1):
                self.sound("lane")
        elif name == "jump":
            if p.jump():
                self.sound("jump")
        elif name == "slide":
            if p.slide():
                self.sound("slide")
        elif name == "aura":
            self.activate_aura()

    def activate_aura(self):
        p = self.player
        if self.meter >= 100 and p.aura <= 0 and p.can_act:
            p.aura = AURA_TIME
            self.sound("aura")
            self.add_banner("LOVE IS A DAGGER!", (90, 255, 150), 2.4)
            self.flash, self.flash_color = 0.25, (80, 255, 140)
            self.shake = 0.35
            for o in self.obstacles:
                if o.active and o.blastable and o.dist < 30:
                    self.blast_obstacle(o)

    def blast_obstacle(self, o):
        o.blast(1 if o.lane_x >= self.player.x else -1)
        self.sound("blast")
        self.score += 150
        sx, sy, _ = self.lm.project(o.lane_x, max(o.dist, 0))
        self.particles.append(Particle(sx, sy - 120, 0, -70, 0.9, (120, 255, 150), kind="text", text="+150"))
        self.burst(sx, sy - 50, (90, 255, 140), 12)

    # -- main update ----------------------------------------------------
    def update(self, dt):
        dt = min(dt, 0.05)
        self.t += dt
        self.update_fx(dt)
        p = self.player
        self.lm.cam += (p.x * 0.4 - self.lm.cam) * min(1.0, 5 * dt)
        if self.mode == self.TITLE:
            self.travel += 14 * dt
            p.update(dt, 14)
            return
        if self.mode == self.PAUSED:
            return
        if self.mode == self.LEVEL_BREAK:
            self.update_break(dt)
            return
        if self.mode == self.GAME_OVER:
            self.speed_mult += (0 - self.speed_mult) * min(1, 2 * dt)
            self.travel += self.speed * self.speed_mult * dt
            p.update(dt, 0)
            return
        if self.mode == self.VICTORY:
            self.victory_t += dt
            self.travel += 12 * dt
            p.update(dt, 12)
            if random.random() < 0.6:
                col = random.choice(((90, 255, 140), GOLD, (90, 190, 255), WHITE))
                self.particles.append(Particle(random.uniform(0, W), -10, random.uniform(-40, 40), random.uniform(80, 200),
                                               4, col, random.randint(6, 12), 60, "confetti"))
            return

        # ---- PLAYING ----
        self.play_time += dt
        self.card_t = max(0.0, self.card_t - dt)
        target = min(MAX_SPEED, BASE_SPEED + 0.16 * self.play_time + 1.5 * self.level_idx)
        if self.reverse:
            target = 13.0                   # obstacles rise from the bottom of the screen at a fair pace
        want = 0.5 if (p.tumble > 0 or p.fall > 0) else 1.0
        self.speed_mult += (want - self.speed_mult) * min(1, 4 * dt)
        self.speed = target * self.speed_mult * (1.12 if p.aura > 0 else 1.0) * (1.1 if self.chase else 1.0)
        step = self.speed * dt
        self.travel += -step if self.reverse else step
        self.level_dist += step
        self.total_dist += step
        self.score += step * 0.5
        p.update(dt, self.speed)
        self.spawn(target)
        for o in self.obstacles:
            o.update(dt, self.speed)
        for c in self.tesseracts:
            c.update(dt, self.speed)
        for o in self.obstacles:
            if o.key == "fist" and not o.landed and o.dist <= 11:
                o.landed = True
                self.shake = max(self.shake, 0.25)
                self.sound("slam")
        for o in self.obstacles:
            if o.key == "mjolnir" and o.launched and not o.announced:
                o.announced = True
                self.sound("thunder")
                self.flash, self.flash_color = 0.12, (190, 220, 255)
                if not self.thor_said and self.boss is None:
                    self.thor_said = True
                    self.add_banner("THOR'S INTERFERENCE! MJOLNIR!", (150, 200, 255), 2.0)
        self.update_scenes(dt)
        self.collisions()
        self.obstacles = [o for o in self.obstacles if not o.dead]
        self.tesseracts = [c for c in self.tesseracts if not c.dead]
        if p.aura > 0:
            self.meter = 100.0 * p.aura / AURA_TIME
            for o in self.obstacles:
                if o.active and o.blastable and o.dist < 16 and abs(o.lane_x - p.x) < 1.7:
                    self.blast_obstacle(o)
            sx = self.lm.px(p.x)
            self.particles.append(Particle(sx + random.uniform(-50, 50), self.lm.gy - p.jump_h * self.lm.sprite_k - random.uniform(10, 150) * self.lm.sprite_k,
                                           random.uniform(-30, 30), -random.uniform(60, 160), 0.6, (110, 255, 160), 5))
        elif self.meter > 100:
            self.meter = 100
        if self.boss is None and not self.boss_done and self.level_dist >= self.level.length:
            self.start_boss()
        if self.boss is not None:
            self.update_boss(dt)

    # -- spawning ---------------------------------------------------------
    def lane_blocked(self, lane, d0, d1):
        lx = lane - 1
        for o in self.obstacles:
            if d0 <= o.dist <= d1 and abs(o.lane_x - lx) < 1.0:
                return True
        return False

    def in_chase_window(self, pos):
        return any(c["start"] <= pos < c["end"] for c in self.chase_plans)

    def spawn(self, speed):
        lvl = self.level
        while self.level_dist + SPAWN_D >= self.next_spawn:
            pos = self.next_spawn
            if self.pending_scenes and pos >= self.pending_scenes[0]["at_dist"]:
                sc = self.pending_scenes.pop(0)
                self.spawn_scene(sc["kind"], pos, speed)
                continue
            if pos <= lvl.length - 30:
                self.spawn_event(pos - self.level_dist, speed)
            gap = speed * random.uniform(0.85, 1.25) * (1 - 0.07 * self.level_idx)
            self.next_spawn += max(20.0, gap * (0.8 if self.in_chase_window(pos) else 1.0))

    def spawn_scene(self, kind, start, speed):
        """Scripted, goofy set pieces: Hulk fists, jet ski rush, gator stampede."""
        ld = self.level_dist
        lanes = [0, 1, 2]
        if kind == "smash":
            safe = 1
            for i in range(7):
                d = start + 22 * i + 10 - ld
                safe = int(clamp(safe + random.choice((-1, 0, 1)), 0, 2))
                others = [ln for ln in lanes if ln != safe]
                for ln in (others if random.random() < 0.55 else [random.choice(others)]):
                    self.obstacles.append(Obstacle("fist", ln - 1, d))
                for j in range(3):
                    self.tesseracts.append(Tesseract(safe - 1, d - 8 + j * 2.4))
            end = start + 22 * 7 + 30
            text, color, snd = "HULK SMASH!", (120, 255, 90), "roar"
        elif kind == "jetskis":
            for i in range(6):
                d = start + 17 * i + 8 - ld
                vx = (1 if i % 2 else -1) * random.uniform(1.6, 2.1)
                ln = random.choice(lanes)
                self.obstacles.append(Obstacle("jetski", ln - 1 - vx * d / max(speed, 20), d, vx))
                for j in range(3):
                    self.tesseracts.append(Tesseract(ln - 1, d + 4 + j * 2.2))
            end = start + 17 * 6 + 30
            text, color, snd = "MOBIUS' JET SKI RUSH!", (255, 170, 60), "roar"
        else:                                                         # stampede
            for i in range(5):
                d = start + 30 * i + 10 - ld
                for ln in lanes:
                    self.obstacles.append(Obstacle("gator", ln - 1, d + random.uniform(0, 1.6)))
                if i in (1, 3):
                    ln = random.choice(lanes)
                    for j in range(3):
                        self.tesseracts.append(GoldenApple(ln - 1, d + 14 + j * 2.6))
                    self.level_apples_spawned += 3
            end = start + 30 * 5 + 30
            text, color, snd = "ALLIGATOR LOKI STAMPEDE!", (90, 255, 140), "roar"
        self.announce.append(dict(at=start - 2, text=text, color=color, snd=snd, end=end))
        self.next_spawn = end + 10

    def spawn_event(self, dist, speed):
        r = random.random()
        lanes = [0, 1, 2]
        if r < 0.26:                                                  # a line of Tesseracts
            free = [ln for ln in lanes if not self.lane_blocked(ln, dist - 6, dist + 16)]
            if free:
                ln = random.choice(free)
                for i in range(6):
                    self.tesseracts.append(Tesseract(ln - 1, dist + i * 2.6))
            return
        if r < 0.36 and self.level_apples_spawned < 12:               # golden apples: jump for them!
            free = [ln for ln in lanes if not self.lane_blocked(ln, dist - 6, dist + 10)]
            if free:
                ln = random.choice(free)
                for i in range(3):
                    self.tesseracts.append(GoldenApple(ln - 1, dist + i * 2.8))
                self.level_apples_spawned += 3
                return
        if self.level_idx == 1 and random.random() < 0.2:             # Thor throws Mjolnir across a lane
            self.obstacles.append(Obstacle.mjolnir(random.choice(lanes) - 1, dist))
            return
        if r < 0.50:                                                  # pit(s) in the road
            n = 1 if random.random() < 0.6 else 2
            start = random.randint(0, 3 - n)
            for ln in range(start, start + n):
                self.obstacles.append(Obstacle("pit", ln - 1, dist))
            return
        keys = [k for k, _ in self.level.obstacles]
        weights = [w for _, w in self.level.obstacles]
        key = random.choices(keys, weights)[0]
        if key == "jetski":
            ln = random.choice(lanes)
            vx = random.choice((-1, 1)) * random.uniform(1.4, 2.0)
            self.obstacles.append(Obstacle("jetski", ln - 1 - vx * dist / max(speed, 20), dist, vx))
            return
        if key == "ship":
            ln = random.choice((0, 2))
            self.obstacles.append(Obstacle("ship", ln - 1, dist))
            return
        n = 1 if random.random() < 0.55 else 2
        for ln in random.sample(lanes, n):
            k2 = random.choices(keys, weights)[0]
            if k2 in ("jetski", "ship"):
                k2 = key
            self.obstacles.append(Obstacle(k2, ln - 1, dist))
        if n == 1 and random.random() < 0.6:                          # reward for dodging
            free = [ln for ln in lanes if not self.lane_blocked(ln, dist - 4, dist + 4) and abs(ln - 1 - self.obstacles[-1].lane_x) > 0.5]
            if free:
                ln = random.choice(free)
                for i in range(3):
                    self.tesseracts.append(Tesseract(ln - 1, dist - 3 + i * 2.6))

    # -- collisions -------------------------------------------------------
    def collisions(self):
        p = self.player
        for c in self.tesseracts:
            if c.collected or p.fall > 0:
                continue
            if c.dist <= 0.9 and c.prev_dist >= -0.9 and abs(p.x - c.lane_x) < 0.55:
                if c.high:
                    if p.jump_h < 30 and p.aura <= 0:
                        continue
                    c.collected = True
                    self.collect_apple(c)
                    continue
                c.collected = True
                self.tess += 1
                self.tess_total += 1
                self.level_tess += 1
                self.score += 50
                self.damage_boss(TESS_DAMAGE)
                self.sound("collect%d" % (self.tess % 5))
                if self.tess_total % 25 == 0:
                    self.add_banner(random.choice(TESS_QUIPS), (120, 220, 255), 1.6)
                if p.aura <= 0:
                    self.meter = min(100.0, self.meter + METER_GAIN)
                    if self.meter >= 100 and not self.meter_ready_said:
                        self.meter_ready_said = True
                        self.sound("ready")
                        self.add_banner("PRESS F FOR GLORIOUS PURPOSE!", (120, 220, 255), 1.8)
                self.burst(self.lm.px(c.lane_x), self.lm.gy - 60 * self.lm.sprite_k, CYAN, 8, 200, 4, 300)
        if not p.vulnerable():
            return
        for o in self.obstacles:
            if not o.active:
                continue
            h = o.depth / 2 + (0.0 if o.kind == "pit" else 0.3)
            if not (o.dist <= h and o.prev_dist >= -h):
                continue
            if abs(p.x - o.lane_x) >= o.width / 2 + 0.22:
                continue
            kind = o.kind
            if kind == "low" and p.jump_h >= o.clear:
                continue
            if kind == "high" and p.sliding:
                continue
            if kind == "pit" and p.jump_h >= o.clear:
                continue
            self.hurt(o)
            break

    def collect_apple(self, c):
        self.apples += 1
        self.level_apples += 1
        self.score += 200
        self.sound("apple")
        sx = self.lm.px(c.lane_x)
        self.particles.append(Particle(sx, self.lm.gy - 200 * self.lm.sprite_k, 0, -80, 0.9, GOLD, kind="text", text="+200"))
        self.burst(sx, self.lm.gy - 150 * self.lm.sprite_k, GOLD, 12, 240, 5, 300)
        if self.apples % APPLES_PER_LIFE == 0 and self.lives < MAX_LIVES:
            self.lives += 1
            self.sound("oneup")
            self.add_banner("ONE-UP! GOLDEN APPLE POWER!", GOLD, 2.2)

    def hurt(self, o):
        p = self.player
        self.lives -= 1
        self.level_hits += 1
        self.shake = 0.5
        if o.kind == "pit":
            o.hit = True
            p.start_fall()
            self.sound("fall")
            self.add_banner("I'VE BEEN FALLING FOR 30 MINUTES!", (120, 230, 255), 2.8)
        else:
            o.blast(1 if o.lane_x >= p.x else -1)
            o.hit = True
            p.start_tumble()
            self.sound("hit")
            self.flash, self.flash_color = 0.18, (255, 70, 60)
            if o.kind == "heavy":
                self.add_banner("PUNY GOD!", (255, 90, 70), 2.0)
            else:
                self.add_banner(random.choice(self.LOW_HIT_QUIPS), (255, 200, 60), 2.0)
            lost = min(self.tess, 10)
            self.tess -= lost
            self.level_tess = max(0, self.level_tess - lost)
            self.meter = max(0.0, self.meter - 25)
            self.meter_ready_said = self.meter >= 100
            sx, sy = self.lm.px(p.x), self.lm.gy - 90 * self.lm.sprite_k
            for _ in range(lost):                                    # scatter!
                a = random.uniform(-math.pi, 0)
                v = random.uniform(180, 420)
                self.particles.append(Particle(sx, sy, math.cos(a) * v, math.sin(a) * v, random.uniform(1.0, 1.6),
                                               CYAN, 11, 900, "cube"))
            self.burst(sx, sy, GOLD, 14)
        if self.chase is not None:
            self.chase["gap"] -= 0.34
            if self.chase["gap"] <= 0.05:                             # the chaser catches Loki!
                self.chase["gap"] = 0.6
                grab = min(self.tess, 10)
                self.tess -= grab
                self.level_tess = max(0, self.level_tess - grab)
                self.add_banner("CAUGHT! GIVE ME THOSE CUBES!", (255, 120, 90), 2.0)
                self.sound("roar")
        if self.lives <= 0:
            self.update_best()
            self.mode = self.GAME_OVER
            self.sound("gameover")
            self.add_banner("THE TRICKSTER HAS FALLEN...", (255, 120, 120), 3.5)

    # -- bosses ---------------------------------------------------------
    def set_reverse(self, on):
        """Flip the camera: Loki runs towards the screen and hazards rise from the bottom."""
        self.reverse = on
        self.lm.rev = on
        self.player.front = on
        self.obstacles.clear()
        self.tesseracts.clear()
        self.travel = -self.travel
        self.shake = 0.6
        self.flash, self.flash_color = 0.5, WHITE

    def start_boss(self):
        kind = self.level.boss
        info = BOSS_INFO[kind]
        self.boss = dict(kind=kind, hp=100.0, t=0.0, next=3.2, n=0, flash=0.0, dead=False, dt=0.0)
        self.next_spawn = 1e9
        self.pending_scenes.clear()
        self.add_banner(info["intro"], info["color"], 3.0)
        self.sound("roar")
        self.shake = 0.8
        if kind == "alioth":
            self.set_reverse(True)

    def damage_boss(self, amount):
        b = self.boss
        if b is None or b["dead"] or b["t"] < 1.5:
            return
        b["hp"] -= amount
        b["flash"] = 0.18
        self.sound("bosshit")

    def update_boss(self, dt):
        b = self.boss
        p = self.player
        if b["dead"]:
            b["dt"] += dt
            self.shake = max(self.shake, 0.1)
            if random.random() < 0.5:
                self.burst(random.uniform(W * 0.25, W * 0.75), random.uniform(HORIZON - 120, HORIZON + 60),
                           random.choice(((255, 200, 60), (255, 110, 40), WHITE)), 10, 320, 6, 300)
            if b["dt"] > 2.6:
                if self.reverse:
                    self.set_reverse(False)
                self.boss = None
                self.boss_done = True
                self.finish_level()
            return
        b["t"] += dt
        b["flash"] = max(0.0, b["flash"] - dt)
        if b["t"] > 2.0:
            b["hp"] -= 0.9 * dt                               # the boss tires out over time
            if p.aura > 0:
                b["hp"] -= 11.0 * dt                          # Glorious Purpose hurts bosses a lot
                b["flash"] = 0.1
            b["next"] -= dt
            if b["next"] <= 0:
                self.boss_attack()
                b["next"] = 7.0 if self.reverse else 6.2
        if b["hp"] <= 0:
            b["dead"] = True
            self.sound("bossdie")
            self.shake = 1.0
            self.flash, self.flash_color = 0.7, WHITE
            self.add_banner(BOSS_INFO[b["kind"]]["defeat"], GOLD, 3.0)
            for o in self.obstacles:
                if o.blastable and o.active:
                    o.blast(1 if o.lane_x >= p.x else -1)

    def boss_attack(self):
        """One attack cycle: a few rows of hazards plus six Tesseracts that hurt the boss when collected."""
        b = self.boss
        n = b["n"]
        b["n"] += 1
        kind = b["kind"]
        rev = self.reverse
        d0 = 28.0 if rev else 70.0
        sp = 0.55 if rev else 1.0
        lanes = [0, 1, 2]
        pat = n % 3

        def row(i):
            return d0 + i * 20 * sp

        def cubes(lane, d, count=2):
            for j in range(count):
                self.tesseracts.append(Tesseract(lane - 1, d - 2.5 + j * 2.4))

        safe = random.choice(lanes)
        for i in range(3):
            d = row(i)
            safe = int(clamp(safe + random.choice((-1, 0, 1)), 0, 2))
            others = [ln for ln in lanes if ln != safe]
            if kind == "hulk":
                if pat == 0:                                        # fist barrage
                    for ln in others:
                        self.obstacles.append(Obstacle("fist", ln - 1, d))
                elif pat == 1:                                      # thrown taxis
                    for ln in random.sample(others, 1 if random.random() < 0.6 else 2):
                        self.obstacles.append(Obstacle("taxi", ln - 1, d))
                else:                                               # shockwaves: jump or dodge
                    for ln in others:
                        self.obstacles.append(Obstacle("shock", ln - 1, d))
            elif kind == "thor":
                if pat == 1:                                        # lightning strikes
                    for ln in others:
                        self.obstacles.append(Obstacle("shock", ln - 1, d))
                else:                                               # Mjolnir barrage
                    for ln in random.sample(others, 1 if pat == 0 else 2):
                        self.obstacles.append(Obstacle.mjolnir(ln - 1, d))
            else:                                                   # alioth (camera reversed)
                if pat == 0:
                    for ln in random.sample(others, 1 if random.random() < 0.5 else 2):
                        self.obstacles.append(Obstacle("smoke", ln - 1, d))
                elif pat == 1:
                    for ln in random.sample(others, 2):
                        self.obstacles.append(Obstacle("gator", ln - 1, d))
                else:
                    if i == 1:
                        self.obstacles.append(Obstacle("ship", random.choice((-1, 1)), d))
                        safe = 1 if self.obstacles[-1].lane_x < 0 else 1
                    else:
                        for ln in random.sample(others, 1):
                            self.obstacles.append(Obstacle("smoke", ln - 1, d))
            cubes(safe, d)
        self.add_banner({"hulk": "HULK SMASH!", "thor": "BY ODIN'S BEARD!", "alioth": "ALIOTH ROARS!"}[kind]
                        if n > 0 else "COLLECT CUBES TO HURT IT!", BOSS_INFO[kind]["color"], 1.6)
        self.sound("roar")

    def update_scenes(self, dt):
        ld = self.level_dist
        for a in list(self.announce):
            if ld >= a["at"]:
                self.announce.remove(a)
                self.add_banner(a["text"], a["color"], 2.4)
                self.sound(a["snd"])
                self.shake = max(self.shake, 0.3)
                self.scene_watch.append(dict(end=a["end"] - 25, hits=self.level_hits))
        for w in list(self.scene_watch):
            if ld >= w["end"]:
                self.scene_watch.remove(w)
                if self.level_hits == w["hits"]:
                    self.score += 500
                    self.scene_points += 500
                    self.sound("oneup")
                    self.add_banner("FLAWLESS! +500", GOLD, 1.8)
        plan = next((c for c in self.chase_plans if c["start"] <= ld < c["end"]), None)
        if plan and self.chase is None:
            self.chase = dict(who=plan["who"], gap=1.0)
            self.player.front = True
            text, color, _ = CHASE_INFO[plan["who"]]
            self.add_banner(text, color, 2.8)
            self.sound("roar")
            self.shake = 0.5
        elif self.chase is not None and plan is None:
            self.end_chase()
        if self.chase is not None:
            self.chase["gap"] = min(1.0, self.chase["gap"] + 0.06 * dt)

    def end_chase(self):
        who = self.chase["who"]
        self.chase = None
        self.player.front = False
        self.score += 1000
        self.scene_points += 1000
        self.sound("clear")
        self.shake = 0.4
        self.flash, self.flash_color = 0.3, GOLD
        self.add_banner(CHASE_INFO[who][2], GOLD, 2.6)
        self.add_banner("ESCAPED! +1000", (150, 255, 170), 2.2)

    def update_best(self):
        if self.score > self.best:
            self.best = int(self.score)
            self.new_best = True
            save_best(self.best)

    def finish_level(self):
        """Level over: show the tally screen (with a goofy Loki) before moving on."""
        if self.chase is not None:
            self.end_chase()
        self.obstacles.clear()
        self.tesseracts.clear()
        hits = self.level_hits
        rows = [
            ("TESSERACTS", str(self.level_tess), self.level_tess * 20, "cube"),
            ("GOLDEN APPLES", "%d / %d" % (self.level_apples, self.level_apples_spawned), self.level_apples * 150, "apple"),
            ("NO-HIT RUN", "PERFECT!" if hits == 0 else "%d HIT%s" % (hits, "" if hits == 1 else "S"), 1000 if hits == 0 else 0, "helmet"),
            ("BOSS DEFEATED", BOSS_INFO[self.level.boss]["name"], 1500, "star"),
            ("LEVEL CLEARED", "", 1000, "flag"),
        ]
        total = sum(r[2] for r in rows)
        self.brk = dict(rows=rows, t=0.0, total=total, score0=self.score, done=False, row=-1, tick=-1,
                        quip=random.choice(BREAK_QUIPS[self.level_idx]))
        self.score += total
        self.mode = self.LEVEL_BREAK
        self.sound("clear")
        self.flash, self.flash_color = 0.5, WHITE
        self.banners.clear()

    def update_break(self, dt):
        b = self.brk
        b["t"] += dt
        n = len(b["rows"])
        idx = min(n, int(b["t"] / BREAK_ROW_T))
        if idx != b["row"]:
            if idx > 0:
                self.sound("ding")
            b["row"] = idx
        if idx < n and b["rows"][idx][2] > 0:
            prog = clamp((b["t"] % BREAK_ROW_T) / 0.8, 0, 1)
            ticks = int(prog * 16)
            if ticks != b["tick"] and prog < 1:
                self.sound("tick")
            b["tick"] = ticks
        if not b["done"] and b["t"] >= n * BREAK_ROW_T + 0.6:
            b["done"] = True
            self.sound("oneup")
        if random.random() < 0.5:
            self.particles.append(Particle(random.uniform(0, W), -12, random.uniform(-30, 30), random.uniform(120, 260),
                                           4, CYAN, 9, 200, "cube"))
        self.travel += 4 * dt
        self.player.update(dt, 0)

    def break_continue(self):
        b = self.brk
        if b is None:
            return
        n = len(b["rows"])
        if not b["done"]:
            b["t"] = n * BREAK_ROW_T + 0.6
            b["done"] = True
            b["row"] = n
            self.sound("ding")
            return
        if self.level_idx >= len(self.levels) - 1:
            self.victory()
            return
        self.level_idx += 1
        self.begin_level()
        self.mode = self.PLAYING
        self.flash, self.flash_color = 0.4, WHITE
        self.sound("start")
        self.particles.clear()
        self.add_banner("LEVEL %d: %s" % (self.level.num, self.level.title), GOLD, 3.0)

    def victory(self):
        self.score += self.lives * 500
        self.update_best()
        self.mode = self.VICTORY
        self.sound("victory")
        self.banners.clear()
        self.particles.clear()
        self.flash, self.flash_color = 0.6, GOLD


# --------------------------------------------------------------------------
# Audio: every sound is synthesised at start-up (no asset files needed)
# --------------------------------------------------------------------------
class Audio:
    RATE = 22050

    def __init__(self):
        self.ok = False
        self.muted = False
        self.sfx = {}
        self.music = None
        self.music_ch = None
        try:
            if not pygame.mixer.get_init():
                pygame.mixer.init(self.RATE, -16, 1, 512)
            self.rate, _, self.channels = pygame.mixer.get_init()
            pygame.mixer.set_num_channels(16)
            self.build()
            self.ok = True
        except Exception:
            self.ok = False

    # -- synthesis helpers ----------------------------------------------
    @staticmethod
    def wave(kind, ph, duty=0.5):
        ph %= 1.0
        if kind == "sq":
            return 1.0 if ph < duty else -1.0
        if kind == "tri":
            return 4 * abs(ph - 0.5) - 1
        if kind == "saw":
            return 2 * ph - 1
        return math.sin(ph * math.tau)

    def tone(self, buf, start, dur, f0, f1=None, kind="sq", vol=0.5, duty=0.5, attack=0.004, vib=0.0):
        """Mix a note (optionally gliding f0 -> f1) into buf starting at `start` seconds."""
        r = self.rate
        f1 = f0 if f1 is None else f1
        n = int(dur * r)
        i0 = int(start * r)
        ph = 0.0
        for i in range(n):
            if i0 + i >= len(buf):
                break
            t = i / n
            f = f0 + (f1 - f0) * t
            if vib:
                f *= 1 + vib * math.sin(i / r * 40)
            ph += f / r
            env = min(1.0, i / max(1, attack * r)) * (1 - t) ** 1.5
            buf[i0 + i] += self.wave(kind, ph, duty) * vol * env

    def noise(self, buf, start, dur, vol=0.5, lp=0.5, lp_end=None):
        r = self.rate
        n = int(dur * r)
        i0 = int(start * r)
        lp_end = lp if lp_end is None else lp_end
        y = 0.0
        for i in range(n):
            if i0 + i >= len(buf):
                break
            t = i / n
            a = lp + (lp_end - lp) * t
            y += (random.uniform(-1, 1) - y) * a
            buf[i0 + i] += y * vol * (1 - t) ** 1.5

    def to_sound(self, buf, gain=1.0):
        from array import array
        data = array("h")
        for v in buf:
            v = int(clamp(v * gain, -1.0, 1.0) * 30000)
            data.append(v)
            if self.channels == 2:
                data.append(v)
        return pygame.mixer.Sound(buffer=data.tobytes())

    def make(self, dur, fn, gain=0.7):
        buf = [0.0] * int(dur * self.rate)
        fn(buf)
        return self.to_sound(buf, gain)

    def build(self):
        T, N = self.tone, self.noise
        S = self.sfx
        S["jump"] = self.make(0.22, lambda b: T(b, 0, 0.2, 280, 760, "sq", 0.45, 0.25))
        S["slide"] = self.make(0.38, lambda b: (N(b, 0, 0.36, 0.7, 0.5, 0.06), T(b, 0, 0.3, 220, 90, "tri", 0.3)))
        S["lane"] = self.make(0.1, lambda b: (N(b, 0, 0.09, 0.5, 0.7, 0.2), T(b, 0, 0.08, 520, 300, "sin", 0.3)))
        pent = (784, 880, 1047, 1175, 1319)
        for i, f in enumerate(pent):
            S["collect%d" % i] = self.make(0.18, lambda b, f=f: (T(b, 0, 0.07, f, f, "sq", 0.3, 0.25),
                                                                   T(b, 0.06, 0.12, f * 1.5, f * 1.5, "sq", 0.3, 0.25)))
        S["slam"] = self.make(0.5, lambda b: (N(b, 0, 0.45, 1.0, 0.18, 0.03), T(b, 0, 0.4, 110, 38, "sin", 0.8)))
        S["apple"] = self.make(0.4, lambda b: [T(b, j * 0.06, 0.2, f, f, "sq", 0.28, 0.25) for j, f in enumerate((1047, 1319, 1568, 2093))])
        S["oneup"] = self.make(0.6, lambda b: [T(b, j * 0.07, 0.15, f, f, "sq", 0.3, 0.5) for j, f in enumerate((659, 784, 1319, 1047, 1175, 1568))])
        S["tick"] = self.make(0.05, lambda b: T(b, 0, 0.04, 1400, 1400, "sq", 0.25, 0.25))
        S["ding"] = self.make(0.35, lambda b: (T(b, 0, 0.3, 1568, 1568, "sin", 0.5), T(b, 0, 0.3, 2093, 2093, "sin", 0.25)))
        S["roar"] = self.make(0.9, lambda b: (N(b, 0, 0.8, 0.7, 0.12, 0.05), T(b, 0, 0.8, 95, 60, "saw", 0.45, vib=0.05)))
        S["thunder"] = self.make(0.7, lambda b: (N(b, 0, 0.6, 0.9, 0.2, 0.04), T(b, 0, 0.5, 1800, 120, "saw", 0.3)))
        S["bosshit"] = self.make(0.18, lambda b: (T(b, 0, 0.15, 420, 150, "sq", 0.4, 0.5), N(b, 0, 0.1, 0.5, 0.5)))
        S["bossdie"] = self.make(2.0, lambda b: (N(b, 0, 1.8, 0.9, 0.3, 0.02), T(b, 0, 1.8, 330, 40, "saw", 0.5, vib=0.04),
                                                 T(b, 1.0, 0.9, 1047, 1568, "sq", 0.25, 0.25)))
        S["ready"] = self.make(0.4, lambda b: [T(b, j * 0.09, 0.15, f, f, "tri", 0.5) for j, f in enumerate((523, 659, 784, 1047))])
        S["hit"] = self.make(0.6, lambda b: (N(b, 0, 0.5, 0.9, 0.35, 0.05), T(b, 0, 0.5, 220, 50, "saw", 0.5)))
        S["fall"] = self.make(1.2, lambda b: T(b, 0, 1.15, 900, 70, "tri", 0.55, vib=0.03))
        S["blast"] = self.make(0.35, lambda b: (N(b, 0, 0.3, 0.8, 0.6, 0.1), T(b, 0, 0.25, 600, 120, "sq", 0.3, 0.5)))
        S["aura"] = self.make(1.0, lambda b: (T(b, 0, 0.9, 160, 1300, "saw", 0.35, vib=0.02),
                                              T(b, 0.1, 0.8, 320, 2000, "sin", 0.3), N(b, 0.5, 0.4, 0.3, 0.8, 0.8)))
        S["start"] = self.make(0.6, lambda b: [T(b, j * 0.1, 0.25, f, f, "sq", 0.35, 0.25) for j, f in enumerate((392, 523, 659, 784))])
        S["clear"] = self.make(0.9, lambda b: [T(b, j * 0.11, 0.3, f, f, "sq", 0.35, 0.25) for j, f in enumerate((523, 659, 784, 1047, 1319))])
        S["gameover"] = self.make(1.6, lambda b: [T(b, j * 0.3, 0.45, f, f * 0.97, "tri", 0.55) for j, f in enumerate((392, 330, 262, 196))])
        notes = (523, 659, 784, 1047, 784, 1047, 1319, 1568)

        def fanfare(b):
            for j, f in enumerate(notes):
                T(b, j * 0.16, 0.4, f, f, "sq", 0.3, 0.25)
                T(b, j * 0.16, 0.4, f / 2, f / 2, "tri", 0.4)
            T(b, 1.3, 1.4, 1047, 1047, "sq", 0.3, 0.25)
            T(b, 1.3, 1.4, 523, 523, "tri", 0.45)
        S["victory"] = self.make(2.8, fanfare)
        self.music = self.make_music()

    def make_music(self):
        """Eight bars of driving minor-key chiptune, A F C G (x2)."""
        bpm = 152
        beat = 60.0 / bpm
        bars = 8
        buf = [0.0] * int(bars * 4 * beat * self.rate)
        T, N = self.tone, self.noise
        roots = (110.0, 87.31, 130.81, 98.0)                 # A2 F2 C3 G2
        chords = ((0, 3, 7), (0, 4, 7), (0, 4, 7), (0, 4, 7))
        for bar in range(bars):
            root = roots[bar % 4]
            sem = chords[bar % 4]
            if bar % 4 == 0:
                sem = (0, 3, 7)
            t0 = bar * 4 * beat
            for i in range(8):                                # bass eighths
                f = root * (2 if i % 4 == 3 else 1)
                T(buf, t0 + i * beat / 2, beat * 0.45, f, f, "sq", 0.2, 0.5)
            for i in range(16):                               # arpeggio
                f = root * 4 * 2 ** (sem[(i * 2 + (i // 4)) % 3] / 12.0)
                if i % 8 >= 6:
                    f *= 2
                T(buf, t0 + i * beat / 4, beat * 0.22, f, f, "sq", 0.1, 0.25)
            for b in range(4):                                # drums
                T(buf, t0 + b * beat, 0.12, 150, 45, "sin", 0.55)
                N(buf, t0 + b * beat + beat / 2, 0.05, 0.22, 0.9)
                if b % 2 == 1:
                    N(buf, t0 + b * beat, 0.11, 0.3, 0.7)
        return self.to_sound(buf, 0.8)

    # -- playback -------------------------------------------------------
    def play(self, name):
        if self.ok and not self.muted:
            snd = self.sfx.get(name)
            if snd:
                snd.play()

    def start_music(self):
        if self.ok and self.music and self.music_ch is None:
            self.music.set_volume(0.0 if self.muted else 0.35)
            self.music_ch = self.music.play(-1)

    def stop_music(self, fade_ms=400):
        if self.ok and self.music_ch is not None:
            self.music_ch.fadeout(fade_ms)
            self.music_ch = None

    def duck(self, on):
        """Quieten the music (level-break screen)."""
        if self.ok and self.music and not self.muted:
            self.music.set_volume(0.12 if on else 0.35)

    def pause(self, paused):
        if self.ok:
            (pygame.mixer.pause if paused else pygame.mixer.unpause)()

    def toggle_mute(self):
        self.muted = not self.muted
        if self.ok and self.music:
            self.music.set_volume(0.0 if self.muted else 0.35)


# --------------------------------------------------------------------------
# Chasers for the front-facing "RUN!" scenes
# --------------------------------------------------------------------------
def draw_pursuer(surf, who, ax, ay, k, t):
    a = (ax, ay)
    if who == "hulk":
        pellipse(surf, (60, 125, 48), a, k, -215, -210, 430, 270)
        for sgn in (-1, 1):
            sw = math.sin(t * 7 + sgn) * 28
            pcirc(surf, (60, 125, 48), a, k, sgn * 240, -150 + sw, 70)
            pcirc(surf, (95, 185, 70), a, k, sgn * 240, -154 + sw, 62)
            for j in range(3):
                pcirc(surf, (70, 140, 55), a, k, sgn * 240 + (j - 1) * 28, -120 + sw, 16)
        pellipse(surf, (110, 60, 150), a, k, -150, -40, 300, 100)                      # torn purple pants
        pellipse(surf, (95, 185, 70), a, k, -105, -395, 210, 230)
        ppoly(surf, (36, 28, 46), a, k, [(-105, -310), (-92, -400), (-45, -428), (0, -414), (50, -430), (95, -398), (105, -310),
                                        (62, -365), (0, -352), (-62, -365)])
        mouth = 26 + 22 * abs(math.sin(t * 6))
        pellipse(surf, (110, 20, 30), a, k, -62, -272, 124, mouth * 1.6)
        for j in range(6):
            prect(surf, WHITE, a, k, -56 + j * 19, -270, 15, 14)
        for sgn in (-1, 1):
            pellipse(surf, WHITE, a, k, sgn * 40 - 24, -332, 48, 30)
            pcirc(surf, (20, 90, 20), a, k, sgn * 40 - sgn * 4, -316, 8)
            pline(surf, (36, 28, 46), a, k, sgn * 82, -352, sgn * 8, -326, 15)
        pcirc(surf, (60, 125, 48), a, k, -14, -290, 6)
        pcirc(surf, (60, 125, 48), a, k, 14, -290, 6)
    elif who == "clock":
        spin = t * 3
        pcirc(surf, (200, 95, 20), a, k, 0, -210, 200)
        pcirc(surf, (255, 150, 30), a, k, 0, -210, 190)
        for sgn in (-1, 1):
            pcirc(surf, (255, 150, 30), a, k, sgn * 105, -395, 44)
            pcirc(surf, (200, 95, 20), a, k, sgn * 105, -395, 20)
        pcirc(surf, (255, 240, 205), a, k, 0, -210, 148)
        for i in range(12):
            ang = spin + i * math.pi / 6
            pline(surf, (120, 60, 20), a, k, math.sin(ang) * 120, -210 - math.cos(ang) * 120,
                  math.sin(ang) * 142, -210 - math.cos(ang) * 142, 8)
        for sgn in (-1, 1):                                                           # googly eyes
            pellipse(surf, WHITE, a, k, sgn * 44 - 28, -285, 56, 76)
            pellipse(surf, (60, 40, 20), a, k, sgn * 44 - 28, -285, 56, 76, 4)
            pcirc(surf, BLACK, a, k, sgn * 44 + math.sin(t * 5) * 8, -240, 15)
            pcirc(surf, (255, 150, 150), a, k, sgn * 92, -190, 20)
        pts = [(i * 7, -150 + (i * 7) ** 2 / 240) for i in range(-12, 13)]
        for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
            pline(surf, (100, 20, 20), a, k, x0, y0, x1, y1, 8)
        ang = t * 1.5
        pline(surf, (80, 40, 15), a, k, 0, -210, math.sin(ang) * 80, -210 - math.cos(ang) * 80, 12)
        for sgn in (-1, 1):                                                           # little running legs
            pline(surf, (80, 40, 15), a, k, sgn * 50, -20, sgn * 50 + math.sin(t * 12 + sgn) * 30, 20, 14)
            pellipse(surf, BLACK, a, k, sgn * 50 + math.sin(t * 12 + sgn) * 30 - 24, 8, 54, 26)
    else:                                                                             # alioth
        for i in range(16):
            ang = i * 0.9 + t * 0.8
            r = 90 + 60 * hrand(i, 7)
            px = math.cos(ang) * (120 + 60 * hrand(i, 9))
            py = -210 + math.sin(ang * 1.3) * (90 + 30 * hrand(i, 11))
            pcirc(surf, (55 + i * 4, 20, 90 + i * 5), a, k, px, py, r)
        for sgn in (-1, 1):
            for j in range(4):
                pcirc(surf, (45 + j * 10, 14, 70 + j * 8), a, k, sgn * (150 + j * 38), -40 + j * 22 + math.sin(t * 4 + j) * 14, 54 - j * 8)
        pcirc(surf, (22, 6, 36), a, k, 0, -205, 150)
        mouth = 60 + 24 * abs(math.sin(t * 5))
        pellipse(surf, (90, 10, 40), a, k, -105, -195, 210, mouth)
        for j in range(9):
            x = -96 + j * 24
            ppoly(surf, WHITE, a, k, [(x, -190), (x + 20, -190), (x + 10, -150)])
            ppoly(surf, WHITE, a, k, [(x, -195 + mouth), (x + 20, -195 + mouth), (x + 10, -235 + mouth)])
        for sgn in (-1, 1):
            blit_glow(surf, ax + sgn * 76 * k, ay - 290 * k, 70 * k, (255, 230, 255), 160)
            ppoly(surf, (255, 245, 255), a, k, [(sgn * 30, -300), (sgn * 120, -330), (sgn * 128, -296), (sgn * 40, -276)])
            pcirc(surf, (190, 60, 220), a, k, sgn * 84, -306, 10)


def draw_thor(surf, ax, ay, k, t, shout):
    a = (ax, ay)
    ppoly(surf, (190, 30, 40), a, k, [(-200, -40), (-130, -310), (130, -310), (200, -40), (130, 50), (-130, 50)])     # cape
    ppoly(surf, (140, 20, 30), a, k, [(-200, -40), (-130, -310), (-60, -300), (-120, 40)])
    pellipse(surf, (70, 90, 130), a, k, -125, -270, 250, 290)                                                        # armour
    for dx, dy, r in ((-62, -180, 30), (62, -180, 30), (0, -150, 26)):
        pcirc(surf, (200, 205, 220), a, k, dx, dy, r)
        pcirc(surf, (120, 125, 145), a, k, dx, dy, r * 0.55)
    prect(surf, (60, 40, 30), a, k, -120, -90, 240, 24)
    for sgn in (-1, 1):                                                                                              # arms
        pcirc(surf, (70, 90, 130), a, k, sgn * 135, -190, 38)
    pline(surf, (225, 190, 150), a, k, 135, -190, 160 + math.sin(t * 6) * 10, -330, 30)                             # arm raised with hammer
    pcirc(surf, (225, 190, 150), a, k, 160 + math.sin(t * 6) * 10, -345, 26)
    pcirc(surf, (225, 190, 150), a, k, 0, -330, 66)                                                                  # head
    ppoly(surf, (235, 195, 70), a, k, [(-72, -320), (-66, -400), (0, -420), (66, -400), (72, -320), (50, -360), (0, -350), (-50, -360)])   # hair
    ppoly(surf, (235, 195, 70), a, k, [(-52, -300), (52, -300), (40, -250), (0, -236), (-40, -250)])                # beard
    prect(surf, (200, 205, 220), a, k, -70, -392, 140, 22)                                                          # helmet band
    for sgn in (-1, 1):
        ppoly(surf, (225, 230, 240), a, k, [(sgn * 70, -384), (sgn * 180, -450), (sgn * 150, -392), (sgn * 200, -380), (sgn * 70, -366)])
        pellipse(surf, WHITE, a, k, sgn * 30 - 16, -338, 32, 20)
        pcirc(surf, (60, 160, 255), a, k, sgn * 30, -328, 7)
    pellipse(surf, (110, 30, 40), a, k, -22, -276 - shout * 4, 44, 14 + shout * 10)                                  # shouting
    hx, hy = 160 + math.sin(t * 6) * 10, -400                                                                        # Mjolnir overhead
    prect(surf, (110, 70, 40), a, k, hx - 8, hy, 16, 80)
    prect(surf, (190, 196, 210), a, k, hx - 62, hy - 56, 124, 62)
    prect(surf, (120, 126, 142), a, k, hx - 62, hy - 22, 124, 28)
    for j in range(5):                                                                                               # crackling lightning
        x0 = hx + (hrand(j, int(t * 12)) - 0.5) * 220
        y0 = hy - 40 + (hrand(j, 7, int(t * 12)) - 0.5) * 120
        pline(surf, (190, 225, 255), a, k, x0, y0, x0 + (hrand(j, 3) - 0.5) * 90, y0 - 50, 4)


def wrap_text(txt, size, maxw):
    words, lines, cur = txt.split(), [], ""
    f = get_font(size)
    for w_ in words:
        trial = (cur + " " + w_).strip()
        if f.size(trial)[0] <= maxw:
            cur = trial
        else:
            lines.append(cur)
            cur = w_
    lines.append(cur)
    return lines


# --------------------------------------------------------------------------
# Game: window, input and rendering
# --------------------------------------------------------------------------
class Game:
    def __init__(self):
        pygame.mixer.pre_init(Audio.RATE, -16, 1, 512)
        pygame.init()
        pygame.display.set_caption("LokiMan -- Burdened With Glorious Purpose")
        try:
            self.screen = pygame.display.set_mode((W, H), pygame.SCALED | pygame.RESIZABLE, vsync=1)
        except pygame.error:
            self.screen = pygame.display.set_mode((W, H), pygame.SCALED | pygame.RESIZABLE)
        self.world = pygame.Surface((W, H))
        self.clock = pygame.time.Clock()
        self.state = GameState()
        self.audio = Audio()
        self.running = True
        self._last_mode = None
        self.accum = 0.0
        self.quality = 0            # 0 full, 1 no glows, 2 lean; drops automatically if the game runs slowly
        self.ema = 1 / 60
        self.slow_t = 0.0
        self.show_fps = False
        self.warmup()
        gc.collect()
        gc.freeze()
        gc.set_threshold(50000, 20, 20)       # fewer garbage-collector hitches

    def warmup(self):
        """Build the expensive caches up front so nothing hitches mid-run."""
        for lvl in self.state.levels:
            lvl.backdrop()
        for v in range(6):
            Level._facade(self.state.levels[0], v, 120, 200)
        p = self.state.player
        for front in (False, True):
            p.front = front
            for pose in ("run", "jump", "slide"):
                for fr in range(12 if pose == "run" else 1):
                    p.sprite(pose, fr)
                    p.ghost_img(pose, fr)
        p.front = False
        for i in range(18):
            Obstacle._smoke_frame(i)

    def set_quality(self, q):
        self.quality = q
        FX["glow"] = q == 0
        FX["tint"] = q == 0
        FX["decor"] = 2 if q >= 2 else 1

    KEYS = {
        pygame.K_a: "left", pygame.K_LEFT: "left",
        pygame.K_d: "right", pygame.K_RIGHT: "right",
        pygame.K_w: "jump", pygame.K_UP: "jump", pygame.K_SPACE: "jump",
        pygame.K_s: "slide", pygame.K_DOWN: "slide",
        pygame.K_f: "aura",
    }

    def handle_event(self, e):
        st = self.state
        if e.type == pygame.QUIT:
            self.running = False
        elif e.type == pygame.KEYDOWN:
            if e.key == pygame.K_ESCAPE:
                self.running = False
            elif e.key == pygame.K_F11 or (e.key == pygame.K_f and e.mod & (pygame.KMOD_META | pygame.KMOD_CTRL)):
                pygame.display.toggle_fullscreen()
            elif e.key == pygame.K_F3:
                self.show_fps = not self.show_fps
            elif e.key == pygame.K_q:
                self.set_quality((self.quality + 1) % 3)
                st.add_banner("GRAPHICS: " + ("FULL", "FAST", "FASTEST")[self.quality], WHITE, 1.2)
            elif e.key in (pygame.K_RETURN, pygame.K_KP_ENTER):
                if st.mode in (st.TITLE, st.GAME_OVER, st.VICTORY):
                    st.start_game()
                elif st.mode == st.LEVEL_BREAK:
                    st.break_continue()
            elif e.key == pygame.K_m:
                self.audio.toggle_mute()
            elif e.key == pygame.K_p:
                if st.mode == st.PLAYING:
                    st.mode = st.PAUSED
                elif st.mode == st.PAUSED:
                    st.mode = st.PLAYING
            elif e.key in self.KEYS:
                if st.mode in (st.TITLE, st.GAME_OVER, st.VICTORY) and e.key == pygame.K_SPACE:
                    st.start_game()
                elif st.mode == st.LEVEL_BREAK and e.key == pygame.K_SPACE:
                    st.break_continue()
                else:
                    st.action(self.KEYS[e.key])

    def step(self, dt):
        """Fixed 120 Hz simulation steps keep physics and collisions frame-rate independent."""
        self.accum += dt
        while self.accum >= SIM_DT:
            self.state.update(SIM_DT)
            self.accum -= SIM_DT
        self.sync_audio()
        # auto-lower graphics if the machine struggles (about 40 fps or worse for two seconds)
        self.ema += (dt - self.ema) * 0.05
        if self.quality < 2 and self.ema > 0.025:
            self.slow_t += dt
            if self.slow_t > 2.0:
                self.set_quality(self.quality + 1)
                self.slow_t = 0.0
        else:
            self.slow_t = max(0.0, self.slow_t - dt)

    def sync_audio(self):
        st, au = self.state, self.audio
        for name in st.sfx_queue:
            au.play(name)
        st.sfx_queue.clear()
        if st.mode != self._last_mode:
            if st.mode == st.PLAYING:
                if self._last_mode == st.PAUSED:
                    au.pause(False)
                elif self._last_mode == st.LEVEL_BREAK:
                    au.duck(False)
                else:
                    au.start_music()
            elif st.mode == st.PAUSED:
                au.pause(True)
            elif st.mode == st.LEVEL_BREAK:
                au.duck(True)
            elif st.mode in (st.GAME_OVER, st.VICTORY):
                au.duck(False)
                au.stop_music()
            self._last_mode = st.mode

    def run(self):
        while self.running:
            dt = min(self.clock.tick(FPS_CAP) / 1000.0, 0.1)
            for e in pygame.event.get():
                self.handle_event(e)
            self.step(dt)
            self.draw()
            pygame.display.flip()
        pygame.quit()

    # -- world ----------------------------------------------------------
    def draw_world(self, surf):
        st = self.state
        lm, lvl, p = st.lm, st.level, st.player
        lm.draw_ground(surf, lvl, st.travel)
        for o in st.obstacles:
            if o.ground:
                o.draw_ground(surf, lm, lvl)
        lvl.draw_decor(surf, lm, st.travel)
        if st.boss is not None:
            self.draw_boss(surf)
        if st.chase is not None:
            gap = st.chase["gap"]
            draw_pursuer(surf, st.chase["who"], lm.px(p.x * 0.5) + math.sin(st.t * 9) * 5,
                         HORIZON + 30 + (1 - gap) * 150 + abs(math.sin(st.t * 8)) * 6, 0.42 + (1 - gap) * 0.9, st.t)
        items = [(o.dist, 0, o) for o in st.obstacles if not o.ground]
        items += [(c.dist, 1, c) for c in st.tesseracts]
        if st.mode != st.LEVEL_BREAK:
            items.append((0.0, 2, p))
        sgn = 1 if lm.rev else -1                      # far objects first (reversed camera: nearest to the camera = highest dist)
        items.sort(key=lambda it: (sgn * it[0], it[1]))
        for _, _, obj in items:
            if obj is p:
                p.draw(surf, st.t)
            else:
                obj.draw(surf, lm)
        for part in st.particles:
            part.draw(surf)
        if st.player.aura > 0 and FX["tint"]:                      # green screen tint
            fill_alpha(surf, (0, 0, W, H), (40, 255, 120), 26 + 10 * math.sin(st.t * 12))

    def draw_boss(self, surf):
        st = self.state
        b = st.boss
        lm = st.lm
        info = BOSS_INFO[b["kind"]]
        t = st.t
        e = clamp(b["t"] / 2.0, 0, 1)
        e = 1 - (1 - e) ** 3
        hurt = b["flash"] > 0
        dead_off = b["dt"] * 160 if b["dead"] else 0
        jitter = (random.randint(-5, 5) if (hurt or b["dead"]) else 0)
        if b["kind"] == "alioth":
            k = 1.15 - 0.3 * (1 - clamp(b["hp"] / 100.0, 0, 1))
            fill_alpha(surf, (0, 0, W, HORIZON + 90), (60, 14, 100), 120)
            ax, ay = W / 2 + jitter, HORIZON + 190 + (1 - e) * 220 + dead_off + math.sin(t * 3) * 8
            draw_pursuer(surf, "alioth", ax, ay, k, t)
            for sgn in (-1, 1):                              # tendrils creeping in from the sides
                for j in range(6):
                    cx = W / 2 + sgn * (W * 0.52 - j * 26 + math.sin(t * 2 + j) * 12)
                    pygame.draw.circle(surf, (50 + j * 6, 16, 80 + j * 8), (int(cx), int(HORIZON + 80 + j * 40)), 60 - j * 6)
            if hurt:
                blit_glow(surf, ax, ay - 200 * k * 0.5, 190, WHITE, 150)
            return
        ax = lm.px(0) + jitter
        ay = HORIZON + 150 + (1 - e) * 260 + abs(math.sin(t * 4)) * 6 + dead_off
        if b["kind"] == "hulk":
            draw_pursuer(surf, "hulk", ax, ay, 0.5, t)
        else:
            draw_thor(surf, ax, ay, 0.5, t, abs(math.sin(t * 5)))
        if hurt:
            blit_glow(surf, ax, ay - 190 * 0.5, 130, WHITE, 150)

    # -- HUD ----------------------------------------------------------------
    @staticmethod
    def helmet_icon(surf, x, y, k=1.0, dim=False):
        body = (50, 90, 60) if dim else GREEN
        horn = (90, 90, 70) if dim else GOLD
        pygame.draw.rect(surf, body, (x - 11 * k, y - 8 * k, 22 * k, 24 * k))
        pygame.draw.polygon(surf, horn, [(x - 6 * k, y - 6 * k), (x - 20 * k, y - 26 * k), (x - 14 * k, y - 26 * k), (x, y - 8 * k)])
        pygame.draw.polygon(surf, horn, [(x + 6 * k, y - 6 * k), (x + 20 * k, y - 26 * k), (x + 14 * k, y - 26 * k), (x, y - 8 * k)])
        pygame.draw.rect(surf, (225, 190, 150), (x - 6 * k, y + 2 * k, 12 * k, 8 * k))

    def draw_hud(self, surf):
        st = self.state
        lvl = st.level
        # top-left: level, name, distance
        fill_alpha(surf, (10, 10, 450, 124), (8, 10, 24), 215)
        pygame.draw.rect(surf, GOLD, (10, 10, 450, 124), 2)
        draw_text(surf, f"LEVEL {lvl.num} / 3", 34, GOLD, (24, 16), "topleft", ow=3)
        draw_text(surf, lvl.title, 22, WHITE, (24, 54), "topleft", ow=2)
        left = max(0, int(lvl.length - st.level_dist))
        if st.boss is not None:
            draw_text(surf, "BOSS FIGHT!", 28, (255, 120, 100), (24, 80), "topleft", ow=3)
        else:
            draw_text(surf, f"DISTANCE LEFT: {left} m", 28, (160, 255, 190), (24, 80), "topleft", ow=3)
        frac = clamp(st.level_dist / lvl.length, 0, 1)
        pygame.draw.rect(surf, (30, 30, 40), (24, 116, 422, 10))
        pygame.draw.rect(surf, GREEN if st.boss is None else (255, 90, 80), (24, 116, int(422 * (frac if st.boss is None else 1)), 10))
        pygame.draw.rect(surf, WHITE, (24, 116, 422, 10), 1)
        # top-right: score, tesseracts, apples
        fill_alpha(surf, (W - 270, 10, 260, 124), (8, 10, 24), 215)
        pygame.draw.rect(surf, GOLD, (W - 270, 10, 260, 124), 2)
        draw_text(surf, "SCORE", 18, (190, 200, 220), (W - 24, 14), "topright", ow=2)
        draw_text(surf, f"{int(st.score):07d}", 46, WHITE, (W - 24, 30), "topright", ow=3)
        draw_cube(surf, W - 244, 104, 15, st.t * 3, glow=False)
        draw_text(surf, f"x {st.tess}", 34, CYAN, (W - 222, 86), "topleft", ow=3)
        draw_apple(surf, W - 126, 106, 13, st.t)
        draw_text(surf, f"x {st.apples}", 34, GOLD, (W - 106, 86), "topleft", ow=3)
        if st.boss is not None:                                           # boss health bar
            b = st.boss
            info = BOSS_INFO[b["kind"]]
            bx0, by0, bw0, bh0 = 470, 20, 210, 34
            fill_alpha(surf, (bx0 - 6, by0 - 6, bw0 + 12, bh0 + 12), (8, 10, 24), 225)
            pygame.draw.rect(surf, (60, 20, 24), (bx0, by0, bw0, bh0))
            pygame.draw.rect(surf, info["color"], (bx0, by0, int(bw0 * clamp(b["hp"] / 100.0, 0, 1)), bh0))
            pygame.draw.rect(surf, WHITE, (bx0, by0, bw0, bh0), 3)
            draw_text(surf, info["name"], 22, WHITE, (bx0 + bw0 // 2, by0 + bh0 // 2), "center", ow=3)
        # bottom-left: lives
        n = min(MAX_LIVES, max(START_LIVES, st.lives))
        fill_alpha(surf, (10, H - 82, 40 + n * 44, 72), (8, 10, 24), 215)
        pygame.draw.rect(surf, GOLD, (10, H - 82, 40 + n * 44, 72), 2)
        draw_text(surf, "LIVES", 20, WHITE, (22, H - 78), "topleft", ow=2)
        for i in range(n):
            self.helmet_icon(surf, 38 + i * 44, H - 34, 1.0, dim=i >= st.lives)
        # bottom-centre: glorious purpose meter
        bx, by, bw, bh = W // 2 - 190, H - 44, 400, 28
        fill_alpha(surf, (bx - 12, by - 40, bw + 24, bh + 52), (8, 10, 24), 215)
        pygame.draw.rect(surf, (10, 25, 15), (bx - 4, by - 4, bw + 8, bh + 8))
        full = st.meter >= 100
        for i in range(int(bw * clamp(st.meter / 100, 0, 1))):
            pygame.draw.line(surf, mix((30, 180, 80), GOLD, i / bw), (bx + i, by), (bx + i, by + bh))
        pygame.draw.rect(surf, GOLD if (full and int(st.t * 6) % 2) else WHITE, (bx - 4, by - 4, bw + 8, bh + 8), 3)
        label = "LOVE IS A DAGGER!" if st.player.aura > 0 else "GLORIOUS PURPOSE"
        draw_text(surf, label, 24, WHITE, (W // 2, by - 20), "center", ow=3)
        if full and st.player.aura <= 0 and int(st.t * 5) % 2 == 0:
            draw_text(surf, "PRESS  F !", 28, (160, 255, 180), (W // 2, by + bh // 2), "center", ow=3)

    def draw_banners(self, surf):
        st = self.state
        for i, b in enumerate(st.banners):
            b.draw(surf, 176 + i * 58)

    # -- overlays -------------------------------------------------------
    def draw_level_card(self, surf):
        st = self.state
        t = st.card_t
        if t <= 0:
            return
        a = clamp(min(t, 3.6 - t) / 0.35, 0, 1)
        lvl = st.level
        fill_alpha(surf, (0, 340, W, 150), BLACK, 190 * a)
        for txt, size, col, y in ((f"LEVEL {lvl.num}", 64, GOLD, 378), (lvl.title, 38, WHITE, 436), (f"~ {lvl.year} ~", 26, (160, 255, 190), 472)):
            img = text_surf(txt, size, col, BLACK, 3).copy()
            img.set_alpha(int(255 * a))
            surf.blit(img, img.get_rect(center=(W // 2, y)))

    def draw_title(self, surf):
        st = self.state
        fill_alpha(surf, (0, 0, W, H), BLACK, 130)
        bob = math.sin(st.t * 3) * 6
        img = text_surf("LOKIMAN", 160, GREEN, (10, 40, 20), 8)
        surf.blit(img, img.get_rect(center=(W // 2, 120 + bob)))
        draw_text(surf, "BURDENED WITH GLORIOUS PURPOSE", 34, (170, 255, 190), (W // 2, 214), ow=3)
        fill_alpha(surf, (W // 2 - 400, 250, 800, 220), (8, 10, 24), 200)
        draw_text(surf, "COLLECT TESSERACTS.  JUMP FOR GOLDEN APPLES.  OUTRUN THE MULTIVERSE.", 22, CYAN, (W // 2, 272), ow=2)
        lines = ["A / D   or   LEFT / RIGHT   -   SHIFT LANES",
                 "W / UP / SPACE   -   JUMP LOW BARRIERS",
                 "S / DOWN   -   SLIDE UNDER HIGH HAZARDS",
                 "F   -   GLORIOUS PURPOSE (WHEN THE METER IS FULL)"]
        for i, ln in enumerate(lines):
            draw_text(surf, ln, 25, WHITE, (W // 2, 318 + i * 34), ow=2)
        draw_text(surf, "P PAUSE    M MUTE    F11 FULLSCREEN    Q GRAPHICS    ESC QUIT", 18, (200, 205, 220), (W // 2, 458), ow=2)
        if st.best:
            draw_text(surf, f"BEST SCORE  {st.best:07d}", 30, GOLD, (W // 2, 505), ow=3)
        if int(st.t * 2.5) % 2 == 0:
            draw_text(surf, "PRESS ENTER TO RUN", 50, GOLD, (W // 2, 570), ow=4)

    # -- level break (tally) screen -------------------------------------
    def draw_break(self, surf):
        st = self.state
        b = st.brk
        t = b["t"]
        rows = b["rows"]
        fill_alpha(surf, (0, 0, W, H), (12, 6, 34), 215)
        fill_alpha(surf, (24, 24, 600, H - 48), (8, 10, 24), 225)
        pygame.draw.rect(surf, GOLD, (24, 24, 600, H - 48), 3)
        draw_text(surf, f"LEVEL {st.level.num} COMPLETE!", 50, GOLD, (324, 76), ow=5)
        draw_text(surf, st.level.title, 24, WHITE, (324, 126), ow=3)
        revealed = 0
        for i, (label, val, pts, icon) in enumerate(rows):
            if t < i * BREAK_ROW_T:
                break
            local = clamp((t - i * BREAK_ROW_T) / 0.8, 0, 1)
            revealed += int(pts * local)
            y = 156 + i * 66
            fill_alpha(surf, (40, y, 568, 58), (30, 34, 66), 230)
            cx, cy = 76, y + 29
            if icon == "cube":
                draw_cube(surf, cx, cy, 18, t * 3, glow=False)
            elif icon == "apple":
                draw_apple(surf, cx, cy + 3, 16, t)
            elif icon == "helmet":
                self.helmet_icon(surf, cx, cy, 1.2)
            elif icon == "star":
                star = [(cx + math.sin(i * math.pi / 5) * (22 if i % 2 == 0 else 10),
                         cy - math.cos(i * math.pi / 5) * (22 if i % 2 == 0 else 10)) for i in range(10)]
                pygame.draw.polygon(surf, GOLD, star)
                pygame.draw.polygon(surf, WHITE, star, 2)
            else:
                pygame.draw.line(surf, WHITE, (cx - 14, cy + 20), (cx - 14, cy - 22), 4)
                pygame.draw.polygon(surf, GOLD, [(cx - 12, cy - 22), (cx + 22, cy - 12), (cx - 12, cy)])
            draw_text(surf, label, 26, WHITE, (110, y + 5), "topleft", ow=3)
            draw_text(surf, val, 22, CYAN if icon != "helmet" else (160, 255, 190), (110, y + 33), "topleft", ow=2)
            col = GOLD if pts > 0 else (150, 150, 160)
            draw_text(surf, "+%d" % int(pts * local), 40, col, (594, y + 29), "midright", ow=3)
        n = len(rows)
        if t >= n * BREAK_ROW_T:
            draw_text(surf, "LEVEL BONUS", 28, (190, 200, 230), (60, 506), "topleft", ow=3)
            draw_text(surf, "+%d" % b["total"], 52, WHITE, (594, 524), "midright", ow=4)
            draw_text(surf, f"SCORE  {int(b['score0'] + b['total']):07d}", 34, GOLD, (324, 570), ow=4)
        if b["done"]:
            if int(st.t * 2.5) % 2 == 0:
                draw_text(surf, "PRESS ENTER TO CONTINUE", 30, (160, 255, 190), (324, 604), ow=3)
        else:
            draw_text(surf, "ENTER: SKIP", 20, (200, 205, 220), (324, 604), ow=2)
        # a very silly Loki
        p = st.player
        p.front = True
        pose = "jump" if math.sin(st.t * 5) > 0 else "run"
        base = p.sprite(pose, int(st.t * 8) % 12)
        p.front = False
        bounce = abs(math.sin(st.t * 5)) * 46
        img = pygame.transform.rotozoom(base, math.sin(st.t * 5) * 14, 2.0)
        surf.blit(img, img.get_rect(midbottom=(780, 560 - bounce)))
        pygame.draw.ellipse(surf, (0, 0, 0), (700, 550, 160, 28))
        quip = wrap_text(b["quip"], 26, 270)
        bh = 36 * len(quip) + 28
        bx, by = 640, 60
        pygame.draw.rect(surf, WHITE, (bx, by, 300, bh), border_radius=16)
        pygame.draw.rect(surf, BLACK, (bx, by, 300, bh), 3, border_radius=16)
        pygame.draw.polygon(surf, WHITE, [(bx + 140, by + bh - 2), (bx + 190, by + bh - 2), (bx + 170, by + bh + 40)])
        pygame.draw.lines(surf, BLACK, False, [(bx + 140, by + bh - 1), (bx + 170, by + bh + 40), (bx + 190, by + bh - 1)], 3)
        for i, ln in enumerate(quip):
            draw_text(surf, ln, 26, BLACK, (bx + 150, by + 30 + i * 36), "center", outline=WHITE, ow=0)

    def draw_end(self, surf, victory):
        st = self.state
        fill_alpha(surf, (0, 0, W, H), BLACK, 175 if victory else 160)
        if victory:
            t = st.victory_t
            k = 1 + 0.04 * math.sin(t * 5)
            for txt, size, col, y in (("MULTIVERSE SAVED...", 88, (120, 255, 160), 120),
                                       ("KNEEL BEFORE YOUR KING!", 72, GOLD, 220)):
                img = text_surf(txt, size, col, BLACK, 6)
                fit = min(1.0, (W - 40) / img.get_width())
                img = pygame.transform.rotozoom(img, math.sin(t * 2) * 2, k * fit)
                surf.blit(img, img.get_rect(center=(W // 2, y)))
            p = st.player
            p.front = True
            big = pygame.transform.rotozoom(p.sprite("jump", 0), math.sin(t * 4) * 8, 1.1)
            p.front = False
            surf.blit(big, big.get_rect(midbottom=(W // 2, 600 + math.sin(t * 4) * 6)))
            blit_glow(surf, W // 2, 500, 170, (90, 255, 140), 90)
            draw_text(surf, f"FINAL SCORE  {int(st.score):07d}", 50, WHITE, (W // 2, 318), ow=4)
            if st.new_best:
                draw_text(surf, "NEW BEST SCORE!", 30, GOLD, (W // 2, 360), ow=3)
            else:
                draw_text(surf, f"BEST  {st.best:07d}", 26, GOLD, (W // 2, 360), ow=3)
            draw_text(surf, f"TESSERACTS {st.tess_total}     GOLDEN APPLES {st.apples}     LIVES LEFT {max(0, st.lives)}",
                      26, CYAN, (W // 2, 402), ow=3)
            if int(t * 2.5) % 2 == 0:
                draw_text(surf, "PRESS ENTER TO RULE AGAIN", 32, GOLD, (W // 2, 620), ow=3)
        else:
            draw_text(surf, "GAME OVER", 120, (255, 90, 80), (W // 2, 220), ow=6)
            draw_text(surf, f"SCORE  {int(st.score):07d}", 50, WHITE, (W // 2, 330), ow=4)
            if st.new_best:
                draw_text(surf, "NEW BEST SCORE!", 30, GOLD, (W // 2, 376), ow=3)
            else:
                draw_text(surf, f"BEST  {st.best:07d}", 28, GOLD, (W // 2, 376), ow=3)
            draw_text(surf, f"REACHED LEVEL {st.level.num}   -   {int(st.total_dist)} m   -   {st.tess_total} TESSERACTS   -   {st.apples} APPLES",
                      22, CYAN, (W // 2, 420), ow=3)
            if int(st.t * 2.5) % 2 == 0:
                draw_text(surf, "PRESS ENTER TO TRY AGAIN", 38, GOLD, (W // 2, 500), ow=4)

    def draw(self):
        st = self.state
        self.draw_world(self.world)
        ox = oy = 0
        if st.shake > 0:
            ox, oy = random.randint(-7, 7), random.randint(-5, 5)
        self.screen.fill(BLACK)
        self.screen.blit(self.world, (ox, oy))
        if st.flash > 0:
            fill_alpha(self.screen, (0, 0, W, H), st.flash_color, 160 * clamp(st.flash / 0.4, 0, 1))
        scr = self.screen
        if st.mode == st.TITLE:
            self.draw_title(scr)
        elif st.mode == st.LEVEL_BREAK:
            for part in st.particles:
                part.draw(scr)
            self.draw_break(scr)
        else:
            if st.mode != st.VICTORY:
                self.draw_hud(scr)
            self.draw_banners(scr)
            if st.mode == st.PAUSED:
                fill_alpha(scr, (0, 0, W, H), BLACK, 150)
                draw_text(scr, "PAUSED", 110, GOLD, (W // 2, H // 2), ow=6)
                draw_text(scr, "PRESS P TO RESUME", 30, WHITE, (W // 2, H // 2 + 80), ow=3)
            elif st.mode == st.GAME_OVER:
                self.draw_end(scr, False)
            elif st.mode == st.VICTORY:
                self.draw_end(scr, True)
        if self.show_fps:
            fps = 1.0 / max(self.ema, 1e-4)
            draw_text(scr, f"FPS {fps:0.0f}  GFX {self.quality}", 22, (255, 255, 120), (W // 2, 12), "midtop", ow=2)


def main():
    Game().run()


if __name__ == "__main__":
    main()
