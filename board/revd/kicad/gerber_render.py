"""Render RS-274X gerbers with a from-scratch parser.

Deliberately does not use KiCad: the point of a pre-order check is to see
what is actually in the files that will be uploaded, using code that had no
hand in writing them. If this render matches the intended board, the export
is faithful; if it does not, something is wrong in the export path that no
amount of KiCad-side DRC would reveal.

Handles the subset KiCad emits: linear/arc interpolation, C/R/O/P
apertures, flashes, and G36/G37 filled regions (the copper pours).
"""
import math
import re
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Circle, Rectangle, Polygon


class Gerber:
    def __init__(self, path):
        self.path = Path(path)
        self.apertures = {}          # dcode -> (shape, [params])
        self.scale = 1e-6            # from %FSLAX46Y46*%
        self.strokes = []            # (x0,y0,x1,y1,width)
        self.flashes = []            # (x,y,shape,params)
        self.regions = []            # [(x,y), ...]
        self._parse()

    def _parse(self):
        txt = self.path.read_text(errors="replace")

        m = re.search(r"%FSLAX(\d)(\d)Y(\d)(\d)\*%", txt)
        if m:
            self.scale = 10 ** -int(m.group(2))

        # Aperture names are either a primitive (C/R/O/P) or a MACRO name
        # such as RoundRect / HorizOval, which KiCad emits for rounded pads.
        # An earlier version matched [A-Z]+ and split "RoundRect" into shape
        # "R" plus junk params, which crashed on the first rounded pad.
        for am in re.finditer(r"%ADD(\d+)([A-Za-z][A-Za-z0-9_]*),?([^*]*)\*%", txt):
            d = int(am.group(1))
            name = am.group(2)
            raw = [v.strip() for v in am.group(3).split("X") if v.strip()]
            nums = []
            for v in raw:
                try:
                    nums.append(float(v))
                except ValueError:
                    pass                      # macro may carry non-numeric args
            if name in ("C", "R", "O", "P"):
                self.apertures[d] = (name, nums)
            elif nums:
                # Macro: approximate by the extent of its coordinate pairs.
                # KiCad's RoundRect is <radius> then x,y corner pairs, so the
                # bounding box of those pairs is the pad outline - close
                # enough for a visual proof.
                pts = nums[1:] if len(nums) > 4 else nums
                xs, ys = pts[0::2], pts[1::2]
                if xs and ys:
                    w = (max(xs) - min(xs)) or nums[0] * 2
                    h = (max(ys) - min(ys)) or nums[0] * 2
                    self.apertures[d] = ("R", [w, h])
                else:
                    self.apertures[d] = ("C", [nums[0] * 2])
            else:
                self.apertures[d] = ("C", [0.2])

        cur = None
        x = y = 0.0
        interp = 1
        in_region = False
        region = []

        # strip attribute blocks so they do not confuse the op scanner
        body = re.sub(r"%[^%]*%", " ", txt)

        for tok in re.finditer(
            r"(G0?[123])\*|D(\d+)\*|G36\*|G37\*|"
            r"((?:[XYIJ]-?\d+)+)\s*D0?([123])\*", body):
            if tok.group(1):
                interp = int(tok.group(1)[-1])
            elif tok.group(2):
                cur = int(tok.group(2))
            elif tok.group(0) == "G36*":
                in_region, region = True, []
            elif tok.group(0) == "G37*":
                if len(region) > 2:
                    self.regions.append(region)
                in_region, region = False, []
            else:
                coords = dict(re.findall(r"([XYIJ])(-?\d+)", tok.group(3)))
                nx = int(coords["X"]) * self.scale if "X" in coords else x
                ny = int(coords["Y"]) * self.scale if "Y" in coords else y
                op = int(tok.group(4))
                if in_region:
                    if op in (1, 2):
                        region.append((nx, ny))
                elif op == 1:
                    shape, params = self.apertures.get(cur, ("C", [0.1]))
                    w = params[0] if params else 0.1
                    if interp in (2, 3) and "I" in coords:
                        # approximate an arc by its chord; adequate for a
                        # visual check, and KiCad emits few arcs here
                        self.strokes.append((x, y, nx, ny, w))
                    else:
                        self.strokes.append((x, y, nx, ny, w))
                elif op == 3:
                    shape, params = self.apertures.get(cur, ("C", [0.1]))
                    self.flashes.append((nx, ny, shape, params))
                x, y = nx, ny

    def bbox(self):
        xs, ys = [], []
        for x0, y0, x1, y1, w in self.strokes:
            xs += [x0, x1]; ys += [y0, y1]
        for x, y, s, p in self.flashes:
            xs.append(x); ys.append(y)
        for r in self.regions:
            xs += [p[0] for p in r]; ys += [p[1] for p in r]
        if not xs:
            return None
        return min(xs), min(ys), max(xs), max(ys)

    def draw(self, ax, color, alpha=1.0, lw_scale=1.0, fill_regions=True):
        for r in self.regions:
            if fill_regions:
                ax.add_patch(Polygon(r, closed=True, facecolor=color,
                                     edgecolor="none", alpha=alpha * 0.45))
        for x0, y0, x1, y1, w in self.strokes:
            ax.plot([x0, x1], [y0, y1], color=color, alpha=alpha,
                    linewidth=max(w * lw_scale, 0.25),
                    solid_capstyle="round", zorder=3)
        for x, y, shape, p in self.flashes:
            if shape == "C" and p:
                ax.add_patch(Circle((x, y), p[0] / 2, facecolor=color,
                                    edgecolor="none", alpha=alpha, zorder=4))
            elif shape in ("R", "O") and len(p) >= 2:
                ax.add_patch(Rectangle((x - p[0] / 2, y - p[1] / 2), p[0], p[1],
                                       facecolor=color, edgecolor="none",
                                       alpha=alpha, zorder=4))
            elif p:
                ax.add_patch(Circle((x, y), p[0] / 2, facecolor=color,
                                    edgecolor="none", alpha=alpha, zorder=4))


