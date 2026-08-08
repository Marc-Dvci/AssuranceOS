"""A dependency-free reader and writer for the workbook subset audit evidence uses.

Control owners send spreadsheets. An access-review campaign arrives as a
workbook, a risk register is a workbook, and a payment run is exported from the
ledger as a workbook. A platform that can only ingest JSON is a platform that
asks the auditee to do the conversion, and the converted extract is no longer
the artefact the owner attested to.

So the vault ingests the workbook itself and this module reads it. The subset is
deliberate and narrow: ``.xlsx`` with a header row per sheet, string and numeric
cells, shared or inline strings, no formulas, no styles, no dates as serial
numbers. That covers an exported register and refuses anything it would have to
guess about — :func:`read_workbook` raises rather than returning a value it
inferred. Formulas are the important exclusion: a cached formula result is the
last value some other program computed, not something this code can verify, so a
formula cell is an error rather than a silently trusted number.

Writing exists for the same reason reading does: the synthetic corpus has to
contain real workbooks, not JSON wearing an ``.xlsx`` extension.
"""

from __future__ import annotations

import re
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence
from xml.etree import ElementTree

_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
_R_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_CELL_REFERENCE = re.compile(r"^([A-Z]+)(\d+)$")


class WorkbookError(ValueError):
    """The workbook is outside the subset this module will read."""


@dataclass(frozen=True)
class Sheet:
    """One sheet, normalised to a header row and dictionary rows."""

    name: str
    columns: tuple[str, ...]
    rows: tuple[dict[str, Any], ...]

    def column(self, name: str) -> list[Any]:
        if name not in self.columns:
            raise WorkbookError(f"sheet {self.name!r} has no column {name!r}")
        return [row.get(name) for row in self.rows]


@dataclass(frozen=True)
class Workbook:
    sheets: tuple[Sheet, ...]

    def sheet(self, name: str) -> Sheet:
        for sheet in self.sheets:
            if sheet.name == name:
                return sheet
        available = ", ".join(item.name for item in self.sheets)
        raise WorkbookError(f"workbook has no sheet {name!r}; sheets are: {available}")

    @property
    def sheet_names(self) -> tuple[str, ...]:
        return tuple(sheet.name for sheet in self.sheets)


# -- reading -------------------------------------------------------------------


def _column_index(reference: str) -> int:
    match = _CELL_REFERENCE.match(reference)
    if match is None:
        raise WorkbookError(f"unreadable cell reference {reference!r}")
    letters = match.group(1)
    index = 0
    for char in letters:
        index = index * 26 + (ord(char) - ord("A") + 1)
    return index - 1


def _shared_strings(archive: zipfile.ZipFile) -> list[str]:
    try:
        raw = archive.read("xl/sharedStrings.xml")
    except KeyError:
        return []
    root = ElementTree.fromstring(raw)
    values: list[str] = []
    for item in root.findall(f"{{{_NS}}}si"):
        values.append("".join(node.text or "" for node in item.iter(f"{{{_NS}}}t")))
    return values


def _cell_value(cell: ElementTree.Element, shared: Sequence[str]) -> Any:
    if cell.find(f"{{{_NS}}}f") is not None:
        raise WorkbookError(
            "the workbook contains a formula cell; a cached formula result is another "
            "program's output and is not admissible without recomputation"
        )
    cell_type = cell.get("t", "n")
    if cell_type == "inlineStr":
        node = cell.find(f"{{{_NS}}}is")
        return "".join(part.text or "" for part in node.iter(f"{{{_NS}}}t")) if node is not None else ""
    value_node = cell.find(f"{{{_NS}}}v")
    if value_node is None or value_node.text is None:
        return None
    text = value_node.text
    if cell_type == "s":
        position = int(text)
        if position >= len(shared):
            raise WorkbookError(f"shared string {position} is out of range")
        return shared[position]
    if cell_type in {"str", "e"}:
        return text
    if cell_type == "b":
        return text == "1"
    try:
        number = float(text)
    except ValueError as error:  # pragma: no cover - malformed input
        raise WorkbookError(f"unreadable numeric cell {text!r}") from error
    return int(number) if number.is_integer() else number


def _sheet_targets(archive: zipfile.ZipFile) -> list[tuple[str, str]]:
    """Sheet names paired with their part paths, in workbook order."""
    workbook = ElementTree.fromstring(archive.read("xl/workbook.xml"))
    relationships = ElementTree.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
    by_id = {
        node.get("Id"): node.get("Target", "")
        for node in relationships
    }
    targets: list[tuple[str, str]] = []
    for node in workbook.iter(f"{{{_NS}}}sheet"):
        name = node.get("name") or ""
        target = by_id.get(node.get(f"{{{_R_NS}}}id"), "")
        if not target:
            raise WorkbookError(f"sheet {name!r} has no resolvable part")
        if not target.startswith("/"):
            target = f"xl/{target.lstrip('/')}"
        targets.append((name, target.lstrip("/")))
    return targets


