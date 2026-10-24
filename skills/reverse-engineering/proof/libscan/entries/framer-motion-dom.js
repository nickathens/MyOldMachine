// Motion without React: the plain animate() and scroll() functions.
import { animate, scroll } from 'framer-motion/dom';

animate('.box', { x: 100, opacity: 1 }, { duration: 0.4 });
scroll(animate('.bar', { scaleX: [0, 1] }));
