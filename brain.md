# Convi - Engineering Brain

This file is the shared memory of the Convi project. It holds what the tool is, how it works, what has
been proven against real Altium and KiCad behaviour, what went wrong before, and what is still open.
It is written for human contributors and AI coding assistants alike. Read it before changing code,
and update it whenever you learn something new.

---

## 0. How to use this file

**Reading order for a new session**

1. Section 1 (goals) and section 2 (working agreement): they define what "correct" means.
2. Section 3 (architecture): know where a change belongs before you make it.
3. The section for the area you are about to touch (4 to 9).
4. Section 12 (known limitations) and section 13 (open items), so known limits are not reported as
   new bugs and open work is not duplicated.

**Evidence levels.** Every technical statement in this file has one of these levels:

| Tag | Meaning |
|---|---|
| (none) | Verified: observed in Altium or KiCad on a real conversion, a probe, or a source-code reading. |
| `UNVERIFIED` | Implemented or believed, but not yet observed end to end. Must be confirmed on the next run. |
| `REJECTED` | Tried and reverted. The reason is given so it is not tried again. |

Never remove an `UNVERIFIED` tag without having seen the result yourself. When a statement turns out
wrong, correct it in place and say what proved it wrong.

**How to add knowledge.** Add a dated entry to section 14 (log) in this form, and move the lasting
fact into the topic section it belongs to:

```
### YYYY-MM-DD <short title>
* Observation: what was seen, on what kind of board, with numbers.
* Cause: what proved it (probe, source reading, file inspection, A/B test).
* Change: what was changed in which file / function.
* Result: VERIFIED (how) or UNVERIFIED (what to check next run).
```

---

## 1. Purpose and acceptance criteria

Convi converts a complete **KiCad 10** project (it also reads KiCad 6 to 9 and 10.99 files) into a native
**Altium Designer** project (2026; 24 and later work) with no manual clean-up:

* `.PrjPcb`, one `.SchDoc` per sheet instance, `.SchLib`, `.PcbDoc`, `.PcbLib`.
* STEP 3D bodies embedded, a clean board shape, rules, net classes and differential pairs.

A conversion is **correct** when all of the following hold:

1. **Connectivity**: every KiCad pad net equals the Altium compiled net, pin by pin.
2. **ECO preview** (what *Design > Update PCB* would do): `add 0, remove 0, re-link 0, footprint 0, pad-net 0`.
3. **Geometry read-back**: every pad (position and net), track, arc and via of the saved PcbDoc equals KiCad.
4. **BOM**: parts, values and fields equal KiCad; DNP parts are in a "KiCad DNP" variant.
5. **DRC**: Altium reports nothing that KiCad's own DRC would not report on the same board. Remaining
   violations are named in the report as KiCad's (warnings, exclusions, real design facts).
6. **ERC**: only the warnings KiCad's design also implies (in practice: "no driving source").
7. **Copper pours**: each poured polygon within about 1 % of KiCad's zone fill, or the difference is
   explained (section 7.4).
8. **Schematic look**: content from KiCad, drawing language from Altium (section 6.1). Nothing overlaps
   that did not overlap in KiCad.

Non-goals: Convi never "improves" a design. It does not change clearances, nets or parts to make a
report look cleaner.

---

## 2. Working agreement (humans and AI assistants)

These rules come from real mistakes. Follow them strictly.

### 2.1 Safety of user data
* **Never modify a source KiCad project.** Read only. Experiments run on copies.
* Every conversion goes to a new output folder (`<Name>`, then `<Name>_v2`, `_v3` ...). An existing,
  non-empty output folder is never overwritten automatically.
* Experiments in Altium (probes, interactive checks) run on copies in `Outputs/convi_out/_dev/...`, which
  are closed and discarded afterwards. Never "fix" a delivered conversion by hand to make a check pass.
* Deleting files is the user's decision. Propose, do not delete.
* Do not take full-screen screenshots (other applications may be visible). Capture the Altium window
  only (`convi/altium/dialogs.capture_window`).
* Do not run probes while the user's conversion is running. Only one Convi script runs at a time
  (`%TEMP%\convi_altium.lock`), but do not compete with the user's session.

### 2.2 Engineering discipline
* **All geometry and decisions live in Python** (testable offline). The DelphiScript builders only
  execute IR commands. Do not put logic into `.pas` files that Python could do.
* **Every Altium API call must be proven before it is relied on**: by a probe in `devtools/probes`, by a
  published Altium script, or by inspecting a file Altium wrote. DelphiScript fails on unknown
  identifiers with a modal dialog even inside `try/except` (section 4.1), so an unproven property can
  stop a whole conversion.
* **Measure before you change**: reproduce the difference, find the cause (source reading, file
  inspection, A/B test on a copy), then change one thing.
* **Do not hide differences.** Never lower a clearance, delete a rule, add No-ERC markers or drop
  objects to make DRC/ERC quiet. Report what remains and why.
* **Keep "no driving source" ERC warnings.** They reflect the source design; never suppress them.
* **Test points, mounting holes and fiducials**: DNP and excluded from the BOM, as in KiCad; never
  give them placeholder 3D boxes.
* Prefer one generic rule over a board-specific special case. If a special case is unavoidable,
  document the condition that triggers it.

### 2.3 Definition of done for a change
1. Offline tests pass: `py -m pytest -q tests/test_offline.py` (31 tests with the KiCad demos in
   `testdata/kicad-src/demos`; 4 are skipped without them).
2. Offline IR regression (section 10.3): the IR of every reference project is byte-identical to the
   previous version, except for the intended change.
3. A real conversion of at least one affected project, checked in Altium (connectivity, ECO, DRC, ERC,
   pours). Use an MCP Altium bridge or the GUI. Without that, the change stays `UNVERIFIED`.
4. Regression round on the reference set when the change touches shared code (rules, pours,
   connectivity, schematic placement).
5. This file updated: the fact in its topic section and a log entry in section 14.

### 2.4 Reporting results
* State numbers: "nets 644/644, pins 2517/2517, DRC 6 (equals KiCad's 6)", not "looks fine".
* Separate three kinds of remaining difference: conversion errors (bugs), known Altium/KiCad engine
  differences (section 12), and facts of the source design (KiCad reports them too).
