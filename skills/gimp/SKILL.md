# GIMP Skill

Headless image editing through GIMP 2.10 or 3: Script-Fu (Scheme) or Python-Fu, batch
filters, layer work, format export, anything the procedure database exposes.

Check the installed version with `gimp-console --version` (or the macOS
bundle path under "Which binary") before choosing a recipe. GIMP 2.10 is
still shipped by older Linux distributions and rejects `--quit`. Use the
GIMP 2.10 compatibility section at the end on those machines. The wrapper
`scripts/batch.py::gimp_script()` detects version 2 or 3 and selects the exit
flags, but does not translate the script's procedure names.

The GIMP 3 recipes below were run against **GIMP 3.2.6** (Homebrew cask, macOS) on
12 Sep 2026 and checked on its output, not assumed. GIMP 3 renamed, moved and
deleted enough of the 2.10 procedure set that a 2.10 script does not degrade,
it fails on its first call.

## GIMP 3: always pass `--quit`

A failing batch command does not look like a failure. GIMP 3 prints the error,
skips every remaining `-b`, and then **stays alive in its main loop forever as
a background process**, at 0% CPU, saying nothing. The trailing
`-b '(gimp-quit 0)'` that 2.10 scripts end with is one of the commands it
skips, which is exactly why it never exits.

Measured: seven failed runs launched together sat parked for 438 seconds and
only ended when they were killed. The same seven with `--quit` failed in 1
second each and printed the reason.

```bash
# Wrong: on any error this process is still running tomorrow.
gimp-console -i --new-instance --batch-interpreter=plug-in-script-fu-eval -b '(script)' -b '(gimp-quit 0)'

# Right: --quit exits whatever happens.
gimp-console -i --new-instance --batch-interpreter=plug-in-script-fu-eval --quit -b '(script)'
```

Exit codes, measured:

| Code | Meaning |
|---|---|
| 0 | the batch command ran |
| 70 | Script-Fu error (`unbound variable`, `Invalid argument name`, …) |
| 64 | calling error, e.g. a Python-Fu traceback |

Never read "no output yet" as progress. Output is only flushed when the
process ends, so a pipe stays empty while the error is already on stderr.
Redirect to a file and read the file if you want to watch a run live.

## Which binary

```bash
GIMP=/Applications/GIMP.app/Contents/MacOS/gimp-console   # macOS, cask install
GIMP=gimp-console                                          # Linux, package install
GIMP=$(command -v gimp-console || echo /Applications/GIMP.app/Contents/MacOS/gimp-console)
```

`gimp-console` is GIMP's no-GUI build. On macOS it is **not on PATH**: the
Homebrew wrapper at `/opt/homebrew/bin/gimp` points at the GUI binary instead,
so a bare `gimp` works but is the full application running without a window.
Both accepted `--quit` and ran the same script in the same time here; prefer
`gimp-console` for unattended work.

