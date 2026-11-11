"""
Draws the rbr2stj logo, derived from the STJ logo (docs/logo/make_logo.py of the STJ standard): the same wireframe road
with its orange racing line, and the name in the same stroked block letters: RBR, an orange arrow, STJ.

    python docs/logo/make_logo.py          # writes docs/logo/rbr2stj_logo.svg
"""

import math
from pathlib import Path

HERE = Path(__file__).resolve().parent
ORANGE = "#fa6b1d"          # sampled from SVJ's logo
BLACK = "#000000"
SIZE = 1254


def bezier(p, u):
    a = (1 - u) ** 3
    b = 3 * (1 - u) ** 2 * u
    c = 3 * (1 - u) * u ** 2
    d = u ** 3
    return (a * p[0][0] + b * p[1][0] + c * p[2][0] + d * p[3][0],
            a * p[0][1] + b * p[1][1] + c * p[2][1] + d * p[3][1])


def road():
    """Wireframe road ribbon in perspective, its node dots and the racing line."""
    ctrl = [(268, 462), (610, 530), (540, 170), (985, 208)]
    n = 14
    half = 50
    left, right, mid = [], [], []
    for k in range(n + 1):
        u = k / n
        x, y = bezier(ctrl, u)
        x2, y2 = bezier(ctrl, min(1.0, u + 1e-3))
        x1, y1 = bezier(ctrl, max(0.0, u - 1e-3))
        dx, dy = x2 - x1, y2 - y1
        ln = math.hypot(dx, dy)
        nx, ny = -dy / ln, dx / ln
        left.append((x + nx * half, y + ny * half))
        right.append((x - nx * half, y - ny * half))
        mid.append((x, y, nx, ny))
    pts = lambda seq: " ".join(f"{x:.1f},{y:.1f}" for x, y in seq)
    seg = lambda a, b, sw: (f'<line x1="{a[0]:.1f}" y1="{a[1]:.1f}" x2="{b[0]:.1f}" y2="{b[1]:.1f}" '
                            f'stroke="{BLACK}" stroke-width="{sw}" stroke-linecap="round"/>')
    parts = []
    # an irregular triangulated mesh, like the node network inside SVJ's car
    links = [(0, "L", 2, "R"), (2, "R", 3, "L"), (3, "L", 5, "R"), (5, "R", 7, "L"), (7, "L", 8, "R"),
             (8, "R", 10, "L"), (10, "L", 12, "R"), (12, "R", 14, "L"), (2, "R", 5, "R"), (7, "L", 10, "L")]
    side = {"L": left, "R": right}
    for a, sa, b, sb in links:
        parts.append(seg(side[sa][a], side[sb][b], 6))
    for k in (0, 3, 7, 10, 14):
        parts.append(seg(left[k], right[k], 6))
    # road edges, heavier, like the car outline in SVJ's logo
    for edge in (left, right):
        parts.append(f'<polyline points="{pts(edge)}" fill="none" stroke="{BLACK}" stroke-width="13" '
                     'stroke-linecap="round" stroke-linejoin="round"/>')
    # node dots where the mesh lines meet
    for x, y in (right[2], left[7], right[8], left[12]):
        parts.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="12" fill="{BLACK}"/>')
    # racing line: inside the road, swapping sides through the bend, leaving as an arrow
    line = []
    for k, (x, y, nx, ny) in enumerate(mid[:-1]):
        off = 0.5 * half * math.cos(math.pi * k / n * 2.0)
        line.append((x + nx * off, y + ny * off))
    ex, ey = line[-1]
    px, py = line[-2]
    ux, uy = ex - px, ey - py
    ul = math.hypot(ux, uy)
    ux, uy = ux / ul, uy / ul
    # bend upwards out of the road, as SVJ's arrow leaves the V
    ang = math.atan2(uy, ux) - 0.55
    tip = (ex + 150 * math.cos(ang), ey + 150 * math.sin(ang))
    ax, ay = math.cos(ang), math.sin(ang)
    base = (tip[0] - ax * 64, tip[1] - ay * 64)
    parts.append(f'<polyline points="{pts(line + [(base[0] + ax * 8, base[1] + ay * 8)])}" fill="none" stroke="{ORANGE}" '
                 'stroke-width="16" stroke-linecap="butt" stroke-linejoin="round"/>')
    wing = 30
    head = [tip, (base[0] - ay * wing, base[1] + ax * wing), (base[0] + ay * wing, base[1] - ax * wing)]
    parts.append(f'<polygon points="{pts(head)}" fill="{ORANGE}"/>')
    return "\n  ".join(parts)



