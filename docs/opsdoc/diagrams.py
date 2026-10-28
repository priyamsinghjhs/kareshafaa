"""Flow and escalation diagrams (matplotlib, Carlito, KareShafaa navy/mint) -> build/diag."""
from pathlib import Path
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch, Circle, FancyArrowPatch
from matplotlib import font_manager as fm

for f in fm.findSystemFonts():
    if "Carlito" in f: fm.fontManager.addfont(f)
plt.rcParams["font.family"] = "Carlito"
NAVY, MINT, GREY, DARK = "#0A2B5E", "#4FD1A1", "#5B6778", "#1F2937"
TINT, MINT_T, AMBER, AMBER_T, RED = "#F3F6FA", "#EAF8F2", "#B7791F", "#FFF7E6", "#B42318"
OUT = Path(__file__).parent / "build" / "diag"; OUT.mkdir(parents=True, exist_ok=True)

BW, BH, DX = 3.05, 1.42, 3.55


def box(ax, x, y, num, title, owner, fill="white", edge=NAVY):
    ax.add_patch(FancyBboxPatch((x, y), BW, BH, boxstyle="round,pad=0.02,rounding_size=0.14", fc=fill, ec=edge, lw=1.4, zorder=3))
    if num is not None:
        ax.add_patch(Circle((x + 0.27, y + BH - 0.27), 0.2, fc=NAVY, ec="none", zorder=4))
        ax.text(x + 0.27, y + BH - 0.27, str(num), ha="center", va="center", color="white", fontsize=9.5, fontweight="bold", zorder=5)
    ax.text(x + (0.56 if num is not None else 0.14), y + BH - 0.27, title, ha="left", va="center", fontsize=10.2, fontweight="bold", color=NAVY, zorder=5)
    ax.text(x + 0.14, y + 0.5, owner, ha="left", va="center", fontsize=8.4, color=DARK, zorder=5, linespacing=1.15)
    return (x, y)


def arrow(ax, a, b, color=NAVY, style="-|>", rad=0.0, lw=1.3, ls="-"):
    ax.add_patch(FancyArrowPatch(a, b, arrowstyle=style, mutation_scale=12, color=color, lw=lw, ls=ls,
                                 connectionstyle=f"arc3,rad={rad}", zorder=2, shrinkA=2, shrinkB=2))


