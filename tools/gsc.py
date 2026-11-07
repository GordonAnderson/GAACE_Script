#!/usr/bin/env python3
"""
gsc.py — GAACE_Script compiler.

Compiles a small C-like script language to the bytecode format understood by
the GAACE_Script VM (see ../src/GAACEScript.h). This is the host-side half of
the design: the device only ever runs the ~20-opcode interpreter loop, never
this compiler.

Language (informal grammar):

    program     := (syscall_decl | statement)*
    syscall_decl:= "syscall" IDENT "(" (IDENT ("," IDENT)*)? ")"
                   (":" type)? "=" NUMBER ";"
    statement   := if_stmt | while_stmt | block
                 | "var" IDENT (":" type)? ";"  (reserve a slot, no assignment)
                 | IDENT "=" expr ";"          (assignment)
                 | expr ";"                     (expression statement)
    type        := "int" | "float"
    if_stmt     := "if" "(" expr ")" block ("else" block)?
    while_stmt  := "while" "(" expr ")" block
    block       := "{" statement* "}"
    expr        := or_expr
    or_expr     := and_expr ("||" and_expr)*
    and_expr    := cmp_expr ("&&" cmp_expr)*
    cmp_expr    := add_expr (("=="|"!="|"<"|"<="|">"|">=") add_expr)*
    add_expr    := mul_expr (("+"|"-") mul_expr)*
    mul_expr    := unary (("*"|"/"|"%") unary)*
    unary       := ("-"|"!") unary | cast | primary
    cast        := "(" type ")" unary
    primary     := NUMBER | FLOAT | "true" | "false" | IDENT
                 | IDENT "(" (expr ("," expr)*)? ")" | cmd_call | "(" expr ")"
                   (true/false are int literals 1/0, e.g. cmd("SADCEN", true))
    cmd_call    := ("cmd" | "cmdf") "(" STRING ("," expr)* ")"
                 | "device" "(" STRING ")"
                 | "ready" "(" expr ")"
                 | ("dcmd" | "dcmdf") "(" expr "," STRING ("," expr)* ")"

Notes:
  - Two numeric types: `int` (int32) and `float` (IEEE754 float32) — see
    "Floats" below. Everything else about the language is untyped text; the
    type system exists only to pick the right opcode (ADD vs FADD, etc.),
    there's no other static checking.
  - `&&` / `||` are NOT short-circuiting — both operands are always evaluated
    (the VM's AND/OR opcodes just combine two already-computed values). Don't
    rely on short-circuit side effects the way you might in C. Both require
    int-typed operands (comparisons already produce int, so `a > b && c > d`
    works fine either way).
  - Variables are plain 32-bit slots, auto-allocated in order of first
    assignment, with their type inferred from that first assignment (there's
    no implicit "starts at zero" at the language level, even though the VM
    does zero its slots on vmInit — this catches typos instead of silently
    running with 0). `var NAME;` / `var NAME: float;` reserves a slot without
    assigning it (default type `int` if omitted), for state meant to persist
    *across* separate vmRun() calls (e.g. a periodic script whose caller
    resets pc/sp between ticks but deliberately leaves vars alone) — declare
    it once, then read/write it normally; what it's seeded with is up to the
    firmware side.
  - `syscall NAME(params) = ID;` declares a callable that maps to VM opcode
    CALL <ID> <argc-from-declaration>. ID must match the order the embedding
    firmware registers it with vmRegisterSyscall() (0 = first registered).
    Add `: float` before the `=` if the syscall's C++ implementation returns
    a float (bit-cast into the same int32_t return type — see
    GAACEScript.h); omitted means `int`.
  - An expression used as a statement (e.g. a bare call for its side effect)
    has its return value discarded (compiled as <expr> POP).

Syscall tables (scripts without declarations):
  - If the firmware registers its syscalls with ScriptRuntime::registerSyscalls()
    the device can describe them (GSYSCALLS). `--upload` asks it; `--syscalls
    FILE` reads a saved copy (`--save-syscalls` / `--list-syscalls` make one).
    Compiling against a table, any syscall in it can be called with no
    `syscall NAME(...) = ID;` line -- id, argument count and return type come
    from the firmware. An explicit declaration must agree with the table or
    it's a compile error; a call to a name the table lacks is one too.
  - `--upload` stamps the table's hash on SCRIPTLOAD/SCRIPTCOMMIT (only when
    the script calls a syscall), and the device NAKs a mismatch. See
    tools/README.md.

Floats:
  - Literals need a decimal point (`3.5`, `0.0`) — `3` is always `int`.
  - `+ - * /` promote automatically: int paired with float produces float
    (an implicit int-to-float conversion is inserted for the int operand).
    `%` (MOD) has no float form — mixing it with a float operand is a
    compile error.
  - Assigning an int-typed expression to a float-typed variable auto-
    promotes; assigning a float-typed expression to an int-typed variable
    does NOT auto-narrow — that's a compile error, use an explicit cast.
  - `(float)expr` / `(int)expr` convert explicitly. `(int)` truncates
    toward zero, matching a C cast.

cmd() -- calling into the command processor directly:
  - `cmd("SADCPIN", pin)` invokes command SADCPIN (with `pin` as its
    argument) against whatever the embedding project's commandProcessor
    already exposes -- no per-project syscall needs writing for this. The
    literal name is stored once in a string-constant pool compiled
    alongside the code (repeated calls to the same name share one copy);
    CMDCALL carries a pool offset, not the string itself, so the VM core
    still touches no strings directly -- see GAACEScript.h.
  - Arguments are typed by the compiler: an int expression is sent as an
    integer, a float expression as a decimal number (`cmd("SETV", 23.76)`
    sends `SETV,23.76`). Calls with a float argument compile to CMDCALLM/
    CMDCALLMF (a 2-byte float-argument mask, max 16 arguments).
  - Returns an int: the response parsed as a number on ACK, 1 for a bare
    ACK (a set or action command that succeeded), TRUE/FALSE as 1/0, and 0
    on NAK or if the response's text isn't a number. NAK and "ACK with
    value 0" are not distinguishable from the return value alone -- check
    what you're calling if that matters.
  - `cmdf("NAME", args...)` is the same call but returns a float: the
    response is parsed as a float32 (so "3.14" comes back as 3.14, not 3).
    Same NAK/no-number -> 0.0 rule. The script has to pick cmd() vs cmdf()
    up front because values carry no runtime type tag -- use cmdf() for any
    command whose reply can have a fractional part.
  - Arguments aren't type-checked (same as syscalls) -- floats can be
    passed, but the receiving command decides how to interpret them.
  - Compiled output for a script that uses cmd() includes a second
    artifact (the pool) alongside the code -- see gsc.py's --format
    handling and tools/README.md.

Devices -- talking to other instruments by name:
  - `dev = device("QUAD");` gives a handle for the instrument whose name is
    QUAD (how names are found is up to the firmware; on the USBrepeater it's
    each instrument's GNAME). The handle follows the instrument, so it still
    works if it's unplugged and plugged into another connector.
  - `ready(dev)` is 1 if that instrument can be reached right now, else 0.
  - `dcmd(dev, "SRFADRV", 1, v);` sends it a command, formatting arguments
    exactly as cmd() does; `x = dcmdf(dev, "GRFADRV", 1);` reads a float
    back. Returns follow cmd()'s rules (ACK + number -> number, bare ACK ->
    1, TRUE/FALSE -> 1/0, NAK, no reply or not reachable -> 0). The script pauses until the reply
    arrives; nothing else on the controller waits.
  - Device names go in the same string pool as cmd() names. A script using
    these needs firmware with device support (otherwise it stops with
    VM_ERR_NO_DEVICE_BRIDGE); the syscall-table hash is unaffected.

Output formats: raw binary, ASCII hex, or a C uint8_t array body suitable for
pasting into firmware source. --upload sends the compiled script straight to
a running controller's control port via SCRIPTLOAD (see WORKFLOW.md).
"""

import argparse
import difflib
import re
import struct
import json
import sys
import time

# ---------------------------------------------------------------------------
# Opcodes — MUST stay numerically in sync with the enum in src/GAACEScript.h.
# ---------------------------------------------------------------------------
OP_HALT     = 0x00
OP_PUSH_I32 = 0x01
OP_DUP      = 0x02
OP_POP      = 0x03
OP_LOAD     = 0x04
OP_STORE    = 0x05
OP_ADD      = 0x06
OP_SUB      = 0x07
OP_MUL      = 0x08
OP_DIV      = 0x09
OP_MOD      = 0x0A
OP_NEG      = 0x0B
OP_EQ       = 0x0C
OP_NE       = 0x0D
OP_LT       = 0x0E
OP_LE       = 0x0F
OP_GT       = 0x10
OP_GE       = 0x11
OP_AND      = 0x12
OP_OR       = 0x13
OP_NOT      = 0x14
OP_JMP      = 0x15
OP_JZ       = 0x16
OP_JNZ      = 0x17
OP_CALL     = 0x18
OP_PUSH_F32 = 0x19
OP_FADD     = 0x1A
OP_FSUB     = 0x1B
OP_FMUL     = 0x1C
OP_FDIV     = 0x1D
OP_FNEG     = 0x1E
OP_FEQ      = 0x1F
OP_FNE      = 0x20
OP_FLT      = 0x21
OP_FLE      = 0x22
OP_FGT      = 0x23
OP_FGE      = 0x24
OP_I2F      = 0x25
OP_F2I      = 0x26
OP_CMDCALL  = 0x27
OP_CMDCALLF = 0x28
OP_CMDCALLM = 0x29   # + <floatMask:u16>: bit i set => arg i is a float
OP_CMDCALLMF = 0x2A
OP_DEVICE    = 0x2B   # <poolOffset:u16>
OP_DREADY    = 0x2C
OP_DCMDCALL  = 0x2D   # <poolOffset:u16> <argc> <floatMask:u16>; stack: handle, args
OP_DCMDCALLF = 0x2E

