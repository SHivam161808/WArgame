#!/usr/bin/env python3
"""Procedural low-poly glTF 2.0 (.glb) model generator for the wargame web app.

Dependency-free (stdlib only): geometry primitives, a tiny scene graph and a GLB encoder.
Usage (from project root):  python3 tools/gen_models.py
Output: webapp/static/models/<id>_blue.glb, <id>_red.glb, missile.glb

Conventions: metres, +Y up, +Z = vehicle front, +X = vehicle left.
Ground vehicles: origin on the ground under the hull centre (lowest track/wheel point y = 0,
hover pads at y ~ 0.4). Aircraft and the missile: origin at the centre of mass.
Named nodes: body (root) > turret (yaws about local Y) > gun (pitches about local X, barrel +Z).
Gunship: rotor_l / rotor_r (spin about local Y). UAV: prop (spins about local Z).
"""
import json
import math
import os
import struct

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, 'webapp', 'static', 'models')
D = math.pi / 180.0
S2 = (-1, 1)


# ------------------------------------------------------------------ vector math
def add(a, b): return [a[0] + b[0], a[1] + b[1], a[2] + b[2]]
def sub(a, b): return [a[0] - b[0], a[1] - b[1], a[2] - b[2]]
def mul_s(a, s): return [a[0] * s, a[1] * s, a[2] * s]
def dot(a, b): return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]
def cross(a, b): return [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]]
def vlen(a): return math.sqrt(a[0] * a[0] + a[1] * a[1] + a[2] * a[2])
def dist(a, b): return vlen(sub(a, b))
def lerp(a, b, t): return [a[i] + (b[i] - a[i]) * t for i in range(3)]


def norm(a):
    l = vlen(a) or 1.0
    return [a[0] / l, a[1] / l, a[2] / l]


# affine transform = (3x3 row-major list m, translation t)
def ID(): return ([1, 0, 0, 0, 1, 0, 0, 0, 1], [0, 0, 0])
def xf3(m, p): return [m[0] * p[0] + m[1] * p[1] + m[2] * p[2], m[3] * p[0] + m[4] * p[1] + m[5] * p[2], m[6] * p[0] + m[7] * p[1] + m[8] * p[2]]


def mmul(A, B):
    a, b = A[0], B[0]
    m = [a[r * 3] * b[c] + a[r * 3 + 1] * b[3 + c] + a[r * 3 + 2] * b[6 + c] for r in range(3) for c in range(3)]
    return (m, add(xf3(a, B[1]), A[1]))


def T(x, y, z): return ([1, 0, 0, 0, 1, 0, 0, 0, 1], [x, y, z])
def RX(a): c, s = math.cos(a), math.sin(a); return ([1, 0, 0, 0, c, -s, 0, s, c], [0, 0, 0])
def RY(a): c, s = math.cos(a), math.sin(a); return ([c, 0, s, 0, 1, 0, -s, 0, c], [0, 0, 0])
def RZ(a): c, s = math.cos(a), math.sin(a); return ([c, -s, 0, s, c, 0, 0, 0, 1], [0, 0, 0])


def TR(x, y, z, rx=0.0, ry=0.0, rz=0.0):
    """translate, then rotate local geometry by R = Ry * Rx * Rz"""
    return mmul(T(x, y, z), mmul(RY(ry), mmul(RX(rx), RZ(rz))))


def basis(o, ex, ey, ez):
    return ([ex[0], ey[0], ez[0], ex[1], ey[1], ez[1], ex[2], ey[2], ez[2]], list(o))


def inv_t3(m):
    """inverse-transpose of a 3x3 (cofactor matrix / det) for normal transforms"""
    a, b, c, d, e, f, g, h, i = m
    A, B, C = e * i - f * h, -(d * i - f * g), d * h - e * g
    det = a * A + b * B + c * C or 1.0
    Dd, E, F = -(b * i - c * h), a * i - c * g, -(a * h - b * g)
    G, H, I = b * f - c * e, -(a * f - c * d), a * e - b * d
    return [v / det for v in (A, B, C, Dd, E, F, G, H, I)]


def perp(w):
    a = [1, 0, 0] if abs(w[0]) < 0.9 else [0, 1, 0]
    return norm(cross(w, a))


def newell(pts):
    n = [0.0, 0.0, 0.0]
    k = len(pts)
    for i in range(k):
        a, b = pts[i], pts[(i + 1) % k]
        n[0] += (a[1] - b[1]) * (a[2] + b[2])
        n[1] += (a[2] - b[2]) * (a[0] + b[0])
        n[2] += (a[0] - b[0]) * (a[1] + b[1])
    return n


def centroid(pts):
    s = [0.0, 0.0, 0.0]
    for p in pts:
        s = add(s, p)
    return mul_s(s, 1.0 / len(pts))


def earclip(pts, n):
    """triangulate a planar (possibly concave) polygon; returns index triples"""
    ax, ay, az = abs(n[0]), abs(n[1]), abs(n[2])
    if ax >= ay and ax >= az:
        i0, i1 = 1, 2
    elif ay >= az:
        i0, i1 = 2, 0
    else:
        i0, i1 = 0, 1
    P = [(p[i0], p[i1]) for p in pts]
    area = sum(P[i][0] * P[(i + 1) % len(P)][1] - P[(i + 1) % len(P)][0] * P[i][1] for i in range(len(P)))
    V = list(range(len(P)))
    if area < 0:
        V.reverse()

    def cr(a, b, c): return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])
    out, guard = [], 0
    while len(V) > 3 and guard < 5000:
        guard += 1
        found = False
        for k in range(len(V)):
            ip, ic, inx = V[k - 1], V[k], V[(k + 1) % len(V)]
            a, b, c = P[ip], P[ic], P[inx]
            if cr(a, b, c) <= 1e-12:
                continue
            inside = False
            for j in V:
                if j in (ip, ic, inx):
                    continue
                q = P[j]
                if cr(a, b, q) >= 0 and cr(b, c, q) >= 0 and cr(c, a, q) >= 0:
                    inside = True
                    break
            if inside:
                continue
            out.append((ip, ic, inx))
            V.pop(k)
            found = True
            break
        if not found:
            break
    for k in range(1, len(V) - 1):
        out.append((V[0], V[k], V[k + 1]))
    return out


def hull2(points):
    """2D convex hull (monotone chain), CCW"""
    p = sorted(points)

    def cr(o, a, b): return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])
    lo, up = [], []
    for q in p:
        while len(lo) >= 2 and cr(lo[-2], lo[-1], q) <= 1e-9:
            lo.pop()
        lo.append(q)
    for q in reversed(p):
        while len(up) >= 2 and cr(up[-2], up[-1], q) <= 1e-9:
            up.pop()
        up.append(q)
    return lo[:-1] + up[:-1]


def sym_sec(z, half):
    """symmetric ring from a half profile [[x, y], ...] listed bottom -> top (x > 0)"""
    return [[x, y, z] for x, y in half] + [[-x, y, z] for x, y in reversed(half)]


def rect(z, hx, y0, y1): return sym_sec(z, [[hx, y0], [hx, y1]])


def oval(z, rx, ry, yc, n=12):
    return [[rx * math.cos((k + 0.5) / n * 2 * math.pi), yc + ry * math.sin((k + 0.5) / n * 2 * math.pi), z] for k in range(n)]


# ------------------------------------------------------------------ geometry
class Bucket:
    __slots__ = ('pos', 'nor', 'idx')

    def __init__(self):
        self.pos, self.nor, self.idx = [], [], []


