"""Dev: build the built-in manufacturing-output templates (convi/templates/outputs).

    py devtools/make_builtin_templates.py <conversion folder of CONVI_TEMPLATE> [--forbid WORD ...]

Input: a Convi conversion of the synthetic template board (devtools/template_board.py) made WITH
manufacturing outputs, so Altium has refreshed the Draftsman drawing with that board's data. The folder
holds CONVI_TEMPLATE.OutJob and CONVI_TEMPLATE.PCBDwf.

Output: convi/templates/outputs/CONVI_TEMPLATE.OutJob and CONVI_TEMPLATE.DwfDot with every absolute path
replaced by a neutral one (C:\\Convi\\CONVI_TEMPLATE\\...). The script then refuses to write anything that
still holds another absolute path, a word given with --forbid (names of real products, customers or people
that must not leak; keep that list outside this repository), or a part/designator the template board does
not have.
"""
import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from convi.outputs import dwf_read, dwf_write, template_dir_of  # noqa: E402

NAME = "CONVI_TEMPLATE"
NEUTRAL_DIR = "C:\\Convi\\" + NAME + "\\"
# designators and values of the synthetic board (devtools/template_board.py)
BOARD_REFS = {"U1", "J1", "R1", "R2", "R3", "R4", "R5", "C1", "C2", "C3", "D1", "TP1", "H1", "H2", "H3", "H4"}
DEST = ROOT / "convi" / "templates" / "outputs"


def neutral_outjob(text: str) -> str:
    old_dir = template_dir_of(text, NAME)
    if old_dir:
        text = text.replace(old_dir, NEUTRAL_DIR)
    return text


# document parameters Altium fills from the computer that published the drawing
MACHINE_PARAMETERS = ("ComputerName", "UserName", "Author", "ModifiedBy", "CreatedBy")


# spelling fixes in the template's own notes
TEXT_FIXES = {"(Standart TG)": "(Standard TG)"}


def neutral_dwf(xml: str) -> str:
    for old, new in TEXT_FIXES.items():
        xml = xml.replace(old, new)
    for name in MACHINE_PARAMETERS:
        xml = re.sub(r"(<Name>%s</Name><Value>)[^<]*(</Value>)" % name, r"\1\2", xml)
    for p in sorted(set(re.findall(r">([A-Za-z]:\\[^<>]*?)<", xml)), key=len, reverse=True):
        i = p.find(NAME + "\\")
        tail = p[i + len(NAME) + 1:] if i >= 0 else Path(p.replace("\\", "/")).name
        xml = xml.replace(">" + p + "<", ">" + NEUTRAL_DIR + tail + "<")
    return xml


def check(label: str, text: str, forbid: list[str]) -> list[str]:
    problems = []
    for p in sorted(set(re.findall(r"[A-Za-z]:\\[^|<>\r\n\"]*", text))):
        if not p.upper().startswith(NEUTRAL_DIR.upper()[:-1]) and not p.upper().startswith("C:\\PROGRAM FILES"):
            problems.append(f"{label}: absolute path {p!r}")
    # whole words, outside embedded images (base64 data holds any letter sequence)
    plain = re.sub(r"<(\w*Bitmap\w*)>[^<]*</\1>", "", text)
    for w in forbid:
        n = len(re.findall(r"(?<![A-Za-z0-9])" + re.escape(w) + r"(?![A-Za-z0-9])", plain, re.I))
        if n:
            problems.append(f"{label}: {n} x forbidden word #{forbid.index(w) + 1}")
    refs = {r.strip() for r in re.findall(r"<ComponentDesignator>([^<]*)</ComponentDesignator>", text)}
    extra = sorted(r for r in refs if r and r not in BOARD_REFS)
    if extra:
        problems.append(f"{label}: designators not on the template board: {extra[:20]}")
    return problems


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("conversion", type=Path)
    ap.add_argument("--forbid", nargs="*", default=[])
    a = ap.parse_args()
    raw = (a.conversion / f"{NAME}.OutJob").read_bytes()
    bom = raw.startswith(b"\xef\xbb\xbf")
    oj = raw.decode("utf-8-sig")
    xml = dwf_read(a.conversion / f"{NAME}.PCBDwf")
    oj, xml = neutral_outjob(oj), neutral_dwf(xml)
    problems = check("OutJob", oj, a.forbid) + check("Draftsman", xml, a.forbid)
    values = sorted(set(re.findall(r"<Value>([^<]{1,60})</Value>", xml)))
    print("Text values in the Draftsman drawing (review them):", values)
    if problems:
        print("NOT written:")
        print("\n".join("  " + p for p in problems))
        return 1
    DEST.mkdir(parents=True, exist_ok=True)
    (DEST / f"{NAME}.OutJob").write_bytes((b"\xef\xbb\xbf" if bom else b"") + oj.encode("utf-8"))
    dwf_write(DEST / f"{NAME}.DwfDot", xml)
    print("written:", DEST / f"{NAME}.OutJob", DEST / f"{NAME}.DwfDot")
    return 0


if __name__ == "__main__":
    sys.exit(main())
