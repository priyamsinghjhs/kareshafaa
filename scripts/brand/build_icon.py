"""KareShafaa badge, hand-built on a 512 grid. Stethoscope = K stem, heart (with brain) = K junction,
syringe = upper arm, capsule = lower arm. One stroke width throughout (18 full / 30 small)."""
NAVY, MINT, WHITE = "#0A2B5E", "#4FD1A1", "#FFFFFF"
HEART = "M262 334 C214 300 194 272 198 244 C202 214 240 202 262 230 C284 202 322 214 326 244 C330 272 310 300 262 334 Z"


def mark(fg=WHITE, accent=MINT, small=False, cut=NAVY):
    sw = 30 if small else 18
    S = f'stroke="{fg}" stroke-width="{sw}" stroke-linecap="round" stroke-linejoin="round" fill="none"'
    o = []
    # stethoscope: binaural loop is the K stem; the tube drops to the chest piece at bottom-left
    if small:
        o.append(f'<path {S} d="M210 262 V132 A30 30 0 0 0 150 132 V362 C150 392 140 404 122 406"/>')
        o.append(f'<circle {S} cx="98" cy="398" r="24"/>')
    else:
        o.append(f'<path {S} d="M206 262 V128 A28 28 0 0 0 150 128 V362 C150 394 140 406 122 408"/>')
        o.append(f'<circle {S} cx="100" cy="400" r="22"/>')
        o.append(f'<circle cx="100" cy="400" r="7" fill="{fg}"/>')
    # arms first, so the heart outline sits on top of their ends
    o.append(f'<path {S} d="M318 222 L350 190"/>')   # needle up to the syringe
    o.append(f'<path {S} d="M316 324 L346 352"/>')   # lower arm down to the capsule
    # syringe (needle side towards the heart)
    o.append('<g transform="translate(384 156) rotate(-45)">')
    if not small:
        o.append(f'<rect x="-32" y="-8" width="30" height="16" rx="2" fill="{accent}"/>')
        o.append(f'<rect x="-36" y="-17" width="66" height="34" rx="6" {S}/>')
        o.append(f'<path {S} d="M30 -25 V25 M30 0 H54 M54 -15 V15"/>')
    else:
        o.append(f'<rect x="-36" y="-20" width="64" height="40" rx="8" fill="{accent}" stroke="{fg}" stroke-width="{sw}" stroke-linejoin="round"/>')
        o.append(f'<path {S} d="M28 0 H50 M50 -16 V16"/>')
    o.append('</g>')
    # capsule, mint half outward
    o.append('<g transform="translate(394 400) rotate(45)">')
    o.append(f'<path d="M0 -31 H36 A31 31 0 0 1 36 31 H0 Z" fill="{accent}"/>')
    o.append(f'<rect x="-67" y="-31" width="134" height="62" rx="31" {S}/>')
    o.append(f'<path {S} d="M0 -31 V31"/>')
    o.append('</g>')
    # heart + brain, enlarged about its centre and moved right of the stem
    o.append('<g transform="translate(286 270) scale(1.2) translate(-262 -270)">')
    o.append(f'<path d="{HEART}" fill="{cut}" stroke="{fg}" stroke-width="{sw/1.2*(0.8 if small else 1)}" stroke-linejoin="round"/>' if cut else
             f'<path d="{HEART}" fill="none" stroke="{fg}" stroke-width="{sw/1.2}" stroke-linejoin="round"/>')
    lw = (sw / 1.2) * 0.5
    L = "M262 238 C250 228 230 234 230 250 C220 256 220 274 232 278 C234 290 250 294 262 286 Z"
    R = "M262 238 C274 228 294 234 294 250 C304 256 304 274 292 278 C290 290 274 294 262 286 Z"
    if small:  # solid halves read better at 16-32 px
        o.append(f'<path d="M258 236 C248 228 228 234 228 250 C218 256 218 276 232 280 C234 292 250 296 258 288 Z" fill="{fg}"/>')
        o.append(f'<path d="M266 236 C276 228 296 234 296 250 C306 256 306 276 292 280 C290 292 274 296 266 288 Z" fill="{accent}"/>')
    else:
        o.append(f'<path d="{L}" fill="none" stroke="{fg}" stroke-width="{lw}" stroke-linejoin="round"/>')
        o.append(f'<path d="{R}" fill="{accent}" stroke="{accent}" stroke-width="{lw}" stroke-linejoin="round"/>')
        o.append(f'<path d="M262 238 V286" stroke="{fg}" stroke-width="{lw}" stroke-linecap="round"/>')
        o.append(f'<path d="M262 252 C254 247 244 249 240 256 M262 270 C254 266 242 268 238 276" fill="none" stroke="{fg}" stroke-width="{lw*0.8}" stroke-linecap="round"/>')
        if cut:
            o.append(f'<path d="M262 260 C270 255 280 257 286 264 M262 278 C268 275 276 277 280 283" fill="none" stroke="{cut}" stroke-width="{lw*0.8}" stroke-linecap="round"/>')
    o.append('</g>')
    return "\n    ".join(o)


def svg(inner, w, h, vb=None, title="KareShafaa"):
    vb = vb or f"0 0 {w} {h}"
    return f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{vb}" width="{w}" height="{h}" role="img" aria-label="{title}">\n  <title>{title}</title>\n  {inner}\n</svg>\n'


def badge(bg=NAVY, fg=WHITE, accent=MINT, small=False, rx=112, scale=1.0, cut=None):
    """Badge on the 512 grid; scale < 1 shrinks the artwork about the centre (maskable safe zone)."""
    cut = cut if cut is not None else bg
    back = f'<rect width="512" height="512" rx="{rx}" fill="{bg}"/>' if bg else ""
    art = mark(fg, accent, small, cut)
    # optical centring: artwork bbox is x 78-456, y 98-458
    t = f'translate(256 256) scale({scale}) translate({-(78+456)/2} {-(98+458)/2})'
    return back + f'\n  <g transform="{t}">\n    {art}\n  </g>'


if __name__ == "__main__":
    open("icon.svg", "w").write(svg(badge(scale=0.92), 512, 512))
    open("icon-small.svg", "w").write(svg(badge(small=True, scale=0.94), 512, 512))