VAR_SLOTS = 16

BINOP_OPCODE = {
    "+": OP_ADD, "-": OP_SUB, "*": OP_MUL, "/": OP_DIV, "%": OP_MOD,
    "==": OP_EQ, "!=": OP_NE, "<": OP_LT, "<=": OP_LE, ">": OP_GT, ">=": OP_GE,
    "&&": OP_AND, "||": OP_OR,
}

# Float variants for the numeric/comparison ops (no float form of MOD, AND, OR).
BINOP_OPCODE_F = {
    "+": OP_FADD, "-": OP_FSUB, "*": OP_FMUL, "/": OP_FDIV,
    "==": OP_FEQ, "!=": OP_FNE, "<": OP_FLT, "<=": OP_FLE, ">": OP_FGT, ">=": OP_FGE,
}

# Ops whose result is always `int` regardless of operand type (comparisons
# and logic), vs. ops that preserve the operand type (arithmetic).
COMPARISON_OPS = {"==", "!=", "<", "<=", ">", ">="}
LOGIC_OPS = {"&&", "||"}


# Bumped when the CLI or its --json output changes in a way a host program
# (the MIPS Qt app runs this as a subprocess) could notice.
__version__ = "1.2.0"


class CompileError(Exception):
    """A compile error. str(e) is just the message. `pos` is a character
    offset into the source (None if the error can't be tied to one spot);
    compile_script() fills in `line`/`column` (1-based) and `source_line`
    from it, so a host editor can underline the offending spot."""

    def __init__(self, message, pos=None):
        super().__init__(message)
        self.message = message
        self.pos = pos
        self.line = None
        self.column = None
        self.source_line = None

    def locate(self, src):
        if self.pos is None:
            return
        pos = min(max(self.pos, 0), len(src))
        line_start = src.rfind("\n", 0, pos) + 1
        line_end = src.find("\n", pos)
        self.line = src.count("\n", 0, pos) + 1
        self.column = pos - line_start + 1
        self.source_line = src[line_start:line_end if line_end != -1 else len(src)].rstrip("\r")

    def describe(self):
        """One-line human form: 'line 3, column 5: message'."""
        if self.line is None:
            return self.message
        return f"line {self.line}, column {self.column}: {self.message}"

    def to_dict(self):
        return {"message": self.message, "offset": self.pos, "line": self.line,
                "column": self.column, "source_line": self.source_line}


# ---------------------------------------------------------------------------
# Syscall tables
#
# A firmware that registers its syscalls with ScriptRuntime::registerSyscalls()
# can describe them to the host (the GSYSCALLS command, or a JSON file saved
# from it). Compiling against such a table means a script can just call
# `read_adc()` with no `syscall ...;` declaration, and the compiler knows each
# syscall's id, argument count and return type from the firmware itself.
# ---------------------------------------------------------------------------
class SyscallTableError(Exception):
    pass


def syscall_table_hash(entries):
    """FNV-1a (32-bit) over "name:argc:i|f;" per entry, in order. MUST match
    syscallTableHash() in src/GAACEScript.cpp (both are pinned to a shared test
    vector). Never 0 -- 0 means "unchecked" wherever a hash is optional."""
    h = 0x811C9DC5
    for name, argc, returns in entries:
        text = f"{name}:{argc}:{'f' if returns == 'float' else 'i'};"
        for b in text.encode("ascii"):
            h = ((h ^ b) * 0x01000193) & 0xFFFFFFFF
    return h or 1


class SyscallTable:
    """An ordered list of (name, argc, returns); a syscall's id is its index.
    `returns` is "int" or "float"."""

    def __init__(self, entries):
        self.entries = [(n, int(a), r) for n, a, r in entries]
        seen = set()
        for name, argc, returns in self.entries:
            if returns not in ("int", "float"):
                raise SyscallTableError(f"syscall '{name}': return type must be 'int' or 'float', not {returns!r}")
            if name in seen:
                raise SyscallTableError(f"syscall '{name}' appears twice in the table")
            if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
                raise SyscallTableError(f"syscall name {name!r} is not a valid identifier")
            seen.add(name)

    @property
    def hash(self):
        return syscall_table_hash(self.entries)

    def lookup(self):
        """name -> (id, argc, return_type), the shape Codegen uses."""
        return {n: (i, a, r) for i, (n, a, r) in enumerate(self.entries)}

    def signature(self, i):
        name, argc, returns = self.entries[i]
        args = ", ".join(f"a{k}" for k in range(argc))
        return f"{returns} {name}({args})"

    def to_json(self):
        return json.dumps({
            "hash": f"{self.hash:08X}",
            "syscalls": [{"name": n, "argc": a, "returns": r} for n, a, r in self.entries],
        }, indent=2) + "\n"

    @classmethod
    def from_json(cls, text):
        try:
            data = json.loads(text)
            table = cls((e["name"], e["argc"], e.get("returns", "int")) for e in data["syscalls"])
        except (ValueError, KeyError, TypeError) as e:
            raise SyscallTableError(f"not a valid syscall table file: {e}")
        stated = data.get("hash")
        if stated is not None and stated.upper() != f"{table.hash:08X}":
            raise SyscallTableError(
                f"syscall table file is inconsistent: its hash says {stated} but its "
                f"entries hash to {table.hash:08X} (hand-edited? re-save it from the device)")
        return table


def query_syscall_table(ser):
    """Asks a connected device for its syscall table (GSYSCALLS) over the open
    serial port `ser`. Returns (table, None), or (None, reason) if the device
    simply doesn't offer one (no response, or NAK: firmware without a table).
    Raises SyscallTableError for a reply that's there but malformed -- that's a
    real problem, not "unsupported"."""
    ser.reset_input_buffer()
    ser.write(b"GSYSCALLS\n")

    header = None
    for _ in range(4):  # tolerate an echoed command line before the ACK
        line = ser.read_until(b"\n")
        if not line:
            return None, "no response"
        if b"\x15" in line:
            return None, "the device NAKed GSYSCALLS"
        if b"\x06" in line:
            header = line.split(b"\x06", 1)[1].strip().decode("ascii", "replace")
            break
    if header is None:
        return None, "no ACK in the response"

    try:
        count_text, hash_text = header.split(",")
        count, reported = int(count_text), int(hash_text, 16)
    except ValueError:
        raise SyscallTableError(f"unrecognised GSYSCALLS header: {header!r}")

    entries = []
    for i in range(count):
        line = ser.read_until(b"\n").strip().decode("ascii", "replace")
        parts = line.split(",")
        if len(parts) != 4 or parts[0] != str(i) or parts[3] not in ("int", "float"):
            raise SyscallTableError(f"unrecognised GSYSCALLS line {i}: {line!r}")
        try:
            entries.append((parts[1], int(parts[2]), parts[3]))
        except ValueError:
            raise SyscallTableError(f"unrecognised GSYSCALLS line {i}: {line!r}")

    table = SyscallTable(entries)
    if table.hash != reported:
        raise SyscallTableError(
            f"device reported table hash {reported:08X} but its entries hash to "
            f"{table.hash:08X} (mismatched gsc.py/firmware hash definition?)")
    return table, None


# ---------------------------------------------------------------------------
# Command list (GCMDS): the names cmd() can call on a device
#
# Unlike the syscall table this changes nothing about the bytecode -- it only
# lets the compiler warn about a cmd("NAME", ...) the device doesn't have. Every
# GAACE_Core device already answers GCMDS (ACK, then one "NAME,help" line per
# command, with '?' commands listed twice as GNAME/SNAME), so no firmware change
# is needed.
# ---------------------------------------------------------------------------
class CommandSet:
    """The commands a device offers: an ordered list of (name, help)."""

    def __init__(self, entries):
        self.entries = [(n, h) for n, h in entries]
        self.names = {n for n, _ in self.entries}

    def to_json(self):
        return json.dumps({"commands": [{"name": n, "help": h} for n, h in self.entries]},
                          indent=2) + "\n"

    @classmethod
    def from_json(cls, text):
        try:
            data = json.loads(text)
            return cls((e["name"], e.get("help", "")) for e in data["commands"])
        except (ValueError, KeyError, TypeError) as e:
            raise SyscallTableError(f"not a valid command list file: {e}")

    @classmethod
    def from_gcmds_lines(cls, lines):
        entries = []
        for line in lines:
            line = line.strip()
            if not line:
                continue
            name, _, help_ = line.partition(",")
            entries.append((name.strip(), help_.strip()))
        return cls(entries)

    def suggest(self, name):
        """The closest known command name to `name`, or None."""
        folded = {n.upper(): n for n in self.names}
        if name.upper() in folded:               # same name, different case
            return folded[name.upper()]
        close = difflib.get_close_matches(name.upper(), list(folded), n=1, cutoff=0.7)
        return folded[close[0]] if close else None


