#!/usr/bin/env python3
"""Grid maze router for the EMG rev E board.

Run with KiCad's bundled interpreter:

    /Applications/KiCad/KiCad.app/Contents/Frameworks/Python.framework/\
Versions/3.9/bin/python3 route_board.py

Why a hand-rolled router: there is no Java runtime, no Docker and no
freerouting.jar on this machine, so the usual Specctra round-trip is not
available. The board is through-hole and only about a third occupied, so a
straightforward A* maze router over a fine grid does the job and leaves the
routing order under our control - which matters here, because the analog
nets should be routed first, on the shortest paths, before the digital and
servo nets get to consume space.

Strategy:
  * GND and SERVO_GND carry no traces at all. They are poured: GND over the
    whole board, SERVO_GND over the bottom strip at a higher zone priority
    so it wins there. That keeps the servo return current inside its own
    copper and out of the analog ground.
  * Everything else is routed as traces, preferring the bottom layer since
    every part is through-hole and reachable from below.
  * Nets are routed shortest-first within priority tiers, so the short
    sensitive analog hops claim their direct paths before anything else.
"""
import heapq
import math
import os
import sys

import pcbnew

HERE = os.path.dirname(os.path.abspath(__file__))
BOARD = os.path.join(HERE, "emg.kicad_pcb")

BOARD_W, BOARD_H = 100.0, 100.0
GRID = 0.25                 # mm per cell
CLEAR = 0.25                # copper-to-copper clearance we design to
W_SIGNAL = 0.3              # default trace width
W_POWER = 0.8               # +-9 V and +3V3 rails
W_SERVO = 1.5               # +5VSRV: five servos, so amps not milliamps
VIA_DIA, VIA_DRILL = 0.8, 0.4
VIA_COST = 12               # in grid steps, discourages needless layer flips

# poured, never routed
POURED = {"GND", "SERVO_GND"}

# routed first: the low-level analog path where trace length and coupling
# actually matter. Order within a tier is shortest-connection-first.
TIER = {
    "EL_P": 0, "EL_N": 0, "DRL_REF": 0, "DRL_S": 0, "DRL_OUT": 0,
    "INA_OUT": 0, "Net-(U1-+)": 0, "Net-(U1--)": 0,
    "Net-(RG-Pad1)": 0, "Net-(RG-Pad2)": 0,
    "Net-(U2A-+)": 1, "Net-(U2A--)": 1, "Net-(R5-Pad2)": 1,
    "LP_OUT": 1, "BUF": 1, "ADC_IN": 1, "VMID": 1, "Net-(U2B-+)": 1,
    # The five servo signals are the longest runs on the board: they cross
    # from the Pico's top-left GPIO down to the header row along the bottom,
    # through the narrow channel beside the Pico. Once the chamfered corners
    # were correctly blocked there was no longer room for all five if the
    # general nets got there first, so they route immediately after the
    # analog chain.
    "SRV1": 2, "SRV2": 2, "SRV3": 2, "SRV4": 2, "SRV5": 2,
}

NX = int(BOARD_W / GRID) + 1
NY = int(BOARD_H / GRID) + 1
LAYERS = (pcbnew.B_Cu, pcbnew.F_Cu)   # index 0 = bottom (preferred), 1 = top


def mm(v):
    return pcbnew.FromMM(v)


def vec(x, y):
    return pcbnew.VECTOR2I(mm(x), mm(y))


def to_cell(x, y):
    return int(round(x / GRID)), int(round(y / GRID))


def to_mm(cx, cy):
    return cx * GRID, cy * GRID


# A via drill must stay this far from any other drilled hole. JLCPCB wants
# 0.5 mm hole-to-hole; add the two hole radii plus margin and forbid vias
# within this radius of a pad centre.
VIA_KEEPOUT = 1.5


