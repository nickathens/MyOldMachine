// Video.js 10 in React.
import { createElement } from 'react';
import { createRoot } from 'react-dom/client';
import { Video, VideoPlayer, VideoSkin } from '@videojs/react/video';

createRoot(document.getElementById('root')).render(
  createElement(VideoPlayer, null, createElement(VideoSkin, null, createElement(Video, { src: '/a.mp4' }))),
);