def query_command_list(ser, idle_timeout=0.4):
    """Asks a connected device for its commands (GCMDS). Returns
    (CommandSet, None), or (None, reason) if the device doesn't answer.
    GCMDS has no count or terminator, so the list ends when the device has been
    silent for `idle_timeout` seconds."""
    ser.reset_input_buffer()
    ser.write(b"GCMDS\n")

    first = None
    for _ in range(4):  # tolerate an echoed command line before the ACK
        line = ser.read_until(b"\n")
        if not line:
            return None, "no response"
        if b"\x15" in line:
            return None, "the device NAKed GCMDS"
        if b"\x06" in line:
            first = line.split(b"\x06", 1)[1]   # ACK is followed directly by the first entry
            break
    if first is None:
        return None, "no ACK in the response"

    lines = [first.decode("ascii", "replace")]
    old_timeout = getattr(ser, "timeout", None)
    try:
        if old_timeout is not None:
            ser.timeout = idle_timeout
        while True:
            line = ser.read_until(b"\n")
            if not line:
                break
            lines.append(line.decode("ascii", "replace"))
    finally:
        if old_timeout is not None:
            ser.timeout = old_timeout
    return CommandSet.from_gcmds_lines(lines), None


def load_command_list_file(path):
    with open(path) as f:
        return CommandSet.from_json(f.read())


# ---------------------------------------------------------------------------
# Lexer
# ---------------------------------------------------------------------------
WHITESPACE_RE = re.compile(r"\s+")
COMMENT_RE = re.compile(r"//[^\n]*")
TOKEN_RE = re.compile(r"""
    (?P<STRING>"[^"\n]*")
  | (?P<FLOAT>[0-9]+\.[0-9]+)
  | (?P<NUMBER>0[xX][0-9a-fA-F]+|[0-9]+)
  | (?P<IDENT>[A-Za-z_][A-Za-z0-9_]*)
  | (?P<OP>==|!=|<=|>=|&&|\|\||[(){};,=+\-*/%<>!:])
""", re.VERBOSE)

KEYWORDS = {"if", "else", "while", "syscall", "var", "int", "float", "cmd", "cmdf",
            "true", "false", "device", "ready", "dcmd", "dcmdf"}


class Token:
    __slots__ = ("kind", "value", "pos")

    def __init__(self, kind, value, pos):
        self.kind = kind
        self.value = value
        self.pos = pos

    def __repr__(self):
        return f"Token({self.kind!r}, {self.value!r})"


def tokenize(src):
    tokens = []
    pos = 0
    n = len(src)
    while pos < n:
        m = WHITESPACE_RE.match(src, pos)
        if m:
            pos = m.end()
            continue
        m = COMMENT_RE.match(src, pos)
        if m:
            pos = m.end()
            continue
        m = TOKEN_RE.match(src, pos)
        if not m:
            raise CompileError(f"unexpected character {src[pos]!r}", pos)
        pos = m.end()
        kind = m.lastgroup
        text = m.group(kind)
        if kind == "IDENT" and text in KEYWORDS:
            tokens.append(Token(text, text, m.start()))
        elif kind == "OP":
            tokens.append(Token(text, text, m.start()))
        elif kind == "STRING":
            tokens.append(Token(kind, text[1:-1], m.start()))  # strip quotes
        else:
            tokens.append(Token(kind, text, m.start()))
    tokens.append(Token("EOF", None, n))
    return tokens


# ---------------------------------------------------------------------------
# AST
# ---------------------------------------------------------------------------
class Num:
    def __init__(self, value, is_float=False): self.value, self.is_float = value, is_float

class Var:
    def __init__(self, name): self.name = name

class Cast:
    def __init__(self, target_type, expr): self.target_type, self.expr = target_type, expr

class Call:
    def __init__(self, name, args): self.name, self.args = name, args

class CmdCall:
    def __init__(self, cmd_name, args, is_float=False):
        self.cmd_name, self.args, self.is_float = cmd_name, args, is_float

class DeviceRef:
    def __init__(self, name): self.name = name

class Ready:
    def __init__(self, dev): self.dev = dev

class DcmdCall:
    def __init__(self, dev, cmd_name, args, is_float=False):
        self.dev, self.cmd_name, self.args, self.is_float = dev, cmd_name, args, is_float

class UnOp:
    def __init__(self, op, operand): self.op, self.operand = op, operand

class BinOp:
    def __init__(self, op, left, right): self.op, self.left, self.right = op, left, right

class Assign:
    def __init__(self, name, expr): self.name, self.expr = name, expr

class ExprStmt:
    def __init__(self, expr): self.expr = expr

class If:
    def __init__(self, cond, then, else_): self.cond, self.then, self.else_ = cond, then, else_

class While:
    def __init__(self, cond, body): self.cond, self.body = cond, body

class Block:
    def __init__(self, stmts): self.stmts = stmts

class SyscallDecl:
    def __init__(self, name, params, id_, return_type="int"):
        self.name, self.params, self.id, self.return_type = name, params, id_, return_type

class VarDecl:
    def __init__(self, name, var_type="int"): self.name, self.var_type = name, var_type


def _at(node, pos):
    """Records the source offset a node started at (first writer wins, so a
    parenthesised expression keeps its inner node's own position)."""
    if not hasattr(node, "pos"):
        node.pos = pos
    return node


def _locating(fn):
    """Codegen wrapper: an error raised without a position gets the position
    of the innermost AST node being generated when it was raised."""
    def wrapper(self, node, *args, **kwargs):
        try:
            return fn(self, node, *args, **kwargs)
        except CompileError as e:
            if e.pos is None:
                e.pos = getattr(node, "pos", None)
            raise
    wrapper.__name__ = fn.__name__
    wrapper.__doc__ = fn.__doc__
    return wrapper