class Geom:
    def __init__(self):
        self.buckets = {}
        self.stack = [ID()]

    @property
    def M(self): return self.stack[-1]

    def push(self, m): self.stack.append(mmul(self.M, m))
    def pop(self): self.stack.pop()

    def at(self, m, fn):
        self.push(m)
        fn()
        self.pop()

    def P(self, p): return add(xf3(self.M[0], p), self.M[1])
    def N(self, n): return norm(xf3(inv_t3(self.M[0]), n))

    def B(self, mat):
        if mat not in self.buckets:
            self.buckets[mat] = Bucket()
        return self.buckets[mat]

    @staticmethod
    def vert(b, p, n):
        b.pos.extend(p)
        b.nor.extend(n)
        return len(b.pos) // 3 - 1

    def empty(self): return all(not b.idx for b in self.buckets.values())
    def tri_count(self): return sum(len(b.idx) // 3 for b in self.buckets.values())

    # -- low level (already-transformed coords)
    def tri(self, mat, a, b, c):
        n = cross(sub(b, a), sub(c, a))
        l = vlen(n)
        if l < 1e-10:
            return
        bk, nn = self.B(mat), mul_s(n, 1.0 / l)
        bk.idx += [self.vert(bk, a, nn), self.vert(bk, b, nn), self.vert(bk, c, nn)]

    @staticmethod
    def tri_i(bk, i, j, k):
        """smooth triangle by vertex index; winding fixed to agree with the vertex normals"""
        def p(q): return bk.pos[q * 3:q * 3 + 3]
        def nq(q): return bk.nor[q * 3:q * 3 + 3]
        a, b, c = p(i), p(j), p(k)
        g = cross(sub(b, a), sub(c, a))
        if vlen(g) < 1e-12:
            return
        if dot(g, add(add(nq(i), nq(j)), nq(k))) < 0:
            bk.idx += [i, k, j]
        else:
            bk.idx += [i, j, k]

    def poly_w(self, mat, P, facing=None):
        """planar polygon (CCW seen from outside, or flipped to face `facing`)"""
        pts = []
        for p in P:
            if not pts or dist(p, pts[-1]) > 1e-7:
                pts.append(p)
        while len(pts) > 2 and dist(pts[0], pts[-1]) < 1e-7:
            pts.pop()
        if len(pts) < 3:
            return
        n = newell(pts)
        l = vlen(n)
        if l < 1e-12:
            return
        n = mul_s(n, 1.0 / l)
        if facing is not None and dot(n, facing) < 0:
            pts.reverse()
            n = mul_s(n, -1)
        sz = max(dist(p, pts[0]) for p in pts)
        tol = 1e-4 * max(1.0, sz) + 1e-5
        if any(abs(dot(sub(p, pts[0]), n)) > tol for p in pts):
            for k in range(1, len(pts) - 1):
                self.tri(mat, pts[0], pts[k], pts[k + 1])
            return
        bk = self.B(mat)
        base = [self.vert(bk, p, n) for p in pts]
        for a, b, c in earclip(pts, n):
            g = cross(sub(pts[b], pts[a]), sub(pts[c], pts[a]))
            if vlen(g) < 1e-12:
                continue
            if dot(g, n) < 0:
                bk.idx += [base[a], base[c], base[b]]
            else:
                bk.idx += [base[a], base[b], base[c]]

    def poly(self, mat, P, facing=None):
        self.poly_w(mat, [self.P(p) for p in P], self.N(facing) if facing is not None else None)

    # -- solids (local coords)
    def loft(self, mat, rings, closed=False, cap_start=None, cap_end=None):
        """loft through rings with equal vertex count; flat shaded; orientation automatic"""
        cap_start = (not closed) if cap_start is None else cap_start
        cap_end = (not closed) if cap_end is None else cap_end
        R = [[self.P(p) for p in r] for r in rings]
        n = len(R[0])
        if closed:
            ref = newell(R[0])
            d = sub(centroid(R[1]), centroid(R[0]))
        else:
            best, ref = 0.0, [0, 0, 0]
            for r in R:
                q = newell(r)
                if vlen(q) > best:
                    best, ref = vlen(q), q
            d = sub(centroid(R[-1]), centroid(R[0]))
        if dot(ref, d) < 0:
            R = [list(reversed(r)) for r in R]
        segs = len(R) if closed else len(R) - 1
        for i in range(segs):
            A, Bq = R[i], R[(i + 1) % len(R)]
            for j in range(n):
                j1 = (j + 1) % n
                self.poly_w(mat, [A[j], A[j1], Bq[j1], Bq[j]])
        if cap_start:
            self.poly_w(mat, list(reversed(R[0])), mul_s(d, -1))
        if cap_end:
            self.poly_w(mat, R[-1], d)

    def box(self, mat, c, s):
        x, y, z = c
        a, b, d = s[0] / 2, s[1] / 2, s[2] / 2

        def r(zz): return [[x - a, y - b, zz], [x + a, y - b, zz], [x + a, y + b, zz], [x - a, y + b, zz]]
        self.loft(mat, [r(z - d), r(z + d)])

    def cbox(self, mat, c, s, ch=0.05):
        """box with chamfered edges"""
        x, y, z = c
        a, b, d = s[0] / 2, s[1] / 2, s[2] / 2
        k = min(ch, a * 0.9, b * 0.9, d * 0.9)

        def octo(zz, i):
            return [[x + a - k, y - b + i, zz], [x + a - i, y - b + k, zz], [x + a - i, y + b - k, zz], [x + a - k, y + b - i, zz],
                    [x - a + k, y + b - i, zz], [x - a + i, y + b - k, zz], [x - a + i, y - b + k, zz], [x - a + k, y - b + i, zz]]
        self.loft(mat, [octo(z - d, k), octo(z - d + k, 0), octo(z + d - k, 0), octo(z + d, k)])

    def prism_x(self, mat, prof, x0, x1):
        """profile [[z, y], ...] extruded along X"""
        self.loft(mat, [[[x0, y, z] for z, y in prof], [[x1, y, z] for z, y in prof]])

    def prism_z(self, mat, prof, z0, z1):
        """profile [[x, y], ...] extruded along Z"""
        self.loft(mat, [[[x, y, z0] for x, y in prof], [[x, y, z1] for x, y in prof]])

    def hull(self, mat, secs, **kw):
        """symmetric hull from [z, half-profile] sections"""
        self.loft(mat, [sym_sec(z, h) for z, h in secs], **kw)

    @staticmethod
    def ring_pts(c, u, v, r, seg, phase):
        out = []
        for j in range(seg):
            t = phase + j / seg * 2 * math.pi
            out.append(add(c, add(mul_s(u, r * math.cos(t)), mul_s(v, r * math.sin(t)))))
        return out

    def cyl(self, mat, a, b, r0, r1=None, seg=16, caps='both', smooth=True, phase=0.0):
        """cylinder / cone frustum from a to b; caps in both|start|end|none"""
        r1 = r0 if r1 is None else r1
        w = norm(sub(b, a))
        u = perp(w)
        v = cross(w, u)
        h = dist(a, b)
        A = self.ring_pts(a, u, v, r0, seg, phase)
        Bq = self.ring_pts(b, u, v, r1, seg, phase)
        cs = caps in ('both', 'start') and r0 > 0
        ce = caps in ('both', 'end') and r1 > 0
        if not smooth:
            self.loft(mat, [A, Bq], cap_start=cs, cap_end=ce)
            return
        bk = self.B(mat)
        ia, ib = [], []
        for j in range(seg):
            t = phase + j / seg * 2 * math.pi
            rad = add(mul_s(u, math.cos(t)), mul_s(v, math.sin(t)))
            nn = self.N(norm(add(mul_s(rad, h), mul_s(w, r0 - r1))))
            ia.append(self.vert(bk, self.P(A[j]), nn))
            ib.append(self.vert(bk, self.P(Bq[j]), nn))
        for j in range(seg):
            j1 = (j + 1) % seg
            if r0 > 0:
                self.tri_i(bk, ia[j], ia[j1], ib[j1])
            if r1 > 0:
                self.tri_i(bk, ia[j], ib[j1], ib[j])
        if cs:
            self.poly(mat, A, mul_s(w, -1))
        if ce:
            self.poly(mat, Bq, w)

    def tube(self, mat, a, b, ro, ri, seg=16, phase=0.0):
        """hollow ring/tube from a to b"""
        w = norm(sub(b, a))
        u = perp(w)
        v = cross(w, u)
        bk = self.B(mat)
        for r, sign in ((ro, 1), (ri, -1)):
            ia, ib = [], []
            for j in range(seg):
                t = phase + j / seg * 2 * math.pi
                rad = add(mul_s(u, math.cos(t)), mul_s(v, math.sin(t)))
                nn = self.N(mul_s(rad, sign))
                ia.append(self.vert(bk, self.P(add(a, mul_s(rad, r))), nn))
                ib.append(self.vert(bk, self.P(add(b, mul_s(rad, r))), nn))
            for j in range(seg):
                j1 = (j + 1) % seg
                self.tri_i(bk, ia[j], ia[j1], ib[j1])
                self.tri_i(bk, ia[j], ib[j1], ib[j])
        for c, f in ((a, mul_s(w, -1)), (b, w)):
            O = self.ring_pts(c, u, v, ro, seg, phase)
            I = self.ring_pts(c, u, v, ri, seg, phase)
            for j in range(seg):
                j1 = (j + 1) % seg
                self.poly(mat, [O[j], O[j1], I[j1], I[j]], f)

    def sphere(self, mat, c, r, seg=12, rings=8, lat0=-90.0, lat1=90.0, sy=1.0, cap=True):
        """UV sphere (or a latitude band of one), +Y polar axis"""
        bk = self.B(mat)
        rows = []

        def pt(la, lo): return [math.cos(la) * math.cos(lo), math.sin(la), -math.cos(la) * math.sin(lo)]
        for i in range(rings + 1):
            la = (lat0 + (lat1 - lat0) * i / rings) * D
            row = []
            for j in range(seg):
                d = pt(la, j / seg * 2 * math.pi)
                row.append(self.vert(bk, self.P([c[0] + d[0] * r, c[1] + d[1] * r * sy, c[2] + d[2] * r]),
                                     self.N(norm([d[0], d[1] / sy, d[2]]))))
            rows.append(row)
        for i in range(rings):
            A, Bq = rows[i], rows[i + 1]
            for j in range(seg):
                j1 = (j + 1) % seg
                self.tri_i(bk, A[j], A[j1], Bq[j1])
                self.tri_i(bk, A[j], Bq[j1], Bq[j])
        for la_deg, f, cond in ((lat0, [0, -1, 0], lat0 > -89.9), (lat1, [0, 1, 0], lat1 < 89.9)):
            if cap and cond:
                la = la_deg * D
                pts = []
                for j in range(seg):
                    d = pt(la, j / seg * 2 * math.pi)
                    pts.append([c[0] + d[0] * r, c[1] + d[1] * r * sy, c[2] + d[2] * r])
                self.poly(mat, pts, f)

    def fin(self, mat, root, tip, chord_dir=(0, 0, 1), thick_dir=(1, 0, 0)):
        """tapered aerofoil plate lofted root -> tip. root/tip = (leading-edge point, chord, thickness);
        chord runs from the leading edge toward -chord_dir"""
        cd, td = norm(list(chord_dir)), norm(list(thick_dir))

        def sec(s):
            le, ch, th = s

            def Pp(f, k): return add(add(le, mul_s(cd, -ch * f)), mul_s(td, th / 2 * k))
            return [Pp(0, 0), Pp(0.25, 1), Pp(0.8, 0.6), Pp(1, 0), Pp(0.8, -0.6), Pp(0.25, -1)]
        self.loft(mat, [sec(root), sec(tip)])

    def beam(self, mat, a, b, w, h):
        """oriented box spanning a -> b"""
        ez = norm(sub(b, a))
        ex = cross([0, 1, 0], ez)
        ex = [1, 0, 0] if vlen(ex) < 1e-6 else norm(ex)
        ey = cross(ez, ex)
        self.at(basis(lerp(a, b, 0.5), ex, ey, ez), lambda: self.box(mat, [0, 0, 0], [w, h, dist(a, b)]))


# ------------------------------------------------------------------ scene graph
class Node:
    def __init__(self, name, t=(0, 0, 0)):
        self.name, self.t, self.g, self.children = name, list(t), Geom(), []

    def child(self, name, t=(0, 0, 0)):
        n = Node(name, t)
        self.children.append(n)
        return n


# ------------------------------------------------------------------ materials
def livery(team):
    L = {
        'blue': dict(hull=[0.30, 0.35, 0.22], hull2=[0.19, 0.23, 0.14], glow=[0.1, 0.9, 1.0]),
        'red': dict(hull=[0.45, 0.38, 0.30], hull2=[0.22, 0.22, 0.23], glow=[1.0, 0.35, 0.05]),
        'neutral': dict(hull=[0.70, 0.71, 0.70], hull2=[0.18, 0.19, 0.20], glow=[1.0, 0.55, 0.15]),
    }[team]

    def pbr(name, c, metal, rough, alpha=1.0, **extra):
        m = {'name': name, 'pbrMetallicRoughness': {'baseColorFactor': list(c) + [alpha], 'metallicFactor': metal, 'roughnessFactor': rough}}
        m.update(extra)
        return m
    return {
        'hull': pbr('hull', L['hull'], 0.25, 0.7),
        'hull2': pbr('hull2', L['hull2'], 0.25, 0.75),
        'dark': pbr('dark', [0.04, 0.04, 0.045], 0.0, 0.95),
        'metal': pbr('metal', [0.22, 0.23, 0.24], 0.8, 0.38),
        'glass': pbr('glass', [0.04, 0.08, 0.2], 0.2, 0.06),
        'glow': pbr('glow', L['glow'], 0.0, 0.5, emissiveFactor=list(L['glow'])),
        'rotor': pbr('rotor', [0.12, 0.12, 0.13], 0.2, 0.6, alpha=0.22, alphaMode='BLEND', doubleSided=True),
    }


# ------------------------------------------------------------------ GLB encoder
def encode_glb(root, mats):
    gltf = {
        'asset': {'version': '2.0', 'generator': 'ai-dssw gen-models'},
        'scene': 0, 'scenes': [{'name': 'Scene', 'nodes': [0]}], 'nodes': [], 'meshes': [], 'materials': [],
        'accessors': [], 'bufferViews': [], 'buffers': [],
    }
    bin_ = bytearray()
    mat_idx = {}

    def view(data, target):
        while len(bin_) % 4:
            bin_.append(0)
        gltf['bufferViews'].append({'buffer': 0, 'byteOffset': len(bin_), 'byteLength': len(data), 'target': target})
        bin_.extend(data)
        return len(gltf['bufferViews']) - 1

    def visit(n):
        idx = len(gltf['nodes'])
        gn = {'name': n.name}
        gltf['nodes'].append(gn)
        if any(v != 0 for v in n.t):
            gn['translation'] = [round(v, 5) for v in n.t]
        if not n.g.empty():
            prims = []
            for mname, b in n.g.buckets.items():
                if not b.idx:
                    continue
                if mname not in mat_idx:
                    mat_idx[mname] = len(gltf['materials'])
                    gltf['materials'].append(mats[mname])
                vc = len(b.pos) // 3
                pos = struct.pack('<%df' % len(b.pos), *b.pos)
                nor = struct.pack('<%df' % len(b.nor), *b.nor)
                # min/max from the float32-rounded values so they match the stored data exactly
                fp = struct.unpack('<%df' % len(b.pos), pos)
                mn = [min(fp[k::3]) for k in range(3)]
                mx = [max(fp[k::3]) for k in range(3)]
                big = vc > 65535
                ind = struct.pack('<%d%s' % (len(b.idx), 'I' if big else 'H'), *b.idx)
                acc = gltf['accessors']
                acc.append({'bufferView': view(pos, 34962), 'componentType': 5126, 'count': vc, 'type': 'VEC3', 'min': mn, 'max': mx})
                acc.append({'bufferView': view(nor, 34962), 'componentType': 5126, 'count': vc, 'type': 'VEC3'})
                acc.append({'bufferView': view(ind, 34963), 'componentType': 5125 if big else 5123, 'count': len(b.idx), 'type': 'SCALAR'})
                prims.append({'attributes': {'POSITION': len(acc) - 3, 'NORMAL': len(acc) - 2}, 'indices': len(acc) - 1,
                              'material': mat_idx[mname], 'mode': 4})
            gltf['meshes'].append({'name': n.name, 'primitives': prims})
            gn['mesh'] = len(gltf['meshes']) - 1
        if n.children:
            gn['children'] = [visit(c) for c in n.children]
        return idx

    visit(root)
    while len(bin_) % 4:
        bin_.append(0)
    gltf['buffers'].append({'byteLength': len(bin_)})
    js = json.dumps(gltf, separators=(',', ':')).encode('utf-8')
    js += b' ' * ((4 - len(js) % 4) % 4)
    total = 12 + 8 + len(js) + 8 + len(bin_)
    return (struct.pack('<III', 0x46546C67, 2, total) + struct.pack('<II', len(js), 0x4E4F534A) + js
            + struct.pack('<II', len(bin_), 0x004E4942) + bytes(bin_))


# ------------------------------------------------------------------ shared parts
def chevron(g, c, size=0.8, w=0.12, h=0.03):
    """team-colour emissive chevron lying flat, pointing +Z"""
    ang = 35 * D
    apex = add(c, [0, 0, size * 0.35])
    for s in S2:
        d = [s * math.sin(ang), 0, -math.cos(ang)]
        m = add(apex, mul_s(d, size / 2 - w * 0.3))
        g.at(TR(m[0], m[1], m[2], 0, math.atan2(d[0], d[2]), 0), lambda: g.box('glow', [0, 0, 0], [w, h, size]))


def antenna(g, base, h, r=0.012):
    g.cyl('metal', base, add(base, [0, 0.07, 0]), r * 4, r * 3.5, 6, caps='end')
    g.cyl('metal', base, add(base, [0, h, 0]), r * 1.6, r * 0.8, 5, caps='end')


def wheel(g, c, r, w, side, seg=14, hub='hull2'):
    x, y, z = c
    xo, xi = x + side * w / 2, x - side * w / 2
    g.cyl('dark', [xi, y, z], [xo, y, z], r, r, seg)
    g.cyl(hub, [xo - side * 0.01, y, z], [xo + side * 0.035, y, z], r * 0.66, r * 0.56, seg - 2, caps='end')
    g.cyl('metal', [xo + side * 0.035, y, z], [xo + side * 0.075, y, z], r * 0.17, r * 0.12, 6, caps='end')


def sprocket(g, c, r, w, side, teeth=11):
    x, y, z = c
    prof = []
    for k in range(teeth * 2):
        a = k / (teeth * 2) * 2 * math.pi
        rr = r * 0.8 if k % 2 else r
        prof.append([z + rr * math.cos(a), y + rr * math.sin(a)])
    g.prism_x('metal', prof, x - w / 2, x + w / 2)
    g.cyl('hull2', [x + side * w / 2, y, z], [x + side * (w / 2 + 0.05), y, z], r * 0.5, r * 0.4, 10, caps='end')


def tracks(g, xc, tw, rw, wheels, spr, idl, t=0.09, gr=0.035, gstep=0.44):
    """pair of track runs with road wheels, drive sprocket and idler. spr/idl = (z, y, r)"""
    yw = rw + t + gr
    pts = []
    for z, y, r in [(z, yw, rw) for z in wheels] + [tuple(spr), tuple(idl)]:
        for k in range(24):
            a = k / 24 * 2 * math.pi
            pts.append((z + (r + t / 2) * math.cos(a), y + (r + t / 2) * math.sin(a)))
    H = hull2(pts)  # CCW in (z, y)
    vn = []
    for i in range(len(H)):
        a, b = H[i - 1], H[(i + 1) % len(H)]
        d = (b[0] - a[0], b[1] - a[1])
        l = math.hypot(*d)
        vn.append((d[1] / l, -d[0] / l))
    for s in S2:
        x0, x1 = s * xc - tw / 2, s * xc + tw / 2
        rings = []
        for p, n in zip(H, vn):
            oz, oy = p[0] + n[0] * t / 2, p[1] + n[1] * t / 2
            iz, iy = p[0] - n[0] * t / 2, p[1] - n[1] * t / 2
            rings.append([[x0, oy, oz], [x1, oy, oz], [x1, iy, iz], [x0, iy, iz]])
        g.loft('dark', rings, closed=True)
        carry = gstep / 2
        for i in range(len(H)):
            a, b = H[i], H[(i + 1) % len(H)]
            L = math.hypot(b[0] - a[0], b[1] - a[1])
            tg = ((b[0] - a[0]) / L, (b[1] - a[1]) / L)
            n = (tg[1], -tg[0])
            u = carry
            while u < L:
                pz = a[0] + tg[0] * u + n[0] * (t / 2 + gr / 2)
                py = a[1] + tg[1] * u + n[1] * (t / 2 + gr / 2)
                g.at(basis([s * xc, py, pz], [1, 0, 0], [0, n[1], n[0]], [0, -tg[1], -tg[0]]),
                     lambda: g.box('dark', [0, 0, 0], [tw * 0.96, gr, 0.11]))
                u += gstep
            carry = u - L
        for z in wheels:
            wheel(g, [s * xc, yw, z], rw, tw * 0.72, s)
        sprocket(g, [s * xc, spr[1], spr[0]], spr[2], tw * 0.5, s)
        wheel(g, [s * xc, idl[1], idl[0]], idl[2], tw * 0.6, s, seg=12)


def rail_barrel(k, z0, z1, gap, rw, rh, coils, ro, y=0.0, bridge=0.8):
    """railgun: two parallel rails with emissive coil rings, extending +Z in gun space"""
    for s in S2:
        k.box('metal', [s * (gap / 2 + rw / 2), y, (z0 + z1) / 2], [rw, rh, z1 - z0])
    k.box('glow', [0, y, (z0 + z1) / 2 + 0.1], [gap * 0.35, rh * 0.25, z1 - z0 - 0.4])
    W = gap + rw * 2
    coil_z = list(coils)
    z = z0 + 0.35
    while z < z1 - 0.3:
        if all(abs(z - c) > 0.16 for c in coil_z):
            for sy in S2:
                k.box('metal', [0, y + sy * (rh / 2 + 0.015), z], [W * 0.9, 0.03, 0.09])
        z += bridge
    for c in coil_z:
        k.tube('metal', [0, y, c - 0.09], [0, y, c + 0.09], ro, ro - 0.035, 16)
        k.tube('glow', [0, y, c - 0.03], [0, y, c + 0.03], ro + 0.035, ro - 0.01, 16)
    k.cbox('metal', [0, y, z1 + 0.06], [W + 0.14, rh + 0.1, 0.18], 0.04)


def hatch(g, c, r, mat='hull2'):
    g.cyl(mat, [c[0], c[1] - 0.03, c[2]], [c[0], c[1] + 0.05, c[2]], r, r * 0.93, 14)
    g.beam('metal', [c[0] - r * 0.4, c[1] + 0.07, c[2]], [c[0] + r * 0.4, c[1] + 0.07, c[2]], 0.03, 0.03)


def smoke_bank(g, base, side, n=4, dz=0.15):
    d = norm([side * 0.55, 0.35, 0.75])
    for k in range(n):
        b = add(base, [0, 0, -k * dz])
        g.cyl('dark', b, add(b, mul_s(d, 0.26)), 0.05, 0.05, 8, caps='end')


def rear_lights(g, z, y, x, mat='glass'):
    for s in S2:
        g.box(mat, [s * x, y, z], [0.16, 0.08, 0.04])


# ================================================================== models
def m_mbt_rail():
    body = Node('body')
    g = body.g
    tracks(g, 1.42, 0.62, 0.36, [-3.25, -1.95, -0.65, 0.65, 1.95, 3.25], (-3.95, 0.66, 0.34), (3.85, 0.6, 0.3))
    g.loft('hull2', [rect(-4.3, 1.1, 0.42, 0.98), rect(3.5, 1.1, 0.42, 0.98), rect(4.3, 1.1, 0.82, 0.98)])
    g.hull('hull', [
        [-4.45, [[1.62, 0.96], [1.72, 1.28], [1.42, 1.56]]],
        [-4.1, [[1.76, 0.96], [1.82, 1.32], [1.52, 1.64]]],
        [1.3, [[1.76, 0.96], [1.82, 1.32], [1.52, 1.64]]],
        [3.0, [[1.76, 0.94], [1.8, 1.16], [1.42, 1.36]]],
        [4.35, [[1.62, 0.84], [1.66, 0.94], [1.1, 1.02]]],
    ])
    for s in S2:  # faceted side skirts with panel joints and team strip
        g.prism_x('hull', [[-4.35, 0.36], [3.55, 0.36], [4.25, 0.78], [4.25, 1.0], [-4.4, 1.0]], s * 1.77, s * 1.87)
        for k in range(6):
            g.box('hull2', [s * 1.875, 0.62, -3.6 + k * 1.45], [0.02, 0.48, 0.06])
        g.box('glow', [s * 1.88, 0.9, -0.6], [0.02, 0.05, 5.8])
        g.box('dark', [s * 0.9, 1.25, -4.47], [0.6, 0.18, 0.06])  # exhausts
        g.box('glass', [s * 1.3, 1.0, 4.27], [0.2, 0.07, 0.05])  # headlights
        g.beam('metal', [s * 0.7, 0.8, 4.28], [s * 0.7, 0.8, 4.5], 0.08, 0.08)  # tow hooks
    g.box('dark', [0, 1.645, -3.3], [2.3, 0.02, 1.4])  # engine grille
    for k in range(7):
        g.box('hull2', [0, 1.665, -3.9 + k * 0.2], [2.4, 0.03, 0.06])
    g.cbox('hull2', [0, 1.2, -4.55], [1.1, 0.4, 0.25], 0.04)
    g.at(TR(0, 1.52, 2.15, 9.4 * D), lambda: g.box('glass', [0, 0, 0], [0.6, 0.06, 0.14]))
    g.at(TR(0, 1.225, 3.6, 14 * D), lambda: chevron(g, [0, 0, 0], 0.9))
    hatch(g, [0, 1.47, 2.6], 0.28)

    tur = body.child('turret', (0, 1.64, -0.5))
    t = tur.g
    t.hull('hull', [
        [-2.3, [[1.05, 0.06], [1.15, 0.36], [0.9, 0.56]]],
        [-1.6, [[1.32, 0.0], [1.44, 0.32], [1.15, 0.6]]],
        [0.8, [[1.4, 0.0], [1.5, 0.32], [1.2, 0.62]]],
        [1.95, [[0.62, 0.05], [0.68, 0.3], [0.5, 0.5]]],
    ])
    t.cyl('dark', [0, -0.06, 0], [0, 0.02, 0], 1.15, 1.15, 20)
    t.box('hull2', [0, 0.62, -1.15], [1.7, 0.05, 1.0])
    for s in S2:  # APS launcher boxes
        t.cbox('hull2', [s * 1.38, 0.48, 0.35], [0.32, 0.28, 0.55], 0.03)
        for dy in (-0.06, 0.06):
            for dx in (-0.07, 0.07):
                t.cyl('dark', [s * 1.38 + dx, 0.48 + dy, 0.62], [s * 1.38 + dx, 0.48 + dy, 0.66], 0.045, 0.045, 8, caps='end')
        t.box('glow', [s * 1.44, 0.18, -0.1], [0.1, 0.05, 1.4])
        smoke_bank(t, [s * 1.2, 0.5, -0.5], s, 3)
    t.cyl('metal', [-0.8, 0.58, -1.2], [-0.8, 0.8, -1.2], 0.05, 0.04, 8)  # sensor mast
    t.cbox('hull2', [-0.8, 0.86, -1.2], [0.32, 0.18, 0.26], 0.03)
    t.cyl('glass', [-0.8, 0.86, -1.08], [-0.8, 0.86, -1.04], 0.06, 0.06, 10, caps='end')
    t.box('glow', [-0.8, 0.955, -1.2], [0.1, 0.012, 0.1])
    t.cbox('hull2', [0.65, 0.72, 0.3], [0.3, 0.22, 0.35], 0.03)  # commander sight
    t.box('glass', [0.65, 0.73, 0.48], [0.22, 0.12, 0.02])
    hatch(t, [0.6, 0.63, -0.5], 0.3)
    hatch(t, [-0.3, 0.63, -0.1], 0.25)
    chevron(t, [0, 0.66, -1.15], 0.9)
    antenna(t, [0.9, 0.56, -2.0], 0.7)
    antenna(t, [-0.9, 0.56, -2.0], 0.5)
    t.cbox('hull2', [0, 0.3, -2.42], [1.6, 0.4, 0.25], 0.04)

    gun = tur.child('gun', (0, 0.3, 1.55))
    k = gun.g
    k.cbox('hull2', [0, 0, 0.2], [0.72, 0.42, 0.8], 0.06)
    k.box('metal', [0, 0, 0.85], [0.56, 0.34, 0.5])
    rail_barrel(k, 0.6, 4.3, 0.17, 0.11, 0.24, [1.35, 1.95, 2.55, 3.15, 3.75], 0.27)
    return body


def m_mbt():
    body = Node('body')
    g = body.g
    tracks(g, 1.38, 0.6, 0.34, [-2.6, -1.56, -0.52, 0.52, 1.56, 2.6], (-3.2, 0.64, 0.32), (3.1, 0.56, 0.28))
    g.loft('hull2', [rect(-3.4, 1.07, 0.42, 1.0), rect(2.6, 1.07, 0.42, 1.0), rect(3.4, 1.07, 0.86, 1.0)])
    g.hull('hull', [
        [-3.5, [[1.7, 0.99], [1.72, 1.08], [1.3, 1.1], [1.25, 1.52]]],
        [2.15, [[1.7, 0.99], [1.72, 1.08], [1.3, 1.1], [1.25, 1.52]]],
        [3.45, [[1.7, 0.99], [1.72, 1.06], [1.3, 1.08], [1.2, 1.12]]],
    ])
    for s in S2:
        g.prism_x('hull2', [[-0.6, 0.7], [3.0, 0.7], [3.42, 0.92], [3.42, 1.07], [-0.6, 1.07]], s * 1.70, s * 1.76)
        g.prism_x('dark', [[-3.3, 0.84], [-0.6, 0.84], [-0.6, 1.0], [-3.3, 1.0]], s * 1.69, s * 1.72)
        g.box('glow', [s * 1.765, 0.98, 1.2], [0.012, 0.045, 3.2])
        g.cbox('hull2', [s * 1.5, 1.24, -2.6], [0.38, 0.3, 1.4], 0.04)  # fender stowage
        g.cbox('hull2', [s * 1.5, 1.22, -0.9], [0.36, 0.26, 1.0], 0.04)
        g.cyl('hull2', [s * 0.12, 1.32, -3.78], [s * 1.0, 1.32, -3.78], 0.27, 0.27, 12)  # fuel drums
        g.beam('metal', [s * 0.56, 1.05, -3.5], [s * 0.56, 1.32, -3.78], 0.06, 0.04)
        g.box('glass', [s * 1.05, 1.15, 3.42], [0.22, 0.1, 0.06])
        g.box('glass', [s * 1.3, 1.28, -3.52], [0.12, 0.06, 0.03])
    g.box('dark', [0, 1.25, -3.52], [1.3, 0.22, 0.04])
    g.box('dark', [0, 1.525, -2.6], [2.0, 0.015, 1.3])
    for k in range(5):
        g.box('hull2', [0, 1.54, -3.1 + k * 0.25], [2.1, 0.025, 0.06])
    g.at(TR(0, 1.32, 2.8, 17.1 * D), lambda: [g.box('hull2', [c * 0.46, 0.04, r], [0.42, 0.08, 0.5]) for r in (-0.3, 0.3) for c in range(-2, 3)])
    g.at(TR(0, 1.5, 2.25, 17.1 * D), lambda: g.box('glass', [0, 0.02, 0], [0.5, 0.05, 0.1]))

    tur = body.child('turret', (0, 1.52, -0.3))
    t = tur.g
    t.hull('hull', [
        [-2.0, [[0.95, 0.04], [1.02, 0.45], [0.85, 0.7]]],
        [-1.2, [[1.18, 0.0], [1.26, 0.42], [1.05, 0.76]]],
        [0.5, [[1.3, 0.0], [1.36, 0.42], [1.1, 0.78]]],
        [1.6, [[1.12, 0.04], [1.15, 0.4], [0.92, 0.62]]],
    ])
    t.cyl('dark', [0, -0.05, 0], [0, 0.02, 0], 1.1, 1.1, 20)
    for s in S2:
        t.cbox('hull2', [s * 0.76, 0.36, 1.68], [0.62, 0.56, 0.16], 0.03)  # ERA cheeks
        t.box('glow', [s * 1.3, 0.2, -0.3], [0.1, 0.05, 1.4])
        smoke_bank(t, [s * 1.25, 0.6, 0.95], s, 4)
        t.beam('metal', [s * 0.95, 0.55, -2.0], [s * 0.95, 0.55, -2.7], 0.04, 0.04)  # bustle basket
        t.beam('metal', [s * 0.95, 0.25, -2.7], [s * 0.95, 0.55, -2.7], 0.04, 0.04)
        antenna(t, [s * 0.75, 0.7, -1.8], 0.8)
    t.box('hull2', [0, 0.25, -2.35], [1.9, 0.04, 0.7])
    t.beam('metal', [-0.95, 0.55, -2.7], [0.95, 0.55, -2.7], 0.04, 0.04)
    t.cbox('hull2', [0, 0.4, -2.35], [1.7, 0.28, 0.6], 0.1)
    # commander cupola with RWS
    t.cyl('hull2', [0.55, 0.72, -0.4], [0.55, 0.92, -0.4], 0.36, 0.34, 14)
    for a in range(5):
        an = (a * 50 + 20) * D
        t.box('glass', [0.55 + 0.33 * math.sin(an), 0.86, -0.4 + 0.33 * math.cos(an)], [0.08, 0.06, 0.08])
    t.cyl('hull2', [0.55, 0.92, -0.4], [0.55, 1.0, -0.4], 0.16, 0.16, 10)
    t.cbox('hull2', [0.55, 1.1, -0.35], [0.3, 0.22, 0.42], 0.03)
    t.cyl('metal', [0.55, 1.12, -0.15], [0.55, 1.12, 0.75], 0.022, 0.022, 6)
    t.box('glass', [0.42, 1.13, -0.12], [0.06, 0.08, 0.03])
    t.box('dark', [0.73, 1.08, -0.4], [0.08, 0.16, 0.26])
    t.cbox('hull2', [-0.62, 0.84, 0.55], [0.34, 0.26, 0.42], 0.03)  # gunner sight
    t.box('glass', [-0.62, 0.86, 0.765], [0.24, 0.14, 0.02])
    hatch(t, [-0.45, 0.78, -0.6], 0.28)
    chevron(t, [0, 0.79, -1.0], 0.8)

    gun = tur.child('gun', (0, 0.42, 1.5))
    k = gun.g
    k.cbox('hull2', [0, 0, 0.12], [0.78, 0.58, 0.62], 0.08)
    k.cyl('metal', [0, 0, 0.4], [0, 0, 4.8], 0.1, 0.085, 14)
    k.cyl('hull2', [0, 0, 0.45], [0, 0, 1.6], 0.118, 0.112, 14)  # thermal sleeve
    k.cyl('metal', [0, 0, 2.3], [0, 0, 2.42], 0.1, 0.15, 14, caps='none')  # fume extractor
    k.cyl('metal', [0, 0, 2.42], [0, 0, 2.95], 0.15, 0.15, 14, caps='none')
    k.cyl('metal', [0, 0, 2.95], [0, 0, 3.07], 0.15, 0.095, 14, caps='none')
    k.cyl('hull2', [0, 0, 3.2], [0, 0, 4.3], 0.106, 0.1, 14)
    k.cyl('metal', [0, 0, 4.75], [0, 0, 4.95], 0.112, 0.112, 14)
    k.box('metal', [0, 0.13, 4.85], [0.06, 0.06, 0.1])
    k.cyl('metal', [0.3, -0.08, 0.4], [0.3, -0.08, 0.55], 0.025, 0.025, 6)
    return body


def m_ifv():
    body = Node('body')
    g = body.g
    tracks(g, 1.33, 0.52, 0.31, [-2.55, -1.55, -0.55, 0.45, 1.45, 2.45], (3.05, 0.66, 0.28), (-3.15, 0.56, 0.26))
    prof = [[1.04, 0.42], [1.06, 0.97], [1.64, 1.0], [1.65, 1.08], [1.36, 1.72]]
    g.hull('hull', [
        [-3.6, prof], [1.6, prof],
        [2.6, [[1.04, 0.5], [1.06, 0.97], [1.64, 1.0], [1.65, 1.08], [1.32, 1.56]]],
        [3.75, [[0.9, 0.98], [0.92, 1.02], [1.4, 1.05], [1.42, 1.1], [1.12, 1.2]]],
    ])
    g.at(TR(0, 0.742, 3.18, -22.6 * D), lambda: g.box('hull2', [0, -0.035, 0], [1.9, 0.04, 0.9]))  # trim vane
    for s in S2:
        g.box('glow', [s * 1.59, 1.25, -1.0], [0.06, 0.045, 4.0])
        g.cbox('hull2', [s * 1.5, 1.2, -3.0], [0.26, 0.22, 0.9], 0.03)
        g.box('hull2', [s * 0.45, 1.05, -3.62], [0.7, 0.9, 0.04])  # rear doors
        g.beam('metal', [s * 0.2, 1.3, -3.65], [s * 0.5, 1.3, -3.65], 0.03, 0.03)
        g.tube('metal', [s * 0.8, 0.68, -3.58], [s * 0.8, 0.68, -3.75], 0.17, 0.13, 12)  # water jets
        g.cyl('dark', [s * 0.8, 0.68, -3.62], [s * 0.8, 0.68, -3.6], 0.13, 0.13, 12, caps='start')
        g.cbox('hull2', [s * 0.55, 1.73, -2.4], [0.6, 0.05, 1.0], 0.02)  # troop hatches
        g.box('glass', [s * 0.9, 1.12, 3.72], [0.18, 0.06, 0.05])
        for z in (-2.6, -1.6, -0.6):
            g.cyl('hull2', [s * 1.5, 1.38, z], [s * 1.6, 1.36, z], 0.07, 0.06, 8, caps='end')  # firing ports
    hatch(g, [0.6, 1.66, 2.0], 0.26)
    g.box('glass', [0.6, 1.66, 2.35], [0.4, 0.06, 0.08])
    g.box('dark', [-0.9, 1.6, 2.1], [0.6, 0.03, 0.8])  # engine grille
    antenna(g, [-1.2, 1.72, -3.3], 1.0)

    tur = body.child('turret', (0, 1.72, -0.1))
    t = tur.g
    t.hull('hull', [
        [-0.95, [[0.72, 0.02], [0.8, 0.26], [0.62, 0.5]]],
        [0.55, [[0.86, 0.0], [0.92, 0.26], [0.66, 0.54]]],
        [1.05, [[0.5, 0.05], [0.55, 0.25], [0.4, 0.44]]],
    ])
    t.cyl('dark', [0, -0.04, 0], [0, 0.02, 0], 0.85, 0.85, 18)
    t.box('metal', [0.97, 0.3, -0.1], [0.14, 0.1, 0.3])  # ATGM launcher arm + twin box
    t.cbox('hull2', [1.14, 0.42, 0.05], [0.34, 0.5, 1.25], 0.03)
    for dy in (-0.12, 0.12):
        t.cyl('dark', [1.14, 0.42 + dy, 0.672], [1.14, 0.42 + dy, 0.685], 0.1, 0.1, 10, caps='end')
        t.tube('metal', [1.14, 0.42 + dy, 0.66], [1.14, 0.42 + dy, 0.7], 0.13, 0.1, 10)
    t.cbox('hull2', [-0.42, 0.62, 0.2], [0.26, 0.22, 0.3], 0.03)  # commander sight
    t.box('glass', [-0.42, 0.63, 0.36], [0.18, 0.1, 0.02])
    hatch(t, [-0.35, 0.52, -0.55], 0.24)
    chevron(t, [0.22, 0.53, -0.25], 0.5, 0.09)
    for s in S2:
        t.box('glow', [s * 0.86, 0.13, -0.1], [0.08, 0.04, 1.0])
    smoke_bank(t, [-0.82, 0.32, -0.3], -1, 3)
    antenna(t, [0.4, 0.5, -0.8], 0.8)
    gun = tur.child('gun', (0, 0.27, 0.9))
    k = gun.g
    k.cbox('hull2', [0, 0, 0.1], [0.42, 0.32, 0.4], 0.04)
    k.cyl('metal', [0, 0, 0.3], [0, 0, 3.0], 0.05, 0.045, 10)
    k.cyl('hull2', [0, 0, 0.3], [0, 0, 1.0], 0.068, 0.065, 10)
    k.cyl('metal', [0, 0, 2.85], [0, 0, 3.1], 0.075, 0.075, 10)
    k.cyl('metal', [0.15, -0.05, 0.3], [0.15, -0.05, 0.6], 0.02, 0.02, 6)
    return body


def m_apc():
    body = Node('body')
    g = body.g
    for s in S2:
        for z in (-2.75, -1.35, 1.05, 2.45):
            wheel(g, [s * 1.22, 0.58, z], 0.58, 0.42, s, seg=18)
    prof = [[0.45, 0.55], [1.05, 0.78], [1.22, 1.2], [1.42, 1.28], [1.18, 1.86]]
    g.hull('hull', [
        [-3.9, prof], [2.2, prof],
        [3.4, [[0.45, 0.62], [1.05, 0.8], [1.22, 1.2], [1.42, 1.28], [1.15, 1.5]]],
        [4.0, [[0.45, 0.9], [0.95, 0.95], [1.15, 1.15], [1.3, 1.22], [1.05, 1.3]]],
    ])
    for s in S2:
        g.box('glow', [s * 1.36, 1.45, -0.6], [0.06, 0.045, 4.6])
        g.box('hull2', [s * 1.37, 1.6, -3.0], [0.06, 0.3, 1.2])  # side stowage
        g.cbox('hull2', [s * 0.85, 1.92, -2.6], [0.45, 0.14, 1.6], 0.03)  # roof bins
        g.box('glass', [s * 0.75, 1.27, 3.98], [0.22, 0.08, 0.05])
        g.box('glass', [s * 1.0, 1.3, -3.92], [0.14, 0.07, 0.03])
        for z in (-2.75, -1.35, 1.05, 2.45):  # wheel arch flares
            g.box('hull2', [s * 1.4, 1.27, z], [0.12, 0.05, 1.3])
    g.box('hull2', [0, 1.15, -3.92], [1.6, 1.2, 0.05])  # rear ramp
    g.box('dark', [-1.36, 1.55, 1.2], [0.06, 0.35, 0.6])  # exhaust grille
    hatch(g, [0.55, 1.87, 1.9], 0.26)
    g.at(TR(0.55, 1.75, 2.6, 30 * D), lambda: g.box('glass', [0, 0.02, 0], [0.45, 0.05, 0.08]))
    hatch(g, [-0.5, 1.87, -0.9], 0.3)
    hatch(g, [0.5, 1.87, -0.9], 0.3)
    chevron(g, [0, 1.875, 1.4], 0.9)
    antenna(g, [1.0, 1.86, -3.5], 1.6)
    antenna(g, [-1.0, 1.86, -3.5], 1.2)

    tur = body.child('turret', (0, 1.86, 0.4))
    t = tur.g
    t.cyl('hull2', [0, 0, 0], [0, 0.1, 0], 0.38, 0.36, 16)
    t.cbox('hull2', [0, 0.28, -0.05], [0.5, 0.32, 0.55], 0.04)
    t.cbox('hull2', [-0.33, 0.3, 0.05], [0.16, 0.24, 0.3], 0.03)
    t.box('glass', [-0.33, 0.31, 0.205], [0.1, 0.12, 0.02])
    t.box('dark', [0.33, 0.25, -0.05], [0.14, 0.2, 0.32])
    t.box('glow', [0, 0.445, -0.15], [0.3, 0.01, 0.06])
    for s in S2:
        smoke_bank(t, [s * 0.3, 0.12, -0.15], s, 2, 0.12)
    gun = tur.child('gun', (0, 0.32, 0.15))
    k = gun.g
    k.box('metal', [0, 0, 0.12], [0.14, 0.15, 0.45])
    k.cyl('metal', [0, 0, 0.35], [0, 0, 1.3], 0.03, 0.03, 8)
    k.cyl('metal', [0, 0, 1.18], [0, 0, 1.36], 0.045, 0.045, 8)
    return body


def m_spg():
    body = Node('body')
    g = body.g
    tracks(g, 1.33, 0.6, 0.34, [-2.95, -1.97, -0.99, 0.0, 0.99, 1.97, 2.95], (3.5, 0.64, 0.32), (-3.5, 0.56, 0.28))
    g.loft('hull2', [rect(-3.65, 1.02, 0.42, 1.0), rect(2.9, 1.02, 0.42, 1.0), rect(3.65, 1.02, 0.84, 1.0)])
    prof = [[1.7, 0.99], [1.72, 1.08], [1.45, 1.1], [1.38, 1.55]]
    g.hull('hull', [[-3.7, prof], [2.4, prof], [3.7, [[1.7, 0.99], [1.72, 1.06], [1.45, 1.08], [1.3, 1.14]]]])
    for s in S2:
        g.box('glow', [s * 1.72, 1.04, 1.0], [0.04, 0.04, 4.6])
        g.box('glass', [s * 1.0, 1.15, 3.72], [0.2, 0.08, 0.05])
        g.cbox('hull2', [s * 1.55, 1.22, 2.6], [0.3, 0.24, 1.2], 0.03)
        g.beam('metal', [s * 0.4, 1.35, 3.25], [s * 0.15, 2.05, 3.25], 0.08, 0.08)  # travel lock
    g.box('metal', [0, 2.08, 3.25], [0.55, 0.08, 0.2])
    hatch(g, [0.6, 1.3, 3.0], 0.26)
    g.box('dark', [0, 1.4, -3.72], [1.6, 0.3, 0.04])

    tur = body.child('turret', (0, 1.55, -1.3))
    t = tur.g
    t.hull('hull', [
        [-2.4, [[1.5, 0.0], [1.58, 1.05], [1.38, 1.5]]],
        [1.35, [[1.58, 0.0], [1.66, 1.1], [1.42, 1.55]]],
        [1.95, [[1.25, 0.12], [1.3, 0.98], [1.1, 1.28]]],
    ])
    t.cyl('dark', [0, -0.05, 0], [0, 0.02, 0], 1.3, 1.3, 20)
    for s in S2:
        t.box('glow', [s * 1.62, 0.8, -0.5], [0.1, 0.05, 3.0])
        t.cbox('hull2', [s * 1.68, 0.45, -1.0], [0.16, 0.5, 1.8], 0.03)
        for k in range(4):
            t.box('dark', [s * 0.8, 1.555, -1.9 + k * 0.18], [0.7, 0.02, 0.08])
        smoke_bank(t, [s * 1.45, 1.2, 1.6], s, 4)
    t.box('hull2', [0, 0.7, -2.42], [1.1, 0.9, 0.05])  # ammo door
    hatch(t, [0.75, 1.54, -0.8], 0.3)
    hatch(t, [-0.75, 1.54, -0.8], 0.3)
    t.cyl('hull2', [0.75, 1.6, -0.8], [0.75, 1.72, -0.8], 0.08, 0.08, 8)
    t.cbox('metal', [0.75, 1.78, -0.7], [0.12, 0.12, 0.3], 0.02)
    t.cyl('metal', [0.75, 1.78, -0.55], [0.75, 1.78, 0.3], 0.02, 0.02, 6)
    t.cbox('hull2', [-0.5, 1.65, 0.6], [0.3, 0.22, 0.3], 0.03)  # meteo / sight box
    t.box('glass', [-0.5, 1.66, 0.76], [0.2, 0.12, 0.02])
    chevron(t, [0, 1.56, 0.4], 1.1, 0.14)
    antenna(t, [1.2, 1.5, -2.1], 1.0)
    antenna(t, [-1.2, 1.5, -2.1], 0.7)
    gun = tur.child('gun', (0, 0.85, 1.7))
    k = gun.g
    k.cbox('hull2', [0, 0, 0.2], [0.9, 0.7, 0.9], 0.08)
    k.box('metal', [0, 0, 1.0], [0.6, 0.45, 0.8])
    k.box('hull2', [0, -0.2, 3.2], [0.22, 0.12, 4.0])
    rail_barrel(k, 1.2, 6.5, 0.18, 0.12, 0.26, [1.8 + 0.6 * i for i in range(8)], 0.3, y=0.02)
    return body


def m_mlrs():
    body = Node('body')
    g = body.g
    for s in S2:
        for z in (4.1, -1.6, -3.2):
            wheel(g, [s * 1.2, 0.6, z], 0.6, 0.44, s, seg=18)
            g.box('hull2', [s * 1.2, 1.27, z], [0.56, 0.05, 1.4])
        g.box('dark', [s * 0.55, 1.0, -0.2], [0.2, 0.3, 11.4])  # chassis rails
        g.cyl('hull2', [s * 1.05, 0.95, 0.2], [s * 1.05, 0.95, 2.6], 0.3, 0.3, 12)
        g.cbox('hull2', [s * 1.1, 0.95, -4.6], [0.5, 0.5, 1.0], 0.03)
        g.cyl('metal', [s * 1.25, 0.15, -5.6], [s * 1.25, 1.15, -5.6], 0.07, 0.07, 8)  # stabiliser jacks
        g.cyl('dark', [s * 1.25, 0.12, -5.6], [s * 1.25, 0.16, -5.6], 0.18, 0.18, 10)
        g.box('glass', [s * 1.42, 2.47, 4.4], [0.05, 0.4, 1.0])  # side windows
        g.box('glass', [s * 1.0, 1.25, 6.01], [0.25, 0.12, 0.04])
        g.beam('metal', [s * 1.4, 2.3, 5.2], [s * 1.6, 2.3, 5.4], 0.03, 0.03)  # mirrors
        g.box('dark', [s * 1.62, 2.2, 5.4], [0.04, 0.3, 0.18])
        g.box('glass', [s * 1.0, 1.3, -5.92], [0.14, 0.08, 0.03])
    g.box('hull2', [0, 1.25, -2.4], [2.7, 0.2, 7.0])  # rear deck
    g.cbox('hull2', [0, 1.75, 2.3], [2.5, 1.0, 2.2], 0.06)  # equipment module
    g.hull('hull', [
        [3.55, [[1.38, 1.0], [1.45, 2.3], [1.2, 3.0]]],
        [5.2, [[1.38, 1.0], [1.45, 2.3], [1.2, 2.98]]],
        [6.0, [[1.32, 1.0], [1.35, 1.95], [1.08, 2.2]]],
    ])
    g.at(TR(0, 2.6, 5.6, 44.3 * D), lambda: [g.box('glass', [s * 0.5, 0.0, 0], [0.9, 0.03, 0.85]) for s in S2])
    g.box('dark', [0, 0.95, 6.05], [2.6, 0.3, 0.2])
    g.box('dark', [0, 1.55, 6.0], [1.6, 0.45, 0.03])
    hatch(g, [-0.5, 2.98, 4.2], 0.3)
    chevron(g, [0.3, 3.0, 4.2], 0.9)
    antenna(g, [1.0, 2.95, 3.8], 1.2)
    for s in S2:
        g.box('glow', [s * 1.43, 2.1, 4.4], [0.05, 0.05, 1.6])

    tur = body.child('turret', (0, 1.35, -3.0))
    t = tur.g
    t.cyl('hull2', [0, 0, 0], [0, 0.28, 0], 1.0, 1.0, 20)
    for s in S2:
        t.prism_x('hull', [[-2.15, 0.15], [0.7, 0.15], [0.7, 0.3], [-1.6, 0.7], [-2.15, 0.7]], s * 1.22, s * 1.36)
    t.cyl('metal', [-1.4, 0.5, -1.9], [1.4, 0.5, -1.9], 0.09, 0.09, 10)
    t.box('glow', [0, 0.29, 0.5], [0.6, 0.01, 0.08])
    gun = tur.child('gun', (0, 0.5, -1.9))
    k = gun.g
    k.cbox('hull', [0, 0.7, 2.4], [2.4, 1.3, 5.2], 0.06)
    for z in (0.4, 1.6, 2.8, 4.0):
        k.box('hull2', [0, 0.7, z], [2.46, 1.36, 0.12])
    for x in (-0.8, 0.0, 0.8):
        for y in (0.38, 1.02):
            k.tube('metal', [x, y, 4.98], [x, y, 5.05], 0.3, 0.26, 14)
            k.cyl('dark', [x, y, 4.99], [x, y, 5.01], 0.26, 0.26, 14, caps='end')
    chevron(k, [0, 1.36, 2.2], 0.7)
    for s in S2:
        k.box('glow', [s * 1.205, 1.1, 2.4], [0.02, 0.06, 4.6])
    return body


def m_recon():
    body = Node('body')
    g = body.g
    g.hull('hull', [
        [-3.0, [[1.25, 0.66], [1.45, 0.88], [1.15, 1.18]]],
        [-2.4, [[1.4, 0.55], [1.5, 0.86], [1.22, 1.25]]],
        [1.0, [[1.38, 0.55], [1.46, 0.82], [1.1, 1.2]]],
        [3.0, [[0.42, 0.68], [0.48, 0.76], [0.25, 0.8]]],
    ])
    for s in S2:
        for z in (-1.9, 1.5):  # hover pads
            g.cyl('hull2', [s * 1.05, 0.46, z], [s * 1.05, 0.62, z], 0.42, 0.46, 16)
            g.cyl('glow', [s * 1.05, 0.4, z], [s * 1.05, 0.46, z], 0.36, 0.4, 16, caps='start')
            g.tube('dark', [s * 1.05, 0.5, z], [s * 1.05, 0.56, z], 0.475, 0.4, 16)
        g.box('glow', [s * 1.47, 0.84, -0.7], [0.04, 0.04, 3.2])
        g.box('dark', [s * 0.6, 0.95, -3.0], [0.6, 0.18, 0.04])
        g.box('glow', [s * 0.6, 0.95, -3.03], [0.5, 0.1, 0.03])
        g.box('glass', [s * 0.38, 0.77, 2.98], [0.1, 0.04, 0.04])
    g.at(TR(0, 1.1, 1.6, 11.3 * D), lambda: g.box('glass', [0, 0, 0], [1.4, 0.04, 0.9]))
    # telescopic sensor mast with radar head
    g.cyl('hull2', [-0.55, 1.15, -2.0], [-0.55, 1.5, -2.0], 0.13, 0.13, 10)
    g.cyl('metal', [-0.55, 1.5, -2.0], [-0.55, 1.72, -2.0], 0.09, 0.09, 10)
    g.cyl('metal', [-0.55, 1.72, -2.0], [-0.55, 1.87, -2.0], 0.06, 0.06, 8)
    g.cbox('hull2', [-0.55, 1.92, -2.0], [0.6, 0.12, 0.1], 0.02)
    g.sphere('glass', [-0.55, 1.86, -1.93], 0.06, 8, 5)
    g.box('glow', [-0.55, 1.985, -2.0], [0.5, 0.015, 0.02])
    chevron(g, [0, 1.255, -1.45], 0.7, 0.1)
    antenna(g, [0.6, 1.18, -2.6], 0.7)

    tur = body.child('turret', (0, 1.22, -0.5))
    t = tur.g
    t.hull('hull', [
        [-0.55, [[0.42, 0.02], [0.48, 0.18], [0.35, 0.32]]],
        [0.35, [[0.5, 0.0], [0.55, 0.18], [0.38, 0.34]]],
        [0.7, [[0.3, 0.04], [0.33, 0.18], [0.22, 0.28]]],
    ])
    t.cyl('dark', [0, -0.03, 0], [0, 0.02, 0], 0.5, 0.5, 16)
    t.box('glass', [0.3, 0.32, 0.25], [0.12, 0.08, 0.1])
    gun = tur.child('gun', (0, 0.17, 0.6))
    k = gun.g
    k.box('hull2', [0, 0, 0.05], [0.24, 0.2, 0.3])
    k.cyl('metal', [0, 0, 0.2], [0, 0, 1.7], 0.035, 0.03, 8)
    k.cyl('metal', [0, 0, 1.6], [0, 0, 1.75], 0.05, 0.05, 8)
    return body


def m_hover_tank():
    body = Node('body')
    g = body.g
    g.hull('hull', [
        [-4.0, [[1.75, 0.78], [1.98, 1.02], [1.65, 1.35]]],
        [-3.4, [[1.88, 0.65], [2.0, 1.0], [1.74, 1.45]]],
        [1.6, [[1.88, 0.65], [2.0, 1.0], [1.74, 1.45]]],
        [4.0, [[1.2, 0.82], [1.3, 0.92], [0.95, 0.97]]],
    ])
    g.hull('hull2', [[-3.2, [[1.3, 0.5], [1.4, 0.66]]], [1.8, [[1.3, 0.5], [1.4, 0.66]]], [2.8, [[1.0, 0.62], [1.1, 0.7]]]])
    for s in S2:
        for z in (-2.55, 2.25):  # hover nacelles
            x = s * 1.45
            g.cyl('hull2', [x, 0.46, z], [x, 0.98, z], 0.52, 0.55, 18)
            g.cyl('glow', [x, 0.4, z], [x, 0.46, z], 0.44, 0.5, 18, caps='start')
            g.tube('glow', [x, 0.7, z], [x, 0.75, z], 0.565, 0.5, 18)
            g.tube('dark', [x, 0.52, z], [x, 0.6, z], 0.545, 0.48, 18)
        g.tube('metal', [s * 0.9, 1.05, -3.9], [s * 0.9, 1.05, -4.25], 0.24, 0.19, 14)  # thrusters
        g.cyl('glow', [s * 0.9, 1.05, -4.1], [s * 0.9, 1.05, -4.05], 0.2, 0.2, 14, caps='start')
        g.box('glow', [s * 1.99, 1.0, -0.9], [0.04, 0.05, 4.6])
        g.box('glass', [s * 0.9, 0.9, 3.98], [0.3, 0.05, 0.04])
        g.cbox('hull2', [s * 1.55, 1.5, -2.9], [0.5, 0.12, 0.9], 0.03)
    g.box('dark', [0, 1.455, -3.0], [1.6, 0.02, 0.7])
    hatch(g, [0.6, 1.2, 2.6], 0.25)
    g.at(TR(0, 1.21, 2.9, 2.4 * D), lambda: chevron(g, [0, 0, 0], 0.9))

    tur = body.child('turret', (0, 1.45, -0.6))
    t = tur.g
    t.hull('hull', [
        [-1.9, [[1.05, 0.04], [1.15, 0.3], [0.92, 0.52]]],
        [0.9, [[1.25, 0.0], [1.32, 0.3], [1.02, 0.58]]],
        [1.8, [[0.62, 0.05], [0.66, 0.28], [0.46, 0.44]]],
    ])
    t.cyl('dark', [0, -0.05, 0], [0, 0.02, 0], 1.05, 1.05, 20)
    for s in S2:
        t.box('glow', [s * 1.24, 0.15, -0.4], [0.1, 0.04, 1.6])
        t.cbox('hull2', [s * 1.2, 0.45, 0.3], [0.26, 0.2, 0.4], 0.03)
        t.cyl('dark', [s * 1.2, 0.45, 0.5], [s * 1.2, 0.45, 0.53], 0.06, 0.06, 8, caps='end')
    t.cbox('hull2', [0.6, 0.66, 0.2], [0.26, 0.18, 0.3], 0.03)
    t.box('glass', [0.6, 0.67, 0.36], [0.18, 0.1, 0.02])
    hatch(t, [-0.45, 0.58, -0.3], 0.26)
    chevron(t, [0.1, 0.555, -1.1], 0.7)
    antenna(t, [-0.8, 0.5, -1.6], 0.7)
    gun = tur.child('gun', (0, 0.28, 1.45))
    k = gun.g
    k.cbox('hull2', [0, 0, 0.15], [0.56, 0.36, 0.6], 0.05)
    k.box('metal', [0, 0, 0.65], [0.44, 0.3, 0.4])
    rail_barrel(k, 0.5, 3.4, 0.13, 0.09, 0.2, [1.0, 1.6, 2.2, 2.8], 0.22)
    return body


def m_ad():
    body = Node('body')
    g = body.g
    tracks(g, 1.3, 0.56, 0.33, [-3.0, -1.8, -0.6, 0.6, 1.8, 3.0], (3.7, 0.62, 0.3), (-3.7, 0.56, 0.27))
    g.loft('hull2', [rect(-3.95, 1.0, 0.42, 1.0), rect(3.2, 1.0, 0.42, 1.0), rect(3.95, 1.0, 0.84, 1.0)])
    prof = [[1.6, 0.98], [1.62, 1.08], [1.4, 1.1], [1.32, 1.5]]
    g.hull('hull', [[-4.0, prof], [2.6, prof], [4.05, [[1.6, 0.96], [1.62, 1.04], [1.4, 1.06], [1.25, 1.12]]]])
    for s in S2:
        g.box('glow', [s * 1.37, 1.3, 0.5], [0.06, 0.04, 4.0])
        g.box('glass', [s * 1.0, 1.12, 4.05], [0.2, 0.07, 0.05])
        g.cbox('hull2', [s * 1.5, 1.22, -3.0], [0.22, 0.22, 1.4], 0.03)
    hatch(g, [0.6, 1.36, 3.2], 0.26)
    g.box('dark', [0, 1.505, -3.2], [1.8, 0.015, 1.0])
    g.box('dark', [0, 1.3, -4.02], [1.4, 0.2, 0.04])

    tur = body.child('turret', (0, 1.5, -0.7))
    t = tur.g
    t.hull('hull', [
        [-1.8, [[0.95, 0.02], [1.0, 0.68], [0.82, 0.92]]],
        [1.1, [[1.02, 0.0], [1.06, 0.7], [0.86, 0.98]]],
        [1.65, [[0.66, 0.06], [0.7, 0.6], [0.55, 0.8]]],
    ])
    t.cyl('dark', [0, -0.05, 0], [0, 0.02, 0], 1.0, 1.0, 20)
    t.cyl('metal', [0, 0.9, -1.35], [0, 1.05, -1.35], 0.12, 0.1, 10)  # radar panel
    t.at(TR(0, 1.42, -1.35, -12 * D), lambda: (
        t.cbox('hull2', [0, 0, 0], [1.7, 0.8, 0.14], 0.04),
        t.box('dark', [0, 0, 0.075], [1.5, 0.66, 0.02]),
        [t.box('glow', [0, y, 0.088], [1.4, 0.015, 0.008]) for y in (-0.2, 0.0, 0.2)]))
    t.cbox('hull2', [0, 1.02, 0.65], [0.36, 0.16, 0.5], 0.03)  # laser emitter
    t.cyl('metal', [0, 1.17, 0.4], [0, 1.17, 1.05], 0.11, 0.11, 14)
    t.cyl('glass', [0, 1.17, 1.05], [0, 1.17, 1.08], 0.085, 0.085, 14, caps='end')
    t.tube('glow', [0, 1.17, 1.0], [0, 1.17, 1.04], 0.13, 0.108, 14)
    for s in S2:
        t.beam('hull2', [s * 0.12, 1.0, 0.6], [s * 0.12, 1.12, 0.6], 0.04, 0.12)
    chevron(t, [0, 0.99, -0.45], 0.7)
    hatch(t, [-0.5, 0.97, 0.3], 0.24)
    antenna(t, [0.7, 0.92, -1.6], 0.6)
    gun = tur.child('gun', (0, 0.65, 0.1))
    k = gun.g
    k.cyl('metal', [-1.1, 0, 0], [1.1, 0, 0], 0.09, 0.09, 10)
    for s in S2:
        for dx in (-0.16, 0.16):
            for dy in (-0.16, 0.16):
                x = s * 1.38 + dx
                k.cyl('hull2', [x, dy, -0.9], [x, dy, 1.3], 0.14, 0.14, 12)
                k.cyl('dark', [x, dy, 1.3], [x, dy, 1.32], 0.12, 0.12, 12, caps='end')
        for z in (-0.7, 0.3, 1.15):
            k.cbox('hull', [s * 1.38, 0, z], [0.66, 0.66, 0.12], 0.03)
        k.box('glow', [s * 1.715, 0, 0.2], [0.02, 0.05, 1.8])
    return body


def m_gunship():
    body = Node('body')
    g = body.g
    g.hull('hull', [
        [7.0, [[0.12, -0.35], [0.16, -0.15], [0.1, 0.05]]],
        [6.2, [[0.42, -0.75], [0.5, -0.25], [0.32, 0.25]]],
        [4.3, [[0.66, -1.0], [0.78, -0.25], [0.5, 0.55]]],
        [1.0, [[0.74, -1.05], [0.86, -0.15], [0.62, 0.85]]],
        [-1.8, [[0.66, -0.9], [0.78, -0.05], [0.58, 0.85]]],
        [-3.6, [[0.32, -0.35], [0.36, 0.05], [0.27, 0.5]]],
        [-7.4, [[0.14, 0.0], [0.16, 0.18], [0.12, 0.32]]],
    ])
    g.hull('glass', [  # tandem canopy
        [5.6, [[0.3, 0.0], [0.25, 0.2], [0.08, 0.28]]],
        [4.6, [[0.5, 0.3], [0.42, 0.75], [0.16, 0.9]]],
        [2.6, [[0.55, 0.55], [0.47, 1.12], [0.18, 1.26]]],
        [1.4, [[0.55, 0.75], [0.44, 1.1], [0.16, 1.18]]],
        [0.6, [[0.5, 0.85], [0.35, 0.95], [0.12, 0.97]]],
    ])
    g.cbox('hull2', [0, 1.0, 3.4], [1.0, 0.45, 0.1], 0.03)
    g.sphere('glass', [0, -0.28, 6.85], 0.15, 10, 6)
    for s in S2:
        g.fin('hull', ([s * 0.7, -0.25, 1.2], 1.8, 0.22), ([s * 3.0, -0.38, 0.95], 1.3, 0.12), (0, 0, 1), (0, 1, 0))
        x = s * 3.45  # wingtip nacelle
        g.cyl('hull', [x, -0.35, -1.4], [x, -0.35, 1.6], 0.5, 0.5, 16, caps='none')
        g.cyl('hull', [x, -0.35, 1.6], [x, -0.35, 2.4], 0.5, 0.2, 16, caps='end')
        g.cyl('dark', [x, -0.35, 2.4], [x, -0.35, 2.43], 0.18, 0.18, 12, caps='end')
        g.cyl('metal', [x, -0.35, -1.7], [x, -0.35, -1.4], 0.38, 0.5, 16, caps='none')
        g.cyl('glow', [x, -0.35, -1.72], [x, -0.35, -1.68], 0.36, 0.36, 16, caps='start')
        g.cyl('hull2', [x, 0.1, 0.3], [x, 0.75, 0.3], 0.26, 0.2, 12, caps='end')
        g.box('glow', [x + s * 0.5, -0.35, 0.3], [0.03, 0.06, 0.6])
        for px in (1.6, 2.5):  # pylons with missiles
            g.box('hull2', [s * px, -0.48, 1.0], [0.08, 0.25, 0.9])
            g.box('hull2', [s * px, -0.6, 0.8], [0.4, 0.05, 1.2])
            for dx in (-0.13, 0.13):
                mx = s * px + dx
                g.cyl('hull2', [mx, -0.7, 0.0], [mx, -0.7, 1.6], 0.07, 0.07, 8, caps='start')
                g.cyl('glass', [mx, -0.7, 1.6], [mx, -0.7, 1.8], 0.07, 0.0, 8)
        g.fin('hull', ([s * 0.15, 0.25, -6.2], 1.0, 0.1), ([s * 1.6, 0.3, -6.7], 0.6, 0.06), (0, 0, 1), (0, 1, 0))
        g.fin('hull', ([s * 1.6, 0.3, -6.4], 0.9, 0.1), ([s * 1.6, 2.1, -7.1], 0.55, 0.06), (0, 0, 1), (1, 0, 0))
        g.box('glow', [s * 0.83, -0.2, 1.5], [0.06, 0.06, 4.0])
        g.cyl('dark', [s * 0.55, 0.5, -1.6], [s * 0.55, 0.5, -2.1], 0.16, 0.16, 10, caps='end')  # engine exhausts
        g.cyl('glow', [s * 0.55, 0.5, -2.11], [s * 0.55, 0.5, -2.09], 0.12, 0.12, 10, caps='start')
    chevron(g, [0, 0.87, -0.8], 0.8)
    antenna(g, [0, 0.4, -4.5], 0.5)

    for name, s in (('rotor_l', 1), ('rotor_r', -1)):
        r = body.child(name, (s * 3.45, 0.85, 0.3))
        rg = r.g
        rg.cyl('metal', [0, -0.1, 0], [0, 0.15, 0], 0.28, 0.24, 12)
        rg.cyl('hull2', [0, 0.15, 0], [0, 0.4, 0], 0.22, 0.02, 12, caps='start')
        rg.cyl('rotor', [0, 0.09, 0], [0, 0.1, 0], 3.0, 3.0, 40)
        for b in range(3):
            rg.at(mmul(RY(b * 120 * D + (0.3 if s > 0 else 0.9)), RX(-s * 8 * D)),
                  lambda: (rg.box('metal', [1.6, 0.0, 0], [2.8, 0.04, 0.3]), rg.box('glow', [2.95, 0.0, 0], [0.1, 0.045, 0.3])))

    tur = body.child('turret', (0, -1.08, 5.3))
    t = tur.g
    t.cyl('hull2', [0, -0.05, 0], [0, 0.25, 0], 0.33, 0.3, 12, caps='end')
    t.sphere('hull2', [0, -0.05, 0], 0.33, 12, 4, -90, 0)
    t.box('glass', [0, -0.05, 0.31], [0.14, 0.1, 0.04])
    gun = tur.child('gun', (0, -0.12, 0.15))
    k = gun.g
    k.cyl('metal', [0, 0, -0.2], [0, 0, 0.45], 0.13, 0.11, 12)
    for b in range(3):
        a = b * 120 * D
        k.cyl('metal', [0.055 * math.cos(a), 0.055 * math.sin(a), 0.4], [0.055 * math.cos(a), 0.055 * math.sin(a), 1.9], 0.026, 0.026, 6)
    k.cyl('metal', [0, 0, 1.6], [0, 0, 1.7], 0.1, 0.1, 10)
    return body


def m_jet():
    body = Node('body')
    g = body.g
    g.hull('hull', [
        [6.6, [[0.02, -0.02], [0.03, 0.0], [0.02, 0.02]]],
        [5.0, [[0.3, -0.32], [0.55, -0.06], [0.26, 0.26]]],
        [2.6, [[0.62, -0.52], [1.0, -0.1], [0.46, 0.46]]],
        [0.0, [[0.9, -0.58], [1.38, -0.1], [0.62, 0.42]]],
        [-3.6, [[0.95, -0.52], [1.38, -0.05], [0.66, 0.38]]],
        [-6.0, [[0.72, -0.34], [1.0, 0.0], [0.52, 0.24]]],
    ])
    g.hull('glass', [
        [4.7, [[0.18, 0.2], [0.13, 0.29], [0.04, 0.32]]],
        [3.4, [[0.36, 0.38], [0.28, 0.68], [0.1, 0.78]]],
        [1.9, [[0.38, 0.44], [0.28, 0.66], [0.1, 0.72]]],
        [0.9, [[0.32, 0.43], [0.2, 0.48], [0.06, 0.5]]],
    ])
    g.box('hull2', [0, -0.59, 0.0], [0.9, 0.02, 3.0])  # weapons bay
    g.box('glow', [0, 0.41, -1.5], [0.08, 0.03, 3.0])
    chevron(g, [0, 0.43, -0.2], 0.7, 0.1)
    for s in S2:
        g.fin('hull', ([s * 1.2, -0.12, 2.0], 7.2, 0.28), ([s * 4.1, -0.18, -3.9], 1.4, 0.06), (0, 0, 1), (0, 1, 0))
        g.box('glow', [s * 4.08, -0.18, -4.4], [0.05, 0.05, 0.3])
        g.at(TR(s * 0.95, 0.15, 0, 0, 0, -s * 26 * D),
             lambda: g.fin('hull', ([0, 0, -3.6], 2.5, 0.12), ([0, 2.3, -5.4], 1.2, 0.05), (0, 0, 1), (1, 0, 0)))
        x, y = s * 0.48, -0.08  # engine nozzles
        g.tube('metal', [x, y, -6.0], [x, y, -6.6], 0.4, 0.33, 16)
        g.cyl('glow', [x, y, -6.25], [x, y, -6.2], 0.34, 0.34, 16, caps='start')
        g.cyl('glow', [x, y, -6.25], [x, y, -6.75], 0.22, 0.05, 12, caps='none')
        g.cbox('dark', [s * 0.95, -0.32, 2.0], [0.4, 0.32, 0.9], 0.05)  # intakes
        g.box('hull2', [s * 0.5, -0.56, -2.5], [0.3, 0.02, 2.0])
    g.cyl('metal', [0, 0, 6.5], [0, 0, 6.9], 0.02, 0.008, 5)
    for s in S2:  # wingtip AAMs and under-wing stores
        store(g, [s * 4.02, -0.2, -2.4], 2.4, 0.07)
        g.box('hull2', [s * 2.5, -0.3, -1.4], [0.06, 0.18, 1.2])
        store(g, [s * 2.5, -0.46, -0.3], 2.6, 0.11)
        g.cbox('hull2', [s * 0.62, -0.62, 1.2], [0.25, 0.08, 1.6], 0.02)  # gear doors
        g.at(TR(s * 1.05, 0.0, 3.4, 0, 0, -s * 30 * D), lambda: g.box('hull2', [0, 0, 0], [0.5, 0.025, 1.4]))  # chine strake
    return body


def store(g, nose, L, r, seg=12):
    """missile / store along +Z ending at `nose`"""
    x, y, z = nose
    g.cyl('hull2', [x, y, z - L], [x, y, z - 0.25], r, r, seg, caps='start')
    g.cyl('glass', [x, y, z - 0.25], [x, y, z], r, 0.0, seg)
    for a in (45, 135, 225, 315):
        g.at(TR(x, y, z - L + 0.25, 0, 0, a * D), lambda: g.fin('hull2', ([0, r, 0.0], 0.2, 0.012), ([0, r + 0.12, -0.1], 0.1, 0.008), (0, 0, 1), (1, 0, 0)))


def m_uav():
    body = Node('body')
    g = body.g
    g.loft('hull', [oval(3.0, 0.04, 0.04, -0.02, 16), oval(2.9, 0.17, 0.17, -0.01, 16), oval(2.75, 0.26, 0.26, 0.0, 16),
                    oval(2.2, 0.36, 0.36, 0.04, 16), oval(1.0, 0.34, 0.34, 0.02, 16), oval(-1.4, 0.26, 0.24, 0.0, 16),
                    oval(-2.8, 0.12, 0.11, 0.0, 16)])
    g.sphere('hull', [0, 0.22, 2.0], 0.3, 12, 4, 0, 90, sy=0.7)  # satcom hump
    g.cyl('hull2', [0, -0.25, 2.2], [0, -0.4, 2.2], 0.1, 0.1, 8)
    g.sphere('glass', [0, -0.5, 2.2], 0.17, 16, 10)  # sensor ball
    g.box('glow', [0, 0.34, 0.0], [0.06, 0.02, 1.4])
    for s in S2:
        g.fin('hull', ([s * 0.25, 0.28, 0.65], 0.8, 0.12), ([s * 6.0, 0.42, 0.45], 0.45, 0.05), (0, 0, 1), (0, 1, 0))
        g.box('hull2', [s * 4.0, 0.36, 0.0], [2.6, 0.03, 0.12])  # aileron
        g.box('glow', [s * 6.0, 0.42, 0.25], [0.03, 0.04, 0.3])
        chevron(g, [s * 4.8, 0.425, 0.25], 0.4, 0.06, 0.02)
        g.at(TR(0, 0.05, 0, 0, 0, -s * 50 * D),
             lambda: g.fin('hull', ([0, 0, -2.0], 0.65, 0.07), ([0, 1.25, -2.5], 0.35, 0.04), (0, 0, 1), (1, 0, 0)))
    antenna(g, [0, 0.3, -1.0], 0.25)
    for s in S2:  # hardpoints with missiles, landing gear
        for px in (1.5, 2.6):
            g.box('hull2', [s * px, 0.15, 0.45], [0.05, 0.16, 0.4])
            store(g, [s * px, 0.0, 0.9], 1.4, 0.06, 10)
        g.beam('metal', [s * 0.25, -0.15, 0.2], [s * 0.7, -0.62, 0.15], 0.05, 0.05)
        g.cyl('dark', [s * 0.66, -0.66, 0.15], [s * 0.78, -0.66, 0.15], 0.13, 0.13, 12)
    g.beam('metal', [0, -0.2, 2.6], [0, -0.6, 2.65], 0.04, 0.04)
    g.cyl('dark', [-0.04, -0.68, 2.65], [0.04, -0.68, 2.65], 0.1, 0.1, 12)
    g.box('glass', [0, 0.18, 2.62], [0.25, 0.06, 0.2])
    g.cyl('hull2', [0, 0, -2.8], [0, 0, -2.9], 0.12, 0.1, 10, caps='none')
    p = body.child('prop', (0, 0, -2.9))
    pg = p.g
    pg.cyl('metal', [0, 0, 0.0], [0, 0, -0.3], 0.12, 0.0, 12, caps='start')
    for b in range(3):
        pg.at(mmul(RZ(b * 120 * D), RY(20 * D)), lambda: pg.box('hull2', [0, 0.45, -0.05], [0.12, 0.7, 0.03]))
    return body


def container(g, c, rot=False):
    x, y, z = c
    L, W = 6.06, 2.44
    sx, sz = (L, W) if rot else (W, L)
    g.box('hull2', [x, 1.295, z], [sx, 2.59, sz])
    n = 12
    for i in range(n):  # corrugation ribs on the long sides
        f = -L / 2 + 0.3 + i * (L - 0.6) / (n - 1)
        for s in S2:
            if rot:
                g.box('hull', [x + f, 1.295, z + s * (W / 2 + 0.02)], [0.12, 2.3, 0.04])
            else:
                g.box('hull', [x + s * (W / 2 + 0.02), 1.295, z + f], [0.04, 2.3, 0.12])
    for s in S2:  # door bars
        if not rot:
            g.beam('metal', [x + s * 0.4, 0.2, z + L / 2 + 0.03], [x + s * 0.4, 2.4, z + L / 2 + 0.03], 0.04, 0.04)


def m_hvt():
    body = Node('body')
    g = body.g
    bx = -1.6

    def bunker():
        prof = [[2.6, 0.0], [2.5, 1.4], [2.0, 2.3], [1.0, 2.7]]
        g.hull('hull', [[-5.0, prof], [2.2, prof]])
        for s in S2:
            g.prism_z('hull2', [[s * 2.55, 0.0], [s * 3.6, 0.0], [s * 2.45, 1.5]], -5.3, 2.5)
            g.box('glow', [s * 1.55, 2.5, -1.4], [0.06, 0.06, 6.6])
        g.prism_x('hull2', [[-5.0, 0.0], [-6.1, 0.0], [-5.0, 1.9]], -2.6, 2.6)
        g.box('dark', [0, 1.0, 2.21], [1.8, 2.0, 0.04])
        g.cbox('hull2', [0, 2.1, 2.3], [2.2, 0.2, 0.25], 0.03)
        g.cbox('hull2', [0, 1.1, 3.6], [3.0, 2.2, 0.5], 0.05)  # blast wall
        for row in range(3):
            for i in range(5):
                g.cbox('hull2', [-2.6 + i * 0.6 + (0.3 if row % 2 else 0), 0.14 + row * 0.26, 2.75], [0.58, 0.26, 0.35], 0.07)
        chevron(g, [0, 2.72, -1.4], 2.2, 0.28, 0.05)
        for z in (-3.8, 0.6):
            g.cyl('metal', [0.4, 2.65, z], [0.4, 3.1, z], 0.15, 0.15, 10)
            g.cyl('metal', [0.4, 3.1, z], [0.4, 3.18, z], 0.25, 0.25, 10)
        antenna(g, [-0.6, 2.7, -4.2], 3.0, 0.02)
        antenna(g, [0.7, 2.7, -4.4], 2.4, 0.02)
        g.beam('metal', [-0.6, 5.0, -4.2], [-0.6 - 0.5, 5.0, -4.2], 0.03, 0.03)
        g.beam('metal', [-0.6, 4.6, -4.2], [-0.6 + 0.5, 4.6, -4.2], 0.03, 0.03)
    g.at(T(bx, 0, 0), bunker)

    container(g, [3.5, 0, -3.1])
    container(g, [3.5, 0, 3.6])
    # satcom dish on container
    g.at(TR(3.5, 2.59, -4.5, -40 * D), lambda: (
        g.cyl('metal', [0, -0.3, 0], [0, 0.2, 0], 0.05, 0.05, 6),
        g.cyl('hull2', [0, 0.2, 0], [0, 0.2, 0.3], 0.15, 0.7, 16, caps='end'),
        g.cyl('metal', [0, 0.2, 0.3], [0, 0.2, 0.75], 0.02, 0.02, 5)))
    # generator
    g.cbox('hull2', [3.6, 0.6, 0.2], [1.6, 1.2, 1.0], 0.06)
    g.box('dark', [3.6, 0.7, 0.71], [1.2, 0.6, 0.03])
    g.cyl('metal', [4.1, 1.2, 0.0], [4.1, 1.9, 0.0], 0.06, 0.06, 8)
    # camouflage net on poles over the forward container + generator
    net_pts = [[1.9, 2.95, -0.8], [4.7, 3.05, -1.0], [5.1, 3.0, 2.2], [4.9, 3.1, 6.9], [2.4, 2.9, 7.0], [1.7, 3.0, 3.0]]
    g.loft('hull2', [[[x, y + 0.05, z] for x, y, z in net_pts], [[x, y, z] for x, y, z in net_pts]])
    for x, z in ((1.9, -0.6), (4.8, -0.8), (4.8, 6.8), (2.4, 6.8)):
        g.cyl('metal', [x, 0, z], [x, 2.98, z], 0.05, 0.05, 6)
    # radar mast (static part); the rotating head is the turret
    rx, rz = -3.9, 5.4
    g.box('hull2', [rx, 0.15, rz], [1.4, 0.3, 1.4])
    g.cyl('metal', [rx, 0.3, rz], [rx, 4.4, rz], 0.16, 0.11, 10)
    for a in (30, 150, 270):
        g.beam('metal', [rx, 3.8, rz], [rx + 2.4 * math.cos(a * D), 0.02, rz + 2.4 * math.sin(a * D)], 0.02, 0.02)
    # sandbag ring around the mast
    for i in range(10):
        a = i / 10 * 2 * math.pi
        g.at(TR(rx + 1.3 * math.cos(a), 0.14, rz + 1.3 * math.sin(a), 0, -a + math.pi / 2, 0),
             lambda: g.cbox('hull2', [0, 0, 0], [0.75, 0.28, 0.35], 0.07))
    tur = body.child('turret', (rx, 4.4, rz))
    t = tur.g
    t.cyl('hull2', [0, 0, 0], [0, 0.25, 0], 0.25, 0.22, 12)
    t.box('hull2', [0, 0.4, 0], [0.5, 0.3, 0.4])
    for s in S2:
        t.beam('metal', [s * 0.27, 0.3, 0], [s * 0.27, 0.65, 0], 0.06, 0.08)
    t.box('glow', [0, 0.56, -0.15], [0.12, 0.02, 0.12])
    gun = tur.child('gun', (0, 0.55, 0))
    k = gun.g
    k.cyl('hull2', [0, 0, -0.15], [0, 0, 0.2], 0.2, 1.0, 24, caps='none')
    k.cyl('metal', [0, 0, 0.2], [0, 0, 0.22], 1.0, 1.0, 24, caps='end')
    for a in (90, 210, 330):
        k.beam('metal', [0.95 * math.cos(a * D), 0.95 * math.sin(a * D), 0.22], [0, 0, 0.9], 0.025, 0.025)
    k.cbox('metal', [0, 0, 0.95], [0.12, 0.12, 0.14], 0.02)
    return body


def m_missile():
    body = Node('body')
    g = body.g
    g.cyl('hull', [0, 0, -0.62], [0, 0, 0.5], 0.065, 0.065, 32, caps='none')
    g.cyl('hull', [0, 0, 0.5], [0, 0, 0.68], 0.065, 0.045, 32, caps='none')
    g.cyl('hull', [0, 0, 0.68], [0, 0, 0.76], 0.045, 0.03, 32, caps='none')
    g.at(mmul(T(0, 0, 0.76), RX(90 * D)), lambda: g.sphere('glass', [0, 0, 0], 0.03, 16, 6, 0, 90))
    for z0 in (0.1, -0.3):
        g.cyl('hull2', [0, 0, z0], [0, 0, z0 + 0.05], 0.0665, 0.0665, 32, caps='none')
    for i in range(4):
        a = (45 + 90 * i) * D
        g.at(RZ(a), lambda: (
            g.fin('hull2', ([0, 0.055, -0.38], 0.24, 0.012), ([0, 0.19, -0.5], 0.12, 0.008), (0, 0, 1), (1, 0, 0)),
            g.fin('hull2', ([0, 0.055, 0.44], 0.1, 0.008), ([0, 0.12, 0.4], 0.06, 0.006), (0, 0, 1), (1, 0, 0))))
    g.cyl('metal', [0, 0, -0.62], [0, 0, -0.74], 0.06, 0.05, 32, caps='none')
    g.cyl('glow', [0, 0, -0.735], [0, 0, -0.73], 0.05, 0.05, 32, caps='start')
    g.tube('metal', [0, 0, -0.74], [0, 0, -0.78], 0.05, 0.038, 32)
    g.cyl('glow', [0, 0, -0.74], [0, 0, -0.84], 0.036, 0.0, 16, caps='none')
    return body


MODELS = {
    'mbt_rail': m_mbt_rail, 'mbt': m_mbt, 'ifv': m_ifv, 'apc': m_apc, 'spg': m_spg, 'mlrs': m_mlrs,
    'recon': m_recon, 'hover_tank': m_hover_tank, 'ad': m_ad, 'gunship': m_gunship, 'jet': m_jet,
    'uav': m_uav, 'hvt': m_hvt,
}


def count_tris(n): return n.g.tri_count() + sum(count_tris(c) for c in n.children)


def main():
    os.makedirs(OUT, exist_ok=True)
    jobs = [(mid, fn, team) for mid, fn in MODELS.items() for team in ('blue', 'red')]
    cache = {}
    for mid, fn, team in jobs:
        if mid not in cache:
            cache[mid] = fn()
        data = encode_glb(cache[mid], livery(team))
        path = os.path.join(OUT, '%s_%s.glb' % (mid, team))
        with open(path, 'wb') as f:
            f.write(data)
        print('%-22s %6d tris %7.1f KB' % (os.path.basename(path), count_tris(cache[mid]), len(data) / 1024))
    root = m_missile()
    data = encode_glb(root, livery('neutral'))
    with open(os.path.join(OUT, 'missile.glb'), 'wb') as f:
        f.write(data)
    print('%-22s %6d tris %7.1f KB' % ('missile.glb', count_tris(root), len(data) / 1024))


if __name__ == '__main__':
    main()
