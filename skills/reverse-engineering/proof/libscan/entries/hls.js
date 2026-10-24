import Hls from 'hls.js';

const video = document.querySelector('video');
if (Hls.isSupported()) {
  const hls = new Hls();
  hls.loadSource('/stream.m3u8');
  hls.attachMedia(video);
}
