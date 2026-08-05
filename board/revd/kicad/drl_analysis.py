"""Driven-right-leg common-mode rejection, solved by nodal analysis.

LTspice on this Mac is GUI-only (the batch binary forks and exits without
writing a .raw or .log) and there is no ngspice, so instead of simulating
we solve the same small-signal network directly. The circuit is linear, so
this is exact rather than approximate - it is the same set of equations
SPICE would assemble.

Topology is taken from ~/Downloads/emg-revd/drl_simulation.cir, which is
the deck the "~19 dB extra CMRR" claim in REV_D_NOTES.md came from.

Question being answered: that deck used a DRL feedback resistor of 10 Meg,
but the shipped schematic has R13 = 3.9 Meg. How much rejection does the
board as drawn actually get?

Unknown node voltages: body, ea, eb, s, drlout, ref.
VMID carries no AC, so it is the AC ground (node 0).
Mains is the 1 V AC stimulus, capacitively coupled to the body.
"""
import numpy as np

# ---- component values, all from the rev D deck / netlist
C_COUPLE = 5e-12      # body-to-mains stray capacitance
R_BDC    = 1e9        # body DC return
R_EA     = 100e3      # skin+electrode, measurement electrode A
R_EB     = 102e3      # ...and B, deliberately mismatched to break symmetry
R_IA     = 1e12       # in-amp input impedance
R_IB     = 1e12
R_SA     = 150e3      # R11 - averaging resistor
R_SB     = 150e3      # R12
R_O      = 390e3      # R14 - body-current limiter into the ref electrode
R_SKIN   = 100e3      # ref electrode contact
C_F      = 47e-12     # C7, across the DRL feedback resistor
A_DC     = 2e5        # op-amp open-loop gain
RP, CP   = 1e3, 1.6e-6   # op-amp dominant pole -> ~99.5 Hz

NODES = ["body", "ea", "eb", "s", "drlout", "ref"]
IDX = {n: i for i, n in enumerate(NODES)}
N = len(NODES)


def solve(f, rf=None):
    """Return the complex body voltage at frequency f.

    rf=None models the passive case: the reference electrode is simply
    returned to VMID through R_O, with no amplifier driving it.
    """
    w = 2 * np.pi * f
    jw = 1j * w
    Y = np.zeros((N, N), dtype=complex)
    I = np.zeros(N, dtype=complex)

    def stamp(a, b, y):
        """Admittance y between nodes a and b; b=None means AC ground."""
        ia = IDX[a]
        if b is None:
            Y[ia, ia] += y
            return
        ib = IDX[b]
        Y[ia, ia] += y
        Y[ib, ib] += y
        Y[ia, ib] -= y
        Y[ib, ia] -= y

    # mains couples in as a current source through C_COUPLE (Norton form:
    # a 1 V source behind C is I = jwC * 1 injected at the body node)
    yc = jw * C_COUPLE
    Y[IDX["body"], IDX["body"]] += yc
    I[IDX["body"]] += yc * 1.0

    stamp("body", None, 1.0 / R_BDC)
    stamp("body", "ea", 1.0 / R_EA)
    stamp("body", "eb", 1.0 / R_EB)
    stamp("ea", None, 1.0 / R_IA)
    stamp("eb", None, 1.0 / R_IB)
    stamp("body", "ref", 1.0 / R_SKIN)

    if rf is None:
        # passive reference: ref returned to VMID through R_O
        stamp("ref", None, 1.0 / R_O)
        # s node is unused; tie it down so the matrix stays non-singular
        stamp("s", None, 1.0)
        stamp("drlout", None, 1.0)
    else:
        stamp("ea", "s", 1.0 / R_SA)
        stamp("eb", "s", 1.0 / R_SB)
        stamp("s", "drlout", 1.0 / rf + jw * C_F)   # R13 with C7 across it
        stamp("ref", "drlout", 1.0 / R_O)
        # op-amp: drlout = -A(f) * V(s), an ideal voltage source, so replace
        # the KCL row at drlout with that constraint
        A = A_DC / (1.0 + jw * RP * CP)
        Y[IDX["drlout"], :] = 0
        I[IDX["drlout"]] = 0
        Y[IDX["drlout"], IDX["drlout"]] = 1.0
        Y[IDX["drlout"], IDX["s"]] = A

    v = np.linalg.solve(Y, I)
    return v


def db(x):
    return 20 * np.log10(abs(x))


print("Driven-right-leg common-mode rejection at the body node")
print("1 V of mains injected through 5 pF; lower |V_body| is better.\n")

f = 50.0
v_passive = solve(f, rf=None)
v_10m = solve(f, rf=10e6)
v_39m = solve(f, rf=3.9e6)

b0 = abs(v_passive[IDX["body"]])
b1 = abs(v_10m[IDX["body"]])
b2 = abs(v_39m[IDX["body"]])

print("  %-28s %12s %12s" % ("configuration", "|V_body|", "vs passive"))
print("  %-28s %12.4e %12s" % ("passive reference (no DRL)", b0, "0.00 dB"))
print("  %-28s %12.4e %9.2f dB" % ("DRL, Rf = 10M  (simulated)", b1, db(b0 / b1)))
print("  %-28s %12.4e %9.2f dB" % ("DRL, Rf = 3.9M (as drawn)", b2, db(b0 / b2)))
print()
print("  penalty for shipping 3.9M instead of 10M: %.2f dB"
      % (db(b0 / b1) - db(b0 / b2)))
print()

# frequency sweep: peaking in the loop would show up as a rise here
print("Frequency response of the rejection (dB improvement over passive):")
print("  %8s %12s %12s" % ("f (Hz)", "Rf=10M", "Rf=3.9M"))
for fr in (10, 20, 50, 100, 150, 200, 500, 1000):
    p = abs(solve(fr, rf=None)[IDX["body"]])
    a = abs(solve(fr, rf=10e6)[IDX["body"]])
    c = abs(solve(fr, rf=3.9e6)[IDX["body"]])
    print("  %8.0f %10.2f dB %10.2f dB" % (fr, db(p / a), db(p / c)))
print()

# The DRL is an inverting summing amp: for a body common-mode Vcm it drives
# the reference electrode with -Rf*(1/R_SA + 1/R_SB)*Vcm. That correction
# gain is what buys rejection - and it is also what limits headroom, because
# U2C runs on +3V3 with its non-inverting input at VMID = 1.65 V, so it can
# only swing about 1.65 V before it clips and the loop stops correcting.
HEADROOM = 1.65
print("Correction gain and the common-mode it can handle before clipping:")
print("  %-8s %14s %18s %14s" % ("R13", "corr. gain", "rejection @50Hz", "max Vcm"))
for name, rf, rej in (("10M", 10e6, db(b0 / b1)), ("3.9M", 3.9e6, db(b0 / b2))):
    gain = rf * (1.0 / R_SA + 1.0 / R_SB)
    print("  %-8s %11.1f x %13.2f dB %11.1f mV"
          % (name, gain, rej, 1e3 * HEADROOM / gain))
print()
print("  Mains common-mode on an ungrounded body is routinely tens of mV,")
print("  so the headroom column is not academic: once U2C clips, the DRL")
print("  stops rejecting anything at all.")
