#!/usr/bin/env python3
"""
LokiMan -- a retro pseudo-3D three-lane runner in the spirit of Pepsiman.

Run:      pip install pygame && python lokiman.py

Controls
    A / D  or  LEFT / RIGHT   shift lanes (leaves a green illusion trail)
    W / UP / SPACE            jump over low barriers
    S / DOWN                  superhero slide under high hazards
    F                         unleash GLORIOUS PURPOSE (when the meter is full)
    F11 or Cmd+F              toggle fullscreen
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
import math
import random
import sys

import pygame

# --------------------------------------------------------------------------
# Constants
# --------------------------------------------------------------------------
W, H = 960, 640
FPS = 60
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


_fonts = {}


def get_font(size):
    f = _fonts.get(size)
    if f is None:
        try:
            f = pygame.font.SysFont("impact,arialblack,dejavusansbold,arial", size, bold=True)
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


_glow_cache = {}


def glow_surf(radius, color, strength=170):
    radius = max(2, int(radius) // 2 * 2)
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

    @staticmethod
    def lane_x(lane):
        return lane - 1

    def clamp_lane(self, lane):
        return int(clamp(lane, 0, self.COUNT - 1))

    @staticmethod
    def scale(dist):
        return ND / (ND + max(dist, -ND * 0.8))

    def project(self, lx, dist):
        """lane-unit x and world distance -> (screen x, screen y, scale)."""
        s = self.scale(dist)
        return W / 2 + lx * LANE_W * s, HORIZON + (PLAYER_Y - HORIZON) * s, s

    def draw_ground(self, surf, level, travel):
        pal = level.pal
        surf.blit(level.backdrop(), (0, 0))
        pygame.draw.rect(surf, pal["ground"], (0, HORIZON, W, H - HORIZON))
        k0 = int(travel // BAND)
        off = travel - k0 * BAND
        cx = W / 2
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
                                    [(cx + l0 * LANE_W * s0, y0), (cx + l1 * LANE_W * s0, y0),
                                     (cx + l1 * LANE_W * s1, y1), (cx + l0 * LANE_W * s1, y1)])

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

    def __init__(self, num, title, year, length, theme, obstacles, pal):
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
        k0 = int(travel // sp)
        fn = getattr(self, "_decor_" + self.theme)
        for k in range(k0 + int(FAR / sp) + 1, k0 - 1, -1):
            dist = k * sp - travel
            if dist < -3 or dist > FAR:
                continue
            s = lm.scale(dist)
            sy = HORIZON + (PLAYER_Y - HORIZON) * s
            fog = clamp(dist / FAR, 0, 1) ** 1.3 * 0.8
            for side in (-1, 1):
                fn(surf, k, side, s, sy, fog)

    def _fogc(self, c, fog):
        return mix(c, self.pal["fog"], fog)

    def _decor_city(self, surf, k, side, s, sy, fog):
        r1, r2, r3 = hrand(k, side, 1), hrand(k, side, 2), hrand(k, side, 3)
        bw, bh = (1.9 + r2 * 0.6) * LANE_W * s, (230 + r3 * 330) * s
        bx = W / 2 + side * (3.05 + r1 * 0.4) * LANE_W * s
        col = self._fogc(mix((38, 42, 66), (74, 60, 84), r2), fog)
        pygame.draw.rect(surf, col, (bx - bw / 2, sy - bh, bw, bh + 2))
        if s > 0.12:
            wc = self._fogc((255, 215, 120), fog)
            dark = self._fogc((22, 24, 40), fog)
            for wy in range(18, int(bh / s) - 8, 30):
                for wx in range(-int(bw / s / 2) + 12, int(bw / s / 2) - 16, 26):
                    c = wc if hrand(k * 7 + wx, side, wy) > 0.55 else dark
                    pygame.draw.rect(surf, c, (bx + wx * s, sy - bh + wy * s, 12 * s, 16 * s))
        if r1 > 0.55:                                          # burning rooftop
            t = pygame.time.get_ticks() / 1000.0
            for j in range(3):
                fx = bx + (j - 1) * 24 * s
                fh = (26 + 12 * math.sin(t * 9 + j + k)) * s
                pygame.draw.circle(surf, (255, 120, 20), (int(fx), int(sy - bh - fh * 0.3)), max(1, int(fh * 0.7)))
                pygame.draw.circle(surf, (255, 220, 60), (int(fx), int(sy - bh - fh * 0.2)), max(1, int(fh * 0.4)))
        if k % 2 == 0:                                         # street lamp
            lx = W / 2 + side * 1.62 * LANE_W * s
            pygame.draw.rect(surf, self._fogc((90, 90, 96), fog), (lx - 3 * s, sy - 150 * s, 6 * s + 1, 150 * s))
            pygame.draw.rect(surf, self._fogc((255, 240, 160), fog), (lx - (side * 22 + 4) * s, sy - 154 * s, 28 * s, 7 * s))

    def _decor_tva(self, surf, k, side, s, sy, fog):
        c1, c2 = ((205, 112, 42), (178, 92, 34)) if k % 2 == 0 else ((178, 92, 34), (205, 112, 42))
        x_in = W / 2 + side * 1.75 * LANE_W * s
        x_out = W / 2 + side * 9 * LANE_W * s
        x0, x1 = min(x_in, x_out), max(x_in, x_out)
        ch = 430 * s
        pygame.draw.rect(surf, self._fogc(c1, fog), (x0, sy - ch, x1 - x0, ch + 2))
        pygame.draw.rect(surf, self._fogc((92, 50, 24), fog), (x0, sy - ch, x1 - x0, 10 * s + 1))   # cornice
        pygame.draw.rect(surf, self._fogc((92, 50, 24), fog), (x0, sy - 40 * s, x1 - x0, 40 * s + 2))  # skirting
        dx = W / 2 + side * 2.55 * LANE_W * s
        dw, dh = 0.9 * LANE_W * s, 290 * s
        pygame.draw.rect(surf, self._fogc((110, 62, 30), fog), (dx - dw / 2, sy - 40 * s - dh, dw, dh))
        pygame.draw.rect(surf, self._fogc((240, 200, 120), fog), (dx - dw * 0.3, sy - 40 * s - dh * 0.75, dw * 0.6, dh * 0.2))
        pillar_w = 0.3 * LANE_W * s
        pygame.draw.rect(surf, self._fogc(c2, fog), (x_in - pillar_w / 2 - side * pillar_w * 0.3, sy - ch, pillar_w, ch + 2))
        if k % 2 == 0:                                           # ceiling light panels
            lw = 3.0 * LANE_W * s
            pygame.draw.rect(surf, self._fogc((92, 50, 24), fog), (W / 2 - lw * 0.75, sy - ch - 4 * s, lw * 1.5, 12 * s + 1))
            pygame.draw.rect(surf, self._fogc((255, 245, 200), fog), (W / 2 - lw / 2, sy - ch - 2 * s, lw, 8 * s + 1))

    def _decor_void(self, surf, k, side, s, sy, fog):
        r1, r2, r3 = hrand(k, side, 1), hrand(k, side, 2), hrand(k, side, 3)
        bx = W / 2 + side * (2.5 + r1 * 2.2) * LANE_W * s
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
    return [
        Level(1, "THE BATTLE OF NEW YORK", "2012", 1500, "city",
              [("taxi", 3), ("glider", 3), ("shock", 3)],
              dict(sky_top=(30, 45, 75), sky_bot=(225, 130, 70), ground=(40, 40, 45),
                   road_a=(66, 66, 74), road_b=(58, 58, 66), line=(240, 200, 40),
                   curb_a=(205, 60, 50), curb_b=(235, 235, 235), side_a=(124, 124, 130),
                   side_b=(112, 112, 118), fog=(200, 120, 80), pit_rim=(255, 150, 40))),
        Level(2, "THE TIME VARIANCE AUTHORITY", "1973-ish", 1800, "tva",
              [("minute", 3), ("cabinet", 3), ("jetski", 3)],
              dict(sky_top=(70, 35, 20), sky_bot=(235, 140, 50), ground=(90, 45, 20),
                   road_a=(204, 112, 42), road_b=(188, 100, 36), line=(250, 230, 170),
                   curb_a=(110, 60, 25), curb_b=(240, 200, 120), side_a=(124, 72, 36),
                   side_b=(108, 62, 30), fog=(230, 150, 70), pit_rim=(255, 230, 120))),
        Level(3, "THE VOID AT THE END OF TIME", "THE END", 2100, "void",
              [("gator", 3), ("ship", 2), ("smoke", 3)],
              dict(sky_top=(25, 10, 45), sky_bot=(150, 60, 170), ground=(30, 15, 45),
                   road_a=(74, 44, 106), road_b=(64, 36, 94), line=(190, 120, 255),
                   curb_a=(130, 60, 200), curb_b=(60, 200, 140), side_a=(52, 30, 72),
                   side_b=(44, 26, 62), fog=(120, 50, 150), pit_rim=(200, 100, 255))),
    ]


# --------------------------------------------------------------------------
# Tesseract
# --------------------------------------------------------------------------
class Tesseract:
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

    def update(self, dt, speed):
        self.prev_dist = self.dist
        self.dist -= speed * dt
        self.t += dt
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
    def draw_ground(self, surf, lm, level):
        """Pits are painted flat on the road."""
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

    def _draw_smoke(self, surf, a, s, t):
        pellipse(surf, (40, 10, 60), a, s, -60, -12, 120, 16)
        w, h = max(8, int(240 * s)), max(8, int(190 * s))
        tmp = pygame.Surface((w, h), pygame.SRCALPHA)
        c = (w // 2, h // 2)
        for j in range(9):
            ang = j * 0.7 + t * 1.3
            r = (34 + 10 * math.sin(t * 3 + j)) * s
            px = c[0] + math.cos(ang) * 62 * s * (0.5 + 0.5 * hrand(j, 3))
            py = c[1] + math.sin(ang) * 40 * s * (0.5 + 0.5 * hrand(j, 4))
            pygame.draw.circle(tmp, (110 + j * 8, 40, 160, 170), (int(px), int(py)), max(2, int(r)))
        pygame.draw.circle(tmp, (30, 6, 50, 220), c, max(2, int(34 * s)))
        for ex in (-12, 12):
            pygame.draw.ellipse(tmp, (255, 240, 255, 255), (c[0] + ex * s - 5 * s, c[1] - 5 * s, 10 * s + 2, 6 * s + 1))
        surf.blit(tmp, (a[0] - w / 2, a[1] - 130 * s - h / 2))


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
        pop = 1.0 + max(0.0, 0.7 * math.exp(-t * 9) * math.cos(t * 28))
        pop *= min(1.0, t / 0.08 + 0.001)
        fade = clamp((self.dur - t) / 0.3, 0, 1)
        size = 74
        img = text_surf(self.text, size, self.color, BLACK, 5)
        fit = min(1.0, (W - 40) / img.get_width())
        k = pop * fit
        img = pygame.transform.rotozoom(img, math.sin(t * 6) * 3, k)
        img.set_alpha(int(255 * fade))
        band = pygame.Surface((W, int(img.get_height() * 0.8)), pygame.SRCALPHA)
        band.fill((0, 0, 0, int(110 * fade)))
        surf.blit(band, (0, y - band.get_height() // 2))
        surf.blit(img, img.get_rect(center=(W // 2 + math.sin(t * 40) * 2 * math.exp(-t * 6), y)))


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
            return
        new = self.lm.clamp_lane(self.lane + d)
        if new != self.lane:
            self.lane = new
            self.ghosts.append([self.x, self.jump_h, 0.4, self.pose(), int(self.run_phase * 2) % 12])

    def jump(self):
        if self.can_act and self.jump_h <= 0 and not self.sliding:
            self.vy = JUMP_V

    def slide(self):
        if not self.can_act:
            return
        if self.jump_h > 0:
            self.vy = min(self.vy, -700)       # slam down, slide on landing
            self.pending_slide = True
        else:
            self.slide_t = SLIDE_TIME

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
        if abs(self.x - target) > 1e-3:
            self.ghost_t -= dt
            if self.ghost_t <= 0:
                self.ghosts.append([self.x, self.jump_h, 0.35, self.pose(), int(self.run_phase * 2) % 12])
                self.ghost_t = 0.03
            step = LANE_SPEED * dt
            self.x += clamp(target - self.x, -step, step)
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

    def sprite(self, pose, frame):
        key = (pose, frame if pose == "run" else 0)
        img = self._sprites.get(key)
        if img is None:
            base = self._render("run" if pose == "slide" else pose, key[1])
            k = self.SPRITE_K
            if pose == "slide":
                base = pygame.transform.rotozoom(pygame.transform.smoothscale(base, (int(130 * 1.35), int(190 * 0.55))), 10, 1.0)
                img = pygame.transform.rotozoom(base, 0, k)
            else:
                img = pygame.transform.rotozoom(base, 0, k)
            self._sprites[key] = img
        return img

    def ghost_img(self, pose, frame):
        key = (pose, frame if pose == "run" else 0)
        g = self._ghosts_img.get(key)
        if g is None:
            g = pygame.mask.from_surface(self.sprite(pose, frame)).to_surface(
                setcolor=(90, 255, 150, 255), unsetcolor=(0, 0, 0, 0))
            self._ghosts_img[key] = g
        return g

    def draw(self, surf, t):
        sx = W / 2 + self.x * LANE_W
        gy = PLAYER_Y
        # shadow
        sh = clamp(1 - self.jump_h / 220, 0.35, 1)
        if self.fall <= 0:
            pellipse(surf, (0, 0, 0), (sx, gy), 1, -42 * sh, -8, 84 * sh, 16)
        # illusion trail
        for x, h, life, pose, frame in self.ghosts:
            img = self.ghost_img(pose, frame)
            img.set_alpha(int(170 * life / 0.4))
            surf.blit(img, img.get_rect(midbottom=(W / 2 + x * LANE_W, gy - h)))
        if self.invuln > 0 and self.tumble <= 0 and self.fall <= 0 and self.aura <= 0 and int(t * 18) % 2:
            return
        cy = gy - self.jump_h
        if self.aura > 0:                                               # green magic aura
            pulse = 1 + 0.12 * math.sin(t * 14)
            blit_glow(surf, sx, cy - 80, 150 * pulse, (60, 255, 120), 200)
            for i in range(6):
                ang = t * 4 + i * math.pi / 3
                pygame.draw.circle(surf, (190, 255, 210), (int(sx + math.cos(ang) * 78), int(cy - 80 + math.sin(ang) * 92)), 5)
        if self.fall > 0:                                               # dropping into the pit
            p = 1 - self.fall / FALL_TIME
            img = pygame.transform.rotozoom(self.sprite("jump", 0), p * 200, 1 - p * 0.35)
            old = surf.get_clip()
            surf.set_clip(pygame.Rect(0, 0, W, gy + 6))
            surf.blit(img, img.get_rect(center=(sx, gy - 70 + p * p * 300)))
            surf.set_clip(old)
            return
        if self.tumble > 0:                                             # slapstick somersault
            p = 1 - self.tumble / TUMBLE_TIME
            img = pygame.transform.rotozoom(self.sprite("jump", 0), -p * 720, 1)
            hop = abs(math.sin(p * math.pi * 2)) * 70
            surf.blit(img, img.get_rect(center=(sx, gy - 70 - hop)))
            for i in range(4):
                ang = t * 9 + i * math.pi / 2
                pygame.draw.circle(surf, GOLD, (int(sx + math.cos(ang) * 44), int(gy - 160 - hop + math.sin(ang) * 10)), 5)
            return
        pose = self.pose()
        img = self.sprite(pose, int(self.run_phase * 2) % 12)
        bob = abs(math.sin(self.run_phase)) * 5 if pose == "run" else 0
        surf.blit(img, img.get_rect(midbottom=(sx, cy - bob + (6 if pose == "slide" else 0))))
        if pose == "slide":                                             # dramatic sparks
            for i in range(5):
                pygame.draw.line(surf, (255, 220, 120), (sx - 50 + i * 24, gy - 4), (sx - 60 + i * 24 - 12, gy - 4 - i % 3 * 5), 2)


# --------------------------------------------------------------------------
# GameState: rules and progression
# --------------------------------------------------------------------------
class GameState:
    TITLE, PLAYING, PAUSED, GAME_OVER, VICTORY = range(5)
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
        self.reset_run()

    @property
    def level(self):
        return self.levels[self.level_idx]

    def reset_run(self):
        self.lives = START_LIVES
        self.score = 0.0
        self.tess = 0
        self.meter = 0.0
        self.level_idx = 0
        self.level_dist = 0.0
        self.total_dist = 0.0
        self.play_time = 0.0
        self.speed = BASE_SPEED
        self.speed_mult = 1.0
        self.obstacles = []
        self.tesseracts = []
        self.next_spawn = 55.0
        self.card_t = 0.0
        self.meter_ready_said = False
        self.victory_t = 0.0
        self.player.reset()
        self.particles.clear()
        self.banners.clear()

    def start_game(self):
        self.reset_run()
        self.mode = self.PLAYING
        self.card_t = 3.6
        self.add_banner("BURDENED WITH GLORIOUS PURPOSE!", (110, 255, 140), 2.8)

    # -- fx -------------------------------------------------------------
    def add_banner(self, text, color, dur=2.4):
        self.banners.append(Banner(text, color, dur))
        if len(self.banners) > 3:
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
            p.move(-1)
        elif name == "right":
            p.move(1)
        elif name == "jump":
            p.jump()
        elif name == "slide":
            p.slide()
        elif name == "aura":
            self.activate_aura()

    def activate_aura(self):
        p = self.player
        if self.meter >= 100 and p.aura <= 0 and p.can_act:
            p.aura = AURA_TIME
            self.add_banner("LOVE IS A DAGGER!", (90, 255, 150), 2.4)
            self.flash, self.flash_color = 0.25, (80, 255, 140)
            self.shake = 0.35
            for o in self.obstacles:
                if o.active and o.kind != "pit" and o.dist < 30:
                    self.blast_obstacle(o)

    def blast_obstacle(self, o):
        o.blast(1 if o.lane_x >= self.player.x else -1)
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
        if self.mode == self.TITLE:
            self.travel += 14 * dt
            p.update(dt, 14)
            return
        if self.mode == self.PAUSED:
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
        want = 0.5 if (p.tumble > 0 or p.fall > 0) else 1.0
        self.speed_mult += (want - self.speed_mult) * min(1, 4 * dt)
        self.speed = target * self.speed_mult * (1.12 if p.aura > 0 else 1.0)
        step = self.speed * dt
        self.travel += step
        self.level_dist += step
        self.total_dist += step
        self.score += step * 0.5
        p.update(dt, self.speed)
        self.spawn(target)
        for o in self.obstacles:
            o.update(dt, self.speed)
        for c in self.tesseracts:
            c.update(dt, self.speed)
        self.collisions()
        self.obstacles = [o for o in self.obstacles if not o.dead]
        self.tesseracts = [c for c in self.tesseracts if not c.dead]
        if p.aura > 0:
            self.meter = 100.0 * p.aura / AURA_TIME
            for o in self.obstacles:
                if o.active and o.kind != "pit" and o.dist < 16 and abs(o.lane_x - p.x) < 1.7:
                    self.blast_obstacle(o)
            sx = W / 2 + p.x * LANE_W
            self.particles.append(Particle(sx + random.uniform(-50, 50), PLAYER_Y - p.jump_h - random.uniform(10, 150),
                                           random.uniform(-30, 30), -random.uniform(60, 160), 0.6, (110, 255, 160), 5))
        elif self.meter > 100:
            self.meter = 100
        if self.level_dist >= self.level.length:
            self.finish_level()

    # -- spawning ---------------------------------------------------------
    def lane_blocked(self, lane, d0, d1):
        lx = lane - 1
        for o in self.obstacles:
            if d0 <= o.dist <= d1 and abs(o.lane_x - lx) < 1.0:
                return True
        return False

    def spawn(self, speed):
        lvl = self.level
        while self.level_dist + SPAWN_D >= self.next_spawn:
            pos = self.next_spawn
            if pos <= lvl.length - 30:
                self.spawn_event(pos - self.level_dist, speed)
            self.next_spawn += max(20.0, speed * random.uniform(0.85, 1.25) * (1 - 0.07 * self.level_idx))

    def spawn_event(self, dist, speed):
        r = random.random()
        lanes = [0, 1, 2]
        if r < 0.28:                                                  # a line of Tesseracts
            free = [ln for ln in lanes if not self.lane_blocked(ln, dist - 6, dist + 16)]
            if free:
                ln = random.choice(free)
                for i in range(6):
                    self.tesseracts.append(Tesseract(ln - 1, dist + i * 2.6))
            return
        if r < 0.42:                                                  # pit(s) in the road
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
                c.collected = True
                self.tess += 1
                self.score += 50
                if p.aura <= 0:
                    self.meter = min(100.0, self.meter + METER_GAIN)
                    if self.meter >= 100 and not self.meter_ready_said:
                        self.meter_ready_said = True
                        self.add_banner("PRESS F FOR GLORIOUS PURPOSE!", (120, 220, 255), 1.8)
                self.burst(W / 2 + c.lane_x * LANE_W, PLAYER_Y - 60, CYAN, 8, 200, 4, 300)
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

    def hurt(self, o):
        p = self.player
        self.lives -= 1
        self.shake = 0.5
        if o.kind == "pit":
            o.hit = True
            p.start_fall()
            self.add_banner("I'VE BEEN FALLING FOR 30 MINUTES!", (120, 230, 255), 2.8)
        else:
            o.blast(1 if o.lane_x >= p.x else -1)
            o.hit = True
            p.start_tumble()
            self.flash, self.flash_color = 0.18, (255, 70, 60)
            if o.kind == "heavy":
                self.add_banner("PUNY GOD!", (255, 90, 70), 2.0)
            else:
                self.add_banner(random.choice(self.LOW_HIT_QUIPS), (255, 200, 60), 2.0)
            lost = min(self.tess, 10)
            self.tess -= lost
            self.meter = max(0.0, self.meter - 25)
            self.meter_ready_said = self.meter >= 100
            sx, sy = W / 2 + p.x * LANE_W, PLAYER_Y - 90
            for _ in range(lost):                                    # scatter!
                a = random.uniform(-math.pi, 0)
                v = random.uniform(180, 420)
                self.particles.append(Particle(sx, sy, math.cos(a) * v, math.sin(a) * v, random.uniform(1.0, 1.6),
                                               CYAN, 11, 900, "cube"))
            self.burst(sx, sy, GOLD, 14)
        if self.lives <= 0:
            self.mode = self.GAME_OVER
            self.add_banner("THE TRICKSTER HAS FALLEN...", (255, 120, 120), 3.5)

    def finish_level(self):
        self.score += 1000
        if self.level_idx >= len(self.levels) - 1:
            self.score += self.lives * 500 + self.tess * 10
            self.mode = self.VICTORY
            self.banners.clear()
            self.flash, self.flash_color = 0.6, GOLD
            return
        self.level_idx += 1
        self.level_dist = 0.0
        self.next_spawn = 70.0
        self.obstacles.clear()
        self.tesseracts.clear()
        self.card_t = 3.6
        self.flash, self.flash_color = 0.5, WHITE
        self.add_banner("LEVEL CLEAR! +1000", GOLD, 2.0)


# --------------------------------------------------------------------------
# Game: window, input and rendering
# --------------------------------------------------------------------------
class Game:
    def __init__(self):
        pygame.init()
        pygame.display.set_caption("LokiMan -- Burdened With Glorious Purpose")
        self.screen = pygame.display.set_mode((W, H), pygame.SCALED | pygame.RESIZABLE)
        self.world = pygame.Surface((W, H))
        self.clock = pygame.time.Clock()
        self.state = GameState()
        self.running = True

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
            elif e.key in (pygame.K_RETURN, pygame.K_KP_ENTER):
                if st.mode in (st.TITLE, st.GAME_OVER, st.VICTORY):
                    st.start_game()
            elif e.key == pygame.K_p:
                if st.mode == st.PLAYING:
                    st.mode = st.PAUSED
                elif st.mode == st.PAUSED:
                    st.mode = st.PLAYING
            elif e.key in self.KEYS:
                if st.mode in (st.TITLE, st.GAME_OVER, st.VICTORY) and e.key == pygame.K_SPACE:
                    st.start_game()
                else:
                    st.action(self.KEYS[e.key])

    def run(self):
        while self.running:
            dt = self.clock.tick(FPS) / 1000.0
            for e in pygame.event.get():
                self.handle_event(e)
            self.state.update(dt)
            self.draw()
            pygame.display.flip()
        pygame.quit()

    # -- world ----------------------------------------------------------
    def draw_world(self, surf):
        st = self.state
        lm, lvl, p = st.lm, st.level, st.player
        lm.draw_ground(surf, lvl, st.travel)
        for o in st.obstacles:
            if o.kind == "pit":
                o.draw_ground(surf, lm, lvl)
        lvl.draw_decor(surf, lm, st.travel)
        items = [(o.dist, 0, o) for o in st.obstacles if o.kind != "pit"]
        items += [(c.dist, 1, c) for c in st.tesseracts]
        items.append((0.0, 2, p))
        items.sort(key=lambda it: (-it[0], it[1]))
        for _, _, obj in items:
            if obj is p:
                p.draw(surf, st.t)
            elif isinstance(obj, Tesseract):
                obj.draw(surf, lm)
            else:
                obj.draw(surf, lm)
        for part in st.particles:
            part.draw(surf)
        if st.player.aura > 0:                                    # green screen tint
            tint = pygame.Surface((W, H), pygame.SRCALPHA)
            tint.fill((40, 255, 120, int(26 + 10 * math.sin(st.t * 12))))
            surf.blit(tint, (0, 0))

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
        panel = pygame.Surface((400, 92), pygame.SRCALPHA)
        panel.fill((0, 0, 0, 140))
        surf.blit(panel, (10, 10))
        draw_text(surf, f"LEVEL {lvl.num}/3", 26, GOLD, (20, 16), "topleft", ow=2)
        draw_text(surf, lvl.title, 18, WHITE, (20, 44), "topleft", ow=2)
        left = max(0, int(lvl.length - st.level_dist))
        draw_text(surf, f"DISTANCE REMAINING: {left} m", 20, (150, 255, 180), (20, 66), "topleft", ow=2)
        frac = clamp(st.level_dist / lvl.length, 0, 1)
        pygame.draw.rect(surf, (30, 30, 30), (20, 90, 380, 8))
        pygame.draw.rect(surf, GREEN, (20, 90, int(380 * frac), 8))
        pygame.draw.rect(surf, WHITE, (20, 90, 380, 8), 1)

        panel2 = pygame.Surface((250, 92), pygame.SRCALPHA)
        panel2.fill((0, 0, 0, 140))
        surf.blit(panel2, (W - 260, 10))
        draw_text(surf, f"{int(st.score):07d}", 38, WHITE, (W - 20, 14), "topright")
        draw_cube(surf, W - 232, 78, 14, st.t * 3)
        draw_text(surf, f"x {st.tess}", 30, CYAN, (W - 205, 62), "topleft", ow=2)

        for i in range(START_LIVES):
            self.helmet_icon(surf, 40 + i * 48, H - 52, 1.0, dim=i >= st.lives)
        draw_text(surf, "LIVES", 16, WHITE, (20, H - 22), "topleft", ow=2)

        bx, by, bw, bh = W // 2 - 190, H - 46, 380, 24
        pygame.draw.rect(surf, (10, 25, 15), (bx - 4, by - 4, bw + 8, bh + 8))
        full = st.meter >= 100
        for i in range(int(bw * clamp(st.meter / 100, 0, 1))):
            pygame.draw.line(surf, mix((30, 180, 80), GOLD, i / bw), (bx + i, by), (bx + i, by + bh))
        pygame.draw.rect(surf, GOLD if (full and int(st.t * 6) % 2) else WHITE, (bx - 4, by - 4, bw + 8, bh + 8), 3)
        label = "LOVE IS A DAGGER!" if st.player.aura > 0 else "GLORIOUS PURPOSE"
        draw_text(surf, label, 18, WHITE, (W // 2, by - 14), "center", ow=2)
        if full and st.player.aura <= 0 and int(st.t * 5) % 2 == 0:
            draw_text(surf, "PRESS  F !", 24, (150, 255, 170), (W // 2, by + bh // 2), "center", ow=2)

    def draw_banners(self, surf):
        st = self.state
        for i, b in enumerate(st.banners):
            b.draw(surf, 190 + i * 78)

    # -- overlays -------------------------------------------------------
    def draw_level_card(self, surf):
        st = self.state
        t = st.card_t
        if t <= 0:
            return
        a = clamp(min(t, 3.6 - t) / 0.35, 0, 1)
        lvl = st.level
        band = pygame.Surface((W, 120), pygame.SRCALPHA)
        band.fill((0, 0, 0, int(160 * a)))
        surf.blit(band, (0, 350))
        for img_args in ((f"LEVEL {lvl.num}", 56, GOLD, 384), (lvl.title, 34, WHITE, 436), (f"~ {lvl.year} ~", 22, (150, 255, 180), 466)):
            img = text_surf(img_args[0], img_args[1], img_args[2], BLACK, 3)
            img = img.copy()
            img.set_alpha(int(255 * a))
            surf.blit(img, img.get_rect(center=(W // 2, img_args[3])))

    def draw_title(self, surf):
        st = self.state
        dim = pygame.Surface((W, H), pygame.SRCALPHA)
        dim.fill((0, 0, 0, 110))
        surf.blit(dim, (0, 0))
        bob = math.sin(st.t * 3) * 6
        img = text_surf("LOKIMAN", 150, GREEN, (10, 40, 20), 8)
        surf.blit(img, img.get_rect(center=(W // 2, 130 + bob)))
        draw_text(surf, "BURDENED WITH GLORIOUS PURPOSE", 30, (150, 255, 180), (W // 2, 215), ow=3)
        lines = ["A / D  or  LEFT / RIGHT   -  SHIFT LANES",
                 "W / UP / SPACE   -  JUMP LOW BARRIERS",
                 "S / DOWN   -  SLIDE UNDER HIGH HAZARDS",
                 "F   -  GLORIOUS PURPOSE (METER FULL)     P  -  PAUSE"]
        for i, ln in enumerate(lines):
            draw_text(surf, ln, 22, WHITE, (W // 2, 380 + i * 30), ow=2)
        draw_text(surf, "COLLECT THE TESSERACTS. OUTRUN THE MULTIVERSE.", 22, CYAN, (W // 2, 340), ow=2)
        if int(st.t * 2.5) % 2 == 0:
            draw_text(surf, "PRESS ENTER TO RUN", 44, GOLD, (W // 2, 540), ow=4)

    def draw_end(self, surf, victory):
        st = self.state
        dim = pygame.Surface((W, H), pygame.SRCALPHA)
        dim.fill((0, 0, 0, 170 if victory else 150))
        surf.blit(dim, (0, 0))
        if victory:
            t = st.victory_t
            k = 1 + 0.04 * math.sin(t * 5)
            for txt, size, col, y in (("MULTIVERSE SAVED...", 84, (120, 255, 160), 130),
                                       ("KNEEL BEFORE YOUR KING!", 70, GOLD, 230)):
                img = text_surf(txt, size, col, BLACK, 6)
                fit = min(1.0, (W - 40) / img.get_width())
                img = pygame.transform.rotozoom(img, math.sin(t * 2) * 2, k * fit)
                surf.blit(img, img.get_rect(center=(W // 2, y)))
            big = pygame.transform.rotozoom(st.player.sprite("jump", 0), 0, 1.1)
            surf.blit(big, big.get_rect(midbottom=(W // 2, 585 + math.sin(t * 4) * 6)))
            blit_glow(surf, W // 2, 490, 160, (90, 255, 140), 90)
            draw_text(surf, f"FINAL SCORE  {int(st.score):07d}", 46, WHITE, (W // 2, 320), ow=4)
            draw_text(surf, f"TESSERACTS {st.tess}     LIVES LEFT {max(0, st.lives)}", 26, CYAN, (W // 2, 372), ow=3)
            if int(t * 2.5) % 2 == 0:
                draw_text(surf, "PRESS ENTER TO RULE AGAIN", 30, GOLD, (W // 2, 618), ow=3)
        else:
            draw_text(surf, "GAME OVER", 110, (255, 90, 80), (W // 2, 230), ow=6)
            draw_text(surf, f"SCORE  {int(st.score):07d}", 44, WHITE, (W // 2, 340), ow=4)
            draw_text(surf, f"REACHED LEVEL {st.level.num}  -  {int(st.total_dist)} m", 26, CYAN, (W // 2, 392), ow=3)
            if int(st.t * 2.5) % 2 == 0:
                draw_text(surf, "PRESS ENTER TO TRY AGAIN", 34, GOLD, (W // 2, 480), ow=3)

    def draw(self):
        st = self.state
        self.draw_world(self.world)
        ox = oy = 0
        if st.shake > 0:
            ox, oy = random.randint(-7, 7), random.randint(-5, 5)
        self.screen.fill(BLACK)
        self.screen.blit(self.world, (ox, oy))
        if st.flash > 0:
            fl = pygame.Surface((W, H), pygame.SRCALPHA)
            fl.fill(st.flash_color + (int(160 * clamp(st.flash / 0.4, 0, 1)),))
            self.screen.blit(fl, (0, 0))
        scr = self.screen
        if st.mode == st.TITLE:
            self.draw_title(scr)
            return
        if st.mode != st.VICTORY:
            self.draw_hud(scr)
        self.draw_banners(scr)
        if st.mode == st.PLAYING or st.mode == st.PAUSED:
            self.draw_level_card(scr)
        if st.mode == st.PAUSED:
            dim = pygame.Surface((W, H), pygame.SRCALPHA)
            dim.fill((0, 0, 0, 140))
            scr.blit(dim, (0, 0))
            draw_text(scr, "PAUSED", 100, GOLD, (W // 2, H // 2), ow=6)
        elif st.mode == st.GAME_OVER:
            self.draw_end(scr, False)
        elif st.mode == st.VICTORY:
            self.draw_end(scr, True)


def main():
    Game().run()


if __name__ == "__main__":
    main()
