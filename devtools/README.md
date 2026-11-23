# Development tools

Helpers for working on Convi. None of them is needed to use the converter. All of them need a running,
licensed Altium Designer unless noted otherwise.

| Tool | Purpose |
|---|---|
| `probe.py <probe.pas> [timeout]` | Run a DelphiScript probe body inside Altium and print its log. Use it to prove an Altium API call before the builders rely on it. |
| `probes/` | Probe bodies (`procedure Body;`, log with `L(msg)`). |
| `try_pcb.py <board.kicad_pcb> pcb\|lib\|both` | Build only the PcbDoc / PcbLib of a board and print the builder log. |
| `try_sch.py <project>` | Build only the SchDocs and SchLib of a project. |
| `batch.py [demo names]` | Convert KiCad demo projects from `testdata/kicad-src/demos` and write a summary. |
| `show.py <document> [out.png] [--3d]` | Open a document in Altium, zoom to fit and capture the Altium window. |
| `altium_dlg.py list\|close\|shot out.png` | List, close or capture open Altium dialogs. |
| `render_fill.py <board> <ref> <pad> <layer> [out.png]` | Render KiCad's copper fill around a pad (no Altium needed). |
| `template_board.py <out folder>` | Generate the synthetic KiCad board the built-in output templates are made on (needs KiCad's `pcbnew` Python module). |
| `make_builtin_templates.py <conversion> [--forbid WORD ...]` | Turn a conversion of that board (with manufacturing outputs) into `convi/templates/outputs`; neutral paths, refuses to write anything that could leak data. |

## Path tokens

Probes never contain machine-specific paths. `probe.py` replaces these tokens before a run:

| Token | Meaning |
|---|---|
| `%ROOT%` | this repository |
| `%OUT%` | development output folder (`Outputs/convi_out/_dev` in a workspace) |
| `%WS%` | workspace root |

## Rules

- Run probes on copies only, never on a delivered conversion or a source project.
- One Convi script runs in Altium at a time; do not run probes during a conversion.
- Capture the Altium window only (`show.py`, `altium_dlg.py shot`), never the full screen.
- Never add anything extracted from Altium's binaries to this repository.

See [brain.md](../brain.md), sections 4 and 10, for the verified Altium facts and the development workflow.