# ---------------------------------------------------------------------------
# Parser (recursive descent)
# ---------------------------------------------------------------------------
class Parser:
    def __init__(self, tokens):
        self.tokens = tokens
        self.i = 0

    def peek(self):
        return self.tokens[self.i]

    def at(self, kind):
        return self.tokens[self.i].kind == kind

    def advance(self):
        t = self.tokens[self.i]
        self.i += 1
        return t

    def expect(self, kind):
        t = self.peek()
        if t.kind != kind:
            raise CompileError(f"expected {kind!r} but found {t.kind!r} ({t.value!r})", t.pos)
        return self.advance()

    def parse_program(self):
        decls = []
        stmts = []
        while not self.at("EOF"):
            if self.at("syscall"):
                decls.append(self.parse_syscall_decl())
            else:
                stmts.append(self.parse_statement())
        return decls, Block(stmts)

    def parse_type(self):
        if self.at("int") or self.at("float"):
            return self.advance().kind
        t = self.peek()
        raise CompileError(f"expected a type ('int' or 'float') but found {t.kind!r}", t.pos)

    def parse_syscall_decl(self):
        start = self.peek().pos
        self.expect("syscall")
        name = self.expect("IDENT").value
        self.expect("(")
        params = []
        if not self.at(")"):
            params.append(self.expect("IDENT").value)
            while self.at(","):
                self.advance()
                params.append(self.expect("IDENT").value)
        self.expect(")")
        return_type = "int"
        if self.at(":"):
            self.advance()
            return_type = self.parse_type()
        self.expect("=")
        id_tok = self.expect("NUMBER")
        self.expect(";")
        return _at(SyscallDecl(name, params, int(id_tok.value, 0), return_type), start)

    def parse_block(self):
        self.expect("{")
        stmts = []
        while not self.at("}"):
            stmts.append(self.parse_statement())
        self.expect("}")
        return Block(stmts)

    def parse_statement(self):
        start = self.peek().pos
        return _at(self._parse_statement(), start)

    def _parse_statement(self):
        if self.at("{"):
            return self.parse_block()
        if self.at("if"):
            return self.parse_if()
        if self.at("while"):
            return self.parse_while()
        if self.at("var"):
            self.advance()
            name = self.expect("IDENT").value
            var_type = "int"
            if self.at(":"):
                self.advance()
                var_type = self.parse_type()
            self.expect(";")
            return VarDecl(name, var_type)
        if self.at("IDENT") and self.tokens[self.i + 1].kind == "=":
            name = self.advance().value
            self.advance()  # '='
            expr = self.parse_expr()
            self.expect(";")
            return Assign(name, expr)
        expr = self.parse_expr()
        self.expect(";")
        return ExprStmt(expr)

    def parse_if(self):
        self.expect("if")
        self.expect("(")
        cond = self.parse_expr()
        self.expect(")")
        then = self.parse_block()
        else_ = None
        if self.at("else"):
            self.advance()
            else_ = self.parse_block()
        return If(cond, then, else_)

    def parse_while(self):
        self.expect("while")
        self.expect("(")
        cond = self.parse_expr()
        self.expect(")")
        body = self.parse_block()
        return While(cond, body)

    def parse_expr(self):
        return self.parse_or()

    def parse_or(self):
        left = self.parse_and()
        while self.at("||"):
            tok = self.advance()
            left = _at(BinOp("||", left, self.parse_and()), tok.pos)
        return left

    def parse_and(self):
        left = self.parse_cmp()
        while self.at("&&"):
            tok = self.advance()
            left = _at(BinOp("&&", left, self.parse_cmp()), tok.pos)
        return left

    def parse_cmp(self):
        left = self.parse_add()
        while self.peek().kind in ("==", "!=", "<", "<=", ">", ">="):
            tok = self.advance()
            left = _at(BinOp(tok.kind, left, self.parse_add()), tok.pos)
        return left

    def parse_add(self):
        left = self.parse_mul()
        while self.peek().kind in ("+", "-"):
            tok = self.advance()
            left = _at(BinOp(tok.kind, left, self.parse_mul()), tok.pos)
        return left

    def parse_mul(self):
        left = self.parse_unary()
        while self.peek().kind in ("*", "/", "%"):
            tok = self.advance()
            left = _at(BinOp(tok.kind, left, self.parse_unary()), tok.pos)
        return left

    def parse_unary(self):
        start = self.peek().pos
        return _at(self._parse_unary(), start)

    def _parse_unary(self):
        if self.peek().kind in ("-", "!"):
            op = self.advance().kind
            return UnOp(op, self.parse_unary())
        if (self.at("(") and self.tokens[self.i + 1].kind in ("int", "float")
                and self.tokens[self.i + 2].kind == ")"):
            self.advance()  # '('
            target_type = self.parse_type()
            self.advance()  # ')'
            return Cast(target_type, self.parse_unary())
        return self.parse_primary()

    def parse_primary(self):
        start = self.peek().pos
        return _at(self._parse_primary(), start)

    def _parse_primary(self):
        t = self.peek()
        if t.kind == "NUMBER":
            self.advance()
            return Num(int(t.value, 0), is_float=False)
        if t.kind == "FLOAT":
            self.advance()
            return Num(float(t.value), is_float=True)
        if t.kind in ("true", "false"):
            # Plain int literals 1/0 -- no bool type. Sent by cmd() as "1"/"0",
            # which GAACE_Core's CMDbool accepts alongside TRUE/FALSE.
            self.advance()
            return Num(1 if t.kind == "true" else 0, is_float=False)
        if t.kind == "IDENT":
            self.advance()
            if self.at("("):
                self.advance()
                args = []
                if not self.at(")"):
                    args.append(self.parse_expr())
                    while self.at(","):
                        self.advance()
                        args.append(self.parse_expr())
                self.expect(")")
                return Call(t.value, args)
            return Var(t.value)
        if t.kind in ("cmd", "cmdf"):
            self.advance()
            self.expect("(")
            name_tok = self.expect("STRING")
            args = []
            while self.at(","):
                self.advance()
                args.append(self.parse_expr())
            self.expect(")")
            node = CmdCall(name_tok.value, args, is_float=(t.kind == "cmdf"))
            node.name_pos = name_tok.pos   # where the "NAME" string is, for warnings
            return node
        if t.kind == "device":
            self.advance()
            self.expect("(")
            name_tok = self.expect("STRING")
            self.expect(")")
            return DeviceRef(name_tok.value)
        if t.kind == "ready":
            self.advance()
            self.expect("(")
            dev = self.parse_expr()
            self.expect(")")
            return Ready(dev)
        if t.kind in ("dcmd", "dcmdf"):
            self.advance()
            self.expect("(")
            dev = self.parse_expr()
            self.expect(",")
            name_tok = self.expect("STRING")
            args = []
            while self.at(","):
                self.advance()
                args.append(self.parse_expr())
            self.expect(")")
            return DcmdCall(dev, name_tok.value, args, is_float=(t.kind == "dcmdf"))
        if t.kind == "(":
            self.advance()
            e = self.parse_expr()
            self.expect(")")
            return e
        raise CompileError(f"unexpected token {t.kind!r} ({t.value!r})", t.pos)


