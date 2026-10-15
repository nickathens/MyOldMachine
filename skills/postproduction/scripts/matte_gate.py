"""The standing gate: no pixels, no jitter, on any matte.

Three numbers per clip, all measured on the DELIVERED alpha:

  HARDNESS   the largest alpha step between two neighbouring pixels, anywhere inside the
             picture. A stair stepped or aliased edge jumps 0 -> 1 in one pixel and reads
             as 1.00 here. The frame cut itself is excluded, because an element that runs
             off the bottom of frame is cut by the frame, not by the matte.

  EDGE       the width of the partial band, partial px / perimeter px, in thousandths of
             frame width so two rasters can be compared.

  PICTURE    the steepest luminance step the element itself puts on the plate, measured on
             the premultiplied picture inside the silhouette. A matte can be perfectly
             soft and the element still read as pasted on, because it is the PICTURE that
             carries the hard rectangles when the element is too bright for the plate.

  JITTER     the high frequency wobble of the silhouette. Area and centroid are fitted
             with a moving quadratic over 5 frames and what will not fit is the wobble:
             a boiling matte cannot be smooth over 5 frames, a real move can.
"""
import cv2, numpy as np, sys, glob, os


def read_alpha(p):
    im = cv2.imread(p, cv2.IMREAD_UNCHANGED)
    if im.ndim == 3 and im.shape[2] == 4:
        im = im[..., 3]
    return im.astype(np.float64)/(65535. if im.dtype == np.uint16 else 255.)


def picture_hardness(pattern, frames=(45, 60, 75, 90)):
    """Steepest luma step inside the silhouette, on the premultiplied picture."""
    st, br = [], []
    for f in frames:
        im = cv2.imread(pattern % f, cv2.IMREAD_UNCHANGED)
        if im is None:
            continue
        im = im.astype(np.float64)/(65535. if im.dtype == np.uint16 else 255.)
        a, P = im[..., 3], im[..., :3]*im[..., 3:4]
        L = P @ np.array([0.0722, 0.7152, 0.2126])          # BGR
        m = a > 0.5
        if m.sum() < 200:
            continue
        mm = m[:, 1:] & m[:, :-1]
        st.append(np.percentile(np.abs(np.diff(L, axis=1))[mm], 99.99))
        br.append(np.percentile(L[m], 99))
    return float(np.mean(st)), float(np.mean(br))


def measure(pattern, y0=None, y1=None, label=''):
    files = sorted(glob.glob(pattern))
    W = None
    hard, edge, area, cx, cy, idx = [], [], [], [], [], []
    for i, f in enumerate(files):
        a = read_alpha(f)
        H, W = a.shape
        yy0 = 0 if y0 is None else y0
        yy1 = H if y1 is None else y1 + 1
        band = a[yy0:yy1]
        if band.max() < 0.02:
            continue
        inner = band[1:-1] if band.shape[0] > 2 else band
        hs = np.abs(np.diff(inner, axis=1)).max()
        vs = np.abs(np.diff(inner, axis=0)).max() if inner.shape[0] > 1 else 0.0
        m = (band > 0.5).astype(np.uint8)
        if m.sum() < 200:
            continue
        per = cv2.morphologyEx(m, cv2.MORPH_GRADIENT, np.ones((3, 3), np.uint8)).sum()
        part = ((band > 0.02) & (band < 0.98)).sum()
        ys, xs = np.nonzero(m)
        hard.append(max(hs, vs)); edge.append(part/max(per, 1)*1000.0/W)
        area.append(m.sum()); cx.append(xs.mean()); cy.append(ys.mean()); idx.append(i)
    hard, edge = np.array(hard), np.array(edge)
    area, cx, cy = np.array(area, float), np.array(cx), np.array(cy)

    def wobble(v):
        r = []
        for k in range(2, len(v)-2):
            w = v[k-2:k+3]
            p = np.polyfit(np.arange(-2, 3), w, 2)
            r.append(w[2] - np.polyval(p, 0))
        return np.array(r) if r else np.zeros(1)

    wa, wx, wy = wobble(area), wobble(cx), wobble(cy)
    print(f'{label}  {len(hard)} live frames, {W} px wide')
    print(f'   HARDNESS  worst single pixel alpha step  {hard.max():.3f}   (aliased edge = 1.000)')
    print(f'   EDGE      partial band  {edge.mean():.2f} thousandths of frame width'
          f'   (min {edge.min():.2f}, max {edge.max():.2f})')
    ph = picture_hardness(pattern.replace('*', '%04d'))
    print(f'   PICTURE   steepest luma step inside the silhouette  {ph[0]:.5f} per px'
          f'   bright end p99 {ph[1]:.4f}')
    print(f'   JITTER    centroid wobble rms  x {wx.std():.3f} px   y {wy.std():.3f} px'
          f'   worst {max(np.abs(wx).max(), np.abs(wy).max()):.2f} px')
    print(f'             area wobble rms  {100*wa.std()/area.mean():.3f}% of area'
          f'   worst {100*np.abs(wa).max()/area.mean():.3f}%')
    return dict(hard=hard.max(), edge=edge.mean(), pic=ph[0], bright=ph[1],
                jx=wx.std(), jy=wy.std(), ja=100*wa.std()/area.mean())


if __name__ == '__main__':
    import sys
    if len(sys.argv) < 2:
        print('usage: matte_gate.py <glob of RGBA or alpha frames> [y0 y1] [label]')
        raise SystemExit(2)
    y0 = int(sys.argv[2]) if len(sys.argv) > 3 else None
    y1 = int(sys.argv[3]) if len(sys.argv) > 3 else None
    measure(sys.argv[1], y0, y1, sys.argv[4] if len(sys.argv) > 4 else sys.argv[1])
    raise SystemExit(0)
