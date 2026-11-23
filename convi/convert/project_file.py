"""Write the Altium .PrjPcb project file (INI format)."""
from __future__ import annotations

from pathlib import Path

# Altium's ERC connection matrix (Project Options > Connection Matrix), rows/columns in this order:
# 0 Input, 1 IO, 2 Output, 3 Open Collector, 4 Passive, 5 HiZ, 6 Open Emitter, 7 Power pin,
# 8 Input, 9 Output, 10 Bidirectional, 11 Unspecified port, 12-15 the same sheet entries, 16 Unconnected.
# Altium's defaults (as Altium writes them).  A PrjPcb without the section reports no pin conflicts at
# all, and Altium writes these defaults the first time the user saves the project: both differ from KiCad.
ALTIUM_ERC_DEFAULT = [
    "NNNNNNNNNNNWNNNWW", "NNWNNNNWWWNWNWNWN", "NWEENEEEENEWNEEWN", "NNENNNWEENNWNENWN",
    "NNNNNNNNNNNNNNNNN", "NNENNNNEENNWNENWN", "NNEWNNWEENNWNENWN", "NWEENEENEEENNEENN",
    "NWEENEEEENEWNEEWW", "NWNNNNNENNEWNNEWN", "NNENNNNEEENWNENWN", "WWWWNWWNWWWNWWWNN",
    "NNNNNNNNNNNWNNNWW", "NWEENEEEENEWNEEWW", "NNENNNNEEENWNENWW", "WWWWNWWNWWWNWWWNW",
    "WNNNNNNNWNNNWWWWN"]
# KiCad pin_map order: input, output, bidirectional, tri_state, passive, free, unspecified, power_in,
# power_out, open_collector, open_emitter, no_connect.  Altium pin type -> KiCad index (Convi maps
# power_in and power_out to Altium Power: power_in is used, so a power_out conflict is not reported).
_ALT_TO_KICAD = [0, 2, 1, 9, 4, 3, 10, 7]
KICAD_PIN_MAP_DEFAULT = [
    [0, 0, 0, 0, 0, 0, 1, 0, 0, 0, 0, 2], [0, 2, 0, 1, 0, 0, 1, 0, 2, 2, 2, 2],
    [0, 0, 0, 0, 0, 0, 1, 0, 1, 0, 1, 2], [0, 1, 0, 0, 0, 0, 1, 1, 2, 1, 1, 2],
    [0, 0, 0, 0, 0, 0, 1, 0, 0, 0, 0, 2], [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 2],
    [1, 1, 1, 1, 1, 0, 1, 1, 1, 1, 1, 2], [0, 0, 0, 1, 0, 0, 1, 0, 0, 0, 0, 2],
    [0, 2, 1, 2, 0, 0, 1, 0, 2, 2, 2, 2], [0, 2, 0, 1, 0, 0, 1, 0, 2, 0, 0, 2],
    [0, 2, 1, 1, 0, 0, 1, 0, 2, 0, 0, 2], [2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2]]


def erc_matrix(pin_map: list | None) -> list[str]:
    """Altium connection matrix that reports what KiCad's ERC reports.

    Pin against pin: KiCad's pin_map (0 OK, 1 warning, 2 error).  Pin against port / sheet entry: not
    reported, as KiCad never compares a pin type with a label's direction.  Ports, sheet entries and the
    Unconnected row/column keep Altium's defaults (KiCad checks dangling labels and pins itself).
    """
    pm = pin_map if (isinstance(pin_map, list) and len(pin_map) == 12
                     and all(isinstance(r, list) and len(r) == 12 for r in pin_map)) else KICAD_PIN_MAP_DEFAULT
    rows = [list(r) for r in ALTIUM_ERC_DEFAULT]
    for i in range(8):
        for j in range(8):
            v = pm[_ALT_TO_KICAD[i]][_ALT_TO_KICAD[j]]
            rows[i][j] = "NWE"[v] if v in (0, 1, 2) else "N"
        for j in range(8, 16):
            rows[i][j] = rows[j][i] = "N"
    return ["".join(r) for r in rows]


def kicad_pin_map(pro: dict | None) -> list | None:
    """The pin conflict table KiCad's ERC uses: the project's pin_map, or all OK when the project sets
    'Conflict problem between pins' (pin_to_pin) to ignore."""
    erc = (pro or {}).get("erc") or {}
    if (erc.get("rule_severities") or {}).get("pin_to_pin") == "ignore":
        return [[0] * 12 for _ in range(12)]
    return erc.get("pin_map")


