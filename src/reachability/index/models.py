from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path


class ModuleKind(str, Enum):
    MODULE = "module"     # any .py file, including files inside a namespace package
    PACKAGE = "package"   # specifically an __init__.py file


class TargetKind(str, Enum):
    FIRST_PARTY = "first_party"
    THIRD_PARTY = "third_party"
    STDLIB = "stdlib"
    UNKNOWN = "unknown"   # e.g. relative import whose level escapes the repo root


@dataclass(frozen=True)
class ModuleRecord:
    dotted_name: str
    file_path: Path
    kind: ModuleKind
    package: str          # dotted name of the module's own package; "" if top-level


@dataclass(frozen=True)
class ImportAlias:
    local_name: str
    target_module: str | None
    target_symbol: str | None
    is_relative: bool
    resolved_absolute: str | None
    target_kind: TargetKind
    lineno: int
    conditional: bool = False          # inside try/except ImportError
    type_checking_only: bool = False   # inside if TYPE_CHECKING:
    via_reexport: bool = False         # resolved_absolute rewritten via one-hop __init__ re-export


@dataclass(frozen=True)
class StarImport:
    module: str | None
    resolved_absolute: str | None
    target_kind: TargetKind
    lineno: int


@dataclass
class ModuleImportTable:
    module: ModuleRecord
    aliases: list[ImportAlias] = field(default_factory=list)
    star_imports: list[StarImport] = field(default_factory=list)


@dataclass
class UnparsedFile:
    file_path: Path
    error: str
    lineno: int | None


@dataclass
class DiscoveryReport:
    modules: list[ModuleRecord]
    import_tables: dict[str, ModuleImportTable]  # keyed by dotted_name
    unparsed: list[UnparsedFile]
    star_import_count: int
