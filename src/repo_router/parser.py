"""Parse the supported sections of a ROUTER.md document."""

from dataclasses import dataclass, field
import re


@dataclass(frozen=True)
class DirectoryEntry:
    path: str
    description: str


@dataclass(frozen=True)
class FileEntry:
    path: str
    description: str


@dataclass
class RouterDocument:
    purpose: str = ""
    directories: list[DirectoryEntry] = field(default_factory=list)
    files: list[FileEntry] = field(default_factory=list)


class RouterParseError(ValueError):
    """A supported ROUTER.md section contains an invalid entry."""


_HEADING = re.compile(r"^##\s+(.+?)\s*$")
_ENTRY = re.compile(r"^\s*-\s+(.+?)\s+—\s+(.+?)\s*$")


def parse_router(contents: str) -> RouterDocument:
    """Parse purpose, directory entries, and important file entries.

    Missing sections produce empty values. Other sections are ignored.
    Entries must use ``- path — description`` on a single line.
    """
    document = RouterDocument()
    purpose_lines: list[str] = []
    section: str | None = None

    for line_number, line in enumerate(contents.splitlines(), start=1):
        heading = _HEADING.match(line)
        if heading:
            section = heading.group(1)
            continue

        if section == "Purpose":
            purpose_lines.append(line)
        elif section in ("Directories", "Important Files") and line.strip():
            entry = _ENTRY.match(line)
            if entry is None:
                raise RouterParseError(f"Malformed entry in {section} at line {line_number}: {line}")
            path, description = entry.groups()
            if section == "Directories":
                document.directories.append(DirectoryEntry(path, description))
            else:
                document.files.append(FileEntry(path, description))

    document.purpose = "\n".join(purpose_lines).strip()
    return document