Always pass `--new-instance` for batch work so it cannot be forwarded to
someone's open editor. `--quit` is for GIMP 3 only. See the
[official command reference](https://www.gimp.org/man/gimp.html).

Two GIMP 3 batch interpreters, both real:

```bash
--batch-interpreter=plug-in-script-fu-eval   # Scheme (TinyScheme), the default choice
--batch-interpreter=python-fu-eval           # Python 3 with the Gimp GI bindings
```

## What changed from 2.10

Every entry on the left answered `Error: eval: unbound variable` when called on
3.2.6. These are not deprecations, they are gone.

| GIMP 2.10 | GIMP 3.2 |
|---|---|
| `file-png-save`, `file-jpeg-save`, `file-gif-save` | `file-png-export`, `file-jpeg-export`, `file-gif-export` |
| `gimp-image-get-active-layer` | `gimp-image-get-layers` (returns a **vector**) or `gimp-image-get-selected-layers` |
| `plug-in-gauss` | GEGL op `gegl:gaussian-blur` through `gimp-drawable-merge-new-filter` |
| `plug-in-unsharp-mask` | GEGL op `gegl:unsharp-mask` through the same |
| `gimp-pdb-query`, `gimp-procedural-db-query` | no Script-Fu equivalent; use Python-Fu (below) |
| `gimp-pdb-get-proc-info`, `gimp-pdb-proc-exists` | same |

Two more traps that are not renames:

- **`gimp-layer-new` moved the name to argument 2.** 2.10:
  `(gimp-layer-new img 64 64 RGB-IMAGE "bg" 100 LAYER-MODE-NORMAL)`. 3.2:
  `(gimp-layer-new img "bg" 64 64 RGB-IMAGE 100 LAYER-MODE-NORMAL)`. The
  symptom is `expected type: string for argument 2`.
- **Layers come back as a vector.** `(car (gimp-image-get-layers img))` is the
  vector; take a layer with `vector-ref`, not another `car`.

What still works unchanged, so do not "fix" it: the 3-argument
`(gimp-file-load RUN-NONINTERACTIVE "f.png" "f.png")` form, and colours given
either as `"#ff8000"` or as `(list 255 128 0)`. Both were checked and both
wrote exactly 255,128,0.

## Named arguments

GIMP 3 warns that positional plug-in arguments are deprecated, and the warning
prints the exact named form it wants:

```
Calling Plug-In PDB procedures with arguments as an ordered list is deprecated.
Please use named arguments: (file-png-export #:run-mode 1 #:image 1 #:file out.png #:options -1)
```

That warning is free, exact, machine-specific documentation of a signature.
Read it instead of guessing. The named form works and is quieter:

```scheme
(file-png-export #:run-mode RUN-NONINTERACTIVE #:image img #:file "out.png" #:options -1)
```

`#:options -1` means "the format's default export settings". Format options
ride along as named arguments, e.g. `#:quality 0.20` on a JPEG export (0.0 to
1.0). Measured on the same 400x300 source: 52 KB at 0.20, 125 KB at 0.95.

## Verified recipes

Each of these was run and its output measured.

**Fill and export** (produced exactly 255,128,0):

```bash
"$GIMP" -i --new-instance --batch-interpreter=plug-in-script-fu-eval --quit -b '
(let* ((img (car (gimp-image-new 64 64 RGB)))
       (layer (car (gimp-layer-new img "bg" 64 64 RGB-IMAGE 100 LAYER-MODE-NORMAL))))
  (gimp-image-insert-layer img layer 0 -1)
  (gimp-context-set-foreground "#ff8000")
  (gimp-drawable-fill layer FILL-FOREGROUND)
  (file-png-export #:run-mode RUN-NONINTERACTIVE #:image img #:file "/tmp/out.png" #:options -1)
  (gimp-image-delete img))'
```

**Convert a format** (JPEG in, PNG out):

```bash
"$GIMP" -i --new-instance --batch-interpreter=plug-in-script-fu-eval --quit -b '
(let ((img (car (gimp-file-load RUN-NONINTERACTIVE "/tmp/in.jpg"))))
  (file-png-export #:run-mode RUN-NONINTERACTIVE #:image img #:file "/tmp/out.png" #:options -1)
  (gimp-image-delete img))'
```

**Resize** (400x300 in, 200x150 out):

```bash
"$GIMP" -i --new-instance --batch-interpreter=plug-in-script-fu-eval --quit -b '
(let ((img (car (gimp-file-load RUN-NONINTERACTIVE "/tmp/in.jpg"))))
  (gimp-image-scale img 200 150)
  (gimp-image-flatten img)
  (file-png-export #:run-mode RUN-NONINTERACTIVE #:image img #:file "/tmp/small.png" #:options -1)
  (gimp-image-delete img))'
```

**Crop** to 300x300 from offset 50,0:

```bash
"$GIMP" -i --new-instance --batch-interpreter=plug-in-script-fu-eval --quit -b '
(let ((img (car (gimp-file-load RUN-NONINTERACTIVE "/tmp/in.jpg"))))
  (gimp-image-crop img 300 300 50 0)
  (gimp-image-flatten img)
  (file-png-export #:run-mode RUN-NONINTERACTIVE #:image img #:file "/tmp/crop.png" #:options -1)
  (gimp-image-delete img))'
```

**Blur** (GEGL, since the old plug-in is gone). On a pure-noise plate this cut
pixel standard deviation to 4% of the source, so it demonstrably ran:

```bash
"$GIMP" -i --new-instance --batch-interpreter=plug-in-script-fu-eval --quit -b '
(let* ((img (car (gimp-file-load RUN-NONINTERACTIVE "/tmp/in.png")))
       (d (vector-ref (car (gimp-image-get-layers img)) 0)))
  (gimp-drawable-merge-new-filter d "gegl:gaussian-blur" 0 LAYER-MODE-REPLACE 1.0
                                  "std-dev-x" 6.0 "std-dev-y" 6.0)
  (file-png-export #:run-mode RUN-NONINTERACTIVE #:image img #:file "/tmp/blur.png" #:options -1)
  (gimp-image-delete img))'
```

**Sharpen** (unsharp mask, same mechanism; raised standard deviation 38% on a
softened plate):

```bash
"$GIMP" -i --new-instance --batch-interpreter=plug-in-script-fu-eval --quit -b '
(let* ((img (car (gimp-file-load RUN-NONINTERACTIVE "/tmp/soft.png")))
       (d (vector-ref (car (gimp-image-get-layers img)) 0)))
  (gimp-drawable-merge-new-filter d "gegl:unsharp-mask" 0 LAYER-MODE-REPLACE 1.0
                                  "std-dev" 3.0 "scale" 1.5)
  (file-png-export #:run-mode RUN-NONINTERACTIVE #:image img #:file "/tmp/sharp.png" #:options -1)
  (gimp-image-delete img))'
```

A misspelled GEGL property is caught, not ignored: it exits 70 with
`Error: Invalid argument name: no-such-property`. So a filter that exits 0
really did apply.

## Finding a procedure name

Guessing costs a full run. Three ways to look it up instead, cheapest first.

**1. Ask the PDB through Python-Fu.** 1024 procedures on this build:

```bash
"$GIMP" -i --new-instance --batch-interpreter=python-fu-eval --quit -b '
import gi
gi.require_version("Gimp", "3.0")
from gi.repository import Gimp
names = Gimp.get_pdb().query_procedures("", "", "", "", "", "", "", "")
Gimp.message(",".join(sorted(n for n in names if "png" in n)))'
```

`query_procedures` takes **eight** filter strings; pass eight empty ones for
everything. Output arrives as a `python-eval.py-Warning:` line on stderr.

**2. Read the registered names out of the plug-in binary:**

```bash
strings /Applications/GIMP.app/Contents/lib/gimp/3.0/plug-ins/file-png/file-png | grep '^file-png'
```

**3. Let the deprecation warning tell you**, as above. Call it positionally
once and GIMP prints the full named signature.

## Noise you can ignore

All of these appear on clean, successful runs:

- `GIMP is started as MacOS application`
- `GIMP-Message: Welcome to GIMP 3.2.6!`
- `EEEEeEeek! 4 GeglBuffers leaked` and the `gegl_tile_cache_destroy` warning
- `scriptfu-WARNING **: Missing arg type: gboolean` (a positional call that
  stopped short of the optional arguments)
- `INFO: a stray image seems to have been left around by a plug-in` (call
  `gimp-image-delete` when you are done to avoid it)

The line that actually means failure is
`batch command experienced an execution error:`, followed by `Error:` and
`Stopping at failing batch command [0]:`.

## When not to use GIMP

GIMP costs about 1.5 seconds of start-up before it does anything. For resize,
crop, format conversion, thumbnails and simple blur, ImageMagick
(`magick in.jpg -resize 800x600 out.png`) is faster and needs no interpreter.
Reach for GIMP when you need layers, masks, a specific GEGL operation, or a
GIMP-only file format.

`scripts/batch.py` follows that rule: the simple operations shell out to
ImageMagick, and `gimp_script()` is there for the cases that genuinely need
GIMP. It detects the installed major version, raises on a failed exit or a GIMP 2
batch error diagnostic, and applies a timeout (300 seconds by default).
The version probe is bounded to ten seconds. It starts its own instance.
Startup cleanup leaves GIMP jobs alone because it cannot prove who started
them. The session Stop hook may clean up only its own GIMP descendants.

```bash
python skills/gimp/scripts/batch.py resize in.jpg out.jpg --width 1920
python skills/gimp/scripts/batch.py thumbnail photos/ thumbs/ --size 256
```

## Examples

"Resize all images in folder to 1080p"
"Convert PNG to JPEG with 85% quality"
"Apply blur to this image"
"Create thumbnail from image"
"Batch crop images to square"

## GIMP 2.10 compatibility

These commands are for GIMP 2.10 only. Do not pass `--quit`, and do not use
the GIMP 3 export names or changed argument order. A final `(gimp-quit 0)`
ends a 2.10 batch. Because a script error may still exit zero, prefer
`gimp_script()` when running unattended: it checks the error diagnostic too.

This fill and export recipe uses the 2.10 argument order and PNG saver:

```bash
"$GIMP" -i --new-instance --batch-interpreter=plug-in-script-fu-eval -b '
(let* ((img (car (gimp-image-new 64 64 RGB)))
       (layer (car (gimp-layer-new img 64 64 RGB-IMAGE "bg" 100 NORMAL-MODE))))
  (gimp-image-insert-layer img layer 0 -1)
  (gimp-context-set-foreground "#ff8000")
  (gimp-drawable-fill layer FOREGROUND-FILL)
  (file-png-save RUN-NONINTERACTIVE img layer "/tmp/out.png" "/tmp/out.png" 0 9 0 0 0 0 0)
  (gimp-image-delete img))' -b '(gimp-quit 0)'
```

The wrapper's ImageMagick operations (resize, convert, thumbnail, blur,
sharpen and square crop) work independently of the installed GIMP version.
