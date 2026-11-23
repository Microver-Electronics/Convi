"""Offline tests of the manufacturing outputs module (no Altium, no house templates needed)."""
import re
import sys

import pytest
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from convi import outputs as O  # noqa: E402

TPL = "\r\n".join([
    "[OutputGroup1]",
    "Name=OLD_BOARD.OutJob",
    "VariantName=[No Variations]",
    "OutputMedium1=GERBER",
    "OutputMedium1_Type=GeneratedFiles",
    "OutputMedium2=Assembly Drawings",
    "OutputMedium2_Type=Publish",
    "OutputMedium3=Video",
    "OutputMedium3_Type=Multimedia",
    "OutputType1=Gerber",
    "OutputName1=Gerber Files",
    "OutputDocumentPath1=OLD_BOARD.PcbDoc",
    "OutputVariantName1=",
    "OutputEnabled1_OutputMedium1=1",
    "OutputEnabled1_OutputMedium2=0",
    "Configuration1_Name1=OutputConfigurationParameter1",
    "Configuration1_Item1=Plot.Set=SerializeLayerHash.Version~2,ClassName~TPlotLayerStateArray,16777217~1,"
    "16908289~1,16908290~1,16908293~1,16908296~1|PlotUsedDrillDrawingLayerPairs=False|"
    "PlotDrillDrawingLayerPair0_LowLayer=Top Layer|DocumentPath=C:\\Old\\OLD_BOARD\\OLD_BOARD.PcbDoc",
    "OutputType2=Assembly",
    "OutputName2=Assembly Drawings",
    "OutputDocumentPath2=OLD_BOARD.PcbDoc",
    "OutputVariantName2=",
    "OutputEnabled2_OutputMedium1=0",
    "OutputEnabled2_OutputMedium2=1",
    "Configuration2_Name1=OutputConfigurationParameter1",
    "Configuration2_Item1=Record=PcbPrintView|DocumentPath=C:\\Old\\OLD_BOARD\\OLD_BOARD.PcbDoc",
    "Configuration2_Name2=OutputConfigurationParameter2",
    "Configuration2_Item2=Name=Top|Record=PcbPrintOut",
    "Configuration2_Name3=OutputConfigurationParameter3",
    "Configuration2_Item3=Layer=TopOverlay|Record=PcbPrintLayer",
    "Configuration2_Name4=OutputConfigurationParameter4",
    "Configuration2_Item4=Layer=Mechanical5|Record=PcbPrintLayer",
    "Configuration2_Name5=OutputConfigurationParameter5",
    "Configuration2_Item5=Layer=Mechanical8|Record=PcbPrintLayer",
    "Configuration2_Name6=OutputConfigurationParameter6",
    "Configuration2_Item6=Layer=Mechanical4|Record=PcbPrintLayer",
    "OpenOutput2=1",
    "PromptOverwrite2=1",
    "",
    "[PublishSettings]",
    "OutputFilePath2=C:\\Old\\OLD_BOARD\\Project Outputs for OLD_BOARD\\OLD_BOARD.PDF",
])

TEMPLATE_LAYERS = {1: "Mechanical 1", 2: "Top 3D Body", 4: "Top Designator", 5: "Top Assembly",
                   8: "Top Component Outline", 21: "Bottom Courtyard"}


@dataclass
class ML:
    number: int
    name: str
    kind: str


BOARD = [ML(1, "Board Outline (Edge.Cuts)", "Board"), ML(2, "Assembly Top (F.Fab)", "AssemblyTop"),
         ML(5, "Courtyard Bottom (B.CrtYd)", "CourtyardBottom"), ML(13, "3D Body Top", "3DBodyTop")]


def test_layer_roles_and_map():
    assert O.layer_role("Top Assembly", 5) == ("assembly", "top")
    assert O.layer_role("Bottom Courtyard", 21) == ("courtyard", "bottom")
    assert O.layer_role("Mechanical 1", 1) == ("board", "")
    assert O.layer_role("Mechanical 13", 13) is None
    mm = O.mech_map_for(TEMPLATE_LAYERS, O.board_roles(BOARD))
    assert mm == {1: 1, 2: 13, 4: None, 5: 2, 8: 2, 21: 5}


def test_retarget_outjob():
    mm = O.mech_map_for(TEMPLATE_LAYERS, O.board_roles(BOARD))
    out = O.retarget_outjob(TPL, "OLD_BOARD", "NEW", Path(r"C:\Out\NEW"), "DNP", mm)
    assert "OLD_BOARD" not in out and "C:\\Old" not in out
    assert "DocumentPath=C:\\Out\\NEW\\NEW.PcbDoc" in out
    assert "VariantName=DNP" in out and "OutputVariantName1=DNP" in out and "OutputVariantName2=DNP" in out
    assert "OpenOutput2=0" in out and "PromptOverwrite2=0" in out
    plot = next(ln for ln in out.split("\r\n") if ln.startswith("Configuration1_Item1="))
    # M1 -> 1, M2 -> 13, M5 -> 2, M8 -> 2 (printed once), M4 dropped; non-mechanical ids kept
    assert "16777217~1,16908289~1,16908301~1,16908290~1|" in plot
    assert "PlotUsedDrillDrawingLayerPairs=True" in plot
    prints = [ln for ln in out.split("\r\n") if ln.startswith("Configuration2_Item")]
    assert [p.split("=", 1)[1] for p in prints] == [
        "Record=PcbPrintView|DocumentPath=C:\\Out\\NEW\\NEW.PcbDoc", "Name=Top|Record=PcbPrintOut",
        "Layer=TopOverlay|Record=PcbPrintLayer", "Layer=Mechanical2|Record=PcbPrintLayer"]
    names = [ln for ln in out.split("\r\n") if ln.startswith("Configuration2_Name")]
    assert names[-1] == "Configuration2_Name4=OutputConfigurationParameter4"


