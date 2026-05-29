#!/usr/bin/env python3
"""
checker.py - Static bash script analyzer.

Lists output files the script would create/write and warns about common bash mistakes,
without executing the script.

Usage: python checker.py <script.sh>
"""

import re
import sys
import argparse
from pathlib import Path
from dataclasses import dataclass
from typing import List


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

@dataclass
class OutputFile:
    lineno: int
    description: str
    path: str
    has_variable: bool = False


@dataclass
class Warning:
    lineno: int   # 0 = script-level (not tied to a specific line)
    message: str


# ---------------------------------------------------------------------------
# Low-level helpers
# ---------------------------------------------------------------------------

def strip_line_comment(line: str) -> str:
    """Remove trailing # comment from a bash line, respecting quoted strings."""
    in_single = in_double = False
    for i, ch in enumerate(line):
        if ch == "'" and not in_double:
            in_single = not in_single
        elif ch == '"' and not in_single:
            in_double = not in_double
        elif ch == '#' and not in_single and not in_double:
            return line[:i]
    return line


def clean_path(path: str) -> str:
    """Strip surrounding single or double quotes from a path token."""
    if len(path) >= 2 and path[0] in ('"', "'") and path[-1] == path[0]:
        return path[1:-1]
    return path


def has_variable(path: str) -> bool:
    return '$' in path


def is_fd_to_fd(path: str) -> bool:
    """True if path is a file-descriptor reference like &1, &2, &-."""
    return bool(re.match(r'^&[\d\-]', path.strip()))


def tokenize(text: str) -> List[str]:
    """Split text on whitespace, respecting single and double quotes."""
    tokens: List[str] = []
    cur: List[str] = []
    in_s = in_d = False
    for ch in text:
        if ch == "'" and not in_d:
            in_s = not in_s
            cur.append(ch)
        elif ch == '"' and not in_s:
            in_d = not in_d
            cur.append(ch)
        elif ch in (' ', '\t') and not in_s and not in_d:
            if cur:
                tokens.append(''.join(cur))
                cur = []
        else:
            cur.append(ch)
    if cur:
        tokens.append(''.join(cur))
    return tokens


def non_flag_tokens(tokens: List[str]) -> List[str]:
    """Return tokens that are not option flags (don't start with -)."""
    return [t for t in tokens if not t.startswith('-')]


def split_pipeline(line: str) -> List[str]:
    """Rough split on |, ;, &&, || to isolate individual command segments."""
    parts = re.split(r'\|&|&&|\|\||[|;]', line)
    return [p.strip() for p in parts if p.strip()]


def remove_bracket_expressions(line: str) -> str:
    """
    Remove [[ ... ]] and [ ... ] conditional blocks so that comparison
    operators like > inside them are not mistaken for redirects.
    """
    line = re.sub(r'\[\[.*?\]\]', '', line)
    line = re.sub(r'\[(?!\[).*?\](?!\])', '', line)
    return line


def mask_single_quoted(line: str) -> str:
    """
    Replace the *contents* of single-quoted strings with Xs so that redirect
    operators embedded in awk/perl/etc. literals are not mistaken for real
    bash redirects.  The surrounding quotes are kept so quote-counting stays
    consistent.
    """
    return re.sub(r"'([^']*)'", lambda m: "'" + 'X' * len(m.group(1)) + "'", line)


# ---------------------------------------------------------------------------
# Redirect detection
# ---------------------------------------------------------------------------

# File-path token: quoted string OR unquoted sequence (stops at shell meta-chars)
_FILE_PAT = r'(?:"[^"]*"|\'[^\']*\'|[^\s;&|<>"\'()\\]+)'

# &>> file  or  &> file
_COMBINED_REDIR_RE = re.compile(r'&(>>?)\s*(' + _FILE_PAT + r')')

# [N]>>  [N]>|  [N]>  [N]<>   (N = digit or {name})
# Negative lookbehind avoids matching inside <<, <&, etc.
_STD_REDIR_RE = re.compile(
    r'(?<![<&\w])(\d+|\{[\w]+\})?(>>|>\|?|<>)\s*(' + _FILE_PAT + r')'
)


