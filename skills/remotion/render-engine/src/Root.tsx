import React from "react";
import { Composition } from "remotion";
import { TitleCard, titleCardDefaults } from "./TitleCard";
import { BarChartBuild, barChartDefaults } from "./BarChartBuild";
import { CardFan, cardFanDefaults, cardFan3DDefaults } from "./CardFan";

// Every composition registered here becomes a --comp <id> target for render.mjs.
// Add a new motion graphic by writing its component file and adding a <Composition> below.
export const RemotionRoot: React.FC = () => {
  return (
    <>
      <Composition
        id="TitleCard"
        component={TitleCard}
        durationInFrames={150}
        fps={30}
        width={1920}
        height={1080}
        defaultProps={titleCardDefaults}
      />
      <Composition
        id="BarChartBuild"
        component={BarChartBuild}
        durationInFrames={180}
        fps={30}
        width={1920}
        height={1080}
        defaultProps={barChartDefaults}
      />
      <Composition
        id="CardFan"
        component={CardFan}
        durationInFrames={240}
        fps={30}
        width={1920}
        height={1080}
        defaultProps={cardFanDefaults}
      />
      {/* CardFan3D is the same CardFan component; cardFan3DDefaults only sets
          dimensional: true to switch it into the real-3D render branch. */}
      <Composition
        id="CardFan3D"
        component={CardFan}
        durationInFrames={240}
        fps={30}
        width={1920}
        height={1080}
        defaultProps={cardFan3DDefaults}
      />
    </>
  );
};