# ---------------------------------------------------------------------------
# Codegen
# ---------------------------------------------------------------------------
class Codegen:
    def __init__(self, syscalls, from_table=False):
        self.syscalls = syscalls  # name -> (id, argc, return_type)
        self.from_table = from_table  # syscalls came from a device/file table
        self.used_syscalls = set()    # names actually called by the script
        self.cmd_uses = []            # (name, source offset) for every cmd()/cmdf()
        self.buf = bytearray()
        self.vars = {}            # name -> slot
        self.var_types = {}       # name -> "int" | "float"
        self.fixups = []          # list of (pos, label)
        self.labels = {}          # label -> address
        self._next_label = 0
        self.pool = bytearray()   # string constants for cmd(), NUL-terminated
        self._pool_offsets = {}   # name -> offset already in self.pool (dedup)

    def pool_offset_for(self, name):
        if name in self._pool_offsets:
            return self._pool_offsets[name]
        if len(name.encode("ascii")) != len(name):
            raise CompileError(f"cmd() name {name!r} must be plain ASCII")
        offset = len(self.pool)
        self.pool += name.encode("ascii") + b"\x00"
        self._pool_offsets[name] = offset
        return offset

    def new_label(self):
        self._next_label += 1
        return self._next_label

    def mark_label(self, label):
        self.labels[label] = len(self.buf)

    def slot_for(self, name, declare=False, var_type=None):
        if name in self.vars:
            return self.vars[name]
        if not declare:
            raise CompileError(f"variable '{name}' used before assignment")
        if len(self.vars) >= VAR_SLOTS:
            raise CompileError(f"too many variables (max {VAR_SLOTS})")
        slot = len(self.vars)
        self.vars[name] = slot
        self.var_types[name] = var_type or "int"
        return slot

    def emit(self, byte):
        self.buf.append(byte)

    def emit_u16_placeholder(self):
        pos = len(self.buf)
        self.buf += b"\x00\x00"
        return pos

    def patch_u16(self, pos, value):
        self.buf[pos] = value & 0xFF
        self.buf[pos + 1] = (value >> 8) & 0xFF

    def emit_push(self, value):
        self.emit(OP_PUSH_I32)
        self.buf += int(value).to_bytes(4, "little", signed=True)

    def emit_push_f32(self, value):
        self.emit(OP_PUSH_F32)
        self.buf += struct.pack("<f", value)

    def emit_convert(self, from_type, to_type):
        if from_type == to_type:
            return
        if from_type == "int" and to_type == "float":
            self.emit(OP_I2F)
        elif from_type == "float" and to_type == "int":
            self.emit(OP_F2I)
        else:
            raise CompileError(f"internal error: bad conversion {from_type} -> {to_type}")

    def infer_type(self, expr):
        """Type of `expr` without emitting anything -- used to decide, e.g.,
        whether a binary op's operands need promoting, before the left
        operand (already emitted) can be converted in place."""
        if isinstance(expr, Num):
            return "float" if expr.is_float else "int"
        if isinstance(expr, Var):
            if expr.name not in self.var_types:
                raise CompileError(f"variable '{expr.name}' used before assignment")
            return self.var_types[expr.name]
        if isinstance(expr, Cast):
            return expr.target_type
        if isinstance(expr, UnOp):
            if expr.op == "!":
                if self.infer_type(expr.operand) != "int":
                    raise CompileError("'!' requires an int operand")
                return "int"
            return self.infer_type(expr.operand)  # '-' preserves type
        if isinstance(expr, BinOp):
            if expr.op in LOGIC_OPS or expr.op in COMPARISON_OPS:
                return "int"
            lt = self.infer_type(expr.left)
            rt = self.infer_type(expr.right)
            common = "float" if (lt == "float" or rt == "float") else "int"
            if expr.op == "%" and common == "float":
                raise CompileError("'%' has no float form")
            return common
        if isinstance(expr, Call):
            if expr.name not in self.syscalls:
                raise CompileError(f"call to undeclared syscall '{expr.name}'")
            return self.syscalls[expr.name][2]
        if isinstance(expr, CmdCall):
            return "float" if expr.is_float else "int"
        if isinstance(expr, (DeviceRef, Ready)):
            return "int"
        if isinstance(expr, DcmdCall):
            return "float" if expr.is_float else "int"
        raise CompileError(f"internal error: unknown expression {expr!r}")

    def emit_jump(self, op, label):
        self.emit(op)
        pos = self.emit_u16_placeholder()
        self.fixups.append((pos, label))

    def resolve_fixups(self):
        for pos, label in self.fixups:
            if label not in self.labels:
                raise CompileError(f"internal error: label {label} never marked")
            self.patch_u16(pos, self.labels[label])

    def gen_program(self, block):
        self.gen_block(block)
        self.emit(OP_HALT)
        self.resolve_fixups()
        return bytes(self.buf)

    def gen_block(self, block):
        for stmt in block.stmts:
            self.gen_stmt(stmt)

    @_locating
    def gen_stmt(self, stmt):
        if isinstance(stmt, Block):
            self.gen_block(stmt)
        elif isinstance(stmt, VarDecl):
            self.slot_for(stmt.name, declare=True, var_type=stmt.var_type)
        elif isinstance(stmt, Assign):
            expr_type = self.gen_expr(stmt.expr)
            if stmt.name in self.vars:
                var_type = self.var_types[stmt.name]
                if expr_type != var_type:
                    if expr_type == "int" and var_type == "float":
                        self.emit_convert("int", "float")
                    else:
                        raise CompileError(
                            f"cannot assign a {expr_type} expression to {var_type} "
                            f"variable '{stmt.name}' -- use an explicit (int)/(float) cast")
                slot = self.vars[stmt.name]
            else:
                slot = self.slot_for(stmt.name, declare=True, var_type=expr_type)
            self.emit(OP_STORE)
            self.emit(slot)
        elif isinstance(stmt, ExprStmt):
            self.gen_expr(stmt.expr)
            self.emit(OP_POP)  # statement's value is unused
        elif isinstance(stmt, If):
            self.gen_expr(stmt.cond)
            else_label = self.new_label()
            end_label = self.new_label()
            self.emit_jump(OP_JZ, else_label)
            self.gen_block(stmt.then)
            self.emit_jump(OP_JMP, end_label)
            self.mark_label(else_label)
            if stmt.else_ is not None:
                self.gen_block(stmt.else_)
            self.mark_label(end_label)
        elif isinstance(stmt, While):
            loop_label = self.new_label()
            end_label = self.new_label()
            self.mark_label(loop_label)
            self.gen_expr(stmt.cond)
            self.emit_jump(OP_JZ, end_label)
            self.gen_block(stmt.body)
            self.emit_jump(OP_JMP, loop_label)
            self.mark_label(end_label)
        else:
            raise CompileError(f"internal error: unknown statement {stmt!r}")

    @_locating
    def gen_expr(self, expr):
        """Emits code to evaluate `expr`, leaving one value on the stack.
        Returns the type ("int" or "float") of that value."""
        if isinstance(expr, Num):
            if expr.is_float:
                self.emit_push_f32(expr.value)
                return "float"
            self.emit_push(expr.value)
            return "int"

        if isinstance(expr, Var):
            t = self.infer_type(expr)  # also raises "used before assignment"
            slot = self.vars[expr.name]
            self.emit(OP_LOAD)
            self.emit(slot)
            return t

        if isinstance(expr, Cast):
            src_type = self.gen_expr(expr.expr)
            self.emit_convert(src_type, expr.target_type)
            return expr.target_type

        if isinstance(expr, UnOp):
            t = self.gen_expr(expr.operand)
            if expr.op == "-":
                self.emit(OP_FNEG if t == "float" else OP_NEG)
                return t
            if t != "int":
                raise CompileError("'!' requires an int operand")
            self.emit(OP_NOT)
            return "int"

        if isinstance(expr, BinOp):
            if expr.op in LOGIC_OPS:
                lt = self.gen_expr(expr.left)
                if lt != "int":
                    raise CompileError(f"'{expr.op}' requires int operands")
                rt = self.gen_expr(expr.right)
                if rt != "int":
                    raise CompileError(f"'{expr.op}' requires int operands")
                self.emit(BINOP_OPCODE[expr.op])
                return "int"

            # Arithmetic / comparison: promote to a common type. The left
            # operand is emitted first, so its conversion (if any) has to
            # happen before the right operand is emitted -- that requires
            # knowing the right operand's type ahead of emitting it, hence
            # infer_type() rather than a second gen_expr() call here.
            lt = self.gen_expr(expr.left)
            rt_type = self.infer_type(expr.right)
            common = "float" if (lt == "float" or rt_type == "float") else "int"
            if expr.op == "%" and common == "float":
                raise CompileError("'%' has no float form")
            self.emit_convert(lt, common)
            rt = self.gen_expr(expr.right)
            self.emit_convert(rt, common)
            self.emit(BINOP_OPCODE_F[expr.op] if common == "float" else BINOP_OPCODE[expr.op])
            return "int" if expr.op in COMPARISON_OPS else common

        if isinstance(expr, Call):
            if expr.name not in self.syscalls:
                if self.from_table:
                    avail = ", ".join(sorted(self.syscalls)) or "(none)"
                    raise CompileError(
                        f"'{expr.name}' is not a syscall on this device (it offers: {avail})")
                raise CompileError(
                    f"call to undeclared syscall '{expr.name}' -- declare it with "
                    f"`syscall {expr.name}(...) = ID;`, or compile against the device's "
                    f"syscall table (--upload queries it; or --syscalls FILE)")
            self.used_syscalls.add(expr.name)
            id_, argc, return_type = self.syscalls[expr.name]
            if len(expr.args) != argc:
                raise CompileError(
                    f"'{expr.name}' declared with {argc} argument(s), called with {len(expr.args)}")
            for a in expr.args:
                self.gen_expr(a)
            self.emit(OP_CALL)
            self.emit(id_)
            self.emit(len(expr.args))
            return return_type

        if isinstance(expr, CmdCall):
            self.cmd_uses.append((expr.cmd_name, getattr(expr, "name_pos", getattr(expr, "pos", None))))
            offset = self.pool_offset_for(expr.cmd_name)
            if offset > 0xFFFF:
                raise CompileError("string pool too large (over 65535 bytes)")
            if len(expr.args) > 0xFF:
                raise CompileError(f"cmd({expr.cmd_name!r}, ...) has too many arguments")
            if len(expr.args) > 16:
                raise CompileError(f"cmd({expr.cmd_name!r}, ...) has more than 16 arguments")
            mask = 0
            for i, a in enumerate(expr.args):
                if self.gen_expr(a) == "float":
                    mask |= 1 << i
            if mask:
                self.emit(OP_CMDCALLMF if expr.is_float else OP_CMDCALLM)
            else:
                self.emit(OP_CMDCALLF if expr.is_float else OP_CMDCALL)
            self.buf += offset.to_bytes(2, "little")
            self.emit(len(expr.args))
            if mask:
                self.buf += mask.to_bytes(2, "little")
            return "float" if expr.is_float else "int"

        if isinstance(expr, DeviceRef):
            offset = self.pool_offset_for(expr.name)
            if offset > 0xFFFF:
                raise CompileError("string pool too large (over 65535 bytes)")
            self.emit(OP_DEVICE)
            self.buf += offset.to_bytes(2, "little")
            return "int"

        if isinstance(expr, Ready):
            if self.gen_expr(expr.dev) != "int":
                raise CompileError("ready() takes a device handle (an int, from device())")
            self.emit(OP_DREADY)
            return "int"

        if isinstance(expr, DcmdCall):
            offset = self.pool_offset_for(expr.cmd_name)
            if offset > 0xFFFF:
                raise CompileError("string pool too large (over 65535 bytes)")
            if len(expr.args) > 16:
                raise CompileError(f"dcmd(..., {expr.cmd_name!r}, ...) has more than 16 arguments")
            if self.gen_expr(expr.dev) != "int":
                raise CompileError("dcmd()'s first argument is a device handle (an int, from device())")
            mask = 0
            for i, a in enumerate(expr.args):
                if self.gen_expr(a) == "float":
                    mask |= 1 << i
            self.emit(OP_DCMDCALLF if expr.is_float else OP_DCMDCALL)
            self.buf += offset.to_bytes(2, "little")
            self.emit(len(expr.args))
            self.buf += mask.to_bytes(2, "little")
            return "float" if expr.is_float else "int"

        raise CompileError(f"internal error: unknown expression {expr!r}")


class CompiledScript:
    """Result of compile_script(): the code, the cmd() string pool, and whether
    the script calls any syscalls (if it doesn't, the syscall-table hash is
    irrelevant to it and isn't stamped on upload)."""

    def __init__(self, code, pool, uses_syscalls, cmd_uses=(), warnings=()):
        self.code, self.pool, self.uses_syscalls = code, pool, uses_syscalls
        self.cmd_uses = list(cmd_uses)   # (name, source offset) per cmd()/cmdf() call
        # CompileError objects used as non-fatal diagnostics (located like errors).
        self.warnings = list(warnings)


def compile_script(src, syscall_table=None, commands=None):
    """Compiles `src`; a CompileError that escapes has line/column filled in.

    With a `syscall_table` (a SyscallTable from the device
    or a saved file), every syscall in it is callable without a declaration,
    and any explicit `syscall NAME(...) = ID;` declaration must agree with it
    -- a disagreement (wrong id, argument count, return type, or a name the
    table doesn't have) is a compile error rather than a script that
    silently calls the wrong function."""
    try:
        compiled = _compile_script(src, syscall_table)
    except CompileError as e:
        e.locate(src)
        raise
    if commands is not None:
        compiled.warnings = check_commands(src, compiled.cmd_uses, commands)
    return compiled