def extract_redirects(raw_line: str, lineno: int) -> List[OutputFile]:
    """Extract all redirect-based output files from a single line."""
    stripped = strip_line_comment(raw_line)
    # Remove [[ ]] / [ ] blocks and mask single-quoted contents so that
    # operators inside awk/perl/etc. literals are not treated as redirects.
    line = mask_single_quoted(remove_bracket_expressions(stripped))
    results: List[OutputFile] = []

    for m in _COMBINED_REDIR_RE.finditer(line):
        op = '&' + m.group(1)
        path = clean_path(m.group(2))
        if path and not is_fd_to_fd(path):
            results.append(OutputFile(
                lineno=lineno, description=f'{op} {path}',
                path=path, has_variable=has_variable(path),
            ))

    for m in _STD_REDIR_RE.finditer(line):
        fd = m.group(1) or ''
        op = m.group(2)
        path = clean_path(m.group(3))
        if path and not is_fd_to_fd(path):
            results.append(OutputFile(
                lineno=lineno, description=f'{fd}{op} {path}',
                path=path, has_variable=has_variable(path),
            ))

    return results


# ---------------------------------------------------------------------------
# Command-based output detection
# ---------------------------------------------------------------------------

def _segment_outputs(segment: str, lineno: int) -> List[OutputFile]:
    """
    Extract output files from a single pipeline segment (no pipes/semicolons).
    Covers: touch, mkdir, cp, mv, ln, install, tee, sponge, dd, truncate,
            mkfifo, mktemp, curl, wget, sed -i, openssl, gpg/gpg2.
    """
    tokens = tokenize(segment.strip())
    if not tokens:
        return []

    # Strip leading env-var assignments: FOO=bar cmd arg ...
    while tokens and re.match(r'^[\w]+=', tokens[0]):
        tokens = tokens[1:]
    if not tokens:
        return []

    cmd = Path(tokens[0]).name   # basename handles /usr/bin/touch → touch
    args = tokens[1:]
    pos = non_flag_tokens(args)  # positional (non-flag) args
    results: List[OutputFile] = []

    def mk(prefix: str, path_raw: str) -> OutputFile:
        p = clean_path(path_raw)
        return OutputFile(lineno=lineno, description=f'{prefix} {p}',
                          path=p, has_variable=has_variable(p))

    if cmd == 'touch':
        for p in pos:
            results.append(mk('touch', p))

    elif cmd == 'mkdir':
        for p in pos:
            results.append(mk('mkdir', p))

    elif cmd in ('cp', 'mv', 'ln', 'install'):
        if len(pos) >= 2:
            results.append(mk(f'{cmd} ->', pos[-1]))

    elif cmd == 'tee':
        for p in pos:
            results.append(mk('tee', p))

    elif cmd == 'sponge':
        if pos:
            results.append(mk('sponge', pos[0]))

    elif cmd == 'dd':
        for a in args:
            m = re.match(r'^of=(.+)$', a)
            if m:
                p = clean_path(m.group(1))
                results.append(OutputFile(
                    lineno=lineno, description=f'dd (of={p})',
                    path=p, has_variable=has_variable(p),
                ))

    elif cmd == 'truncate':
        if pos:
            results.append(mk('truncate', pos[-1]))

    elif cmd == 'mkfifo':
        for p in pos:
            results.append(mk('mkfifo', p))

    elif cmd == 'mktemp':
        results.append(OutputFile(
            lineno=lineno,
            description='mktemp (temp file - path captured by variable)',
            path='<mktemp>',
            has_variable=False,
        ))

    elif cmd == 'curl':
        i = 0
        while i < len(args):
            if args[i] in ('-o', '--output') and i + 1 < len(args):
                results.append(mk('curl -o', args[i + 1]))
                i += 2
            elif re.match(r'^-o(.+)', args[i]):
                results.append(mk('curl -o', re.match(r'^-o(.+)', args[i]).group(1)))
                i += 1
            elif args[i] == '-O':
                results.append(OutputFile(
                    lineno=lineno,
                    description='curl -O (saves with remote filename)',
                    path='<remote filename>',
                    has_variable=False,
                ))
                i += 1
            else:
                i += 1

    elif cmd == 'wget':
        i = 0
        while i < len(args):
            if args[i] in ('-O', '--output-document') and i + 1 < len(args):
                results.append(mk('wget -O', args[i + 1]))
                i += 2
            elif re.match(r'^-O(.+)', args[i]):
                results.append(mk('wget -O', re.match(r'^-O(.+)', args[i]).group(1)))
                i += 1
            else:
                i += 1

    elif cmd == 'sed':
        if any(re.match(r'^-[^-]*i|^--in-place', a) for a in args):
            # Filter out sed expressions (quoted strings or patterns like s/x/y/)
            files = [
                p for p in pos
                if not (p.startswith(("'", '"')) or re.match(r"^[s/]", p))
            ]
            if not files and pos:
                files = [pos[-1]]
            for f in files:
                results.append(mk('sed -i (in-place)', f))

    elif cmd == 'openssl':
        i = 0
        while i < len(args):
            if args[i] == '-out' and i + 1 < len(args):
                results.append(mk('openssl -out', args[i + 1]))
                i += 2
            else:
                i += 1

    elif cmd in ('gpg', 'gpg2'):
        i = 0
        while i < len(args):
            if args[i] in ('-o', '--output') and i + 1 < len(args):
                results.append(mk(f'{cmd} -o', args[i + 1]))
                i += 2
            else:
                i += 1

    return results


