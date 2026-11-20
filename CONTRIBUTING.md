# Contributing to Convi

Thank you for helping. Convi's value is that a conversion needs no manual clean-up, so every change is
judged by measured results in Altium and KiCad, not by how plausible it looks.

## Before you start

1. Read [brain.md](brain.md): goals, architecture, verified facts, known limitations and open items.
   Many "bugs" are documented engine differences (brain.md section 12).
2. Open an issue describing the difference: board type, what KiCad shows, what Altium shows, numbers.
   Never attach a design you are not allowed to share; a minimal KiCad project that reproduces the case is best.

## Rules

* Never modify a source KiCad project; experiments run on copies.
* Geometry and decisions live in Python (`convi/`); the DelphiScript builders only execute IR commands.
* Prove every Altium API call with a probe (`devtools/probes`) before relying on it. An unknown property
  stops a script with a modal dialog even inside `try/except`.
* Do not hide differences: no lowered clearances, deleted rules, No-ERC markers or dropped objects to make
  DRC/ERC quiet. Report what remains and why.
* Do not commit customer or company designs, or anything extracted from Altium's binaries.
* Code, comments, messages and documentation are in English.

## Definition of done

1. `py -m pytest -q tests/test_offline.py` passes (with the KiCad demos in `testdata/kicad-src/demos`
   for the full set).
2. Offline IR regression (brain.md section 10.3): the IR of the reference projects is unchanged except for
   the intended difference.
3. At least one affected project converted and checked in Altium: connectivity, ECO preview, DRC, ERC and,
   for copper changes, pour areas against KiCad's fill. Until then the change is marked `UNVERIFIED` in
   brain.md.
4. brain.md updated: the fact in its topic section and a dated log entry.

## Pull requests

Keep a pull request to one topic. Describe the observation, the cause, the change and the measured result
(before / after numbers).

By submitting a contribution you agree that it is licensed under the MIT License of this repository.
