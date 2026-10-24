import { createElement, useState } from 'react';
import { createRoot } from 'react-dom/client';

function App() {
  const [n, setN] = useState(0);
  return createElement('button', { onClick: () => setN(n + 1) }, 'n=' + n);
}
createRoot(document.getElementById('root')).render(createElement(App));