def flow():
    fig, ax = plt.subplots(figsize=(17.5, 15.2)); ax.set_xlim(-1.6, 17.9); ax.set_ylim(0.05, 15.4); ax.axis("off")
    X0H = -0.9 + BW / 2 + 0.3
    rows = {"A": 12.4, "B": 9.0, "C": 5.6, "D": 2.2}
    names = {"A": "A · Result and triage", "B": "B · Patient contact and understanding", "C": "C · Diagnosis and referral", "D": "D · Care and closure"}
    for k, y in rows.items():
        ax.add_patch(FancyBboxPatch((-1.45, y - 0.55), 19.2, BH + 1.45, boxstyle="round,pad=0,rounding_size=0.2", fc=TINT if k in "AC" else "white", ec="#C9D3E0", lw=0.8, zorder=0))
        ax.text(17.55, y + BH + 0.62, names[k], fontsize=11.5, fontweight="bold", color=NAVY, va="center", ha="right")
    X = [-0.9 + i * DX for i in range(5)]
    A, B, C, D = rows["A"], rows["B"], rows["C"], rows["D"]
    box(ax, X[0], A, 1, "Result arrives", "HL7 v2 / FHIR R4 or Results inbox\nMatched to patient · ACK AA/AE\nOwner: system; coordinator if no match")
    box(ax, X[1], A, 2, "Rules engine", "Tier T0–T3, pathway, target date,\nrule ID; case + accountable owner\nOwner: rules engine (no ML)")
    box(ax, X[2], A, 3, "Clinical review", "Only if flagged (unsafe, conflicting,\namended, out of range …)\nOwner: clinical lead · GP · specialist")
    box(ax, X[3], A, 4, "Triage", "T2/T3: automatic by rule\nT0/T1: clinician confirms\nSLA T0 1 h · T1 4 h · T2 1 wd · T3 2 wd")
    box(ax, X[0], B, 5, "Notify patient", "Channel ladder: app → SMS →\nWhatsApp → phone-call task\nOwner: care coordinator (sends)")
    box(ax, X[1], B, 6, "Navigator call", "If no digital channel, interpreter\nneeded or teach-back not passed\nOwner: care coordinator")
    box(ax, X[2], B, 7, "Teach-back", "3 questions, all correct = pass\nPatient in app, or navigator by phone\nPass → Acknowledged")
    box(ax, X[3] + 0.0, B, None, "Exceptions", "Unreachable: ≥ 2 failed calls\nDeclined → clinician review\nConsent withdrawn → review", fill=AMBER_T, edge=AMBER)
    box(ax, X[0], C, 8, "Confirmatory test", "Ordered where the pathway\nneeds confirmation\nOwner: GP")
    box(ax, X[1], C, 9, "Confirmatory result", "Positive → consultation\nNegative FIT/breast → may close\nOwner: GP · specialist")
    box(ax, X[2], C, 10, "Consultation", "Notes, medicines, plan;\nrefer or manage in primary care\nOwner: GP")
    box(ax, X[3], C, 11, "Referral sent", "Consent to share checked\nTarget provider and service\nOwner: GP (or coordinator)")
    box(ax, X[4], C, 12, "Referral decision", "Accept, or reject with a reason\ncode → re-route by referrer\nOwner: receiving specialist")
    box(ax, X[0], D, 13, "Appointment booked", "Booking inside the pathway target\n(reason needed if beyond)\nOwner: specialist · coordinator")
    box(ax, X[1], D, 14, "Attended / no-show", "No-show: barrier code,\nrebooking message, rebook\nOwner: specialist")
    box(ax, X[2], D, 15, "Care plan", "Treatment or surveillance\nplan recorded\nOwner: specialist · GP")
    box(ax, X[3], D, 16, "Closure guard", "Evidence of the right type, or\nauthorised reason + note (≥ 10)\nOnly a human can close")
    box(ax, X[4], D, None, "Closed", "Re-open: clinical lead only\n(with a reason)\nAll steps in the audit log", fill=MINT_T, edge="#2F9E77")
    # main arrows
    for row in (A, C, D):
        n = 4 if row != C else 5
        if row == D: n = 5
        for i in range(n - 1):
            if row == A and i >= 3: break
            arrow(ax, (X[i] + BW, row + BH / 2), (X[i + 1], row + BH / 2))
    arrow(ax, (X[0] + BW, B + BH / 2), (X[1], B + BH / 2)); arrow(ax, (X[1] + BW, B + BH / 2), (X[2], B + BH / 2))
    arrow(ax, (X[2] + BW, B + BH / 2), (X[3], B + BH / 2), color=AMBER, ls="--")
    # row changes (elbow: down from the last box of a row into the first box of the next)
    def drop(x_from, y_from, y_to):
        mid = y_from - 0.62
        ax.plot([x_from, x_from], [y_from, mid], color=NAVY, lw=1.3, zorder=2)
        ax.plot([x_from, X[0] + BW / 2], [mid, mid], color=NAVY, lw=1.3, zorder=2)
        arrow(ax, (X[0] + BW / 2, mid), (X[0] + BW / 2, y_to + BH))
    drop(X[3] + BW / 2, A, B)
    # B -> C: from Teach-back (7) down to confirmatory (8), plus arc 7 -> 10
    mid = B - 0.62
    ax.plot([X[2] + BW / 2, X[2] + BW / 2], [B, mid], color=NAVY, lw=1.3); ax.plot([X[2] + BW / 2, X[0] + BW / 2], [mid, mid], color=NAVY, lw=1.3)
    arrow(ax, (X[0] + BW / 2, mid), (X[0] + BW / 2, C + BH))
    arrow(ax, (X[2] + BW / 2 + 0.35, mid), (X[2] + BW / 2 + 0.35, C + BH), color=GREY, ls="--")
    ax.text(X[2] + BW / 2 + 0.5, mid - 0.2, "no confirmatory\ntest needed", fontsize=8.2, color=GREY, style="italic", va="top")
    # reject loop 12 -> 11
    arrow(ax, (X[4] + BW / 2, C + BH), (X[3] + BW / 2, C + BH), rad=0.38, color=AMBER, ls="--")
    ax.text(X[3] + BW / 2 - 0.1, C + BH + 0.5, "rejected → re-route", fontsize=8.4, color=AMBER, style="italic", ha="right")
    # C -> D
    mid = C - 1.05
    ax.plot([X[4] + BW / 2 + 0.6, X[4] + BW / 2 + 0.6], [C, mid], color=NAVY, lw=1.3); ax.plot([X[4] + BW / 2 + 0.6, X[0] + BW / 2], [mid, mid], color=NAVY, lw=1.3)
    arrow(ax, (X[0] + BW / 2, mid), (X[0] + BW / 2, D + BH))
    ax.text(X[2] + BW / 2, mid + 0.14, "accepted", fontsize=8.4, color=GREY, style="italic", ha="center")
    # no-show loop 14 -> 13
    arrow(ax, (X[1] + BW / 2, D + BH), (X[0] + BW / 2 + 0.8, D + BH), rad=0.45, color=AMBER, ls="--")
    ax.text(X[0] + BW + 0.65, D + BH + 0.72, "no-show → rebook", fontsize=8.4, color=AMBER, style="italic", ha="center")
    # closure from confirmatory negative (9 -> 16): annotation only
    ax.text(X[1] + BW / 2, C - 0.32, "negative (FIT, breast) → closure guard", fontsize=8.0, color=GREY, style="italic", ha="center")
    # band 17
    ax.add_patch(FancyBboxPatch((-1.45, 0.2), 19.2, 1.2, boxstyle="round,pad=0,rounding_size=0.2", fc=NAVY, ec=NAVY, zorder=1))
    ax.add_patch(Circle((-0.95, 0.8), 0.2, fc=MINT, ec="none", zorder=4)); ax.text(-0.95, 0.8, "17", ha="center", va="center", fontsize=9, fontweight="bold", color=NAVY, zorder=5)
    ax.text(-0.6, 0.98, "Throughout: every step has an owner, a step deadline (SLA) and the pathway target. The deadline check runs every 5 minutes.",
            fontsize=9.6, color="white", va="center", fontweight="bold")
    ax.text(-0.6, 0.57, "L1 50 % → owner reminder  ·  L2 100 % → supervisor / programme coordinator  ·  L3 150 % → clinical lead  ·  L4 200 % (T0/T1) → medical director",
            fontsize=9.2, color="white", va="center")
    fig.savefig(OUT / "flow.png", dpi=170, bbox_inches="tight", facecolor="white"); plt.close(fig)


