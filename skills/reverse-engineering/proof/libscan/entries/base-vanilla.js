// No library: the native primitives a hand-built page uses, as a negative control.
const el = document.querySelector('.box');
let t0 = 0;
function frame(t) {
  if (!t0) t0 = t;
  el.style.transform = `translateX(${Math.min(200, (t - t0) / 5)}px)`;
  requestAnimationFrame(frame);
}
requestAnimationFrame(frame);
new IntersectionObserver((es) => es.forEach((e) => e.target.classList.toggle('in', e.isIntersecting))).observe(el);
const v = document.querySelector('video');
if (v) setTimeout(() => v.play().catch(() => {}), 10000);