W = 40                       # stroke of the letters
R = 30                       # corner radius
TOP, BOTTOM = 650, 820
CELL = 125                   # outer width of a letter
GAP = 24
ARROW = 120


def glyph(ch, x0):
    """Stroked centre-line path of one letter whose outer box starts at x0."""
    a, b = x0 + W / 2, x0 + CELL - W / 2
    yt, yb, ym = TOP + W / 2, BOTTOM - W / 2, (TOP + BOTTOM) / 2
    if ch == "R":
        return (f"M {a},{BOTTOM} V {yt} H {b - R} A {R} {R} 0 0 1 {b},{yt + R} V {ym - R} A {R} {R} 0 0 1 {b - R},{ym} "
                f"H {a} M {a + 30},{ym} L {b},{BOTTOM}")
    if ch == "B":
        b1, r = b - 14, 26
        return (f"M {a},{yt} H {b1 - r} A {r} {r} 0 0 1 {b1},{yt + r} V {ym - r} A {r} {r} 0 0 1 {b1 - r},{ym} H {a} "
                f"M {a},{ym} H {b - r} A {r} {r} 0 0 1 {b},{ym + r} V {yb - r} A {r} {r} 0 0 1 {b - r},{yb} H {a} "
                f"M {a},{yt} V {yb}")
    if ch == "S":
        xl, xr = x0 + W / 2, x0 + CELL - W / 2
        return (f"M {xr},{yt} H {xl + R} A {R} {R} 0 0 0 {xl},{yt + R} V {ym - R} A {R} {R} 0 0 0 {xl + R},{ym} "
                f"H {xr - R} A {R} {R} 0 0 1 {xr},{ym + R} V {yb - R} A {R} {R} 0 0 1 {xr - R},{yb} H {x0}")
    if ch == "T":
        cx = x0 + CELL / 2
        return f"M {x0},{yt} H {x0 + CELL} M {cx},{yt} V {BOTTOM}"
    if ch == "J":
        jx = x0 + CELL - W / 2
        return f"M {jx},{TOP} V {yb - R} A {R} {R} 0 0 1 {jx - R},{yb} H {x0}"
    raise ValueError(ch)


def letters():
    seq = "RBR>STJ"
    total = 6 * CELL + ARROW + 6 * GAP
    x = (SIZE - total) / 2
    common = f'fill="none" stroke-width="{W}" stroke-linecap="butt" stroke-linejoin="round"'
    out = []
    for ch in seq:
        if ch == ">":
            ym = (TOP + BOTTOM) / 2
            out.append(f'<path d="M {x},{ym} H {x + ARROW - 34}" stroke="{ORANGE}" {common}/>')
            out.append(f'<polygon points="{x + ARROW},{ym} {x + ARROW - 52},{ym - 38} {x + ARROW - 52},{ym + 38}" fill="{ORANGE}"/>')
            x += ARROW + GAP
        else:
            out.append(f'<path d="{glyph(ch, x)}" stroke="{BLACK}" {common}/>')
            x += CELL + GAP
    return "\n  ".join(out)


def main():
    svg = f'''<svg xmlns="http://www.w3.org/2000/svg" width="{SIZE}" height="{SIZE}" viewBox="0 0 {SIZE} {SIZE}">
  <title>rbr2stj</title>
  <rect width="{SIZE}" height="{SIZE}" fill="#ffffff"/>
  {road()}
  {letters()}
</svg>
'''
    (HERE / "rbr2stj_logo.svg").write_text(svg, encoding="utf-8")
    print("wrote", HERE / "rbr2stj_logo.svg")


if __name__ == "__main__":
    main()
