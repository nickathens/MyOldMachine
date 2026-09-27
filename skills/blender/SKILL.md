# Blender Skill

3D modeling, rendering, and animation via Blender's Python API.

## Capabilities

- **Render scenes**: Create and render 3D scenes from descriptions
- **Generate objects**: Procedural geometry (cubes, spheres, landscapes, abstract shapes)
- **Materials**: Glass, metal, emission, procedural textures
- **Animation**: Rotating objects, camera movements, keyframe animation
- **Batch processing**: Render multiple frames/variations
- **Export**: Images (PNG), videos (MP4), 3D models (GLB, FBX, OBJ)

## Usage

Run Blender in background mode with Python scripts:
```bash
blender --background --python script.py
```

## Script Location

`scripts/render.py` - four ready scenes (`glass_sphere`, `spinning_cube`, `abstract`, `product`) plus the building blocks for your own (materials, primitives, 3D text, rotation and camera-orbit animation):

```bash
R=skills/blender/scripts/render.py
blender --background --python $R -- --scene product --output /tmp/shot.png --width 1920 --height 1080
blender --background --python $R -- --scene spinning_cube --animation --frames 120 --output /tmp/spin.mp4
# --engine EEVEE|CYCLES, --samples N
```

Exit 0 means the file exists; any error exits 1. (Blender itself exits 0 after an uncaught Python error, which is how `--animation` looked fine while it failed on every call on Blender 5.2: it set the old `file_format='FFMPEG'` without the 5.x `media_type='VIDEO'`. Fixed 2026-09-27.) An animation always writes an .mp4, even if the output name said .png. Model export (GLB, FBX, OBJ) is not in render.py: write a short script with `bpy.ops.export_scene.gltf(...)` and friends.

## Examples

"Render a glass sphere on a reflective surface"
"Create a 5-second spinning cube animation"
"Generate a low-poly landscape"
"Render abstract geometric pattern"

## Limitations

- GTX 970 GPU: Use Cycles CPU or EEVEE for faster renders
- Complex scenes may take several minutes
- Keep resolution reasonable (1080p max recommended)

## Version

Blender 5.0.1 (snap)
