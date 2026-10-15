"""The standing gate: no pixels, no jitter, on any matte.

Four numbers per clip, measured on the DELIVERED frames, never on an intermediate:

  HARDNESS   the largest alpha step between two neighbouring pixels, anywhere inside the
             picture. A stair stepped or aliased edge jumps 0 -> 1 in one pixel and reads
             as 1.000 here. The frame cut itself is excluded, because an element that runs
             off the bottom of frame is cut by the frame, not by the matte.

  EDGE       the width of the partial band, partial px / perimeter px, in thousandths of
             frame width so two rasters can be compared.

  PICTURE    the steepest luminance step the element itself puts on the plate, measured on
             the premultiplied picture inside the silhouette, across and down. A matte can
             be perfectly soft and the element still read as pasted on, because it is the
             PICTURE that carries the hard rectangles when the element is too bright for
             the plate. RGBA frames only: a bare matte carries no picture.

  JITTER     two parts. POPS: a piece of matte that overlaps nothing in EITHER neighbouring
             frame, each neighbour grown by the element's own travel that frame, is a speck
             that flashes for one frame. WOBBLE: area and centroid are fitted with a moving
             quadratic over 5 frames and what will not fit is the wobble: a boiling matte
             cannot be smooth over 5 frames, a real move can.

Measure a reference already approved on the same plate the same way, and compare. The
gate: no aliased edge, no pops, and PICTURE no steeper than the reference's.

Usage, on RGBA or bare matte frames, the glob quoted so the shell leaves it alone:
  ~/.venvs/post/bin/python matte_gate.py 'FRAMES_*.png' [Y0 Y1] [LABEL]
"""
import glob
import math
import os
import sys

import cv2
import numpy as np

PRESENT = 0.02      # alpha above this is matte: the low end of the partial band
SOLID = 0.5         # the silhouette
BODY_PX = 200       # less silhouette than this and a frame has no element to measure
PICTURE_FRAMES = 8  # frames sampled evenly across the live ones for PICTURE
USAGE = "usage: matte_gate.py 'FRAMES_*.png' [Y0 Y1] [LABEL]   (RGBA or bare matte frames; Y0 Y1 limit the rows)"


def _unit(im):
    if im.dtype == np.uint8:
        return im.astype(np.float64) / 255.
    if im.dtype == np.uint16:
        return im.astype(np.float64) / 65535.
    return im.astype(np.float64)


def load(path):
    """RGBA, or a bare matte (grey, or grey written as RGB). Colour with no alpha is refused."""
    im = cv2.imread(path, cv2.IMREAD_UNCHANGED)
    if im is None:
        raise SystemExit(f'matte_gate: cannot read {path}')
    if im.ndim == 3 and im.shape[2] == 3:
        if not ((im[..., 0] == im[..., 1]).all() and (im[..., 1] == im[..., 2]).all()):
            raise SystemExit(f'matte_gate: {path} is colour with no alpha. '
                             'Give RGBA frames, or the matte itself.')
        im = im[..., 0]
    elif im.ndim == 3 and im.shape[2] != 4:
        raise SystemExit(f'matte_gate: {path} has {im.shape[2]} channels. '
                         'Give RGBA frames, or the matte itself.')
    return im


def alpha_of(im):
    return _unit(im if im.ndim == 2 else im[..., 3])


def premultiplied_luma(im):
    """Luma of the premultiplied picture (Rec.709 weights, OpenCV's BGR); None for a bare matte."""
    if im.ndim == 2:
        return None
    a = _unit(im[..., 3])
    return (_unit(im[..., :3]) * a[..., None]) @ np.array([0.0722, 0.7152, 0.2126])


def _pieces(band):
    """The frame as the pop test sees it: matte mask, its pieces, and where the element is."""
    present = (band > PRESENT).astype(np.uint8)
    n, lab, stats, cents = cv2.connectedComponentsWithStats(present, connectivity=8)
    # The element is its largest piece, so a speck cannot drag the travel estimate.
    centre = tuple(cents[1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))]) if n > 1 else None
    return dict(present=present, n=n, lab=lab, stats=stats, centre=centre, alpha=band)


def _travel(c0, c1):
    if c0 is None or c1 is None:
        return 0.0
    return math.hypot(c1[0] - c0[0], c1[1] - c0[1])


def _reach(present, travel):
    """A neighbour's matte grown by the element's travel between the two frames, plus slack.

    A distance transform rather than a dilation, so a long travel costs no more than a short one."""
    dist = cv2.distanceTransform(1 - present, cv2.DIST_L2, cv2.DIST_MASK_PRECISE)
    return dist <= travel + 1.5


def pops(prev, cur, nxt):
    """Pieces of cur's matte that overlap nothing in either neighbour: [(px, peak alpha)]."""
    if cur['n'] <= 1:
        return []
    near = (_reach(prev['present'], _travel(prev['centre'], cur['centre']))
            | _reach(nxt['present'], _travel(cur['centre'], nxt['centre'])))
    seen = set(np.unique(cur['lab'][near]).tolist())
    out = []
    for k in range(1, cur['n']):
        if k in seen:
            continue
        x, y, w, h, px = (int(v) for v in cur['stats'][k])
        own = cur['lab'][y:y + h, x:x + w] == k
        out.append((px, float(cur['alpha'][y:y + h, x:x + w][own].max())))
    return out


