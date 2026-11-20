"""KiCad dimensions -> plain Altium graphics (lines + text).

Altium dimension objects behave differently from KiCad's; drawing the
dimension as graphics keeps the exact appearance of the KiCad drawing.
"""
from __future__ import annotations

import math

from .. import sexpr as S
from ..altium.ir import mm
from ..kicad.common import parse_effects
from ..kicad.pcb import Text


def emit_dimension(conv, ir, node):
    layer = str(S.val(node, "layer", "Dwgs.User"))
    code = conv.lm.get(layer)
    if code is None:
        return
    dtype = str(S.val(node, "type", "aligned"))
    pts = [(p[1], p[2]) for p in S.pts(node) if p[0] == "xy"]
    style = S.find(node, "style")
    th = S.fval(style, "thickness", 0.15) if style is not None else 0.15
    w = mm(th)
    height = S.fval(node, "height", 0.0)
    lines = []
    if dtype in ("aligned", "orthogonal") and len(pts) >= 2:
        a, b = pts[0], pts[1]
        if dtype == "orthogonal":
            orient = int(S.fval(node, "orientation", 0))
            if orient == 0:      # horizontal
                b2 = (b[0], a[1])
            else:
                b2 = (a[0], b[1])
            dx, dy = b2[0] - a[0], b2[1] - a[1]
        else:
            dx, dy = b[0] - a[0], b[1] - a[1]
        L = math.hypot(dx, dy) or 1.0
        nx, ny = dy / L, -dx / L  # KiCad: height offsets along the normal
        off = height
        ea = (a[0] + nx * off, a[1] + ny * off)
        eb = (a[0] + dx + nx * off, a[1] + dy + ny * off)
        lines.append((a, ea))
        lines.append(((a[0] + dx, a[1] + dy) if dtype == "orthogonal" else b, eb))
        lines.append((ea, eb))
        ux, uy = dx / L, dy / L
        al = S.fval(style, "arrow_length", 1.27) if style is not None else 1.27
        for (p, sgn) in ((ea, 1), (eb, -1)):
            for ang in (25, -25):
                c, s = math.cos(math.radians(ang)), math.sin(math.radians(ang))
                vx = (ux * c - uy * s) * sgn * al
                vy = (ux * s + uy * c) * sgn * al
                lines.append((p, (p[0] + vx, p[1] + vy)))
    elif dtype in ("leader", "radial", "center") and len(pts) >= 2:
        lines.append((pts[0], pts[1]))
    for p, q in lines:
        A, B = conv.P(p), conv.P(q)
        ir.cmd("TRACK", x1=A[0], y1=A[1], x2=B[0], y2=B[1], w=w, l=code, net=0)
    gt = S.find(node, "gr_text")
    if gt is not None:
        txt = str(gt[1]) if len(gt) > 1 else ""
        x, y, ang = S.at(gt)
        eff = parse_effects(S.find(gt, "effects"))
        t = Text(text=txt, pos=(x, y), angle=ang, layer=layer, effects=eff)
        conv._text_cmd(ir, "TEXT", t, (x, y), ang, code, txt)
