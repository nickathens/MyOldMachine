# Godot Skill

Game development with Godot 4 engine.

## Capabilities

- **2D Games**: Platformers, puzzles, arcade games
- **3D Games**: First-person, third-person, simulations
- **Export**: Web (HTML5), Linux, Windows, Android
- **Headless**: Run game logic without display
- **GDScript**: Python-like scripting language

## Commands

```bash
# Run project
godot --path /path/to/project

# Export game (requires export presets configured)
godot --headless --path /path/to/project --export-release "Linux" game.x86_64

# Run specific scene
godot --path /path/to/project res://scenes/main.tscn
```

## Project Structure

```
project/
├── project.godot       # Project config
├── scenes/
│   ├── main.tscn      # Main scene
│   └── player.tscn    # Player scene
├── scripts/
│   ├── player.gd      # Player logic
│   └── game.gd        # Game manager
├── assets/
│   ├── sprites/
│   └── audio/
└── export_presets.cfg  # Export settings
```

## GDScript Example

```gdscript
extends CharacterBody2D

const SPEED = 300.0
const JUMP_VELOCITY = -400.0

func _physics_process(delta):
    if not is_on_floor():
        velocity.y += 980 * delta

    if Input.is_action_just_pressed("jump") and is_on_floor():
        velocity.y = JUMP_VELOCITY

    var direction = Input.get_axis("left", "right")
    velocity.x = direction * SPEED

    move_and_slide()
```

## Examples

"Create a simple platformer prototype"
"Build a pong clone"
"Make a puzzle game with drag-and-drop"
"Generate a game jam template"

## Limitations

- GUI editor requires display (use for design, bot assists with code)
- Export to mobile requires SDK setup
- Complex 3D may be slow on older GPUs
