# Changelog

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