def check_commands(src, cmd_uses, commands):
    """Warnings (located CompileError objects) for every cmd()/cmdf() whose
    command name the device's command list doesn't contain. Not errors: the
    device may be case-insensitive, or the list may be out of date. The call
    still compiles and, on the device, a NAK just reads as 0."""
    warnings = []
    for name, pos in cmd_uses:
        if name in commands.names:
            continue
        near = commands.suggest(name)
        if near is not None and near.upper() == name.upper():
            hint = f" -- the device has '{near}' (names are case-sensitive unless CASE is FALSE)"
        elif near is not None:
            hint = f" -- did you mean '{near}'?"
        else:
            hint = ""
        w = CompileError(f"the device has no command '{name}'; at run time cmd() would "
                         f"return 0{hint}", pos)
        w.locate(src)
        warnings.append(w)
    return warnings


def _compile_script(src, syscall_table):
    tokens = tokenize(src)
    decls, program = Parser(tokens).parse_program()

    syscalls = syscall_table.lookup() if syscall_table else {}
    declared = set()
    for d in decls:
        if d.name in declared:
            raise CompileError(f"syscall '{d.name}' declared more than once",
                               getattr(d, "pos", None))
        declared.add(d.name)
        decl = (d.id, len(d.params), d.return_type)
        if syscall_table:
            actual = syscalls.get(d.name)
            if actual is None:
                raise CompileError(
                    f"syscall '{d.name}' is declared but the device has no such syscall "
                    f"(it offers: {', '.join(sorted(syscalls)) or '(none)'})",
                    getattr(d, "pos", None))
            if decl != actual:
                raise CompileError(
                    f"syscall '{d.name}' is declared as id {decl[0]}, {decl[1]} arg(s), "
                    f"returns {decl[2]}, but the device has id {actual[0]}, {actual[1]} "
                    f"arg(s), returns {actual[2]} -- delete the declaration to use the "
                    f"device's own", getattr(d, "pos", None))
        else:
            syscalls[d.name] = decl
    codegen = Codegen(syscalls, from_table=bool(syscall_table))
    code = codegen.gen_program(program)
    return CompiledScript(code, bytes(codegen.pool), bool(codegen.used_syscalls),
                          codegen.cmd_uses)


def compile_source_with_pool(src, syscall_table=None):
    """Returns (code, pool). `pool` is the string-constant table cmd() calls
    reference (empty bytes if the script never uses cmd()) -- see gsc.py's
    module docstring and tools/README.md for what to do with it."""
    result = compile_script(src, syscall_table)
    return result.code, result.pool


def compile_source(src):
    """Back-compat wrapper for callers that only need the code (no cmd())."""
    return compile_source_with_pool(src)[0]


def build_scriptload_payload(code, pool):
    """[codeLen:u16 LE][code][pool] -- the wire format SCRIPTLOAD expects.
    Shared by --scriptload (file output) and --upload (sends it directly)."""
    return len(code).to_bytes(2, "little") + code + pool


# ---------------------------------------------------------------------------
# Disassembler (debugging aid)
# ---------------------------------------------------------------------------
_OPNAMES = {
    OP_HALT: "HALT", OP_PUSH_I32: "PUSH_I32", OP_DUP: "DUP", OP_POP: "POP",
    OP_LOAD: "LOAD", OP_STORE: "STORE", OP_ADD: "ADD", OP_SUB: "SUB",
    OP_MUL: "MUL", OP_DIV: "DIV", OP_MOD: "MOD", OP_NEG: "NEG",
    OP_EQ: "EQ", OP_NE: "NE", OP_LT: "LT", OP_LE: "LE", OP_GT: "GT", OP_GE: "GE",
    OP_AND: "AND", OP_OR: "OR", OP_NOT: "NOT",
    OP_JMP: "JMP", OP_JZ: "JZ", OP_JNZ: "JNZ", OP_CALL: "CALL",
    OP_PUSH_F32: "PUSH_F32", OP_FADD: "FADD", OP_FSUB: "FSUB", OP_FMUL: "FMUL",
    OP_FDIV: "FDIV", OP_FNEG: "FNEG", OP_FEQ: "FEQ", OP_FNE: "FNE",
    OP_FLT: "FLT", OP_FLE: "FLE", OP_FGT: "FGT", OP_FGE: "FGE",
    OP_I2F: "I2F", OP_F2I: "F2I", OP_CMDCALL: "CMDCALL", OP_CMDCALLF: "CMDCALLF",
    OP_CMDCALLM: "CMDCALLM", OP_CMDCALLMF: "CMDCALLMF",
    OP_DEVICE: "DEVICE", OP_DREADY: "DREADY", OP_DCMDCALL: "DCMDCALL", OP_DCMDCALLF: "DCMDCALLF",
}
_OPERAND_LEN = {
    OP_PUSH_I32: 4, OP_PUSH_F32: 4, OP_LOAD: 1, OP_STORE: 1,
    OP_JMP: 2, OP_JZ: 2, OP_JNZ: 2, OP_CALL: 2, OP_CMDCALL: 3, OP_CMDCALLF: 3,
    OP_CMDCALLM: 5, OP_CMDCALLMF: 5, OP_DEVICE: 2, OP_DCMDCALL: 5, OP_DCMDCALLF: 5,
}


def _pool_string_at(pool, offset):
    end = pool.find(b"\x00", offset)
    if end == -1:
        return None
    return pool[offset:end].decode("ascii", errors="replace")