* When KiCad is the reference, use KiCad's own output (`kicad-cli pcb drc`, `kicad-cli sch erc`,
  `kicad-cli sch export pdf`), not memory of how KiCad behaves.

---

## 3. Architecture

```
KiCad files --> convi/kicad (parsers) --> convi/convert (KiCad -> Convi IR) --> *.cvi files
                                                                                   |
Altium (running instance) <-- X2.EXE -RScriptingSystem:RunScriptFile <-------------+
     builder_*.pas reads the IR and creates objects through the Altium API, then saves.
                                   |
                    verify.pas: compile project, dump netlist + PCB pads --> convi/verify.py
```

Altium writes every binary file itself, so the output files are always valid native data. The only
exceptions are two small in-place patches of values that scripts cannot set (section 4.7).

### 3.1 Pipeline (`convi/pipeline.py`)
1. `inspect_project` (`convi/inspect.py`): discover files, parse everything, resolve 3D models, list missing
   items (sub-sheets, STEP files, undefined path variables).
2. Close any project already open from the same output folder (`close_project.pas`).
3. PCB converter prepares footprint library names (deduplicated by geometry signature).
4. Schematic converter writes `sch.cvi` and `schlib.cvi`; the builder creates the SchLib and the SchDocs.
5. Write a schematic-only `.PrjPcb`, **compile in Altium**, compare with the KiCad board pin by pin.
   **Repair loop** (at most 3 rounds): a KiCad net that Altium splits gets a "glue" power port named after
   the KiCad net on one pin of each piece; rebuild and recompile.
6. Net names for the PCB = Altium compiled net names; component links (`SourceUniqueId`) = Altium
   compiled `DM_UniqueIdPath + DM_UniqueId`.
7. PCB converter writes `pcb.cvi` and `pcblib.cvi`; the builders run.
8. Final `.PrjPcb` (documents in KiCad page order, DNP variant, parameters, ERC matrix).
9. Separate pour pass (`repour.pas`), optional unused-via-pad removal (`unusedpads.pas`), board check
   (`pcbcheck.pas`: read-back + DRC), pour refinement (`_refine_pours`, section 7.3).
10. Final verify: compile and read the PcbDoc back, then the ECO preview.
11. Open the project in Altium; write `convi_report.json` (work files and logs in `_convi/`).

### 3.2 File map
| Path | Role |
|---|---|
| `convi/sexpr.py` | fast S-expression reader (QStr for quoted strings, Blob for `\|base64\|`) |
| `convi/kicad/pcb.py` | board model; footprint children to absolute and local coordinates |
| `convi/kicad/sch.py` | schematic model, library symbol flattening (`extends`), KiCad symbol transform |
| `convi/kicad/project.py` | `.kicad_pro`: net classes, patterns, assignments, rules, severities, bus aliases |
| `convi/kicad/embedded.py` | KiCad 9/10 embedded files (zstd + base64) |
| `convi/kicad/connectivity.py` | KiCad's own open connections per net (routing status of the source) |
| `convi/models3d.py` | 3D model resolution; download of KiCad standard-library models (cache `%LOCALAPPDATA%\Convi`) |
| `convi/convert/pcb_convert.py` | board to IR: stack-up, outline, components in library frame, pads, zones, text, bodies |
| `convi/convert/layers.py` | KiCad to Altium layer map, mechanical layer kinds and pairs |
| `convi/convert/outline.py` | Edge.Cuts chaining to board shape and cutouts |
| `convi/convert/rules.py` | net classes, rules in priority order, diff pairs, pad/footprint overrides |
| `convi/convert/dru.py` | `.kicad_dru` custom rules to Altium rules |
| `convi/convert/sch_convert.py` | hierarchy to SchDoc/SchLib IR, glue planning, harnesses, house-style check |
| `convi/convert/sheet_template.py` | customer sheet templates (`__Templates`, `CONVI_TEMPLATES`) |
| `convi/convert/project_file.py` | `.PrjPcb` writer (options, variants, ERC matrix) |
| `convi/altium/builder_common_head.pas` + `builder_pcb.pas` / `builder_sch.pas` + `builder_common_main.pas` | concatenated into one DelphiScript per run |
| `convi/altium/verify.pas`, `pcbcheck.pas`, `repour.pas`, `unusedpads.pas`, `open_project.pas`, `close_project.pas` | helper scripts |
| `convi/altium/runner.py` | runs a script in Altium: lock, started/done markers, dialog watchdog |
| `convi/altium/dialogs.py` | Win32 dialog helpers, window capture (Altium window only) |
| `convi/altium/pcbdoc_patch.py`, `schdoc_patch.py` | pure-Python compound-file reader and same-size in-place writer |
| `convi/verify.py` | netlist parsing, KiCad/Altium comparison, BOM comparison, ECO preview |
| `convi/pcbcheck.py` | PcbDoc read-back comparison, DRC report parsing, pour areas |
| `convi/layout.py` | CONVI workspace (`Tool/`, `Projects/`, `Outputs/`), `CONVI_WORKSPACE` |
| `convi/cli.py`, `convi/gui.py` | `convi inspect` / `convi convert`, Tk wizard (`Convi.bat`) |
| `devtools/` | probes, `try_pcb.py`, `try_sch.py`, `batch.py`, `show.py` |
| `tests/test_offline.py` | offline tests (no Altium needed) |

### 3.3 IR format (`convi/altium/ir.py`)
UTF-8 with BOM, one command per line, TAB-separated `key=value`. PCB coordinates are integer Altium
units. Schematic values are floats sent as integer milli-units and read back with `VD()`, which is
locale independent: never use `StrToFloat` in the builders. Region and polygon points are bare
`P<TAB>x<TAB>y` lines.

---

## 4. Verified Altium facts

Measured on Altium Designer 26.x unless stated otherwise.

