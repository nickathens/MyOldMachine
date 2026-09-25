# The Rive editor, plans, and handing work over

The editor is where a person designs and animates by hand: a vector design tool, a timeline, a state machine graph, a data binding panel and a Luau script editor in one app. It runs in a browser (editor.rive.app) or as a desktop app for macOS and Windows. On this Mac the desktop app is installed (`brew install --cask rive`, 0.8.5940 on 25 Sep 2026; it updates itself, so the nightly never reports it). It needs a Rive account signed in at the screen before it does anything, and nothing in it can be driven with no one at the screen, except through MCP below.

## What the plans unlock

From rive.app/pricing, rendered 25 Sep 2026 (Rive's docs pricing page gives different Voyager figures; the live page wins):

| | Free | Cadet | Voyager | Enterprise |
|---|---|---|---|---|
| Price a seat a month | 0 | 9 yearly, 17 monthly | 32 yearly, 49 monthly | 120, yearly only |
| Seats | | up to 3 | up to 25 | |
| Files | unlimited personal, 3 collaborative, 1 project | unlimited | unlimited | unlimited |
| Export `.riv` from the editor | no | yes | yes | yes |
| Export video and images (Cloud Renderer) | the table says every plan, the docs say paid only; unconfirmed | yes | yes | yes |
| Backup `.rev` export, revision history | no | yes | yes | yes |
| Libraries, CDN asset hosting, embed links, batch export | | | yes | yes |
| AI agent | free with limits (announced 30 Apr 2026) | yes | 20 USD of credits a seat a month | 40 USD |
| Early Access app | | pricing page: Cadet and up; docs: Voyager | yes | yes |
| Lottie import | | | | yes |

The CLI is free with no limits during its technical preview; Rive has said publish calls will likely be limited afterwards, generously. Everything this skill renders locally is not a publish call. A student plan exists for personal education only.

What that means here: with a free account, work is designed and animated in the editor but cannot leave it as a `.riv` or a video. The CLI builds `.riv` files and renders video from RML without any plan. `rive create --from-remote-file` pulls a file from the account into an RML project; whether a free account may use it that way was not tested (no account yet), and Rive may treat it as an export: check before relying on it.

## Moving work between the CLI and the editor

| From | To | How | Login |
|---|---|---|---|
| a CLI project | a `.rev` a designer opens | `rive <dir> --once --rev=file.rev` | yes |
| a CLI project | a file in the account, with history | `rive push [--name "label"]` (first push creates the file and records `push:` in `rive.yaml`) | yes |
| the account file | the CLI project | `rive pull --yes` (overwrites the project's files; files that are only local are listed, not deleted) | yes |
| an editor `.rev` | a CLI project | `rive create <dir> --from-rev=file.rev` | no |
| an account file | a CLI project | `rive create <dir> --from-remote-file[=<id>]` | yes |

A push is authoritative: someone with the file open sees their edits replaced. Before handing a file over: give every artboard and state an `x`/`y`, every text style `familyName`/`styleName`, every artboard a `LayoutComponentStyle` (the editor cannot add one later), and set `viewModelInstanceId` so artboards open populated.

`--from-rev` is also the way to learn from real files: the scene comes out as RML with every script and asset as a file, laid out like the editor's Assets panel. Skinned meshes and bones are much easier to take from a `.rev` than to write.

## Coaching someone in the editor

Point to the parts by their names in the app: the Toolbar (tools, Design and Animate modes), the Hierarchy (tree; first item is on top), the Inspector (properties of the selection), the Stage, the Timeline in Animate mode, the State Machine graph, the Data panel (view models, instances, binds), the Assets panel (fonts, images, audio, scripts), the Debug panel (console, problems, AI changes, tests, audio). A key is added by changing a property in Animate mode; interpolation is set per key in the timeline's graph. Useful editor-only tools the CLI lacks: SVG and Figma paste import, PSD import by layer, Google Fonts picker, a free Soundly library of about 3,000 sounds for Audio Events, the Shape Builder, freehand pencil and brush.

## Rive's AI agent

Built into the editor (the Agent panel): writes scripts, layouts, data models and animation from a prompt; billed in AI credits (1 USD of credit is 1 USD of use), monthly credits per paid seat, top-ups do not expire, zero data retention with its model provider (Rive's docs). A GitHub issue from March 2026 (rive-app/rive-runtime#87) called it unreliable and credit-hungry; untested here.

## MCP: an external agent driving the editor (untested here)

The desktop editor can serve an MCP endpoint at `http://127.0.0.1:9791/mcp` that lets an agent create artboards, shapes, layouts, animations, state machines, view models and bindings, and edit scripts and shaders, inside the file that is open. The stable app on this Mac contains that server's code. Requirements: the desktop app running in the logged-in GUI session, signed in, with a file open; Rive's docs also say "the Rive Early Access app must remain open", and which plan gets Early Access is stated two ways. Connect Claude with:

```bash
claude mcp add --transport http rive http://127.0.0.1:9791/mcp
```

Not tested (no account), and it only works while someone is at the Mac: treat it as a co-pilot session with the user at the screen, not a headless route. The CLI is the headless route.

## Other people's MCP servers and skills

`ODU33104/rive-mcp` writes `.riv` files without the editor but its licence forbids modification, redistribution and AI-assisted analysis of its code: do not use it. `FUNGnix/rive-mcp` (MIT) writes `.riv` binaries from an in-memory scene graph; the official CLI supersedes that approach. See `sources.md`.