def disassemble(code: bytes, pool: bytes = b"") -> str:
    lines = []
    pc = 0
    n = len(code)
    while pc < n:
        op = code[pc]
        name = _OPNAMES.get(op, f"??({op})")
        extra = _OPERAND_LEN.get(op, 0)
        if op == OP_PUSH_I32:
            val = int.from_bytes(code[pc + 1:pc + 5], "little", signed=True)
            lines.append(f"{pc:4d}: {name} {val}")
        elif op == OP_PUSH_F32:
            val = struct.unpack("<f", code[pc + 1:pc + 5])[0]
            lines.append(f"{pc:4d}: {name} {val}")
        elif op in (OP_LOAD, OP_STORE):
            lines.append(f"{pc:4d}: {name} slot={code[pc + 1]}")
        elif op in (OP_JMP, OP_JZ, OP_JNZ):
            target = int.from_bytes(code[pc + 1:pc + 3], "little")
            lines.append(f"{pc:4d}: {name} -> {target}")
        elif op == OP_CALL:
            lines.append(f"{pc:4d}: {name} id={code[pc + 1]} argc={code[pc + 2]}")
        elif op in (OP_CMDCALL, OP_CMDCALLF, OP_CMDCALLM, OP_CMDCALLMF):
            offset = int.from_bytes(code[pc + 1:pc + 3], "little")
            argc = code[pc + 3]
            name_str = _pool_string_at(pool, offset)
            label = f'"{name_str}"' if name_str is not None else f"offset={offset}"
            extra_txt = ""
            if op in (OP_CMDCALLM, OP_CMDCALLMF):
                extra_txt = f" floatmask=0x{int.from_bytes(code[pc + 4:pc + 6], 'little'):04x}"
            lines.append(f"{pc:4d}: {name} {label} argc={argc}{extra_txt}")
        elif op == OP_DEVICE:
            offset = int.from_bytes(code[pc + 1:pc + 3], "little")
            name_str = _pool_string_at(pool, offset)
            lines.append(f'{pc:4d}: {name} "{name_str}"' if name_str is not None
                         else f"{pc:4d}: {name} offset={offset}")
        elif op in (OP_DCMDCALL, OP_DCMDCALLF):
            offset = int.from_bytes(code[pc + 1:pc + 3], "little")
            name_str = _pool_string_at(pool, offset)
            label = f'"{name_str}"' if name_str is not None else f"offset={offset}"
            mask = int.from_bytes(code[pc + 4:pc + 6], "little")
            lines.append(f"{pc:4d}: {name} {label} argc={code[pc + 3]} floatmask=0x{mask:04x}")
        else:
            lines.append(f"{pc:4d}: {name}")
        pc += 1 + extra
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main(argv=None):
    ap = argparse.ArgumentParser(description="GAACE_Script compiler")
    ap.add_argument("source", nargs="?",
                     help="path to .gs source file, or '-' for stdin (not needed with "
                          "--list-syscalls)")
    ap.add_argument("-o", "--output", help="output file (default: stdout for hex/carray, "
                                            "<source>.bin for bin)")
    ap.add_argument("--format", choices=["bin", "hex", "carray"], default="bin")
    ap.add_argument("--carray-name", default="script", help="identifier for --format carray")
    ap.add_argument("--disasm", action="store_true", help="print disassembly instead of writing output")
    ap.add_argument("--scriptload", action="store_true",
                     help="wrap code+pool as [codeLen:u16][code][pool], ready to paste "
                          "straight into SCRIPTLOAD,<slot>,<hex> (see GAACEScriptRuntime.h). "
                          "Without this flag, code and pool (if the script uses cmd()) are "
                          "emitted as-is, matching vmInit()/vmSetPool() called by hand.")
    ap.add_argument("--upload", metavar="PORT",
                     help="compile and send straight to a running controller's control port "
                          "(e.g. --upload /dev/ttyUSB0 or --upload COM5): a single SCRIPTLOAD "
                          "for a small script, or SCRIPTAPPEND chunks + SCRIPTCOMMIT for a "
                          "bigger one (see --chunk-bytes). Requires --slot and pyserial "
                          "(pip install pyserial). Implies the same wire format as "
                          "--scriptload; --format/-o are ignored.")
    ap.add_argument("--slot", type=int, metavar="N", help="script slot for --upload")
    ap.add_argument("--baud", type=int, default=115200, help="baud rate for --upload (default 115200)")
    ap.add_argument("--timeout", type=float, default=3.0,
                     help="seconds to wait for ACK/NAK from --upload (default 3.0)")
    ap.add_argument("--chunk-bytes", type=int, default=200, metavar="N",
                     help="decoded bytes per SCRIPTAPPEND chunk for --upload (default 200). "
                          "A payload at or under this size uses a single SCRIPTLOAD instead. "
                          "Keep well under ~250 -- see GAACEScriptRuntime.h for why that's the "
                          "real ceiling (commandProcessor's shared 512-byte scratch arena), "
                          "regardless of GAACE_SCRIPT_MAX_CODE_LEN.")
    ap.add_argument("--syscalls", metavar="FILE",
                     help="compile against this saved syscall table (JSON, from "
                          "--save-syscalls) instead of asking a device: syscalls in it "
                          "need no declaration, and any declaration in the script must "
                          "agree with it. With --upload this replaces the device query; "
                          "the device then verifies the table's hash at load time.")
    ap.add_argument("--list-syscalls", metavar="PORT",
                     help="ask the controller on PORT for its syscall table, print it, and "
                          "exit (no source file needed). Add --save-syscalls to keep it.")
    ap.add_argument("--save-syscalls", metavar="FILE",
                     help="with --upload or --list-syscalls: write the table the device "
                          "reported to FILE (JSON) for later offline use with --syscalls")
    ap.add_argument("--no-device-syscalls", action="store_true",
                     help="with --upload: don't ask the device for its syscall table; the "
                          "script must declare every syscall it uses, as in older versions")
    ap.add_argument("--commands", metavar="FILE",
                     help="warn about cmd()/cmdf() names missing from this saved command "
                          "list (JSON, from --save-commands). With --upload the list is "
                          "asked of the device (GCMDS) instead, unless this is given.")
    ap.add_argument("--list-commands", metavar="PORT",
                     help="ask the controller on PORT for its commands (GCMDS), print them, "
                          "and exit. Add --save-commands to keep the list.")
    ap.add_argument("--save-commands", metavar="FILE",
                     help="with --upload or --list-commands: write the command list the "
                          "device reported to FILE (JSON) for later use with --commands")
    ap.add_argument("--no-device-commands", action="store_true",
                     help="with --upload: don't ask the device for its command list "
                          "(no unknown-command warnings)")
    ap.add_argument("--json", action="store_true",
                     help="compile only, and print one JSON object on stdout instead of "
                          "text: {\"ok\": true, \"payload\": <SCRIPTLOAD hex>, ...} or "
                          "{\"ok\": false, \"error\": {\"message\", \"line\", \"column\", "
                          "...}}. Exit code is 0 on success, 1 on error. For programs that "
                          "drive gsc.py (see MIPS_HOST_APP_INTEGRATION.md).")
    ap.add_argument("--version", action="version", version=f"gsc {__version__}")
    args = ap.parse_args(argv)
    if args.json and (args.upload or args.list_syscalls or args.list_commands):
        ap.error("--json compiles only; it can't be combined with --upload, "
                 "--list-syscalls or --list-commands")

    if args.list_syscalls:
        return list_syscalls(args)
    if args.list_commands:
        return list_commands(args)
    if args.source is None:
        ap.error("a source file is required (or use --list-syscalls PORT)")

    try:
        src = sys.stdin.read() if args.source == "-" else open(args.source).read()
    except OSError as e:
        if args.json:
            return _print_json({"ok": False, "error": {"message": f"cannot read {args.source}: {e}"}})
        raise

    if args.json:
        return compile_json(args, src)

    if args.upload:
        return compile_and_upload(args, src)

    table = None
    if args.syscalls:
        try:
            table = load_syscall_table_file(args.syscalls)
        except (OSError, SyscallTableError) as e:
            print(f"error: {e}", file=sys.stderr)
            return 1

    commands = None
    if args.commands:
        try:
            commands = load_command_list_file(args.commands)
        except (OSError, SyscallTableError) as e:
            print(f"error: {e}", file=sys.stderr)
            return 1

    try:
        compiled = compile_script(src, table, commands)
    except CompileError as e:
        print(f"error: {e.describe()}", file=sys.stderr)
        return 1
    _print_warnings(compiled)
    code, pool = compiled.code, compiled.pool

    if args.disasm:
        print(disassemble(code, pool))
        return 0

    if table is not None and compiled.uses_syscalls and (args.scriptload or args.format == "carray"):
        print(f"note: compiled against a syscall table with hash {table.hash:08X}; append "
              f"it as the optional last argument of SCRIPTLOAD/SCRIPTCOMMIT so the device "
              f"can reject the script if its syscalls differ", file=sys.stderr)

    if args.scriptload:
        payload = build_scriptload_payload(code, pool)
        _emit(payload, args, name_suffix="")
        return 0

    if pool and args.format in ("bin", "hex"):
        print(f"warning: this script uses cmd() (pool is {len(pool)} bytes) but "
              f"--format {args.format} only emits the code; the pool isn't written "
              f"anywhere. Use --format carray (emits both), or --scriptload (combines "
              f"them for the SCRIPTLOAD wire format).", file=sys.stderr)

    if args.format == "carray":
        _emit_carray(code, pool, args)
    else:
        _emit(code, args, name_suffix="")

    return 0


def _print_json(obj):
    """Prints `obj` as the one JSON line --json emits; exit code 0 if it
    reports success, 1 otherwise."""
    obj = {"version": __version__, **obj}
    print(json.dumps(obj))
    return 0 if obj.get("ok") else 1


def compile_json(args, src):
    """--json: compile `src` (against --syscalls FILE if given) and report the
    outcome as JSON instead of text. Nothing else goes to stdout."""
    table = None
    if args.syscalls:
        try:
            table = load_syscall_table_file(args.syscalls)
        except (OSError, SyscallTableError) as e:
            return _print_json({"ok": False, "error": {"message": str(e)}})
    commands = None
    if args.commands:
        try:
            commands = load_command_list_file(args.commands)
        except (OSError, SyscallTableError) as e:
            return _print_json({"ok": False, "error": {"message": str(e)}})
    try:
        compiled = compile_script(src, table, commands)
    except CompileError as e:
        return _print_json({"ok": False, "error": e.to_dict()})
    payload = build_scriptload_payload(compiled.code, compiled.pool)
    # Same rule as --upload: only a script that calls syscalls is stamped.
    stamp = table.hash if (table is not None and compiled.uses_syscalls) else None
    return _print_json({
        "ok": True,
        "payload": payload.hex(),            # exactly what SCRIPTLOAD/SCRIPTAPPEND carry
        "code_len": len(compiled.code),
        "pool_len": len(compiled.pool),
        "uses_syscalls": compiled.uses_syscalls,
        "syscall_hash": f"{stamp:08X}" if stamp else None,  # last arg of SCRIPTLOAD/COMMIT
        "disasm": disassemble(compiled.code, compiled.pool),
        "warnings": [w.to_dict() for w in compiled.warnings],  # e.g. unknown cmd() names
    })


def upload_script(port, slot, payload_hex, baud=115200, timeout=3.0, chunk_bytes=200,
                  syscall_hash=None):
    """Sends a compiled script to `port` and reports the result.

    A payload that fits in `chunk_bytes` or fewer decoded bytes goes via a
    single SCRIPTLOAD,<slot>,<hex> -- unchanged from before. Anything
    bigger is split into SCRIPTAPPEND,<slot>,<offset>,<hexchunk> calls of at
    most `chunk_bytes` decoded bytes each, finished with
    SCRIPTCOMMIT,<slot>,<totalLen>. This is what gets a script past
    SCRIPTLOAD's real ceiling (commandProcessor's shared 512-byte scratch
    arena -- see GAACEScriptRuntime.h) without needing GAACE_SCRIPT_MAX_CODE_LEN
    raised to help: each chunk individually stays well under that arena
    regardless of how large the total script or the slot's buffer are.

    `syscall_hash` (an int from SyscallTable.hash) is appended as the optional
    last argument of SCRIPTLOAD/SCRIPTCOMMIT, so the device can refuse a
    script compiled against a different syscall table than its own.

    Returns a process exit code (0 on success, 1 otherwise) so this
    composes with shell scripting.
    """
    ser = _open_port(port, baud, timeout)
    if ser is None:
        return 1
    with ser:
        time.sleep(0.2)  # let the connection settle before writing
        return _upload_on(ser, slot, payload_hex, timeout, chunk_bytes, syscall_hash)