def extract_command_outputs(raw_line: str, lineno: int) -> List[OutputFile]:
    """Extract command-based output files from a full line (handles pipelines)."""
    line = strip_line_comment(raw_line)
    results: List[OutputFile] = []
    for seg in split_pipeline(line):
        results.extend(_segment_outputs(seg, lineno))
    # Also detect mktemp used inside $() command substitution: VAR=$(mktemp ...)
    if re.search(r'\$\(mktemp\b', line):
        results.append(OutputFile(
            lineno=lineno,
            description='mktemp (temp file - path captured by variable)',
            path='<mktemp>',
            has_variable=False,
        ))
    return results


# ---------------------------------------------------------------------------
# AWK in-script redirect detection
# ---------------------------------------------------------------------------

_AWK_PRINT_RE = re.compile(r'print\s*(>>?)\s*["\']([^"\']+)["\']')


def extract_awk_outputs(raw_line: str, lineno: int) -> List[OutputFile]:
    """Detect print > "file" or print >> "file" patterns inside awk scripts."""
    results: List[OutputFile] = []
    for m in re.finditer(r"""awk\s+(?:'([^']*)'|"([^"]*)")""", raw_line):
        body = m.group(1) or m.group(2) or ''
        for fm in _AWK_PRINT_RE.finditer(body):
            path = fm.group(2)
            results.append(OutputFile(
                lineno=lineno,
                description=f'awk print {fm.group(1)} {path}',
                path=path,
                has_variable=has_variable(path),
            ))
    return results


# ---------------------------------------------------------------------------
# Variable assignment tracking
# ---------------------------------------------------------------------------

def collect_assignments(lines: List[str]) -> dict:
    """Return {varname: first_lineno} for all variable assignments in the script."""
    assignments: dict = {}
    for i, line in enumerate(lines, 1):
        for m in re.finditer(r'(?:^|(?<=\s))([\w]+)=(?!=)', strip_line_comment(line)):
            name = m.group(1)
            if name not in assignments:
                assignments[name] = i
    return assignments


# ---------------------------------------------------------------------------
# Warning checks
# ---------------------------------------------------------------------------

_SPECIAL_VAR_RE = re.compile(r'^\$[?#@*!\$\-0-9]$')


