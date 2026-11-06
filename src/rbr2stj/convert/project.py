"""Reference line geometry: evaluate the driveline as STJ defines it and project points onto it.

STJ (spec §4.2, §7.1): `s` is the stored chainage where given, within a segment s maps linearly onto the Hermite
parameter u (0..1); the centerline passes through the points with cubic Hermite tangents dP/du per point. `t` is the
lateral offset, positive to the LEFT of the race direction. Heading is counter-clockwise from +X.

This is our own implementation of that definition (original code); the STJ validator has its own, and the tests
compare the two on the synthetic stage.
"""

from __future__ import annotations

import math


class RefLine:
    SAMPLES = 16        # dense samples per segment used for projection

    def __init__(self, points, tangents=None, chainage=None, tangents_in=None):
        self.points = [tuple(float(c) for c in p) for p in points]
        self.tangents = [tuple(float(c) for c in t) for t in tangents] if tangents else None
        self.t_in = [tuple(float(c) for c in t) for t in tangents_in] if tangents_in else self.tangents
        n = len(self.points)
        if n < 2:
            raise ValueError("a reference line needs at least two points")
        if chainage is None:
            s = [0.0]
            for a, b in zip(self.points, self.points[1:]):
                s.append(s[-1] + math.hypot(b[0] - a[0], b[1] - a[1]))
            self.s = s
        else:
            self.s = [float(c) for c in chainage]
        self.length = self.s[-1]
        self._dense = self._build_dense()

    def _pos(self, i: int, u: float):
        a, b = self.points[i], self.points[i + 1]
        if not self.tangents:
            return tuple(a[k] + u * (b[k] - a[k]) for k in range(3))
        ta, tb = self.tangents[i], self.t_in[i + 1]
        u2, u3 = u * u, u * u * u
        h00, h10, h01, h11 = 2 * u3 - 3 * u2 + 1, u3 - 2 * u2 + u, -2 * u3 + 3 * u2, u3 - u2
        return tuple(h00 * a[k] + h10 * ta[k] + h01 * b[k] + h11 * tb[k] for k in range(3))

    def _deriv(self, i: int, u: float):
        a, b = self.points[i], self.points[i + 1]
        if not self.tangents:
            return tuple(b[k] - a[k] for k in range(3))
        ta, tb = self.tangents[i], self.t_in[i + 1]
        u2 = u * u
        d00, d10, d01, d11 = 6 * u2 - 6 * u, 3 * u2 - 4 * u + 1, -6 * u2 + 6 * u, 3 * u2 - 2 * u
        return tuple(d00 * a[k] + d10 * ta[k] + d01 * b[k] + d11 * tb[k] for k in range(3))

    def _build_dense(self):
        out = []
        for i in range(len(self.points) - 1):
            n = self.SAMPLES if self.tangents else 1
            for k in range(n):
                u = k / n
                x, y, _z = self._pos(i, u)
                out.append((x, y, self.s[i] + u * (self.s[i + 1] - self.s[i])))
        x, y, _z = self.points[-1]
        out.append((x, y, self.s[-1]))
        return out

    def _segment(self, s: float):
        s = min(max(s, self.s[0]), self.s[-1])
        lo, hi = 0, len(self.s) - 1
        while hi - lo > 1:
            mid = (lo + hi) // 2
            if self.s[mid] <= s:
                lo = mid
            else:
                hi = mid
        return lo, s

    def frame(self, s: float):
        """(x, y, z, heading) at s."""
        i, s = self._segment(s)
        ds = self.s[i + 1] - self.s[i]
        u = 0.0 if ds < 1e-12 else (s - self.s[i]) / ds
        x, y, z = self._pos(i, u)
        dx, dy, _dz = self._deriv(i, u)
        return x, y, z, math.atan2(dy, dx)

    def heading(self, s: float) -> float:
        return self.frame(s)[3]

    def point(self, s: float, t: float = 0.0):
        x, y, z, h = self.frame(s)
        return (x - t * math.sin(h), y + t * math.cos(h), z)

    def project(self, x: float, y: float):
        """(s, t) of the nearest point on the line; t > 0 to the left."""
        best = None
        d = self._dense
        for a, b in zip(d, d[1:]):
            dx, dy = b[0] - a[0], b[1] - a[1]
            ds = dx * dx + dy * dy
            u = 0.0 if ds < 1e-18 else max(0.0, min(1.0, ((x - a[0]) * dx + (y - a[1]) * dy) / ds))
            d2 = (x - a[0] - u * dx) ** 2 + (y - a[1] - u * dy) ** 2
            if best is None or d2 < best[0]:
                seg = math.sqrt(ds)
                side = 0.0 if seg < 1e-9 else (dx * (y - a[1]) - dy * (x - a[0])) / seg
                best = (d2, a[2] + u * (b[2] - a[2]), side)
        return best[1], best[2]

    def xy_length(self) -> float:
        """Geometric x/y length of the curve (sum of dense samples)."""
        d = self._dense
        return sum(math.hypot(b[0] - a[0], b[1] - a[1]) for a, b in zip(d, d[1:]))

    def length_3d(self) -> float:
        total = 0.0
        prev = self._pos(0, 0.0)
        for i in range(len(self.points) - 1):
            n = self.SAMPLES
            for k in range(1, n + 1):
                p = self._pos(i, k / n)
                total += math.dist(prev, p)
                prev = p
        return total