def _open_port(port, baud, timeout):
    """Opens `port` with pyserial; prints the reason and returns None on failure."""
    try:
        import serial
    except ImportError:
        print("error: --upload requires pyserial -- pip install pyserial", file=sys.stderr)
        return None

    try:
        return serial.Serial(port, baud, timeout=timeout)
    except serial.SerialException as e:
        print(f"error: could not open {port}: {e}", file=sys.stderr)
        return None


def _upload_on(ser, slot, payload_hex, timeout, chunk_bytes, syscall_hash=None):
    """The SCRIPTLOAD / SCRIPTAPPEND+SCRIPTCOMMIT exchange on an already-open
    port (see upload_script() for the protocol)."""
    hash_arg = f",{syscall_hash:08X}" if syscall_hash else ""

    def send(line):
        ser.reset_input_buffer()
        ser.write((line + "\n").encode("ascii"))
        return ser.read(256)

    def report_failure(step, response):
        if not response:
            print(f"error: no response from device within {timeout}s after {step} "
                  f"(wrong port/baud, or the control port isn't the one you opened?)",
                  file=sys.stderr)
        elif response[0] == 0x15:
            print(f"NAK: device rejected {step} (bad slot/offset, malformed hex, over the "
                  f"configured size limit -- see GSCRIPTLIMITS -- or, if a syscall table "
                  f"hash was sent, the firmware's syscalls differ from the ones this "
                  f"script was compiled against: re-run without --syscalls, or re-save "
                  f"the table with --list-syscalls ... --save-syscalls)", file=sys.stderr)
        else:
            print(f"warning: unexpected response from device after {step}: "
                  f"{response!r}", file=sys.stderr)

    total_len = len(payload_hex) // 2

    if total_len <= chunk_bytes:
        response = send(f"SCRIPTLOAD,{slot},{payload_hex}{hash_arg}")
        if not response or response[0] != 0x06:
            report_failure("SCRIPTLOAD", response)
            return 1
        print(f"OK: script loaded into slot {slot} ({total_len} bytes, single SCRIPTLOAD)")
        return 0

    chunk_count = 0
    offset = 0
    while offset < total_len:
        chunk_hex = payload_hex[offset * 2: (offset + chunk_bytes) * 2]
        response = send(f"SCRIPTAPPEND,{slot},{offset},{chunk_hex}")
        if not response or response[0] != 0x06:
            report_failure(f"SCRIPTAPPEND at offset {offset}", response)
            return 1
        offset += chunk_bytes
        chunk_count += 1

    response = send(f"SCRIPTCOMMIT,{slot},{total_len}{hash_arg}")
    if not response or response[0] != 0x06:
        report_failure("SCRIPTCOMMIT", response)
        return 1

    print(f"OK: script loaded into slot {slot} ({total_len} bytes, "
          f"{chunk_count} SCRIPTAPPEND chunks + SCRIPTCOMMIT)")
    return 0


def load_syscall_table_file(path):
    with open(path) as f:
        return SyscallTable.from_json(f.read())


def _device_table(ser, args):
    """The syscall table to compile against for an --upload on open port `ser`:
    from --syscalls FILE, else asked of the device, else None (the script then
    has to declare its own syscalls). Prints what it did. Raises
    SyscallTableError for an unusable file or malformed device reply."""
    if args.syscalls:
        return load_syscall_table_file(args.syscalls)
    if args.no_device_syscalls:
        return None
    table, reason = query_syscall_table(ser)
    if table is None:
        print(f"note: no syscall table from the device ({reason}); the script must declare "
              f"its own syscalls, and the load is not checked against the firmware's",
              file=sys.stderr)
        return None
    print(f"syscall table from device: {len(table.entries)} syscalls, hash {table.hash:08X}")
    if args.save_syscalls:
        with open(args.save_syscalls, "w") as f:
            f.write(table.to_json())
        print(f"saved to {args.save_syscalls}")
    return table


def _print_warnings(compiled):
    for w in compiled.warnings:
        print(f"warning: {w.describe()}", file=sys.stderr)


def _device_commands(ser, args):
    """The command list to check cmd() names against for an --upload: from
    --commands FILE, else asked of the device (GCMDS), else None (no check)."""
    if args.commands:
        return load_command_list_file(args.commands)
    if args.no_device_commands:
        return None
    commands, reason = query_command_list(ser)
    if commands is None:
        print(f"note: no command list from the device ({reason}); cmd() names are not "
              f"checked", file=sys.stderr)
        return None
    print(f"command list from device: {len(commands.entries)} commands")
    if args.save_commands:
        with open(args.save_commands, "w") as f:
            f.write(commands.to_json())
        print(f"saved to {args.save_commands}")
    return commands


def compile_and_upload(args, src):
    """--upload: connect, learn the device's syscall table, compile against it,
    and send the result stamped with the table's hash."""
    if args.slot is None:
        print("error: --upload requires --slot", file=sys.stderr)
        return 1
    ser = _open_port(args.upload, args.baud, args.timeout)
    if ser is None:
        return 1
    with ser:
        time.sleep(0.2)  # let the connection settle before writing
        try:
            table = _device_table(ser, args)
        except (OSError, SyscallTableError) as e:
            print(f"error: {e}", file=sys.stderr)
            return 1

        try:
            commands = _device_commands(ser, args)
        except (OSError, SyscallTableError) as e:
            print(f"error: {e}", file=sys.stderr)
            return 1

        try:
            compiled = compile_script(src, table, commands)
        except CompileError as e:
            print(f"error: {e.describe()}", file=sys.stderr)
            return 1
        _print_warnings(compiled)
        if args.disasm:
            print(disassemble(compiled.code, compiled.pool))

        payload = build_scriptload_payload(compiled.code, compiled.pool)
        # Only a script that actually calls syscalls depends on the table, so
        # only those are stamped (and refused after a mismatching firmware
        # update); a cmd()-only script keeps working across syscall changes.
        stamp = table.hash if (table is not None and compiled.uses_syscalls) else None
        return _upload_on(ser, args.slot, payload.hex(), args.timeout,
                          args.chunk_bytes, stamp)


def list_syscalls(args):
    """--list-syscalls PORT: print the device's syscall table."""
    ser = _open_port(args.list_syscalls, args.baud, args.timeout)
    if ser is None:
        return 1
    with ser:
        time.sleep(0.2)
        try:
            table, reason = query_syscall_table(ser)
        except SyscallTableError as e:
            print(f"error: {e}", file=sys.stderr)
            return 1
    if table is None:
        print(f"error: the device offers no syscall table ({reason}) -- its firmware may "
              f"predate GSYSCALLS or never called registerSyscalls()", file=sys.stderr)
        return 1
    print(f"{len(table.entries)} syscalls, table hash {table.hash:08X}")
    for i in range(len(table.entries)):
        print(f"  {i}: {table.signature(i)}")
    if args.save_syscalls:
        with open(args.save_syscalls, "w") as f:
            f.write(table.to_json())
        print(f"saved to {args.save_syscalls}")
    return 0


def list_commands(args):
    """--list-commands PORT: print the device's command list."""
    ser = _open_port(args.list_commands, args.baud, args.timeout)
    if ser is None:
        return 1
    with ser:
        time.sleep(0.2)
        commands, reason = query_command_list(ser)
    if commands is None:
        print(f"error: the device gave no command list ({reason})", file=sys.stderr)
        return 1
    print(f"{len(commands.entries)} commands")
    for name, help_ in commands.entries:
        print(f"  {name}: {help_}")
    if args.save_commands:
        with open(args.save_commands, "w") as f:
            f.write(commands.to_json())
        print(f"saved to {args.save_commands}")
    return 0


def _emit(payload, args, name_suffix):
    if args.format == "bin":
        out_path = args.output or (args.source + ".bin" if args.source != "-" else "a.out.bin")
        with open(out_path, "wb") as f:
            f.write(payload)
    else:  # hex
        text = payload.hex()
        if args.output:
            open(args.output, "w").write(text + "\n")
        else:
            print(text)


def _emit_carray(code, pool, args):
    def array_text(name, data):
        body = ", ".join(f"0x{b:02X}" for b in data)
        return (f"const uint8_t {name}[] = {{ {body} }};\n"
                f"const uint16_t {name}_len = {len(data)};\n")

    text = array_text(args.carray_name, code)
    if pool:
        text += array_text(f"{args.carray_name}_pool", pool)
    if args.output:
        open(args.output, "w").write(text)
    else:
        print(text, end="")


if __name__ == "__main__":
    sys.exit(main())
