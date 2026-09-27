# Web Build Skill

Static site generation and web development.

## Tools

- **Hugo** - Fast static site generator
- **npm/Node** - For JS-based builds

Ubuntu 24.04's apt Hugo (0.123.7) is too old for the current Ananke theme
("can't evaluate field Locale in type *langs.Language"), and so is every
Ananke release from v2.10 on; v2.9.2 is the newest that builds with it
(checked 2026-09-27), hence the pin below. With a newer Hugo (brew, the snap,
or a release binary; `hugo version` says which) skip the pin.

## Hugo Commands

```bash
# Create new site
hugo new site mysite
cd mysite

# Add theme
git init
git submodule add https://github.com/theNewDynamic/gohugo-theme-ananke themes/ananke
git -C themes/ananke checkout v2.9.2      # only with an old Hugo such as apt's 0.123
echo "theme = 'ananke'" >> hugo.toml

# Create content
hugo new posts/my-first-post.md

# Dev server: it never returns, so not in the foreground of a turn; build and
# look at public/ instead, or run it in the background for the user to open
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

Publishing puts the site on the internet: confirm with the user first. surge
is what the presentations skill publishes with.
