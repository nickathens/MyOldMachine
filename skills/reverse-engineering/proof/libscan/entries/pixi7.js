import { Application, Graphics } from 'pixi.js';

const app = new Application({ width: 400, height: 300 });
document.body.appendChild(app.view);
const g = new Graphics();
g.beginFill(0xff0000).drawRect(0, 0, 100, 100).endFill();
app.stage.addChild(g);