def test_inner_layers_follow_the_board():
    ids = ["ClassName~X", "16777217~1", "16777218~1", "16777219~1", "16842751~1", "16908289~1"]
    assert O._plot_inner_layers(ids, 8) == ["ClassName~X", "16777217~1", "16777218~1", "16777219~1",
                                            "16777220~1", "16777221~1", "16777222~1", "16777223~1",
                                            "16842751~1", "16908289~1"]
    assert O._plot_inner_layers(ids, 2) == ["ClassName~X", "16777217~1", "16842751~1", "16908289~1"]


def test_containers():
    cs = O.read_containers(TPL)
    assert [(c.name, c.kind) for c in cs] == [("GERBER", "generate"), ("Assembly Drawings", "publish")]
    assert cs[0].label == "GERBER (Gerber)"


def test_odb_layers():
    parts = ["PlotMechanical1Plot=True", "PlotMechanical4Plot=True", "PlotMechanical9Plot=False",
             "Plot_V7_1=True", "Plot_V7_1_name=Mechanical Layer 21", "PlotBackDrillingPairs=False"]
    res = O._retarget_odb(parts, {1: 1, 4: None, 21: 5, 9: None})
    assert res == ["PlotMechanical1Plot=True", "PlotMechanical4Plot=False", "PlotMechanical9Plot=False",
                   "PlotBackDrillingPairs=False"]
    res = O._retarget_odb(parts, {1: 1, 21: 17})
    assert "Plot_V7_1_name=Mechanical Layer 17" in res


def test_lz4_fallback_roundtrip(tmp_path):
    data = ("<Document>" + "abc" * 50000 + "</Document>").encode()
    blk = O._lz4_block_literals(data)
    assert O._lz4_block_decompress(blk, len(data)) == data
    # a block with a match: literals 'abcd', then 8 bytes copied from offset 4
    blk = bytes([0x44]) + b"abcd" + bytes([4, 0]) + bytes([0x00])
    assert O._lz4_block_decompress(blk, 12) == b"abcdabcdabcd"
    p = tmp_path / "x.PCBDwf"
    O.dwf_write(p, "<a>é</a>" * 1000)
    assert O.dwf_read(p) == "<a>é</a>" * 1000


PAGE_END = ("</Items><Margin/><Size><Height>1122.5</Height><Width>1587.4</Width></Size>"
            "<StandardSizeName>A3</StandardSizeName></Page>")
DWF = ('<Document><Pages><Page><Items><Item i:type="Text"><Id>6</Id><ClipToBounds>true</ClipToBounds>'
       '<Rect><Height>70</Height><Width>800</Width><X>400</X><Y>950</Y></Rect><Text>OLD_BOARD</Text></Item>'
       '<Item i:type="DrillViewView"><Id>5</Id><BoardSourceName>C:\\Old\\OLD_BOARD\\OLD_BOARD.PcbDoc'
       '</BoardSourceName><Location><X>100</X><Y>100</Y></Location><Scale><Denominator>1</Denominator>'
       '<Numenator>2</Numenator></Scale><ViewSide>Top</ViewSide></Item>'
       '<Item i:type="DrillTableBoardView"><Id>7</Id><Location><X>1000</X><Y>180</Y></Location>'
       '<Size><Height>180</Height><Width>300</Width></Size></Item>' + PAGE_END +
       '<Page><Items><Item i:type="BoardRealisticViewData"><Id>8</Id><Location><X>20</X><Y>40</Y></Location>'
       '<Scale><Denominator>1</Denominator><Numenator>1</Numenator></Scale><VerticalAlignment>2</VerticalAlignment>'
       '<Height>900</Height><Settings><Width>2362</Width></Settings><Width>750</Width></Item>' + PAGE_END +
       '</Pages><SourceDocumentName>D:\\Other\\OLD_BOARD.PcbDoc</SourceDocumentName></Document>')


def test_retarget_dwf():
    x = O.retarget_dwf(DWF, Path("C:/Out/NEW/NEW.PcbDoc"), "OLD_BOARD", "NEW", (80, 100), (40, 40),
                       new_drill_rows=0)
    assert "OLD_BOARD" not in x
    assert "<Text>NEW</Text>" in x and "<ClipToBounds>false</ClipToBounds>" in x
    assert x.count(str(Path("C:/Out/NEW/NEW.PcbDoc"))) == 2
    # the drill view may use the sheet left of the drill table and under the title: 40 mm at 5:1 = 756 px
    assert "<Numenator>5</Numenator>" in x
    # the realistic view becomes square (the board is), centred in its part of the sheet
    w = float(re.search(r"</Settings><Width>([\d.]+)</Width>", x).group(1))
    h = float(re.search(r"</VerticalAlignment><Height>([\d.]+)</Height>", x).group(1))
    assert abs(w - h) < 1e-6


