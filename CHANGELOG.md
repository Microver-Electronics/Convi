# Changelog

## 0.3.0 - 2026-11-23

* Built-in manufacturing-output templates: an Output Job with ten containers and a seven-sheet Draftsman
  fabrication drawing, so manufacturing outputs work without a template of your own. Made on a synthetic test
  board (`devtools/template_board.py`, `devtools/make_builtin_templates.py`); they hold no product data.
* Template choice: your own templates stay the default when Convi finds them; wizard *Template* list (yours,
  built-in, other folder) and `--output-template auto|builtin|DIR`.
* README: wizard screenshots and the built-in Draftsman drawing.

## 0.2.0 - 2026-11-23

* Manufacturing outputs (optional): your own Altium Output Job and Draftsman template are retargeted to the
  converted board (paths, DNP variant, mechanical layers by purpose, inner copper layers, drill pairs, one
  Draftsman view per copper layer, views scaled to their frames) and run after the conversion. Wizard step
  *Options & outputs*, command line `--outputs [all|NAME,NAME]` and `--list-outputs`.
* New wizard: five steps (project, check, options and outputs, convert, result) with the workspace project list,
  live stages and log, and the verification results in numbers. The previous Tk wizard stays as a fallback
  (`CONVI_GUI=classic`).
* Part values are no longer shown on the board (they stay in the BOM and the component parameters).
* Board-only footprints that share a reference (e.g. logos `G***`) get unique designators.
* `convi_report.json` records the KiCad source folder.
* New dependencies: `lz4`, `customtkinter` (run `install.bat` again after updating).
* `Convi.bat` keeps the window open when Convi stops with an error.

## 0.1.0 (R1) - 2026-11-20

First public release.

* Complete KiCad 10 project conversion (also reads KiCad 6 to 9 and 10.99 files) into a native Altium Designer
  project: PrjPcb, SchDoc per sheet instance, SchLib, PcbDoc, PcbLib with embedded STEP bodies.
* Built-in verification in Altium: pin-by-pin connectivity, ECO preview, PcbDoc read-back, DRC with every
  remaining violation classified, BOM comparison.
* Rules from net classes, project settings and `.kicad_dru` custom rules; KiCad severities and DRC exclusions.
* Copper pours in KiCad priority order with neck-width refinement against KiCad's fill.
* Hierarchical schematics with ports, power ports, signal harnesses for group buses, repeated sheets,
  optional customer sheet templates.
* Wizard (`Convi.bat`) and command line (`convi inspect`, `convi convert`).
