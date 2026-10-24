import { createElement } from 'react';
import { createRoot } from 'react-dom/client';
import { motion } from 'framer-motion';

createRoot(document.getElementById('root')).render(
  createElement(motion.div, { animate: { x: 100, opacity: 1 }, initial: { opacity: 0 }, transition: { duration: 0.4 } }),
);