def escalation():
    fig, ax = plt.subplots(figsize=(16, 4.6)); ax.set_xlim(0, 16); ax.set_ylim(0, 4.6); ax.axis("off")
    X0, X1 = 0.4, 15.6; L = X1 - X0
    def xp(f): return X0 + L * f / 2.0
    for f0, f1, col in ((0, 0.5, "#D7F3E7"), (0.5, 1.0, "#FCE9C2"), (1.0, 2.0, "#F8D3CF")):
        ax.add_patch(FancyBboxPatch((xp(f0), 3.35), xp(f1) - xp(f0), 0.32, boxstyle="square,pad=0", fc=col, ec="none"))
    for f, t in ((0, "Step starts"), (0.5, "50 % (default)"), (1.0, "100 % = breach"), (1.5, "150 %"), (2.0, "200 %")):
        ax.plot([xp(f), xp(f)], [3.25, 3.77], color=NAVY, lw=1.2)
        ax.text(xp(f), 4.0, t, ha="center" if 0 < f < 2 else ("left" if f == 0 else "right"), fontsize=10.5, color=NAVY, fontweight="bold")
    lv = [(0.5, "L1 · Reminder", "To the accountable owner. Patient-facing\nsteps also send a patient reminder\n(only 08:00–21:00 UAE time)."),
          (1.0, "L2 · Step breached", "To the owner's supervisor or the\nprogramme coordinator supervisor.\nT0 goes straight to L3."),
          (1.5, "L3 · Clinical lead", "To the programme clinical lead.\nAcknowledged with a note\n(at least 3 characters)."),
          (2.0, "L4 · Medical director", "T0 and T1 only. Medical director or\nduty clinician (a clinical lead with\nno programme).")]
    BWE = 3.6
    for i, (f, t, d) in enumerate(lv):
        bx = X0 + i * (L - BWE) / 3
        ax.add_patch(FancyBboxPatch((bx, 0.15), BWE, 2.3, boxstyle="round,pad=0.02,rounding_size=0.14", fc="white", ec=NAVY, lw=1.3))
        ax.text(bx + 0.18, 2.1, t, fontsize=11.5, fontweight="bold", color=NAVY, va="center")
        ax.text(bx + 0.18, 1.08, d, fontsize=9.6, color=DARK, va="center", linespacing=1.3)
        ax.plot([xp(f), bx + BWE / 2], [3.25, 2.47], color=NAVY, lw=1, ls=":")
    fig.savefig(OUT / "escalation.png", dpi=170, bbox_inches="tight", facecolor="white"); plt.close(fig)


flow(); escalation(); print("diagrams ok")
