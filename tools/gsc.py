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
    primary     := NUMBER | FLOAT | IDENT | IDENT "(" (expr ("," expr)*)? ")"
                 | cmd_call | "(" expr ")"
    cmd_call    := "cmd" "(" STRING ("," expr)* ")"

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
  - Returns an int: the response parsed as a number on ACK, 0 on NAK or if
    the response has no trailing number (e.g. a plain action command with
    no return value). NAK and "ACK with value 0" are not distinguishable
    from the return value alone -- check what you're calling if that
    matters.
  - Arguments aren't type-checked (same as syscalls) -- floats can be
    passed, but the receiving command decides how to interpret them.
  - Compiled output for a script that uses cmd() includes a second
    artifact (the pool) alongside the code -- see gsc.py's --format
    handling and tools/README.md.

Output formats: raw binary, ASCII hex, or a C uint8_t array body suitable for
pasting into firmware source. --upload sends the compiled script straight to
a running controller's control port via SCRIPTLOAD (see WORKFLOW.md).
"""

import argparse
import re
import struct
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


class CompileError(Exception):
    pass


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

KEYWORDS = {"if", "else", "while", "syscall", "var", "int", "float", "cmd"}


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
            raise CompileError(f"unexpected character {src[pos]!r} at offset {pos}")
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
    def __init__(self, cmd_name, args): self.cmd_name, self.args = cmd_name, args

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
            raise CompileError(f"expected {kind!r} but found {t.kind!r} ({t.value!r}) at offset {t.pos}")
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
        raise CompileError(f"expected a type ('int' or 'float') but found {t.kind!r} at offset {t.pos}")

    def parse_syscall_decl(self):
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
        return SyscallDecl(name, params, int(id_tok.value, 0), return_type)

    def parse_block(self):
        self.expect("{")
        stmts = []
        while not self.at("}"):
            stmts.append(self.parse_statement())
        self.expect("}")
        return Block(stmts)

    def parse_statement(self):
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
            self.advance()
            left = BinOp("||", left, self.parse_and())
        return left

    def parse_and(self):
        left = self.parse_cmp()
        while self.at("&&"):
            self.advance()
            left = BinOp("&&", left, self.parse_cmp())
        return left

    def parse_cmp(self):
        left = self.parse_add()
        while self.peek().kind in ("==", "!=", "<", "<=", ">", ">="):
            op = self.advance().kind
            left = BinOp(op, left, self.parse_add())
        return left

    def parse_add(self):
        left = self.parse_mul()
        while self.peek().kind in ("+", "-"):
            op = self.advance().kind
            left = BinOp(op, left, self.parse_mul())
        return left

    def parse_mul(self):
        left = self.parse_unary()
        while self.peek().kind in ("*", "/", "%"):
            op = self.advance().kind
            left = BinOp(op, left, self.parse_unary())
        return left

    def parse_unary(self):
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
        t = self.peek()
        if t.kind == "NUMBER":
            self.advance()
            return Num(int(t.value, 0), is_float=False)
        if t.kind == "FLOAT":
            self.advance()
            return Num(float(t.value), is_float=True)
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
        if t.kind == "cmd":
            self.advance()
            self.expect("(")
            name_tok = self.expect("STRING")
            args = []
            while self.at(","):
                self.advance()
                args.append(self.parse_expr())
            self.expect(")")
            return CmdCall(name_tok.value, args)
        if t.kind == "(":
            self.advance()
            e = self.parse_expr()
            self.expect(")")
            return e
        raise CompileError(f"unexpected token {t.kind!r} ({t.value!r}) at offset {t.pos}")


# ---------------------------------------------------------------------------
# Codegen
# ---------------------------------------------------------------------------
class Codegen:
    def __init__(self, syscalls):
        self.syscalls = syscalls  # name -> (id, argc, return_type)
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
            return "int"
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
                raise CompileError(f"call to undeclared syscall '{expr.name}'")
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
            offset = self.pool_offset_for(expr.cmd_name)
            if offset > 0xFFFF:
                raise CompileError("string pool too large (over 65535 bytes)")
            if len(expr.args) > 0xFF:
                raise CompileError(f"cmd({expr.cmd_name!r}, ...) has too many arguments")
            for a in expr.args:
                self.gen_expr(a)
            self.emit(OP_CMDCALL)
            self.buf += offset.to_bytes(2, "little")
            self.emit(len(expr.args))
            return "int"

        raise CompileError(f"internal error: unknown expression {expr!r}")


def compile_source_with_pool(src):
    """Returns (code, pool). `pool` is the string-constant table cmd() calls
    reference (empty bytes if the script never uses cmd()) -- see gsc.py's
    module docstring and tools/README.md for what to do with it."""
    tokens = tokenize(src)
    decls, program = Parser(tokens).parse_program()
    syscalls = {}
    for d in decls:
        if d.name in syscalls:
            raise CompileError(f"syscall '{d.name}' declared more than once")
        syscalls[d.name] = (d.id, len(d.params), d.return_type)
    codegen = Codegen(syscalls)
    code = codegen.gen_program(program)
    return code, bytes(codegen.pool)


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
    OP_I2F: "I2F", OP_F2I: "F2I", OP_CMDCALL: "CMDCALL",
}
_OPERAND_LEN = {
    OP_PUSH_I32: 4, OP_PUSH_F32: 4, OP_LOAD: 1, OP_STORE: 1,
    OP_JMP: 2, OP_JZ: 2, OP_JNZ: 2, OP_CALL: 2, OP_CMDCALL: 3,
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
        elif op == OP_CMDCALL:
            offset = int.from_bytes(code[pc + 1:pc + 3], "little")
            argc = code[pc + 3]
            name_str = _pool_string_at(pool, offset)
            label = f'"{name_str}"' if name_str is not None else f"offset={offset}"
            lines.append(f"{pc:4d}: {name} {label} argc={argc}")
        else:
            lines.append(f"{pc:4d}: {name}")
        pc += 1 + extra
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main(argv=None):
    ap = argparse.ArgumentParser(description="GAACE_Script compiler")
    ap.add_argument("source", help="path to .gs source file, or '-' for stdin")
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
                          "via SCRIPTLOAD,<slot>,<hex> (e.g. --upload /dev/ttyUSB0 or "
                          "--upload COM5). Requires --slot and pyserial (pip install pyserial). "
                          "Implies the same wire format as --scriptload; --format/-o are "
                          "ignored.")
    ap.add_argument("--slot", type=int, metavar="N", help="script slot for --upload")
    ap.add_argument("--baud", type=int, default=115200, help="baud rate for --upload (default 115200)")
    ap.add_argument("--timeout", type=float, default=3.0,
                     help="seconds to wait for ACK/NAK from --upload (default 3.0)")
    args = ap.parse_args(argv)

    src = sys.stdin.read() if args.source == "-" else open(args.source).read()

    try:
        code, pool = compile_source_with_pool(src)
    except CompileError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    if args.disasm:
        print(disassemble(code, pool))
        if not args.upload:
            return 0

    if args.upload:
        if args.slot is None:
            print("error: --upload requires --slot", file=sys.stderr)
            return 1
        payload = build_scriptload_payload(code, pool)
        return upload_script(args.upload, args.slot, payload.hex(),
                              baud=args.baud, timeout=args.timeout)

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


def upload_script(port, slot, payload_hex, baud=115200, timeout=3.0):
    """Sends SCRIPTLOAD,<slot>,<payload_hex> to `port` and reports ACK/NAK.

    Opens the port, sends the line, and reads back whatever the device
    writes within `timeout` seconds -- ACK (0x06) means the load succeeded,
    NAK (0x15) means the device rejected it (bad slot, malformed hex, or
    over its configured size limit). Returns a process exit code (0 on
    ACK, 1 otherwise) so this composes with shell scripting.
    """
    try:
        import serial
    except ImportError:
        print("error: --upload requires pyserial -- pip install pyserial", file=sys.stderr)
        return 1

    line = f"SCRIPTLOAD,{slot},{payload_hex}\n"

    try:
        with serial.Serial(port, baud, timeout=timeout) as ser:
            time.sleep(0.2)  # let the connection settle before writing
            ser.reset_input_buffer()
            ser.write(line.encode("ascii"))
            response = ser.read(256)
    except serial.SerialException as e:
        print(f"error: could not open {port}: {e}", file=sys.stderr)
        return 1

    if not response:
        print(f"error: no response from device within {timeout}s "
              f"(wrong port/baud, or the control port isn't the one you opened?)",
              file=sys.stderr)
        return 1
    if response[0] == 0x06:
        print(f"OK: script loaded into slot {slot} ({len(payload_hex) // 2} bytes)")
        return 0
    if response[0] == 0x15:
        print(f"NAK: device rejected the script (bad slot, malformed hex, or over "
              f"the configured size limit -- see GSCRIPTLIMITS)", file=sys.stderr)
        return 1
    print(f"warning: unexpected response from device: {response!r}", file=sys.stderr)
    return 1


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
