#!/usr/bin/env python3
"""
Blender 5.x rendering script - run with:
blender --background --python render.py -- [args]
"""
import bpy
import sys
import os
import math
import argparse
import mathutils
import uuid

# Blender version check
BLENDER_VERSION = bpy.app.version
IS_BLENDER_5 = BLENDER_VERSION[0] >= 5


def clear_scene():
    """Remove all objects from scene"""
    bpy.ops.object.select_all(action='SELECT')
    bpy.ops.object.delete()


def setup_render(width=1920, height=1080, engine='EEVEE', samples=64):
    """Configure render settings (Blender 5 compatible)"""
    scene = bpy.context.scene
    scene.render.resolution_x = width
    scene.render.resolution_y = height
    scene.render.film_transparent = False

    if engine == 'CYCLES':
        scene.render.engine = 'CYCLES'
        scene.cycles.samples = samples
        scene.cycles.device = 'CPU'  # GTX 970 - use CPU for reliability
    else:
        scene.render.engine = 'BLENDER_EEVEE'
        # Blender 5 uses taa_samples, older versions use taa_render_samples
        if hasattr(scene.eevee, 'taa_samples'):
            scene.eevee.taa_samples = samples
        elif hasattr(scene.eevee, 'taa_render_samples'):
            scene.eevee.taa_render_samples = samples


def add_camera(location=(7, -7, 5), target=(0, 0, 0)):
    """Add and point camera"""
    bpy.ops.object.camera_add(location=location)
    camera = bpy.context.object
    bpy.context.scene.camera = camera

    # Point at target
    direction = mathutils.Vector(target) - mathutils.Vector(location)
    rot_quat = direction.to_track_quat('-Z', 'Y')
    camera.rotation_euler = rot_quat.to_euler()
    return camera


def add_light(type='SUN', location=(5, 5, 10), energy=5):
    """Add light source"""
    bpy.ops.object.light_add(type=type, location=location)
    light = bpy.context.object
    light.data.energy = energy
    return light


def add_hdri(path=None):
    """Add HDRI environment lighting"""
    world = bpy.context.scene.world
    if world is None:
        world = bpy.data.worlds.new("World")
        bpy.context.scene.world = world

    world.use_nodes = True
    nodes = world.node_tree.nodes
    nodes.clear()

    bg = nodes.new('ShaderNodeBackground')
    bg.inputs['Color'].default_value = (0.05, 0.05, 0.08, 1)
    bg.inputs['Strength'].default_value = 1.0

    output = nodes.new('ShaderNodeOutputWorld')
    world.node_tree.links.new(bg.outputs['Background'], output.inputs['Surface'])


def create_material(name, color=(0.8, 0.8, 0.8, 1), metallic=0, roughness=0.5,
                   emission=None, glass=False):
    """Create a PBR material (Blender 5 compatible)"""
    mat = bpy.data.materials.new(name=name)
    mat.use_nodes = True
    nodes = mat.node_tree.nodes
    links = mat.node_tree.links

    nodes.clear()
    output = nodes.new('ShaderNodeOutputMaterial')

    if glass:
        bsdf = nodes.new('ShaderNodeBsdfGlass')
        bsdf.inputs['Color'].default_value = color
        bsdf.inputs['Roughness'].default_value = roughness
        bsdf.inputs['IOR'].default_value = 1.45
    else:
        bsdf = nodes.new('ShaderNodeBsdfPrincipled')
        bsdf.inputs['Base Color'].default_value = color
        bsdf.inputs['Metallic'].default_value = metallic
        bsdf.inputs['Roughness'].default_value = roughness

        if emission:
            # Blender 4+: Emission Color and Emission Strength are separate
            # Try both naming conventions for compatibility
            try:
                if 'Emission Color' in bsdf.inputs:
                    bsdf.inputs['Emission Color'].default_value = emission
                elif 'Emission' in bsdf.inputs:
                    bsdf.inputs['Emission'].default_value = emission

                if 'Emission Strength' in bsdf.inputs:
                    bsdf.inputs['Emission Strength'].default_value = 5.0
            except Exception:
                pass  # Skip emission if not supported

    links.new(bsdf.outputs[0], output.inputs['Surface'])
    return mat


def add_cube(location=(0, 0, 0), size=2, material=None):
    """Add a cube"""
    bpy.ops.mesh.primitive_cube_add(size=size, location=location)
    obj = bpy.context.object
    if material:
        obj.data.materials.append(material)
    return obj


