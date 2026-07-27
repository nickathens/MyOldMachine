// Vendored from github.com/Vincentwei1021/video-shotcraft (Apache-2.0)
// at 93fe427, 2026-07-27. See ../../../references/NOTICE.md. Unmodified.
/** Deterministic PRNG — same seed always yields the same sequence. */
export const mulberry32 = (seed: number) => {
  let a = seed | 0;
  return () => {
    a = (a + 0x6d2b79f5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
};
