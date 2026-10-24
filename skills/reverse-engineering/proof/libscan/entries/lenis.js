import Lenis from 'lenis';

const lenis = new Lenis({ duration: 1.2 });
lenis.on('scroll', (e) => document.body.style.setProperty('--y', e.scroll));
function raf(t) {
  lenis.raf(t);
  requestAnimationFrame(raf);
}
requestAnimationFrame(raf);