### 4.1 Running scripts
* `X2.EXE "-RScriptingSystem:RunScriptFile(FileName=...|ProcName=Main)"` runs a script in the open Altium.
* Altium **ignores** a run request while a previous script is still finishing. Every script therefore
  writes a `started` marker first, and the runner re-sends the request every 20 s, but only when exactly
  one X2.EXE runs (a second instance waits forever on Altium's start-up mutex).
* Script errors open modal dialogs. Compile errors: "...Continue execution?" (#32770). Runtime errors and
  access violations: `TMessageForm` "Error", whose OK button ignores BM_CLICK (also post Enter). The
  runner saves a picture to `_convi/altium_error.png` / `%TEMP%\convi_last_error.png` first.
* WPF dialogs (`HwndWrapper`, e.g. "Unsaved Changes") have no Win32 buttons: use UI Automation.
* Delphi-style dialogs (e.g. `TRemoveUnusedPadsOptions`) use `TXPGroupButton` radios that offer neither
  Invoke nor SelectionItem in managed UIA. Working driver: C# through PowerShell `Add-Type`, MSAA
  `AccessibleObjectFromWindow` per child HWND, `accState & 0x10` for checked, `accDoDefaultAction`, with
  posted `WM_LBUTTONDOWN/UP` as fallback. Anything not set as intended -> Cancel.
* Before closing or rewriting a document, call `Doc.SetModified(False)` so no "Save changes?" prompt appears.
* Never call `ISch_GraphicalObject.Mirror()` from a script: it starts an interactive command, and every
  save afterwards fails with "A command is currently active".
* DelphiScript is case-insensitive: a variable `Vi` collides with a function `VI`. `Out` and `Dump` are
  reserved. `raise Exception.Create` is not supported; `ExceptionMessage` works.
* A routine must be declared **above** its first caller, otherwise "Undeclared identifier" at run time.
* An undeclared identifier or an unknown property stops the script with an "Error" dialog **even inside
  try/except**. Prove every property with a probe first.
* Store Altium objects in `TInterfaceList`, never in a Variant array (access violation).
* Line numbers in script error dialogs are 0-based.
* `TStringList.SaveToFile` writes ANSI: characters above 126 become `?`. `verify.pas` writes them as
  `{U+XXXX}` and `verify.py` decodes them.
* Switching Online DRC off during the build gave no speed gain. `REJECTED`; the user's setting is not touched.
* Build speed is about 1500 IR lines per second; reducing IR lines is the main lever (section 5.5).

### 4.2 PCB objects
* New document: `CreateNewDocumentFromDocumentKind('PCB')`, `Doc.SetFileName`, `Doc.DoFileSave('')`.
* Coordinates are absolute. Keep the design at least 1000 mil away from 0 (Convi offsets by 125 mm).
* Layer stack: `LayerStack_V7.InsertLayer(eMidLayerN)`; copper layers via `FirstLayer/NextLayer`; the
  dielectric under a copper layer is `LayerObject.Dielectric`.
* Mechanical layers: `LayerObject_V7[ILayer.MechanicalLayer(n)]` with `MechanicalLayerEnabled`, `Name`,
  `Kind` (`TMechanicalLayerKind`); pairs via `Board.MechanicalPairs.AddPair`.
* Board outline and polygons: `TPolySegment`; a polygon needs `PointCount=N` and `Segments[N]=Segments[0]`;
  an arc segment is centre/radius/Angle1..Angle2 (counter-clockwise).
* Components: set Layer and Rotation before adding children; children use absolute coordinates. Convi builds
  every footprint in **library frame on top**, then sets `Comp.Layer := eBottomLayer` (Altium mirrors library
  y to -y, like KiCad's flip), `MoveToXY`, `Rotation := KiCad angle`. A bottom flip sets Rotation to 180, so
  always set the rotation again after a flip.
* 3D: `ModelFactory_FromFilename`, `Model.SetState(rx, ry, rz, dz)` with R = Rz * Ry * Rx (KiCad uses the same
  order with negated angles); the model origin starts at (0, 0), use `MoveByXY`.
* Extruded body: `SetOutlineContour` + `ModelFactory_CreateExtruded(minZ, maxZ, color)`.
* Text bounding boxes update only after `UpdateTextPosition`; library texts have none (estimate them).
* Pad mask/paste values: `Pad.GetState_Cache` / `SetState_Cache` with `eCacheManual`.
* Classes: `PCBServer.PCBClassFactoryByClassMember(kind)` (not `PCBObjectFactory`).
* Via tenting holds only with a manual mask: `C := Via.GetState_Cache; C.SolderMaskExpansion := ..;
  C.SolderMaskExpansionValid := eCacheManual; Via.SetState_Cache := C`, then `IsTenting_Top/Bottom`.
  `SolderMaskExpansionMode` / `eMaskExpansionMode_Manual` do not exist in DelphiScript.
* `SetState_IsPadRemoved` / `IsPadRemoved[L]` do **not** exist for vias. `REJECTED`. Unused via pads are
  removed with `PCB:RemoveUnusedPads` instead (section 7.5).
* Altium draws no paste for through-hole pads: Convi adds Top/Bottom Paste regions for THT pads whose KiCad
  layers include F.Paste / B.Paste.
* Altium flashes NPTH pads on copper (KiCad does not); the drill removes the copper. Harmless.
* TrueType text: at Size S Altium draws 0.6405 x the glyph size KiCad's outline font draws at S, so Convi sends
  `Size = h / 0.6405`. The baseline runs through YLocation, ink starts at XLocation, mirrored text ends at
  XLocation. Convi places each single-line TTF text by KiCad's ink edge and baseline taken from KiCad's
  `render_cache` (verified within 0.3 mil of KiCad's ink).
* Altium's stroke font is wider than KiCad's newstroke ink at the same size.

### 4.3 Rules and queries
* **A newly added rule gets priority 1** (highest): emit the least specific rule first. Rule names must be
  unique across rule kinds.
* Room: `eRule_ConfinementConstraint` with `Kind`, `Layer`, `BoundingRect` (rectangle only), `DRCEnabled`.
  Length `MinLimit/MaxLimit`; matched lengths `Tolerance`; max via count `Limit`; routing layers
  `RoutingLayers[L]`; diff pair `MaxUncoupledLength`.
* Diff-pair rules store per-layer values; Altium keeps per-layer entries only where they differ from the most
  frequent one. Convi sets them per layer (`SetDiffPairLayers`).
* Query facts: `TouchesRoom('R')` works (touch semantics, needs a room); `TouchesComponent` does not exist;
  `InRegionAbsolute` tests the object's reference point; a pair rule must be scoped with `InDifferentialPair(..)`
  (`InNetClass` never matches it); an unknown query word raises a modal "Undeclared identifier" dialog;
  `IsBoardCutoutRegion` is valid (AD24+).
* A room scoped `InComponent(X)` reports a Room Definition violation when the component is larger than the
  room, so courtyard rooms are created with DRC disabled.
* Altium puts some default rules back after they are deleted (Height). Convi switches such rules off
  (`RULECLEAR`: DRCEnabled and Enabled False, comment "Convi: off ..."). Supported kinds include Height,
  ShortCircuit (`eRule_ShortCircuit`, ordinal 15) and Net Antennae.
* The Height rule (default 25.4 mm) has no KiCad equivalent: off.
* Net Antennae corresponds to KiCad `track_dangling` / `via_dangling`: off when both are ignored in KiCad,
  otherwise its violations are reported as KiCad warnings.
* DRC from a script: `Board.RunBatchDesignRuleCheck(report, eDRC_Text, False, False)`; violations via the
  `eViolationObject` iterator (`Rule.RuleKind`, `Rule.Name`); text report lines `Rule|||Description`.
  Older versions (AD24) put no description into the dump: names come from `drc_report.txt`.
* Dead copper without a net appears as an "Un-Routed" entry ("Polygon ... Dead Copper - Net Not Assigned").
  It is not an open connection of a net: `pcbcheck` names it `(no net)` and reports it separately.

### 4.4 Polygons and pouring
* Pouring inside the builder script (even after `PostProcess`) uses stale state: pads of flipped bottom
  components are not seen and the pour runs over them. A **separate script pass** (`repour.pas`: open, rebuild
  every poured polygon, save) pours correctly.
* `repour.pas` and `pcbcheck.pas` close the board if it is open, reopen it from disk and close it when done.
  A board left open by a previous pass poured with stale rule matches and ran no DRC (zone clearances lost,
  a pour emptied). `pcbcheck` deletes the old DRC report first and retries once if none is written.
* An interactive repour through an automation bridge pours over stale copper (order dependent). Convi's
  `repour.pas` removes and re-adds every polygon, so its results are deterministic. Compare only pours made
  by `repour.pas`.
* `IPCB_Polygon.InNet` is False even when the polygon has a net: test `Net <> Nil`.
* Polygons are groups: `FreePrimitives` / `AddPCBObject(region)` work, but a user repour replaces them, so
  importing KiCad's fill as regions does not help. `REJECTED`.
* The pour engine resolves a polygon's clearance with the polygon object itself. A scope on the polygon side
  that tests position (`TouchesRoom`) never matches it; a scope naming the polygon (`InNamedPolygon`) does.
  Pad-class scopes on the other side work.
* A polygon-scoped clearance rule (`InPolygon` / `IsPolygon`) does not let the pour keep narrow strips; only a
  lower global clearance would. Convi does not change clearances.
* Neck removal: `RemoveNarrowNecks` with `NeckWidthThreshold` is Altium's counterpart of KiCad's
  `min_thickness` pruning, but the algorithms differ (section 7.2).
* Altium removes dead copper that KiCad keeps when no item of the net reaches the zone on its layer (an inner
  plane with no vias yet). Such polygons get `RemoveDead False` and an info line.
* Altium's pour covers same-net via pads inside another zone's clearance hole (KiCad has only the via), and
  covers same-net higher-priority zones (KiCad knocks the lower zone out of them). The copper union is equal;
  per-polygon areas count the overlap twice.
* A pour's clearance around a rectangular pad has square corners (KiCad rounds them): about 1.3 mm2 less copper
  per rectangular THT pad at a 2.5 mm clearance. Altium limit.
* Altium counts the interior of a thermal-relief THT pad as pour copper, KiCad does not (affects area comparison only).
* The creepage rule (`eRule_Creepage`) exists but its distance cannot be set from scripts (section 4.7). With
  `APPLYTOPOLYGONPOUR=TRUE` the pour honours creepage; KiCad's filler does not, so Convi writes FALSE.
* Altium's creepage rule reaches keep-out regions; KiCad creepage is between nets only, so Convi's creepage
  scopes exclude `IsKeepOut`.
* Altium's clearance rules reach board cutouts; KiCad's net class and custom clearances do not apply to
  Edge.Cuts (only the copper-to-edge clearance does). Convi adds `Convi_BoardCutout` (`IsBoardCutoutRegion` vs
  All, 0) when the board has cutouts.

### 4.5 Schematic
* Pin hot spot = `Location + PinLength` along `Orientation` (proved by compiling a test sheet).
* Placement: Altium position = Loc + Mirror^m * R(orientation) * library point. Convi places children at
  absolute positions and sets `SetState_OrientationWithoutRotating` + `IsMirrored`.
* Connections need bit-identical coordinates: convert mm to integer nm to mil, flip Y against a page height
  rounded up to 50 mil.
* Part-0 graphics are not drawn on placed parts: KiCad unit-0 graphics are emitted for every part.
* Hidden pins never connect to wires (only through `HiddenNetName`).
* Altium silently replaces **duplicate component unique IDs**: one ID per part; the PCB link is taken from the
  compile (`DM_UniqueIdPath` + `DM_UniqueId`, path = sheet symbol IDs).
* Project variants link a part by the path Altium compiles: `\UID` for a sheet placed once, **not**
  `\SheetSymbolUID\UID` (that still varies the BOM but ERC reports "Incorrect link in project variant"). Convi
  takes the variant path from the compile.
* `PartCount` must be set before `CurrentPartID`.
* Altium takes all parts of a component from one library component.
* Pin text fonts: `Name_FontMode/Designator_FontMode := ePinItemMode_Custom` + custom font IDs; name offset via
  `Name_PositionMode` + `Name_CustomPosition_Margin`.
* Font size: KiCad size = cap height; 1 pt in Altium is about 10 mil em; Arial cap height = 0.716 em, so
  `pt = mm / 0.0254 / 0.716 / 10` (1.27 mm -> 7 pt).
* Project tree order of child sheets = creation order of the sheet symbols: emit them in KiCad page order.
* KiCad pin-name offset 0 (names outside, along the pin) has no Altium equivalent: Convi hides Altium's pin
  texts and draws name and number as symbol labels.
* Global labels as power ports: KiCad angle 0 = Altium orientation 0 (points right).
* Altium paints in creation order (KiCad paints fills and images on background layers): filled shapes and
  images are emitted first.
* Port cross references (sheet numbers) are regenerated by Altium at every compile, always in font ID 1 (Times
  New Roman 10); not controllable from the project. Setting `SystemFont` to Arial does not change them and
  shrinks the border zone labels. `REJECTED`.
* Altium's A3 template (15500 x 11000 mil = 393.7 x 279.4 mm) is smaller than ISO A3; a KiCad A3 drawing that
  fills the page goes to A2. Not a bug.
* Sheet symbols without sheet pins must sit on the 50 mil grid ("Off-grid object" otherwise).
* Signal harnesses (proved on a hand-made test sheet): harness connector + entries + signal harness + port /
  sheet entries join nets by **entry name** (order free). Altium writes `<sheet>.Harness` files itself and names
  member nets `<port>.<entry>`. No script property sets a connector's side: Convi patches it in the closed file
  (section 4.7). The harness type label is set through `GetState_HarnessConnectorType` after the entries exist.
  File units: Location/XSize/PrimaryConnectionPosition in 10 mil, DistanceFromTop in 100 mil.
* A net label never joins a port of the same name; KiCad joins a local and a global label of the same name.

### 4.6 Project file (`.PrjPcb`)
* `HierarchyMode=2` (hierarchical: net labels local, ports to sheet entries, power ports global).
* `AllowPortNetNames=1`: KiCad names a net after its hierarchical label when no local label is on it; with 0,
  sheet-to-sheet nets came out as `NetR32_2` etc. The option takes effect only after the project is re-opened.
* Local labels with the same text on different sheets are joined by Altium even in hierarchical mode.
  `NameNetsHierarchically=1` does not help; `AppendSheetNumberToLocalNets=1` does (`SDA_3` / `SDA_6`). Convi
  switches it on only when two KiCad nets share a short name.
* `ClassGenCCAutoEnabled=0`, `ClassGenCCAutoRoomEnabled=0` per document: no rooms on the first ECO.
* A `.PrjPcb` without an `[ERC Connection Matrix]` reports no pin conflicts at all; the first save in Altium
  writes Altium's defaults and new warnings appear. Convi writes the matrix: pin x pin from KiCad's
  `erc.pin_map` (all N when KiCad's `pin_to_pin` is ignored), pin x port / sheet entry not reported (KiCad never
  compares them), others Altium defaults.
* Intersheet references: `AutoCrossReferences=1`, `CrossRefSheetStyle=2`, `CrossRefLocationStyle=0`,
  `CrossRefPorts=3`, `CrossRefCrossSheets=1`.

### 4.7 Binary patching (values scripts cannot set)
* A PcbDoc / SchDoc is an OLE compound file. PCB rules are records `[uint16 kind][uint32 len][|KEY=VALUE|...\0]`
  in stream `Rules6/Data`.
* `altium/pcbdoc_patch.py` sets the creepage rule's `CHECKDISTANCE` and `APPLYTOPOLYGONPOUR` after the build
  (document closed, before the pour), keeping the stream size (the COMMENT is rewritten and padded). Altium
  opens the patched file and shows the new distance.
* `altium/schdoc_patch.py` flips `HarnessConnectorSide` (1 = entries at Location.X, 0 = mirrored) with a same-size
  edit in the `Additional` stream.
* Rule: patch only closed documents, only same-size edits, and verify that Altium opens the result.

---

## 5. Verified KiCad facts

* KiCad 10: board format 20260206, schematic 20260306. KiCad 10.99 stores footprint children in library frame
  (`FP_AFFINE_VERSION = 20260616`). Nets: KiCad 10 `(net "name")`, older `(net N "name")`.
* Pads: position footprint-relative, **angle absolute**. Footprint graphics local (rotated and moved). Texts
  (format <= 10): footprint-relative position, absolute angle. Footprint zones: absolute.
* Back-side footprints: stored local coordinates are already flipped (y negated vs. library).
* Footprint texts stay upright unless `(unlocked yes)`.
* Symbol transform (`sch_symbol.cpp`): table for 0/90/180/270, mirror composed afterwards; library coordinates
  are Y-up and inverted on load. Verified: 98.7 % of about 20 000 demo pins land on wires or labels.
* Field draw rotation and effective justification follow `sch_field.cpp`.
* Labels: a local label at angle 0 has its text right of the anchor; hierarchical/global labels at angle 0 have
  the body to the right and the wire on the left.
* Names are escaped (`{slash}`, `{colon}` ...): `unescape_name`. Overbar `~{RST}` equals Altium `R\S\T\`.
* Old-style power symbols: `#PWR` reference, hidden `power_in` pin, no `(power)` flag -> power port.
* 3D transform: `T(offset) * Rz(-rz) * Ry(-ry) * Rx(-rx) * S`; back side `Rz(t) * Ry(pi) * Rz(pi)`.
* Embedded files: footprints list names, the data lives at board level.
* KiCad 9/10 keeps bus aliases in the `.kicad_pro` (`schematic.bus_aliases`), not in the sheets.
* KiCad never connects `no_connect` pins, even stacked ones; Altium joins pins on one spot.
* Two pads with one number (L-shaped corner pads) each get their own `unconnected-(...)` net in KiCad.
* Equal-priority zones are ordered by UUID (`ZONE::HigherPriority`: larger UUID wins).
* Zone fill clearance: max(zone clearance, class clearance of the zone net, class clearance of the other item),
  and **custom rules beat the zone's own clearance**.
* Clearance resolution order: pad/footprint override > custom rules (last matching wins) > zone clearance >
  diff-pair gap > net class max(A, B) > board minimum.
* A custom `clearance` rule below the board minimum keeps its own value in DRC (proved with
  `pcbnew.WriteDRCReport` on KiCad 7.0.11; consistent with KiCad 9 issue reports). Whether net class / pad /
  zone clearances below the minimum are floored is `UNVERIFIED` (open item).
* NPTH holes in the zone filler (`zone_filler.cpp`, `knockoutPadClearance`): an NPTH hole gets only the
  physical / physical-hole / hole clearances. The copper CLEARANCE constraint applies only to pads that flash
  copper or have a plated hole.
* `min_hole_clearance` is measured from the drill edge and applies on top of copper clearance.
* `min_thickness` pruning is deflate + re-inflate by min_thickness/2: it cuts long thin channels but keeps a short
  pinch between two round clearances down to about 0.9 x min_thickness.
* Area rules (`insideArea`, `intersectsArea`, `enclosedByArea`) never shape zone fills: the filler works on
  unfilled zones.
* Thermal spoke angle: 45 deg for round pads (and custom pads with a round anchor), 90 deg otherwise, unless the
  pad sets `thermal_bridge_angle`.
* "Silkscreen clipped by solder mask" is an overlap check; `min_silk_clearance` is silk to silk only.
* Project severities (`board.design_settings.rule_severities`, `erc.rule_severities`) and DRC exclusions
  (`drc_exclusions`: `type|x|y|uuid1|uuid2`) live in the `.kicad_pro`.
* KiCad's ERC does not flag symbols off the 1.27 mm grid; Altium reports them.
* Net class `Default` in a custom-rule condition = a net in no other class.

---

## 6. Mapping decisions

### 6.1 Principle: content from KiCad, drawing language from Altium
Nets, pins, parts, BOM, values, sheet layout, page numbers, title block, rules and pours must equal KiCad.
Symbol styles, wire colour and width, line widths, ports and templates follow a hand-drawn Altium house style.
Do not copy KiCad's look (KiCad ground triangles drawn as signal ground, carets as arrows: `REJECTED`).
`check_house_style()` warns when a power port or wire leaves the house style.

* **Wire colour**: Altium's default wire, except when the KiCad project gives its net classes schematic colours.
  Then the colours are project content: wires keep the class colour and width (wire_width > 10 mil -> Medium; a
  wire's own stroke wins) and the report says so.
* **Power ports**: names with EARTH/CHASSIS/PE/FG -> earth; GND/VSS/GROUND -> power ground; others -> bar.
* **Sheet templates**: optional customer templates (`<size> - template - <customer>.SchDot`); the smallest size
  of the family that fits; indirect title-block parameters (`=TitleParameter`) are filled too. KiCad page-layout
  captions (`${COMMENTn}` "Drawn by", "Checked by" ...) map to the matching parameters.

### 6.2 Schematic
* Global labels -> power ports (exact global connectivity in a hierarchical project; option `--global-labels`).
  Hierarchical labels -> ports; sheet pins -> sheet entries; local labels -> net labels.
* A local label that has a same-named global or hierarchical label becomes a port, pointing away from its wire,
  unless it is already wired to that label.
* Repeated sheets -> one SchDoc per instance (designators match the PCB).
* Multi-unit symbols whose units use separate identical library copies are merged into one library component.
* Stacked pins: a stacked pin whose board pad has its own `unconnected-` net is isolated (hidden, no net) on the
  placed part; in the SchLib only when that holds for every part using the symbol. Stacked no-connect pins
  (except the shown one) become hidden pins without a net.
* KiCad hidden stacked pins become normal pins without visible texts; only hidden `power_in` pins stay hidden.
* Off-grid symbols are moved onto the grid together with their wire ends, labels, junctions and no-connects, only
  when the move is safe (every wire through a pin parallel to the move, nothing else on the way). Pinless symbols
  snap their origin to 50 mil.
* A power symbol whose pin only touches a PWR_FLAG (no wire) is dropped with the flag; KiCad's PWR_FLAG has no
  Altium counterpart.
* Group buses (`PREFIX{ALIAS}`) -> signal harnesses when the bus has a recognisable shape (one group label, entries
  on one side of one run); anything else stays a bus with an info line.
* Text markup `_{..}` / `^{..}` -> plain text (not in net names).
* KiCad tables -> text frames and grid lines; schematic rule areas -> dashed outline (the net class reaches the board
  through the project file).
* Not convertible: the KiCad DNP cross on the symbol (Altium shows "Not Fitted" only on the compiled view with the
  variant active).

### 6.3 PCB
* Footprints not linked to a symbol (logos, mounting holes) -> Graphical/Mechanical component kind; symbols "not
  on board" -> Mechanical; DNP -> variant "KiCad DNP".
* PCB designator = schematic designator when the KiCad board is stale (e.g. `REF**`).
* Zones -> polygons (pour order by priority, then UUID, descending); keep-outs -> keep-out regions per layer;
  teardrops -> regions; dimensions -> plain graphics; tables -> texts + lines. Barcodes and images -> reported.
* A filled copper rectangle/polygon whose stroke is below max(board minimum track width, 0.05 mm) becomes the region
  alone (logo strokes of 0.002 mm otherwise hit the Width rule thousands of times).
* Footprint signature (deduplication) ignores representation noise: rectangle vs 4-point polygon, -0.0 vs 0.0,
  reversed line ends, pad order of bottom-side copies. Real variants (e.g. different logos) stay separate.

### 6.4 3D models
* Standard KiCad library models that are not installed are downloaded from the official KiCad 3D library.
* Models behind an undefined path variable: the file name is looked up in KiCad's own library (local first).
  **Substituting a library model by name is off** (`ModelResolver.by_name = False`): vendor footprints carry
  offsets and rotations for their own STEP files, so substitutes came out at wrong heights and rotations.
  Third-party models must come from the user.
* Grey placeholder boxes are optional (`--placeholder-bodies`) and never used for test points, fiducials, mounting
  holes, BOM-excluded or DNP parts.

---

## 7. Copper pours

### 7.1 Goal
Same copper as KiCad's fill, using Altium's own pour engine (the user must be able to repour). Convi never alters
clearances to imitate KiCad.

### 7.2 Engine differences that matter
* KiCad prunes by deflate/re-inflate (`min_thickness`); Altium removes every neck below `NeckWidthThreshold`.
  Neck removal at KiCad's minimum width matches KiCad within about 1 % on most layers, but can drop a connection
  KiCad keeps through a short pinch, or drop islands that hang on a short pinch.
* Without neck removal Altium keeps channels and slivers KiCad removes, and lower-priority zones lose area.

### 7.3 Strategy (`pipeline._refine_pours`)
1. First pour at KiCad's `min_thickness`.
2. Candidates, judged in **one** re-pour + check:
   * nets with more unrouted connections than KiCad itself has (`kicad/connectivity.open_connections`) -> those
     polygons neck-free;
   * polygons pouring less than KiCad's fill by more than max(1 mm2, 0.1 %) -> neck limit 0.9 x min_thickness.
3. Keep a change only where it helped (fewer unrouted / area gain >= max(1 mm2, 1/4 of the shortfall)). A changed
   polygon that does not overshoot KiCad's area (+ max(1 mm2, 0.1 %)) keeps its change.
4. A kept neck-free polygon that overshoots KiCad is tried at half the minimum width. If unrouted connections get
   worse, first only the polygons on the layers of the lost connections go back to neck-free, then all.
5. One final re-pour + check restores what did not help. Worst case: 2 to 3 re-pours + checks.
* `repour.pas` logs `AREA<TAB>name<TAB>mm2 x 1000` per polygon after pouring, so area decisions need no board dump.

### 7.4 Comparing pours with KiCad
* Per polygon: Altium poured area (regions: MainContour minus Holes, shoelace) vs KiCad `filled_polys` of the zone.
* For geometry questions rasterise per (layer, net) at 0.05 mm, or use shapely on both outlines.
* Expected, explained differences: same-net overlap counted twice (section 4.4), thermal-relief pad interiors,
  same-net via pads in clearance holes, square corners at rectangular pads, inner-layer unconnected pads (Gerber
  option `IncludeUnconnectedMidLayerPads=False`), islands hanging only on a thermal spoke (Altium removes them).

### 7.5 Unused via pads
When every via has `remove_unused_layers` with one `keep_end_layers` setting, Convi runs `PCB:RemoveUnusedPads`
after the first pour (`unusedpads.pas`; the runner answers its dialog through the MSAA driver) and repours. Result on
an 8-layer BGA board: Altium removed 4393 via pad shapes (Convi predicted 4396 from tracks and
`zone_layer_connections`), DRC equal to KiCad's, inner pours grew to KiCad's areas.

---

## 8. Design rules

### 8.1 Ordering
Altium rules are emitted so that their priorities reproduce KiCad's resolution order (section 5): pad/footprint
overrides, custom rules (last wins), zone rules, diff-pair gaps, net classes (max(A, B) emulated by sorting class
rules by gap; classes below Default are scoped class-to-class), board minimum. Minimum limits include KiCad's DRC
tolerance (0.0005 mm).

### 8.2 Zones and holes
* Zone rules hold max(zone clearance, own class clearance) and exclude classes with a larger clearance (their class
  rule decides).
* A clearance rule whose scope the pour engine cannot match (position-based) is repeated as `<rule>_pour` naming the
  affected polygons (`InNamedPolygon`).
* NPTH pads that are exactly their hole get a pad-class hole-clearance rule (`Convi_HoleClearance_*`) above the class
  and custom rules, at the hole clearance; only custom hole/physical clearance rules raise it per pour. Plated
  classes keep the normal placement plus `_pour` variants with copper clearances.
* Only custom rules that **surely** apply raise a `_pour` gap; location-dependent ones (rule areas, courtyards) do not.
* Clearance violations against an NPTH that is exactly its hole are reported as "the KiCad board has this too"
  (KiCad's `hole_clearance` measures the same).

### 8.3 Custom rules (`.kicad_dru`, `convert/dru.py`)
* Conditions are converted to disjunctive normal form and split into one Altium rule per term (`split_dnf`; limit
  128; impossible and mirrored duplicate terms dropped). `not` over a mixed A/B term is pushed inside (De Morgan).
* `intersectsCourtyard(R)` on a pad side -> `InComponent(R)`.
* `insideArea` / `intersectsArea` on a named rule area -> rooms (a rectilinear area = union of rectangles, DRC off);
  `enclosedByArea` only for a one-rectangle area. Area terms add `and not (IsPolygon or InPolygon)` because KiCad's
  area rules never shape fills (an attempt to apply them to zones wholly inside the area emptied pours: `REJECTED`).
* `Type == 'Zone'` -> `(IsPolygon or InPolygon)`.
* `edge_clearance` -> Board Outline Clearance with the condition's scope.
* Not converted (listed in the report): terms that combine A and B in one comparison (`A.Layer == B.Layer`),
  `insideCourtyard()`, `Pad_Type`, other functions without an Altium query.

### 8.4 Severities, exclusions, special rules
* A KiCad check set to `ignore` keeps its Altium rule with DRC off (`RULE nodrc=1`). Rules whose KiCad check is a
  warning are listed; their Altium violations are named as KiCad warnings in the report.
* KiCad DRC exclusions are mapped to designator pairs; an Altium violation between such a pair is reported as
  "excluded in KiCad".
* Diff pairs without width/gap in any net class use the class track width / clearance.
* Diff-pair rules with layer conditions: per copper layer the highest-priority KiCad rule, sent per layer.
* Silk to solder mask: gap 0 (KiCad's check is overlap only).
* Thermal spoke angle: minority-angle pads get pad class `Convi_ThermalAngle_<a>` and a copy of each relief rule
  just above it.
* Mouse bites (NPTH pad = drill cutting board cutouts): outline scope `OnCopper And Not (IsPad And (PadIsPlated =
  'False'))` and ShortCircuit scopes `Not IsBoardCutoutRegion`.
* Creepage: distance written into the file (section 4.7); scopes exclude keep-outs.

---

## 9. Schematic layout details
* Net names for the PCB come from Altium's compile; verify accepts `name_<n>` where Convi appended sheet numbers.
* Equal hierarchical names on one root net: KiCad and Altium may choose different names; not a conversion error.
* BOM fields that differ only in case collide in Altium (names ignore case): the empty one is not written; the BOM
  comparison ignores name case and surrounding blanks.
* Sheet entries and sheet symbol names use the converted font (Arial), not the document font.
* SchLib descriptions come from `ki_description` (KiCad 7 symbols) or the description field.

---

## 10. Verification and development workflow

### 10.1 What every conversion checks (report: `convi_report.json`, `_convi/`)
Connectivity pin by pin, ECO preview, first-pad placement of every component, PcbDoc read-back
(`_convi/pcbcheck.json`), Altium DRC (`_convi/drc_report.txt`) with each remaining violation classified (KiCad's own
unrouted connections, KiCad warnings, KiCad exclusions, NPTH hole clearances, conversion differences), BOM, 3D body count.

### 10.2 Tools
* `py devtools/probe.py devtools/probes/xxx.pas [timeout]`: run a probe body with `L()` logging. Probes use path
  tokens, never absolute paths: `%ROOT%` (tool folder), `%OUT%` (`Outputs/convi_out/_dev`), `%WS%` (workspace).
* `py devtools/try_pcb.py <board> pcb|lib|both`, `py devtools/try_sch.py <project>`: build parts of a conversion.
* `py devtools/batch.py [demo names]`: convert KiCad demos, summary in `_dev/batch_summary.txt`.
* `py devtools/show.py <doc> [out.png] [--3d]`: open and capture (Altium window only).
* `devtools/probes/p32_crop.pas`: close-up of a schematic area (copy, delete outside a box, zoom all).
* Altium API reference: the Altium scripting documentation and published Altium script collections. Do not
  commit anything extracted from Altium's own binaries.
* KiCad reference: `kicad-cli pcb drc`, `kicad-cli sch erc`, `kicad-cli sch export pdf` from the same KiCad
  version as the project (KiCad 7 cannot read KiCad 10 files). KiCad sources for behaviour questions
  (`zone_filler.cpp`, `sch_symbol.cpp`, `sch_field.cpp`, DRC engine).
* An MCP server that drives Altium is very useful for checks (polygons, rules, DRC, ERC, BOM). Use it read-only on
  delivered conversions; experiments only on `_dev` copies.

### 10.3 Offline IR regression (before every commit that touches converters)
Generate the IR of a reference set with the old and the new code and compare after normalising paths:

```
for each project: parse board + schematic, run SchConverter / PcbConverter, write
  sch.ir, schlib.ir, pcb.ir, pcblib.ir, issues.txt   (old tree -> out/old, new tree -> out/new)
diff -r out/old out/new   (paths replaced by a placeholder first)
```

The reference set must include KiCad's bundled demo projects (13 of them: hierarchy, multi-unit, buses, 3D,
tables, KiCad 10 formats) plus every real project used for validation (section 11). Any difference must be the
intended one.

### 10.4 Regression round in Altium
Convert the reference projects again, then compare with the previous version: nets, pins, BOM, pads/tracks/arcs/vias,
ECO, DRC and ERC counts, pour areas, total time. Record the numbers in the log.

---

## 11. Validation history (anonymised)

| Board | Characteristics | Final result |
|---|---|---|
| KiCad demos (13) | hierarchy, multi-unit, buses, KiCad 6-10 formats | 11 with ECO 0, nets/pins 100 %; one with 3 pins touching wire middles in the source; one with an inline group bus `I2C{SCL, SDA}` (2 pins) |
| A | power converter, KiCad 10, 11 sheets, 373 parts, unfinished routing | 169/169 nets, 982/982 pins, ECO 0; unrouted = KiCad's 48; pours within 1 % |
| B | multilayer board with a customer sheet template | DRC = KiCad warnings only; TTF silk within 0.3 mil |
| C | 4-layer high-voltage board, 26 custom rules | nets/BOM exact, DRC = KiCad (6 fine-pitch), 9 of 11 pours within 0.11 % |
| D | first real hierarchical sheets | DRC 0, ERC matrix as KiCad |
| E | open hardware, two boards on one panel, repeated sheets, mouse bites | 162/162 nets; remaining DRC = KiCad warnings + zone slivers |
| F | 8-layer, group buses across hierarchy, BGA via pad removal | 259/259 nets, 1559/1559 pins, DRC 6 = KiCad 10 DRC exactly, harness ERC 0 errors |
| G | 8-layer SoM carrier, 644 nets, 117 zones, layer-specific diff pairs | 644/644 nets, 2517/2517 pins, BOM 544/544, ECO 0, inner planes within 0.2 % |

Typical times: 45 s to 340 s per project depending on size (Python about 10 s; the rest is Altium).

---

## 12. Known limitations (do not report as new bugs)
* Pour engine: narrow strips KiCad keeps between fine-pitch pads (0.4 mm pitch) may stay unrouted in Altium.
* Square pour corners at rectangular pads; dead-copper removal of islands hanging on a thermal spoke.
* A zone and stub track on an `unconnected-` net of a pin: Altium has no net for it -> dead copper and a short.
  Needs a schematic net on that pin (manual).
* A few single pours differ by more than 1 % without a found cause (thermal relief on some bottom-side SMD pads
  appears covered by the pour although the relief rule names the polygon). Open.
* Port cross-reference font (Times New Roman 10) is fixed by Altium.
* Net ties with two pad groups: Altium warns "isolated copper".
* Custom rules listed in section 8.3 as not converted.
* Floating directives from schematic rule-area net-class flags (an Altium blanket would be exact; blanket API untested).
* PcbLib rule checks run through an automation bridge may report "origin outside the footprint" on every footprint
  (the bridge does not subtract the library origin); placement on the board is exact.

---

## 13. Open items
1. Blind acceptance test on a KiCad project Convi has never seen, checked fully in Altium.
2. KiCad 10 reference DRC (`kicad-cli pcb drc`): confirm whether net class / pad / zone clearances below the board
   minimum are floored (`rules.py` still floors them; custom rules no longer).
3. No automated test for the Altium-side steps (repour, DRC, compare): regressions need manual runs.
4. Not yet exercised: blind/buried vias, flex/rigid-flex, variant BOMs beyond DNP, hatched pours, KiCad 10 schematic
   variants, PCB barcodes and images.
5. Known limits worth revisiting: section 12, plus the run-time cost of the half-neck retries on boards with many
   fine-pitch slivers (+78 s on one board).
6. KiCad worksheet (`.kicad_wks`) conversion for projects without a customer template (Altium's standard title
   block shows no Title/Revision).

---

## 14. Log

New entries go on top. Keep entries short; move lasting facts into sections 4 to 12.

### 2026-11-20 Release R1
* State: all reference boards converted and checked in Altium. ERC only "no driving source"; connectivity, BOM and
  ECO identical to KiCad on every board; DRC differences explained (section 12).
* Repository prepared for open source: comments, messages and tests in English and anonymous; templates folder
  discovery made generic (`__Templates`, `__<Name>Templates`, `CONVI_TEMPLATES`); no change to conversion output
  (offline IR of 13 KiCad demos byte-identical before and after).
