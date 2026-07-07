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

## Machine Safety

Godot is the heaviest tool in this skill, and MyOldMachine frequently runs on old
or low-power hardware. A headed window drives the GPU, and a runaway import or a
scene that never reaches idle can wedge a marginal machine hard enough that only a
power cycle recovers it. On any machine you cannot physically reach, treat these as
rules, not suggestions:

- **Run headless for anything automated.** Use `--headless` for imports, CI,
  scripted runs, and agent-driven work. `--headless` selects the dummy display and
  audio drivers, so it never touches the real GPU. Only open a headed window when a
  person is at the machine and the GPU is known good.
- **Bound every invocation.** Wrap the command in `timeout <seconds>` and pass
  `--quit-after <frames>` (or `--quit` to stop after the first iteration) so a stuck
  import or an idle-less scene cannot run forever. An unbounded `godot --path .` is
  the classic way to hang a headless box.
- **Do one import pass, then run.** `godot --headless --import --path .` imports
  resources and quits; do it once instead of letting every launch reimport.
- **Force software rendering to capture frames on a weak or shared GPU.** Do not
  point a headed capture at the physical GPU. Run under a virtual display
  (`xvfb-run`) with the Mesa software rasteriser (`LIBGL_ALWAYS_SOFTWARE=1`); this
  renders on the CPU and cannot crash the graphics driver. Capturing from a headed
  window on an old GPU is the single most likely way to freeze the machine.
- **Validate scenes without `--check-only`.** `--check-only` is for `--script`
  (GDScript) and chokes on `.tscn` files. To confirm a scene loads, run it headless
  with `--quit-after 1` and check the exit code and log instead.
- **One Godot process at a time.** Do not launch parallel editors or runs. The Stop
  hook in `hooks.json` already kills orphaned headless Godot processes when a task
  ends; keep it that way.

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
