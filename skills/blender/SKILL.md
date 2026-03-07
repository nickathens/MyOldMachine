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

`scripts/render.py` - Main rendering script with scene builder

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
