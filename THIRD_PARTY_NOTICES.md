# Third-party notices

Convi's own source code is released under the MIT License (see `LICENSE`). This repository contains no
third-party source code, libraries, models or data files. The items below are used at run time only.

## Python dependencies (installed by pip, not distributed with Convi)

| Package | Purpose | License |
|---|---|---|
| [zstandard](https://pypi.org/project/zstandard/) | reading files embedded in KiCad 9/10 boards | BSD-3-Clause |
| [Pillow](https://pypi.org/project/pillow/) | schematic images, window captures | MIT-CMU (HPND) |
| [pytest](https://pypi.org/project/pytest/) (development only) | offline tests | MIT |

## KiCad library 3D models

When a project refers to a model of KiCad's standard library that is not installed locally, Convi can
download that model from the official KiCad 3D model library into a local cache (`%LOCALAPPDATA%\Convi`)
and embed it in the converted design. Use `--offline` to turn this off.

The KiCad libraries are licensed under CC-BY-SA 4.0 with the KiCad libraries exception: using the library
data in a design does not by itself require the design or files generated from it to be licensed under
CC-BY-SA 4.0. Redistributing the libraries, or parts of them, as a collection is not covered by the
exception. Convi does not redistribute them. See https://www.kicad.org/libraries/license/.

## Altium Designer

Convi drives a licensed, locally installed copy of Altium Designer through its documented scripting
interface. Every Altium file Convi produces is written by Altium itself. Convi contains no Altium code, and
no data extracted from Altium's binaries; such dumps must not be added to this repository.

## Trademarks

Altium and Altium Designer are trademarks of their owner. KiCad is a trademark of its owner. All other
product names are trademarks of their respective owners. They are used here only to describe
interoperability. Convi is not affiliated with, endorsed by or sponsored by any of them.
