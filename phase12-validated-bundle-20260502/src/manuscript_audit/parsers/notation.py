from __future__ import annotations

import re

from manuscript_audit.schemas.artifacts import NotationSummary, NotationSymbol, ParsedManuscript

COMMAND_RE = re.compile(r"\\[A-Za-z]+")
SINGLE_LETTER_RE = re.compile(r"(?<![A-Za-z])[A-Za-z](?![A-Za-z])")
DEFINITION_PATTERNS = [
    re.compile(r"\\([A-Za-z]+)\s+denotes\b", re.IGNORECASE),
    re.compile(r"\\([A-Za-z]+)\s+is\b", re.IGNORECASE),
    re.compile(r"\b([A-Za-z])\s+denotes\b", re.IGNORECASE),
    re.compile(r"\b([A-Za-z])\s+is\b", re.IGNORECASE),
    re.compile(r"where\s+\\([A-Za-z]+)\s+(?:denotes|is|are)\b", re.IGNORECASE),
    re.compile(r"where\s+([A-Za-z])\s+(?:denotes|is|are)\b", re.IGNORECASE),
    re.compile(r"let\s+\\([A-Za-z]+)\s+(?:denote|be)\b", re.IGNORECASE),
    re.compile(r"let\s+([A-Za-z])\s+(?:denote|be)\b", re.IGNORECASE),
]
IGNORED_COMMANDS = {
    "begin",
    "end",
    "label",
    "ref",
    "eqref",
    "frac",
    "left",
    "right",
    "times",
    "cdot",
    "text",
    "mathrm",
    "mathbf",
    "mathit",
}
IGNORED_SYMBOLS = {"e", "i"}


def _equation_symbols(equation_block: str) -> set[str]:
    symbols: set[str] = set()
    for command in COMMAND_RE.findall(equation_block):
        normalized = command.lstrip("\\")
        if normalized not in IGNORED_COMMANDS:
            symbols.add(f"\\{normalized}")
    cleaned = COMMAND_RE.sub(" ", equation_block)
    for letter in SINGLE_LETTER_RE.findall(cleaned):
        normalized = letter.strip()
        if normalized and normalized.lower() not in IGNORED_SYMBOLS:
            symbols.add(normalized)
    return symbols


def _is_latex_definition_pattern(pattern: re.Pattern[str]) -> bool:
    return (
        pattern.pattern.startswith("\\\\")
        or "where\\s+\\\\" in pattern.pattern
        or "let\\s+\\\\" in pattern.pattern
    )


def _definition_hints(text: str) -> dict[str, str]:
    hints: dict[str, str] = {}
    for line in text.splitlines():
        stripped = line.strip().replace("$", " ")
        if not stripped:
            continue
        for pattern in DEFINITION_PATTERNS:
            for match in pattern.finditer(stripped):
                symbol = match.group(1)
                normalized = f"\\{symbol}" if _is_latex_definition_pattern(pattern) else symbol
                hints.setdefault(normalized, line.strip())
    return hints


def extract_notation_summary(parsed: ParsedManuscript) -> NotationSummary:
    symbols_used: set[str] = set()
    for block in parsed.equation_blocks:
        symbols_used.update(_equation_symbols(block))
    hints = _definition_hints(parsed.full_text)
    notation_symbols = [
        NotationSymbol(
            symbol=symbol,
            used_in_equations=True,
            defined_in_text=symbol in hints,
            definition_hint=hints.get(symbol),
        )
        for symbol in sorted(symbols_used)
    ]
    undefined_symbols = [symbol.symbol for symbol in notation_symbols if not symbol.defined_in_text]
    return NotationSummary(
        equation_symbol_count=len(notation_symbols),
        defined_symbol_count=sum(symbol.defined_in_text for symbol in notation_symbols),
        undefined_symbols=undefined_symbols,
        symbols=notation_symbols,
    )
