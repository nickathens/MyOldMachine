import { Application, Graphics } from 'pixi.js';

(async () => {
  const app = new Application();
  await app.init({ width: 400, height: 300 });
  document.body.appendChild(app.canvas);
  app.stage.addChild(new Graphics().rect(0, 0, 100, 100).fill(0xff0000));
})();
