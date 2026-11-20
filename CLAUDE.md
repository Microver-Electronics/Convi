# Instructions for AI coding assistants

This repository is Convi, a KiCad 10 to Altium Designer converter. Start every session by reading
`brain.md` completely; it is the project's shared memory and its working agreement (section 2) is binding.

Key rules (details in brain.md):

* Read only: never modify a source KiCad project or a delivered conversion. Experiments run on copies in
  `Outputs/convi_out/_dev`; deleting files is the user's decision.
* All logic in Python; DelphiScript builders only execute. Prove every Altium API call with a probe first.
* Do not hide differences (no lowered clearances, No-ERC markers, deleted rules). Keep "no driving
  source" ERC warnings. Test points, mounting holes and fiducials stay DNP and out of the BOM.
* Mark anything not yet observed in Altium as `UNVERIFIED`. Report results as numbers and separate
  conversion errors, known engine differences and facts of the source design.
* Capture the Altium window only, never the full screen. One Convi script runs at a time.
* After a change: offline tests, offline IR regression, a real conversion checked in Altium, then update
  brain.md (topic section + log entry).

Commands:

```bat
py -m pip install -e .[dev]
py -m pytest -q tests/test_offline.py
convi inspect <kicad project>
convi convert <kicad project> -y
py devtools/probe.py devtools/probes/<probe>.pas
```