def excellon_holes(path):
    txt = Path(path).read_text(errors="replace")
    tools, holes, cur = {}, [], None
    metric = "METRIC" in txt
    for line in txt.splitlines():
        line = line.strip()
        m = re.match(r"^T(\d+)C([\d.]+)", line)
        if m:
            tools[int(m.group(1))] = float(m.group(2)); continue
        m = re.match(r"^T(\d+)$", line)
        if m:
            cur = int(m.group(1)); continue
        m = re.match(r"^X(-?[\d.]+)Y(-?[\d.]+)", line)
        if m and cur is not None:
            x, y = float(m.group(1)), float(m.group(2))
            if not metric:
                x, y = x * 25.4, y * 25.4
            holes.append((x, y, tools.get(cur, 0.3)))
    return tools, holes


def main(gdir, out):
    gdir = Path(gdir)
    edge = Gerber(gdir / "emg-Edge_Cuts.gm1")
    top = Gerber(gdir / "emg-F_Cu.gtl")
    bot = Gerber(gdir / "emg-B_Cu.gbl")
    silk = Gerber(gdir / "emg-F_Silkscreen.gto")
    tools, holes = excellon_holes(gdir / "emg.drl")

    bb = edge.bbox()
    print("outline bbox mm: %.3f, %.3f -> %.3f, %.3f  (%.2f x %.2f)"
          % (bb[0], bb[1], bb[2], bb[3], bb[2] - bb[0], bb[3] - bb[1]))
    print("F.Cu   strokes %4d flashes %4d regions %d" %
          (len(top.strokes), len(top.flashes), len(top.regions)))
    print("B.Cu   strokes %4d flashes %4d regions %d" %
          (len(bot.strokes), len(bot.flashes), len(bot.regions)))
    print("silk   strokes %4d flashes %4d" % (len(silk.strokes), len(silk.flashes)))
    print("drill  tools %d, holes %d" % (len(tools), len(holes)))

    fig, axes = plt.subplots(1, 3, figsize=(19, 7.2))
    views = [
        ("TOP  copper + silk + drill", [(top, "#B8722C", 1.0)], True, True),
        ("BOTTOM  copper (as seen through the board)", [(bot, "#2E7D8F", 1.0)], False, True),
        ("BOTH layers + outline", [(top, "#B8722C", .8), (bot, "#2E7D8F", .8)], False, True),
    ]
    for ax, (title, layers, with_silk, with_drill) in zip(axes, views):
        for g, col, a in layers:
            g.draw(ax, col, alpha=a, lw_scale=1.0)
        if with_silk:
            silk.draw(ax, "#DCDCDC", alpha=.95)
        if with_drill:
            for x, y, d in holes:
                ax.add_patch(Circle((x, y), d / 2, facecolor="white",
                                    edgecolor="#333", linewidth=.3, zorder=6))
        # outline last, on top
        for x0, y0, x1, y1, w in edge.strokes:
            ax.plot([x0, x1], [y0, y1], color="#111", linewidth=1.6, zorder=8)
        ax.set_aspect("equal")
        ax.set_xlim(bb[0] - 3, bb[2] + 3)
        # Gerber Y increases upward. Plot it that way: inverting the axis
        # mirrors the silkscreen, and mirrored top silk is a real fab defect,
        # so the render must not be able to fake one either way.
        ax.set_ylim(bb[1] - 3, bb[3] + 3)
        ax.set_title(title, fontsize=11)
        ax.set_facecolor("#20262B")
        ax.tick_params(labelsize=8, colors="#888")
    fig.suptitle("EMG rev E - rendered from the shipped gerbers "
                 "(independent parser, %.1f x %.1f mm)"
                 % (bb[2] - bb[0], bb[3] - bb[1]), fontsize=13)
    fig.tight_layout()
    fig.savefig(out, dpi=115, facecolor="white")
    print("wrote", out)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