class Grid:
    """occupancy[layer][y][x] -> None free, or a net name string.

    novia[y][x] is True where a layer change would put a via drill too close
    to a pad drill. Without this the router happily dropped vias 0.015 mm
    from a through-hole pad, which DRC rejects.
    """

    def __init__(self):
        self.occ = [[[None] * NX for _ in range(NY)] for _ in LAYERS]
        self.novia = [[False] * NX for _ in range(NY)]

    def block_via(self, x, y, r):
        cx0, cy0 = to_cell(x, y)
        rc = int(math.ceil(r / GRID))
        for cy in range(max(0, cy0 - rc), min(NY - 1, cy0 + rc) + 1):
            for cx in range(max(0, cx0 - rc), min(NX - 1, cx0 + rc) + 1):
                if (cx - cx0) ** 2 + (cy - cy0) ** 2 <= rc * rc:
                    self.novia[cy][cx] = True

    def block_rect(self, layer, x1, y1, x2, y2, net):
        cx1, cy1 = to_cell(x1, y1)
        cx2, cy2 = to_cell(x2, y2)
        for cy in range(max(0, cy1), min(NY - 1, cy2) + 1):
            row = self.occ[layer][cy]
            for cx in range(max(0, cx1), min(NX - 1, cx2) + 1):
                if row[cx] is None or net is None:
                    row[cx] = net

    def free(self, layer, cx, cy, net):
        if not (0 <= cx < NX and 0 <= cy < NY):
            return False
        o = self.occ[layer][cy][cx]
        return o is None or o == net


def build_grid(board):
    g = Grid()
    # board edge: block a border so copper keeps clear of the outline
    edge = CLEAR + W_SIGNAL / 2.0 + 0.3
    for li in range(len(LAYERS)):
        g.block_rect(li, 0, 0, BOARD_W, edge, "#EDGE")
        g.block_rect(li, 0, BOARD_H - edge, BOARD_W, BOARD_H, "#EDGE")
        g.block_rect(li, 0, 0, edge, BOARD_H, "#EDGE")
        g.block_rect(li, BOARD_W - edge, 0, BOARD_W, BOARD_H, "#EDGE")

    # The outline has 5 mm chamfers. A rectangular border does not cover the
    # diagonals, so SRV1 was routed straight across the bottom-left corner
    # cut. Block the four corner triangles explicitly.
    chamfer = 5.0
    for cy in range(NY):
        for cx in range(NX):
            x, y = to_mm(cx, cy)
            near = (
                x + y < chamfer + edge,
                (BOARD_W - x) + y < chamfer + edge,
                x + (BOARD_H - y) < chamfer + edge,
                (BOARD_W - x) + (BOARD_H - y) < chamfer + edge,
            )
            if any(near):
                for li in range(len(LAYERS)):
                    g.occ[li][cy][cx] = "#EDGE"

    pads = []
    for fp in board.GetFootprints():
        for pad in fp.Pads():
            bb = pad.GetBoundingBox()
            x1, y1 = bb.GetX() / 1e6, bb.GetY() / 1e6
            x2 = (bb.GetX() + bb.GetWidth()) / 1e6
            y2 = (bb.GetY() + bb.GetHeight()) / 1e6
            net = pad.GetNetname() or "#NC"
            pos = pad.GetPosition()
            pads.append({
                "ref": fp.GetReference(), "num": pad.GetPadName(), "net": net,
                "x": pos.x / 1e6, "y": pos.y / 1e6,
                "box": (x1, y1, x2, y2),
                "through": pad.GetAttribute() == pcbnew.PAD_ATTRIB_PTH,
            })
    # keep-out around every pad, on both layers (all pads here are through-hole)
    pad_clear = CLEAR + W_SIGNAL / 2.0
    for p in pads:
        x1, y1, x2, y2 = p["box"]
        for li in range(len(LAYERS)):
            g.block_rect(li, x1 - pad_clear, y1 - pad_clear,
                         x2 + pad_clear, y2 + pad_clear, p["net"])
        g.block_via(p["x"], p["y"], VIA_KEEPOUT)
    return g, pads