def add_sphere(location=(0, 0, 0), radius=1, segments=32, material=None):
    """Add a UV sphere"""
    bpy.ops.mesh.primitive_uv_sphere_add(radius=radius, segments=segments,
                                          ring_count=segments//2, location=location)
    obj = bpy.context.object
    bpy.ops.object.shade_smooth()
    if material:
        obj.data.materials.append(material)
    return obj


def add_plane(location=(0, 0, 0), size=20, material=None):
    """Add a ground plane"""
    bpy.ops.mesh.primitive_plane_add(size=size, location=location)
    obj = bpy.context.object
    if material:
        obj.data.materials.append(material)
    return obj


def add_torus(location=(0, 0, 0), major_radius=1, minor_radius=0.3, material=None):
    """Add a torus"""
    bpy.ops.mesh.primitive_torus_add(major_radius=major_radius,
                                      minor_radius=minor_radius, location=location)
    obj = bpy.context.object
    bpy.ops.object.shade_smooth()
    if material:
        obj.data.materials.append(material)
    return obj


def add_cone(location=(0, 0, 0), radius=1, depth=2, material=None):
    """Add a cone"""
    bpy.ops.mesh.primitive_cone_add(radius1=radius, depth=depth, location=location)
    obj = bpy.context.object
    bpy.ops.object.shade_smooth()
    if material:
        obj.data.materials.append(material)
    return obj


def add_cylinder(location=(0, 0, 0), radius=1, depth=2, material=None):
    """Add a cylinder"""
    bpy.ops.mesh.primitive_cylinder_add(radius=radius, depth=depth, location=location)
    obj = bpy.context.object
    bpy.ops.object.shade_smooth()
    if material:
        obj.data.materials.append(material)
    return obj


def add_text(text="Text", location=(0, 0, 0), size=1, extrude=0.1, material=None):
    """Add 3D text"""
    bpy.ops.object.text_add(location=location)
    obj = bpy.context.object
    obj.data.body = text
    obj.data.size = size
    obj.data.extrude = extrude
    obj.data.bevel_depth = 0.02
    if material:
        obj.data.materials.append(material)
    return obj


def animate_rotation(obj, frames=120, axis='Z'):
    """Add rotation animation to object"""
    scene = bpy.context.scene
    scene.frame_start = 1
    scene.frame_end = frames

    axis_idx = {'X': 0, 'Y': 1, 'Z': 2}[axis.upper()]

    obj.rotation_euler[axis_idx] = 0
    obj.keyframe_insert(data_path='rotation_euler', frame=1)

    obj.rotation_euler[axis_idx] = math.radians(360)
    obj.keyframe_insert(data_path='rotation_euler', frame=frames)

    # Make linear interpolation
    for fcurve in obj.animation_data.action.fcurves:
        for kf in fcurve.keyframe_points:
            kf.interpolation = 'LINEAR'


def animate_camera_orbit(camera, target=(0, 0, 0), radius=8, height=4, frames=120):
    """Animate camera orbiting around target"""
    scene = bpy.context.scene
    scene.frame_start = 1
    scene.frame_end = frames

    for frame in range(1, frames + 1):
        angle = (frame / frames) * 2 * math.pi
        x = target[0] + radius * math.cos(angle)
        y = target[1] + radius * math.sin(angle)
        z = target[2] + height

        camera.location = (x, y, z)

        # Point at target
        direction = mathutils.Vector(target) - mathutils.Vector((x, y, z))
        rot_quat = direction.to_track_quat('-Z', 'Y')
        camera.rotation_euler = rot_quat.to_euler()

        camera.keyframe_insert(data_path='location', frame=frame)
        camera.keyframe_insert(data_path='rotation_euler', frame=frame)


def render_image(output_path):
    """Render single image"""
    bpy.context.scene.render.filepath = output_path
    bpy.context.scene.render.image_settings.file_format = 'PNG'
    bpy.ops.render.render(write_still=True)
    print(f"Rendered: {output_path}")


def render_animation(output_path, format='FFMPEG'):
    """Render animation as video"""
    scene = bpy.context.scene
    scene.render.filepath = output_path

    if format == 'FFMPEG':
        scene.render.image_settings.file_format = 'FFMPEG'
        scene.render.ffmpeg.format = 'MPEG4'
        scene.render.ffmpeg.codec = 'H264'
        scene.render.ffmpeg.constant_rate_factor = 'HIGH'
        scene.render.ffmpeg.audio_codec = 'AAC'

    bpy.ops.render.render(animation=True)
    print(f"Rendered animation: {output_path}")


def scene_glass_sphere():
    """Demo scene: glass sphere on reflective surface"""
    clear_scene()
    setup_render(1280, 720, 'EEVEE', 128)
    add_hdri()

    # Ground plane
    ground_mat = create_material('Ground', (0.02, 0.02, 0.02, 1), metallic=0.9, roughness=0.1)
    add_plane((0, 0, 0), 20, ground_mat)

    # Glass sphere
    glass_mat = create_material('Glass', (0.9, 0.95, 1.0, 1), glass=True)
    add_sphere((0, 0, 1.5), 1.5, 64, glass_mat)

    # Lights
    add_light('AREA', (3, -3, 5), 500)
    add_light('AREA', (-3, 2, 4), 300)

    # Camera
    bpy.ops.object.camera_add(location=(6, -6, 4))
    camera = bpy.context.object
    bpy.context.scene.camera = camera
    camera.rotation_euler = (math.radians(65), 0, math.radians(45))


def scene_spinning_cube(frames=120):
    """Demo scene: spinning metallic cube"""
    clear_scene()
    setup_render(1280, 720, 'EEVEE', 64)
    add_hdri()

    # Ground
    ground_mat = create_material('Ground', (0.1, 0.1, 0.1, 1), roughness=0.8)
    add_plane((0, 0, 0), 20, ground_mat)

    # Spinning cube
    cube_mat = create_material('Cube', (0.8, 0.4, 0.1, 1), metallic=1.0, roughness=0.2)
    cube = add_cube((0, 0, 1.5), 2, cube_mat)
    animate_rotation(cube, frames, 'Z')

    # Lights
    add_light('SUN', (5, 5, 10), 3)
    add_light('AREA', (-3, -3, 5), 200)

    # Camera
    bpy.ops.object.camera_add(location=(5, -5, 4))
    camera = bpy.context.object
    bpy.context.scene.camera = camera
    camera.rotation_euler = (math.radians(60), 0, math.radians(45))


def scene_abstract_geometry():
    """Demo scene: abstract geometric composition"""
    clear_scene()
    setup_render(1280, 720, 'EEVEE', 128)
    add_hdri()

    # Dark ground
    ground_mat = create_material('Ground', (0.01, 0.01, 0.02, 1), roughness=0.9)
    add_plane((0, 0, 0), 30, ground_mat)

    # Emissive shapes
    colors = [
        (1.0, 0.2, 0.4, 1),   # Pink
        (0.2, 0.6, 1.0, 1),   # Blue
        (0.2, 1.0, 0.6, 1),   # Green
        (1.0, 0.8, 0.2, 1),   # Yellow
    ]

    import random
    random.seed(42)

    for i in range(12):
        x = random.uniform(-4, 4)
        y = random.uniform(-4, 4)
        z = random.uniform(0.5, 3)
        color = random.choice(colors)

        mat = create_material(f'Emit{i}', color, emission=color)

        shape = random.choice(['sphere', 'cube', 'torus'])
        if shape == 'sphere':
            add_sphere((x, y, z), random.uniform(0.3, 0.8), 32, mat)
        elif shape == 'cube':
            add_cube((x, y, z), random.uniform(0.5, 1.2), mat)
        else:
            add_torus((x, y, z), random.uniform(0.4, 0.8), 0.15, mat)

    # Camera
    bpy.ops.object.camera_add(location=(8, -8, 6))
    camera = bpy.context.object
    bpy.context.scene.camera = camera
    camera.rotation_euler = (math.radians(60), 0, math.radians(45))

    add_light('SUN', (5, 5, 10), 1)


def scene_product_shot():
    """Demo scene: product visualization setup"""
    clear_scene()
    setup_render(1920, 1080, 'EEVEE', 128)
    add_hdri()

    # Infinite backdrop (curved plane)
    bpy.ops.mesh.primitive_plane_add(size=20, location=(0, 0, 0))
    backdrop = bpy.context.object

    # Simple white material
    backdrop_mat = create_material('Backdrop', (0.95, 0.95, 0.95, 1), roughness=0.5)
    backdrop.data.materials.append(backdrop_mat)

    # Placeholder object (sphere as product stand-in)
    product_mat = create_material('Product', (0.8, 0.2, 0.1, 1), metallic=0.0, roughness=0.3)
    add_sphere((0, 0, 1), 1, 64, product_mat)

    # Three-point lighting
    add_light('AREA', (3, -2, 4), 400)   # Key light
    add_light('AREA', (-3, -1, 3), 200)  # Fill light
    add_light('AREA', (0, 3, 2), 150)    # Rim light

    # Camera
    bpy.ops.object.camera_add(location=(4, -4, 3))
    camera = bpy.context.object
    bpy.context.scene.camera = camera
    camera.rotation_euler = (math.radians(70), 0, math.radians(45))


if __name__ == '__main__':
    # Parse args after --
    argv = sys.argv
    if '--' in argv:
        argv = argv[argv.index('--') + 1:]
    else:
        argv = []

    parser = argparse.ArgumentParser(description='Blender 5.x rendering script')
    parser.add_argument('--scene', default='glass_sphere',
                       choices=['glass_sphere', 'spinning_cube', 'abstract', 'product'])
    parser.add_argument('--output', default=f'/tmp/blender_render_{uuid.uuid4().hex[:8]}.png')
    parser.add_argument('--animation', action='store_true')
    parser.add_argument('--frames', type=int, default=120)
    parser.add_argument('--width', type=int, default=1280)
    parser.add_argument('--height', type=int, default=720)
    parser.add_argument('--engine', default='EEVEE', choices=['EEVEE', 'CYCLES'])
    parser.add_argument('--samples', type=int, default=64)
    args = parser.parse_args(argv)

    print(f"Blender {'.'.join(map(str, BLENDER_VERSION))}")
    print(f"Scene: {args.scene}, Engine: {args.engine}, Output: {args.output}")

    # Build scene
    if args.scene == 'glass_sphere':
        scene_glass_sphere()
    elif args.scene == 'spinning_cube':
        scene_spinning_cube(args.frames)
    elif args.scene == 'abstract':
        scene_abstract_geometry()
    elif args.scene == 'product':
        scene_product_shot()

    # Override settings if specified
    setup_render(args.width, args.height, args.engine, args.samples)

    # Render
    if args.animation:
        render_animation(args.output)
    else:
        render_image(args.output)
