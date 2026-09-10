from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path


class NodeKind(str, Enum):
    MODULE = "module"
    FUNCTION = "function"
    ASYNCFUNCTION = "asyncfunction"
    CLASS = "class"
    METHOD = "method"
    ALIAS = "alias"


@dataclass(frozen=True)
class SymbolNode:
    node_id: str
    dotted_module: str
    qualname: str
    kind: NodeKind
    file: Path
    lineno: int
    is_async: bool = False
    decorators: list[str] = field(default_factory=list)
    bases: list[str] = field(default_factory=list)
    target_id: str | None = None
    conditional: bool = False
    type_checking_only: bool = False


@dataclass
class ModuleSymbolTable:
    module: str
    nodes: list[SymbolNode] = field(default_factory=list)