def astar(g, start, goal, net, width):
    """start/goal are (layer_index, cx, cy). Returns list of nodes or None."""
    pad = int(math.ceil((CLEAR + width / 2.0) / GRID))

    def passable(li, cx, cy):
        for dy in range(-pad, pad + 1):
            for dx in range(-pad, pad + 1):
                if not g.free(li, cx + dx, cy + dy, net):
                    return False
        return True

    def h(n):
        return abs(n[1] - goal[1]) + abs(n[2] - goal[2])

    openq = [(h(start), 0, start, None)]
    came, gs = {}, {start: 0}
    steps = (1, 0), (-1, 0), (0, 1), (0, -1)
    while openq:
        _, cost, node, parent = heapq.heappop(openq)
        if node in came:
            continue
        came[node] = parent
        if (node[1], node[2]) == (goal[1], goal[2]):
            path = []
            while node is not None:
                path.append(node)
                node = came[node]
            return path[::-1]
        li, cx, cy = node
        for dx, dy in steps:
            nb = (li, cx + dx, cy + dy)
            if nb in came or not passable(li, cx + dx, cy + dy):
                continue
            nc = cost + 1
            if gs.get(nb, 1 << 30) > nc:
                gs[nb] = nc
                heapq.heappush(openq, (nc + h(nb), nc, nb, node))
        other = 1 - li
        nb = (other, cx, cy)
        if nb not in came and not g.novia[cy][cx] and passable(other, cx, cy):
            nc = cost + VIA_COST
            if gs.get(nb, 1 << 30) > nc:
                gs[nb] = nc
                heapq.heappush(openq, (nc + h(nb), nc, nb, node))
    return None


def commit(board, path, net_item, width):
    """Turn a node path into tracks + vias, merging colinear runs."""
    tracks, vias = 0, 0
    run = [path[0]]
    for node in path[1:]:
        if node[0] != run[-1][0]:            # layer change -> via
            if len(run) > 1:
                tracks += emit_run(board, run, net_item, width)
            x, y = to_mm(run[-1][1], run[-1][2])
            v = pcbnew.PCB_VIA(board)
            v.SetPosition(vec(x, y))
            v.SetWidth(mm(VIA_DIA))
            v.SetDrill(mm(VIA_DRILL))
            v.SetNet(net_item)
            board.Add(v)
            vias += 1
            run = [node]
        else:
            run.append(node)
    if len(run) > 1:
        tracks += emit_run(board, run, net_item, width)
    return tracks, vias


def emit_run(board, run, net_item, width):
    pts = [run[0]]
    for i in range(1, len(run) - 1):
        ax, ay = run[i - 1][1], run[i - 1][2]
        bx, by = run[i][1], run[i][2]
        cx, cy = run[i + 1][1], run[i + 1][2]
        if (bx - ax, by - ay) != (cx - bx, cy - by):
            pts.append(run[i])
    pts.append(run[-1])
    n = 0
    for a, b in zip(pts, pts[1:]):
        t = pcbnew.PCB_TRACK(board)
        t.SetStart(vec(*to_mm(a[1], a[2])))
        t.SetEnd(vec(*to_mm(b[1], b[2])))
        t.SetWidth(mm(width))
        t.SetLayer(LAYERS[a[0]])
        t.SetNet(net_item)
        board.Add(t)
        n += 1
    return n


def width_for(net):
    if net == "+5VSRV":
        return W_SERVO
    if net in ("+9V", "-9V", "+3V3"):
        return W_POWER
    return W_SIGNAL


