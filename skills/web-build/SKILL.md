# Web Build Skill

Static site generation and web development.

## Tools

- **Hugo** - Fast static site generator
- **npm/Node** - For JS-based builds

## Hugo Commands

```bash
# Create new site
hugo new site mysite
cd mysite

# Add theme
git init
git submodule add https://github.com/theNewDynamic/gohugo-theme-ananke themes/ananke
echo "theme = 'ananke'" >> hugo.toml

# Create content
hugo new posts/my-first-post.md

# Dev server
hugo server -D

# Build for production
hugo --minify
```

## Project Structure

```
mysite/
├── archetypes/
├── content/
│   └── posts/
├── layouts/
├── static/
│   ├── css/
│   ├── js/
│   └── images/
├── themes/
└── hugo.toml
```

## Examples

"Create a Hugo site for my portfolio"
"Build the site for production"
"Add a new blog post"
"Set up a simple HTML landing page"

## Deployment

```bash
# Build
hugo --minify

# Output is in ./public/
# Deploy to Netlify, Vercel, GitHub Pages, etc.
```