def test_drill_table_keeps_its_top():
    # template: 2 hole rows in a 180 px table (45 px a line); the new board has 4 rows: 270 px, top kept
    tpl = DWF.replace("<Document>", "<Document><DrillTableInformation>" + "".join(
        f"<LayerStackHole><HoleSize>{s}</HoleSize><Lenght>0</Lenght><Plated>true</Plated></LayerStackHole>"
        for s in (1.0, 2.0)) + "</DrillTableInformation>")
    x = O.retarget_dwf(tpl, Path("C:/N.PcbDoc"), "OLD_BOARD", "NEW", (80, 100), (40, 40), new_drill_rows=4)
    assert "<Location><X>1000</X><Y>90.0</Y></Location>" in x


def test_one_layer_view_per_copper_layer():
    sheet = lambda ids, ls: "<Page><Id>%d</Id><Items>" % (ids[0] + 100) + "".join(
        f'<Item i:type="BoardFabricationViewData"><Id>{i}</Id><Location><X>{100 + 700 * k}</X><Y>200</Y>'
        f'</Location><Scale><Denominator>1</Denominator><Numenator>2</Numenator></Scale><LayerV6Id>{l}'
        f'</LayerV6Id><LayerV7Id>{l}</LayerV7Id><ShowedLayers><BoardLayerDisplayData><V7LayerId>{l}'
        f'</V7LayerId><Visible>true</Visible></BoardLayerDisplayData></ShowedLayers></Item>'
        for k, (i, l) in enumerate(zip(ids, ls))) + PAGE_END
    doc = "<Document><Pages>" + sheet([10, 11], [1, 2]) + sheet([12, 13], [3, 32]) + "</Pages></Document>"
    x = O.expand_layer_pages(doc, 8)
    pages = re.findall(r"<Page>.*?</Page>", x, re.S)
    assert [re.findall(r"<LayerV7Id>(\d+)</LayerV7Id>", p) for p in pages] == [
        ["1", "2"], ["3", "4"], ["5", "6"], ["7", "32"]]
    ids = re.findall(r"<(?:Page|Item i:type=\"\w+\")><Id>(\d+)</Id>", x)
    assert len(ids) == len(set(ids)) == 12
    # the layer's own entry is the visible one
    assert re.search(r"<LayerV7Id>4</LayerV7Id><ShowedLayers><BoardLayerDisplayData><V7LayerId>4</V7LayerId>"
                     r"<Visible>true</Visible>", x)
    assert len(re.findall(r"<Page>", O.expand_layer_pages(doc, 2))) == 1


def test_template_choice(tmp_path, monkeypatch):
    mine = tmp_path / "mine"
    mine.mkdir()
    (mine / "A.OutJob").write_text("[OutputJobFile]\n", encoding="utf-8")
    (mine / "A.DwfDot").write_bytes(b"")
    t = O.find_output_templates(tmp_path, choice=str(mine))
    assert (t.outjob.name, t.draftsman.name, t.builtin) == ("A.OutJob", "A.DwfDot", False)
    assert O.find_output_templates(tmp_path, choice=str(mine / "A.OutJob")).outjob.name == "A.OutJob"
    monkeypatch.setenv(O.OUTPUT_TEMPLATES_ENV, str(mine))
    t = O.find_output_templates(tmp_path)
    assert t.outjob == mine / "A.OutJob" and not t.builtin and t.label == str(mine)
    assert O.find_output_templates(tmp_path, choice=str(tmp_path / "none")).outjob is None


def test_builtin_templates_are_neutral():
    b = O.builtin_templates()
    if b.outjob is None:
        pytest.skip("no built-in templates in this tree")
    assert b.builtin and b.label == "built-in" and b.draftsman is not None
    assert O.find_output_templates(choice="builtin").outjob == b.outjob
    text = b.outjob.read_bytes().decode("utf-8-sig")
    xml = O.dwf_read(b.draftsman)
    for doc in (text, xml):
        for p in set(re.findall(r"[A-Za-z]:\\[^|<>\r\n\"]*", doc)):
            assert p.upper().startswith("C:\\CONVI\\CONVI_TEMPLATE") or p.upper().startswith("C:\\PROGRAM FILES"), p
    refs = {r.strip() for r in re.findall(r"<ComponentDesignator>([^<]*)</ComponentDesignator>", xml)} - {""}
    assert refs <= {"U1", "J1", "R1", "R2", "R3", "R4", "R5", "C1", "C2", "C3", "D1", "TP1", "H1", "H2", "H3", "H4"}
    names = [c.name for c in O.list_template_containers(b)]
    assert len(names) >= 10 and "DRAFTSMAN" in names and "GERBER" in names
