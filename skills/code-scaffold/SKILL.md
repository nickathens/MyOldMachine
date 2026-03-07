# Code Scaffold Skill

Generate project templates and boilerplate code.

## Tools

- **cookiecutter** - Python project templates
- **degit** - Clone git repos without history (fast)

## Templates Available

### Python
- `gh:audreyfeldroy/cookiecutter-pypackage` - Python package
- `gh:tiangolo/full-stack-fastapi-template` - FastAPI full stack

### Web
- `degit sveltejs/template` - Svelte
- `degit vitejs/vite/packages/create-vite/template-react` - React + Vite
- `degit vuejs/create-vue-templates/default` - Vue 3

### Games
- Create Godot project structure manually

## Commands

```bash
# Python package
cookiecutter gh:audreyfeldroy/cookiecutter-pypackage

# Svelte app
degit sveltejs/template my-svelte-app

# React + Vite
npm create vite@latest my-app -- --template react

# Plain HTML/CSS/JS
mkdir my-site && cd my-site
touch index.html style.css script.js
```

## Examples

"Create a new Python package called mylib"
"Scaffold a React app"
"Create a Svelte project"
"Set up a basic HTML/CSS/JS website"