def main():
    board = pcbnew.LoadBoard(BOARD)

    # Start from a clean slate so the script is idempotent. Removing tracks
    # and then walking footprints in the same session leaves SWIG holding
    # stale handles (footprints come back as bare SwigPyObject), so drop the
    # tracks, flush to disk, and reload before touching anything else.
    tracks = list(board.GetTracks())
    if tracks:
        for t in tracks:
            board.Remove(t)
        for i in reversed(range(board.GetAreaCount())):
            z = board.GetArea(i)
            if z.GetNetname() == "SERVO_GND":
                board.Remove(z)
        pcbnew.SaveBoard(BOARD, board)
        board = pcbnew.LoadBoard(BOARD)
        print("cleared %d existing tracks/vias before re-routing" % len(tracks))

    g, pads = build_grid(board)

    bynet = {}
    for p in pads:
        if p["net"] in ("#NC", "") or p["net"] in POURED:
            continue
        bynet.setdefault(p["net"], []).append(p)

    # order: tier, then shortest span first
    def span(ps):
        xs = [p["x"] for p in ps]; ys = [p["y"] for p in ps]
        return (max(xs) - min(xs)) + (max(ys) - min(ys))

    # Within a tier, route the LONGEST span first. Routing short nets first
    # is what stranded SRV5 and Net-(U2B-+): by the time the long runs were
    # attempted, the escape corridors beside the Pico had already been taken.
    # Hardest-first is the standard fix.
    order = sorted(bynet, key=lambda n: (TIER.get(n, 3), -span(bynet[n])))

    total_t = total_v = 0
    failed = []
    for net in order:
        ps = bynet[net]
        if len(ps) < 2:
            continue
        width = width_for(net)
        net_item = board.FindNet(net)
        # greedy MST: repeatedly connect the nearest unconnected pad
        connected = [ps[0]]
        remaining = ps[1:]
        while remaining:
            best = None
            for a in connected:
                for b in remaining:
                    d = abs(a["x"] - b["x"]) + abs(a["y"] - b["y"])
                    if best is None or d < best[0]:
                        best = (d, a, b)
            _, a, b = best
            remaining.remove(b)
            sa = (0,) + to_cell(a["x"], a["y"])
            sb = (0,) + to_cell(b["x"], b["y"])
            path = astar(g, sa, sb, net, width)
            if path is None:
                path = astar(g, (1,) + sa[1:], (1,) + sb[1:], net, width)
            if path is None:
                failed.append((net, a["ref"] + "." + a["num"],
                               b["ref"] + "." + b["num"]))
                connected.append(b)
                continue
            t, v = commit(board, path, net_item, width)
            total_t += t; total_v += v
            # reserve the corridor we just used
            keep = int(math.ceil((width / 2.0) / GRID))
            for li, cx, cy in path:
                for dy in range(-keep, keep + 1):
                    for dx in range(-keep, keep + 1):
                        if 0 <= cx + dx < NX and 0 <= cy + dy < NY:
                            if g.occ[li][cy + dy][cx + dx] is None:
                                g.occ[li][cy + dy][cx + dx] = net
            connected.append(b)

    print("routed %d nets: %d track segments, %d vias"
          % (len(order), total_t, total_v))
    if failed:
        print("UNROUTED (%d):" % len(failed))
        for net, a, b in failed:
            print("   %-14s %s <-> %s" % (net, a, b))
    else:
        print("UNROUTED: none")

    # ---- SERVO_GND pour over the bottom strip, at a higher priority than
    #      the board-wide GND pour so it owns that copper.
    sg = board.FindNet("SERVO_GND")
    if sg is not None:
        for layer in (pcbnew.F_Cu, pcbnew.B_Cu):
            z = pcbnew.ZONE(board)
            z.SetLayer(layer)
            z.SetNet(sg)
            o = z.Outline()
            o.NewOutline()
            for x, y in ((6.0, 82.0), (94.0, 82.0), (94.0, 94.0), (6.0, 94.0)):
                o.Append(mm(x), mm(y))
            z.SetLocalClearance(mm(0.3))
            z.SetMinThickness(mm(0.25))
            z.SetAssignedPriority(1)
            z.SetPadConnection(pcbnew.ZONE_CONNECTION_FULL)
            board.Add(z)
        print("added SERVO_GND pours (priority 1) over the servo strip")

    filler = pcbnew.ZONE_FILLER(board)
    filler.Fill(board.Zones())
    print("zones filled")

    pcbnew.SaveBoard(BOARD, board)
    print("saved")


if __name__ == "__main__":
    main()
