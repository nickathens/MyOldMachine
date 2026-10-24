import { WebGLRenderer, Scene, PerspectiveCamera, Mesh, BoxGeometry, MeshBasicMaterial } from 'three';

const renderer = new WebGLRenderer({ antialias: true });
const scene = new Scene();
const camera = new PerspectiveCamera(50, 1, 0.1, 100);
scene.add(new Mesh(new BoxGeometry(), new MeshBasicMaterial({ color: 0xff0000 })));
renderer.setAnimationLoop(() => renderer.render(scene, camera));
document.body.appendChild(renderer.domElement);