def write_prjpcb(path: Path, schdocs: list[str], pcbdoc: str | None, schlib: str | None, pcblib: str | None,
                 parameters: dict | None = None, variants: list | None = None, hierarchy_mode: int = 2,
                 allow_port_net_names: bool = False, cross_refs: bool = False, erc_pin_map: list | None = None,
                 append_sheet_numbers: bool = False, extra_docs: list[str] | None = None):
    """schdocs: file names, top sheet first.  variants: [(name, [(designator, uid_path)...])].
    extra_docs: further project documents (the OutJob and Draftsman drawing of the manufacturing outputs)."""
    lines = [
        "[Design]",
        "Version=1.0",
        # net identifier scope: 0 Automatic, 1 Flat, 2 Hierarchical (chosen by SchConverter)
        f"HierarchyMode={int(hierarchy_mode)}",
        "ChannelRoomNamingStyle=0",
        "ReleasesFolder=",
        "ChannelDesignatorFormatString=$Component_$RoomName",
        "ChannelRoomLevelSeperator=_",
        "OpenOutputs=1",
        "ArchiveProject=0",
        "TimestampOutput=0",
        "SeparateFolders=0",
        "TemplateLocationPath=",
        "PinSwapBy_Netlabel=1",
        "PinSwapBy_Pin=1",
        f"AllowPortNetNames={1 if allow_port_net_names else 0}",
        "AllowSheetEntryNetNames=0",
        f"AppendSheetNumberToLocalNets={1 if append_sheet_numbers else 0}",
        "NetlistSinglePinNets=0",
        "DefaultConfiguration=Sources",
        "UserID=0xFFFFFFFF",
        "DefaultPcbProtel=1",
        "DefaultPcbPcad=0",
        "ReorderDocumentsOnCompile=1",
        "NameNetsHierarchically=0",
        "PowerPortNamesTakePriority=0",
        "PushECOToAnnotationFile=1",
        "DItemRevisionGUID=",
        "ReportSuppressedErrorsInMessages=0",
        "OutputPath=Project Outputs",
        "LogFolderPath=",
        "ManagedProjectGUID=",
        "IncludeDesignInRelease=0",
        # KiCad intersheet references ('[4,7]' after a global label) -> Altium automatic cross
        # references on ports: sheet numbers, no zone (Sheet Style 0 None 1 Name 2 Number; Location
        # Style 0 None 1 Zone 2 X,Y; Ports 0 off 1 sheet entry 2 ports 3 both)
        f"AutoCrossReferences={1 if cross_refs else 0}",
        "CrossRefSheetStyle=2",
        "CrossRefLocationStyle=0",
        f"CrossRefPorts={3 if cross_refs else 0}",
        f"CrossRefCrossSheets={1 if cross_refs else 0}",
        "CrossRefSheetEntries=0",
        "CrossRefFollowFromMainSettings=0",
        "",
        "[Preferences]",
        "PrefsVaultGUID=",
        "PrefsRevisionGUID=",
        "",
    ]
    docs = list(schdocs) + ([pcbdoc] if pcbdoc else []) + ([schlib] if schlib else []) + ([pcblib] if pcblib else [])
    docs += list(extra_docs or [])
    for i, d in enumerate(docs, 1):
        lines += [
            f"[Document{i}]",
            f"DocumentPath={d}",
            "AnnotationEnabled=1",
            "AnnotateStartValue=1",
            "AnnotationIndexControlEnabled=0",
            "AnnotateSuffix=",
            "AnnotateScope=All",
            "AnnotateOrder=-1",
            "DoLibraryUpdate=1",
            "DoDatabaseUpdate=1",
            # KiCad has no per-sheet rooms/component classes: keep the first ECO clean
            "ClassGenCCAutoEnabled=0",
            "ClassGenCCAutoRoomEnabled=0",
            "ClassGenNCAutoScope=None",
            "DItemRevisionGUID=",
            "GenerateClassCluster=0",
            "",
        ]
    lines += [
        "[Configuration1]",
        "Name=Sources",
        "ParameterCount=0",
        "ConstraintFileCount=0",
        "ReleaseItemId=",
        "Variant=[No Variations]",
        "OutputJobsCount=0",
        "ContentTypeGUID=CB6F2064-E317-11DF-B822-12313F0024A2",
        "ConfigurationType=Source",
        "",
        "[PrjClassGen]",
        "CompClassManualEnabled=0",
        "CompClassManualRoomEnabled=0",
        "NetClassAutoBusEnabled=0",
        "NetClassAutoCompEnabled=0",
        "NetClassAutoNamedHarnessEnabled=0",
        "NetClassManualEnabled=1",
        "NetClassSeparateForBusSections=0",
        "",
    ]
    for i, (name, members) in enumerate(variants or [], 1):
        lines += [f"[ProjectVariant{i}]", f"Description={name}", "AllowFabrication=0", "ParameterCount=0",
                  f"VariationCount={len(members)}"]
        for j, (des, uid) in enumerate(members, 1):
            lines.append(f"Variation{j}=Designator={des}|UniqueId={uid}|Kind=1|AlternatePart=")
        lines.append("")
    lines.append("[ERC Connection Matrix]")
    lines += [f"L{i}={row}" for i, row in enumerate(erc_matrix(erc_pin_map), 1)]
    lines.append("")
    for i, (k, v) in enumerate((parameters or {}).items(), 1):
        lines += [f"[Parameter{i}]", f"Name={k}", f"Value={v}", ""]
    Path(path).write_text("\r\n".join(lines) + "\r\n", encoding="utf-8")