def read_workbook(path: Path | str) -> Workbook:
    """Read a header-row workbook into normalised sheets.

    The first row of each sheet is the header. Rows shorter than the header are
    padded with ``None`` so every row has every key, which keeps a downstream
    reconciliation from mistaking a trailing empty cell for a missing column.
    """
    location = Path(path)
    with zipfile.ZipFile(location) as archive:
        shared = _shared_strings(archive)
        sheets: list[Sheet] = []
        for name, target in _sheet_targets(archive):
            root = ElementTree.fromstring(archive.read(target))
            grid: list[list[Any]] = []
            for row in root.iter(f"{{{_NS}}}row"):
                cells: list[Any] = []
                for cell in row.findall(f"{{{_NS}}}c"):
                    reference = cell.get("r")
                    if reference is not None:
                        index = _column_index(reference)
                        while len(cells) < index:
                            cells.append(None)
                    cells.append(_cell_value(cell, shared))
                grid.append(cells)
            if not grid:
                sheets.append(Sheet(name=name, columns=(), rows=()))
                continue
            header = [str(value) if value is not None else "" for value in grid[0]]
            rows = tuple(
                {
                    column: (record[position] if position < len(record) else None)
                    for position, column in enumerate(header)
                }
                for record in grid[1:]
                if any(value is not None and value != "" for value in record)
            )
            sheets.append(Sheet(name=name, columns=tuple(header), rows=rows))
    return Workbook(sheets=tuple(sheets))


# -- writing -------------------------------------------------------------------


def _escape(value: str) -> str:
    return (
        value.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _column_letters(index: int) -> str:
    letters = ""
    position = index + 1
    while position:
        position, remainder = divmod(position - 1, 26)
        letters = chr(ord("A") + remainder) + letters
    return letters


def _cell_xml(reference: str, value: Any) -> str:
    if value is None or value == "":
        return f'<c r="{reference}"/>'
    if isinstance(value, bool):
        return f'<c r="{reference}" t="b"><v>{1 if value else 0}</v></c>'
    if isinstance(value, (int, float)):
        return f'<c r="{reference}"><v>{value}</v></c>'
    return f'<c r="{reference}" t="inlineStr"><is><t>{_escape(str(value))}</t></is></c>'


def write_workbook(
    path: Path | str,
    sheets: Mapping[str, tuple[Sequence[str], Iterable[Sequence[Any]]]],
) -> Path:
    """Write sheets as ``{name: (header, rows)}`` to a minimal ``.xlsx``.

    Values are written as inline strings or numbers. Nothing is styled and no
    date is converted to a serial number: a date is written as its ISO text so
    that reading it back returns exactly what was written, with no timezone or
    epoch convention in between.
    """
    location = Path(path)
    location.parent.mkdir(parents=True, exist_ok=True)

    sheet_names = list(sheets)
    workbook_sheets = "".join(
        f'<sheet name="{_escape(name)}" sheetId="{position + 1}" r:id="rId{position + 1}"/>'
        for position, name in enumerate(sheet_names)
    )
    relationships = "".join(
        f'<Relationship Id="rId{position + 1}" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" '
        f'Target="worksheets/sheet{position + 1}.xml"/>'
        for position in range(len(sheet_names))
    )
    overrides = "".join(
        f'<Override PartName="/xl/worksheets/sheet{position + 1}.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        for position in range(len(sheet_names))
    )

    # A fixed timestamp keeps the archive byte-identical across regenerations, so
    # a corpus rebuild that changed no data does not change the evidence hash.
    fixed_time = (2026, 1, 1, 0, 0, 0)
    with zipfile.ZipFile(location, "w", zipfile.ZIP_DEFLATED) as archive:

        def add(name: str, content: str) -> None:
            info = zipfile.ZipInfo(name, date_time=fixed_time)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o600 << 16
            archive.writestr(info, content)

        add(
            "[Content_Types].xml",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-'
            'officedocument.spreadsheetml.sheet.main+xml"/>'
            f"{overrides}</Types>",
        )
        add(
            "_rels/.rels",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/'
            'relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>',
        )
        add(
            "xl/workbook.xml",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            f'<workbook xmlns="{_NS}" xmlns:r="{_R_NS}">'
            f"<sheets>{workbook_sheets}</sheets></workbook>",
        )
        add(
            "xl/_rels/workbook.xml.rels",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            f"{relationships}</Relationships>",
        )
        for position, name in enumerate(sheet_names):
            header, rows = sheets[name]
            lines = [
                "<row r=\"1\">"
                + "".join(
                    _cell_xml(f"{_column_letters(column)}1", value)
                    for column, value in enumerate(header)
                )
                + "</row>"
            ]
            for offset, record in enumerate(rows, start=2):
                lines.append(
                    f'<row r="{offset}">'
                    + "".join(
                        _cell_xml(f"{_column_letters(column)}{offset}", value)
                        for column, value in enumerate(record)
                    )
                    + "</row>"
                )
            add(
                f"xl/worksheets/sheet{position + 1}.xml",
                '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                f'<worksheet xmlns="{_NS}"><sheetData>{"".join(lines)}</sheetData></worksheet>',
            )
    return location
