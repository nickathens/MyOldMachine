// A React look-alike, as a negative control: none of this is React.
import { h, render } from 'preact';
import { useState } from 'preact/hooks';
import { forwardRef, memo } from 'preact/compat';

const Button = memo(forwardRef((props, ref) => h('button', { ref, onClick: props.onClick }, props.label)));
function App() {
  const [n, setN] = useState(0);
  return h(Button, { onClick: () => setN(n + 1), label: 'n=' + n });
}
render(h(App, null), document.getElementById('root'));