def check_set_flags(lines: List[str]) -> List[Warning]:
    """Warn if set -e or set -u are absent from the script."""
    full = '\n'.join(lines)
    warns: List[Warning] = []
    if not re.search(r'set\s+-[a-zA-Z]*e|set\s+-o\s+errexit', full):
        warns.append(Warning(0, "'set -e' not found - script will not exit on error"))
    if not re.search(r'set\s+-[a-zA-Z]*u|set\s+-o\s+nounset', full):
        warns.append(Warning(0, "'set -u' not found - unset variables will be silently empty"))
    return warns


def check_unquoted_vars(lines: List[str]) -> List[Warning]:
    """Warn about $VAR appearing outside double quotes (word-splitting risk)."""
    warns: List[Warning] = []
    for i, line in enumerate(lines, 1):
        stripped = strip_line_comment(line)
        # Mask out already-quoted regions so we only inspect unquoted text
        masked = re.sub(r'"[^"]*"', '""', stripped)
        masked = re.sub(r"'[^']*'", "''", masked)
        for m in re.finditer(r'\$\{?[\w]+\}?', masked):
            if not _SPECIAL_VAR_RE.match(m.group()):
                warns.append(Warning(i, f'unquoted variable {m.group()}'))
                break  # one warning per line to keep output readable
    return warns


def check_var_before_assign(lines: List[str], assignments: dict) -> List[Warning]:
    """Warn about variables used before their first assignment line."""
    warns: List[Warning] = []
    reported: set = set()
    for i, line in enumerate(lines, 1):
        for m in re.finditer(r'\$\{?([\w]+)\}?', strip_line_comment(line)):
            var = m.group(1)
            if re.match(r'^[?#@*!\$\-0-9]+$', var):
                continue
            if var in assignments and assignments[var] > i:
                key = (var, i)
                if key not in reported:
                    reported.add(key)
                    warns.append(Warning(
                        i,
                        f'variable {var} used before assignment '
                        f'(first assigned at line {assignments[var]})',
                    ))
    return warns


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def analyze(path: Path) -> int:
    """Analyze a bash script. Returns 0 if clean, 1 if issues found, 2 on error."""
    try:
        lines = path.read_text(encoding='utf-8', errors='replace').splitlines()
    except FileNotFoundError:
        print(f'Error: file not found: {path}', file=sys.stderr)
        return 2

    outputs: List[OutputFile] = []
    for i, line in enumerate(lines, 1):
        outputs.extend(extract_redirects(line, i))
        outputs.extend(extract_command_outputs(line, i))
        outputs.extend(extract_awk_outputs(line, i))

    # Deduplicate by (lineno, path)
    seen: set = set()
    unique: List[OutputFile] = []
    for of in outputs:
        key = (of.lineno, of.path)
        if key not in seen:
            seen.add(key)
            unique.append(of)
    unique.sort(key=lambda x: x.lineno)

    assignments = collect_assignments(lines)
    warns: List[Warning] = []
    warns.extend(check_set_flags(lines))
    warns.extend(check_unquoted_vars(lines))
    warns.extend(check_var_before_assign(lines, assignments))
    warns.sort(key=lambda x: x.lineno)

    print(f'\n=== Analyzing: {path} ===\n')

    print('=== Output Files ===')
    if unique:
        for of in unique:
            note = '  [path contains variable]' if of.has_variable else ''
            print(f'  line {of.lineno:4d} : {of.description}{note}')
    else:
        print('  (none detected)')

    print()
    print('=== Warnings ===')
    if warns:
        for w in warns:
            loc = f'line {w.lineno:4d}' if w.lineno else 'script  '
            print(f'  [WARN] {loc} : {w.message}')
    else:
        print('  (none)')

    print()
    return 1 if (unique or warns) else 0


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description='Static bash script checker - list output files and warn about common mistakes.'
    )
    parser.add_argument('script', type=Path, help='Bash script to analyze')
    args = parser.parse_args()
    sys.exit(analyze(args.script))


if __name__ == '__main__':
    main()
