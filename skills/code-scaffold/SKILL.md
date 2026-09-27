# Code Scaffold Skill

Generate project templates and boilerplate code.

Every command below ran without a terminal on 2026-09-27; that matters,
because a scaffolder that stops to ask questions hangs the turn.

## Commands

```bash
# Python package (cookiecutter; pip install cookiecutter if missing). --no-input plus key=value,
# or it prompts for every field.
cookiecutter --no-input -o ~/projects gh:audreyfeldroy/cookiecutter-pypackage \
  project_name=mylib full_name="Your Name" github_username=yourname

# React + Vite
npm create vite@latest my-app -- --template react        # react-ts, vue, svelte, vanilla too

# Vue 3 (official create-vue, defaults)
npm create vue@latest my-app -- --default

# Svelte / SvelteKit (official sv)
npx --yes sv create my-app --template minimal --types ts --no-add-ons --no-install

# FastAPI full stack: a plain repository now (no cookiecutter or copier file), so clone it
git clone --depth 1 https://github.com/fastapi/full-stack-fastapi-template my-app

# Plain HTML/CSS/JS
mkdir my-site && cd my-site && touch index.html style.css script.js
```

- A cookiecutter template runs its own hook code. cookiecutter-pypackage's
  hook offers to create a GitHub repository only when asked (`--github
  private|public`); do not pass that without the user's go ahead.
- Godot projects: see the godot skill (write project.godot and scenes as
  text; never open the editor here).

Fixed 2026-09-27: `degit` (used for Svelte and Vue) is not installed; the
Svelte starter it cloned, sveltejs/template, is archived; the FastAPI
template is no longer a cookiecutter template; and a bare `cookiecutter`
call waited for answers.

## Examples

"Create a new Python package called mylib"
"Scaffold a React app"
"Create a Svelte project"
"Set up a basic HTML/CSS/JS website"
