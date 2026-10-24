// Video.js 10, plain HTML: custom elements registered by side-effect imports.
import '@videojs/html/video/player';
import '@videojs/html/video/skin';

document.body.insertAdjacentHTML('beforeend',
  '<video-player><video-skin><video slot="media" src="/a.mp4"></video></video-skin></video-player>');