def picture_hardness(files, y0=None, y1=None):
    """Steepest luma step inside the silhouette, on the premultiplied picture, and its bright end.

    None when the frames are a bare matte."""
    steps, bright = [], []
    for f in files:
        im = load(f)
        L = premultiplied_luma(im)
        if L is None:
            return None
        a = alpha_of(im)
        rows = slice(y0 or 0, None if y1 is None else y1 + 1)
        a, L = a[rows], L[rows]
        m = a > SOLID
        if m.sum() < BODY_PX:
            continue
        across = np.abs(np.diff(L, axis=1))[m[:, 1:] & m[:, :-1]]
        down = np.abs(np.diff(L, axis=0))[m[1:] & m[:-1]]
        steps.append(np.percentile(np.concatenate([across, down]), 99.99))
        bright.append(np.percentile(L[m], 99))
    if not steps:
        return None
    return float(np.mean(steps)), float(np.mean(bright))


def _wobble(v):
    r = []
    for k in range(2, len(v) - 2):
        w = v[k - 2:k + 3]
        p = np.polyfit(np.arange(-2, 3), w, 2)
        r.append(w[2] - np.polyval(p, 0))
    return np.array(r) if r else np.zeros(1)


def measure(pattern, y0=None, y1=None, label=''):
    files = sorted(glob.glob(pattern))
    if not files:
        raise SystemExit(f'matte_gate: nothing matches {pattern}')
    W = None
    hard, edge, area, cx, cy, live = [], [], [], [], [], []
    window, popped, judged = [], [], 0
    for i, f in enumerate(files):
        im = load(f)
        a = alpha_of(im)
        del im
        H, W = a.shape
        band = a[(y0 or 0):(H if y1 is None else y1 + 1)]
        window.append(_pieces(band))
        if len(window) == 3:
            judged += 1
            popped += [(files[i - 1], px, peak) for px, peak in pops(*window)]
            window.pop(0)
        if band.max() < PRESENT:
            continue
        inner = band[1:-1] if band.shape[0] > 2 else band
        hs = np.abs(np.diff(inner, axis=1)).max()
        vs = np.abs(np.diff(inner, axis=0)).max() if inner.shape[0] > 1 else 0.0
        m = (band > SOLID).astype(np.uint8)
        if m.sum() < BODY_PX:
            continue
        per = cv2.morphologyEx(m, cv2.MORPH_GRADIENT, np.ones((3, 3), np.uint8)).sum()
        part = ((band > PRESENT) & (band < 0.98)).sum()
        ys, xs = np.nonzero(m)
        hard.append(max(hs, vs))
        edge.append(part / max(per, 1) * 1000.0 / W)
        area.append(m.sum())
        cx.append(xs.mean())
        cy.append(ys.mean())
        live.append(f)

    print(f'{label}  {len(live)} live frames of {len(files)}, {W} px wide')
    pop_line = (f'   JITTER    pieces that pop for one frame  {len(popped)}'
                f'   (judged {judged} frames)')
    if popped:
        worst = max(popped, key=lambda p: p[1])
        pop_line += (f'   largest {worst[1]} px, peak alpha {max(p[2] for p in popped):.2f},'
                     f' first in {os.path.basename(popped[0][0])}')
    if not live:
        print(f'   no frame carries {BODY_PX} px of silhouette, so only the pops are measured')
        print(pop_line)
        return dict(live=0, pops=len(popped), popped=popped, judged=judged,
                    hard=None, edge=None, pic=None, bright=None, jx=None, jy=None, ja=None)

    hard, edge = np.array(hard), np.array(edge)
    area, cx, cy = np.array(area, float), np.array(cx), np.array(cy)
    wa, wx, wy = _wobble(area), _wobble(cx), _wobble(cy)
    print(f'   HARDNESS  worst single pixel alpha step  {hard.max():.3f}   (aliased edge = 1.000)')
    print(f'   EDGE      partial band  {edge.mean():.2f} thousandths of frame width'
          f'   (min {edge.min():.2f}, max {edge.max():.2f})')
    picks = sorted({live[int(j)] for j in
                    np.linspace(0, len(live) - 1, min(len(live), PICTURE_FRAMES)).round()})
    ph = picture_hardness(picks, y0, y1)
    if ph is None:
        print('   PICTURE   n/a: a bare matte carries no picture')
    else:
        print(f'   PICTURE   steepest luma step inside the silhouette  {ph[0]:.5f} per px'
              f'   bright end p99 {ph[1]:.4f}')
    print(pop_line)
    print(f'             centroid wobble rms  x {wx.std():.3f} px   y {wy.std():.3f} px'
          f'   worst {max(np.abs(wx).max(), np.abs(wy).max()):.2f} px')
    print(f'             area wobble rms  {100*wa.std()/area.mean():.3f}% of area'
          f'   worst {100*np.abs(wa).max()/area.mean():.3f}%')
    return dict(live=len(live), pops=len(popped), popped=popped, judged=judged,
                hard=float(hard.max()), edge=float(edge.mean()),
                pic=None if ph is None else ph[0], bright=None if ph is None else ph[1],
                jx=float(wx.std()), jy=float(wy.std()), ja=float(100*wa.std()/area.mean()))


def main(argv):
    if len(argv) < 2 or len(argv) == 3:
        print(USAGE)
        return 2
    y0 = int(argv[2]) if len(argv) > 3 else None
    y1 = int(argv[3]) if len(argv) > 3 else None
    measure(argv[1], y0, y1, argv[4] if len(argv) > 4 else argv[1])
    return 0


if __name__ == '__main__':
    raise SystemExit(main(sys.argv))
