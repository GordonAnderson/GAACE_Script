#!/usr/bin/env python3
"""
gsc.py — GAACE_Script compiler.

Compiles a small C-like script language to the bytecode format understood by
the GAACE_Script VM (see ../src/GAACEScript.h). This is the host-side half of
the design: the device only ever runs the ~20-opcode interpreter loop, never
this compiler.

Language (informal grammar):

    program     := (syscall_decl | statement)*
    syscall_decl:= "syscall" IDENT "(" (IDENT ("," IDENT)*)? ")" "=" NUMBER ";"
    statement   := if_stmt | while_stmt | block
                 | IDENT "=" expr ";"          (assignment)
                 | expr ";"                     (expression statement)
    if_stmt     := "if" "(" expr ")" block ("else" block)?
    while_stmt  := "while" "(" expr ")" block
    block       := "{" statement* "}"
    expr        := or_expr
    or_expr     := and_expr ("||" and_expr)*
    and_expr    := cmp_expr ("&&" cmp_expr)*
    cmp_expr    := add_expr (("=="|"!="|"<"|"<="|">"|">=") add_expr)*
    add_expr    := mul_expr (("+"|"-") mul_expr)*
    mul_expr    := unary (("*"|"/"|"%") unary)*
    unary       := ("-"|"!") unary | primary
    primary     := NUMBER | IDENT | IDENT "(" (expr ("," expr)*)? ")"
                 | "(" expr ")"

Notes:
  - `&&` / `||` are NOT short-circuiting — both operands are always evaluated
    (the VM's AND/OR opcodes just combine two already-computed values). Don't
    rely on short-circuit side effects the way you might in C.
  - Variables are plain int32 slots, auto-allocated in order of first
    assignment. Reading a variable before it's ever been assigned is a
    compile error (there's no implicit "starts at zero" at the language
    level, even though the VM does zero its slots on vmInit — this catches
    typos instead of silently running with 0).
  - `syscall NAME(params) = ID;` declares a callable that maps to VM opcode
    CALL <ID> <argc-from-declaration>. ID must match the order the embedding
    firmware registers it with vmRegisterSyscall() (0 = first registered).
  - An expression used as a statement (e.g. a bare call for its side effect)
    has its return value discarded (compiled as <expr> POP).

Output formats: raw binary, ASCII hex, or a C uint8_t array body suitable for
pasting into firmware source.
"""

import argparse
import re
import sys

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

VAR_SLOTS = 16

BINOP_OPCODE = {
    "+": OP_ADD, "-": OP_SUB, "*": OP_MUL, "/": OP_DIV, "%": OP_MOD,
    "==": OP_EQ, "!=": OP_NE, "<": OP_LT, "<=": OP_LE, ">": OP_GT, ">=": OP_GE,
    "&&": OP_AND, "||": OP_OR,
}


class CompileError(Exception):
    pass


# ---------------------------------------------------------------------------
# Lexer
# ---------------------------------------------------------------------------
WHITESPACE_RE = re.compile(r"\s+")
COMMENT_RE = re.compile(r"//[^\n]*")
TOKEN_RE = re.compile(r"""
    (?P<NUMBER>0[xX][0-9a-fA-F]+|[0-9]+)
  | (?P<IDENT>[A-Za-z_][A-Za-z0-9_]*)
  | (?P<OP>==|!=|<=|>=|&&|\|\||[(){};,=+\-*/%<>!])
""", re.VERBOSE)

KEYWORDS = {"if", "else", "while", "syscall"}


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
        else:
            tokens.append(Token(kind, text, m.start()))
    tokens.append(Token("EOF", None, n))
    return tokens


# ---------------------------------------------------------------------------
# AST
# ---------------------------------------------------------------------------
class Num:
    def __init__(self, value): self.value = value

class Var:
    def __init__(self, name): self.name = name

class Call:
    def __init__(self, name, args): self.name, self.args = name, args

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
    def __init__(self, name, params, id_): self.name, self.params, self.id = name, params, id_


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
        self.expect("=")
        id_tok = self.expect("NUMBER")
        self.expect(";")
        return SyscallDecl(name, params, int(id_tok.value, 0))

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
        return self.parse_primary()

    def parse_primary(self):
        t = self.peek()
        if t.kind == "NUMBER":
            self.advance()
            return Num(int(t.value, 0))
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
        self.syscalls = syscalls  # name -> (id, argc)
        self.buf = bytearray()
        self.vars = {}            # name -> slot
        self.fixups = []          # list of (pos, label)
        self.labels = {}          # label -> address
        self._next_label = 0

    def new_label(self):
        self._next_label += 1
        return self._next_label

    def mark_label(self, label):
        self.labels[label] = len(self.buf)

    def slot_for(self, name, declare=False):
        if name in self.vars:
            return self.vars[name]
        if not declare:
            raise CompileError(f"variable '{name}' used before assignment")
        if len(self.vars) >= VAR_SLOTS:
            raise CompileError(f"too many variables (max {VAR_SLOTS})")
        slot = len(self.vars)
        self.vars[name] = slot
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
        elif isinstance(stmt, Assign):
            self.gen_expr(stmt.expr)
            slot = self.slot_for(stmt.name, declare=True)
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
        if isinstance(expr, Num):
            self.emit_push(expr.value)
        elif isinstance(expr, Var):
            slot = self.slot_for(expr.name)
            self.emit(OP_LOAD)
            self.emit(slot)
        elif isinstance(expr, UnOp):
            self.gen_expr(expr.operand)
            self.emit(OP_NEG if expr.op == "-" else OP_NOT)
        elif isinstance(expr, BinOp):
            self.gen_expr(expr.left)
            self.gen_expr(expr.right)
            self.emit(BINOP_OPCODE[expr.op])
        elif isinstance(expr, Call):
            if expr.name not in self.syscalls:
                raise CompileError(f"call to undeclared syscall '{expr.name}'")
            id_, argc = self.syscalls[expr.name]
            if len(expr.args) != argc:
                raise CompileError(
                    f"'{expr.name}' declared with {argc} argument(s), called with {len(expr.args)}")
            for a in expr.args:
                self.gen_expr(a)
            self.emit(OP_CALL)
            self.emit(id_)
            self.emit(len(expr.args))
        else:
            raise CompileError(f"internal error: unknown expression {expr!r}")


def compile_source(src):
    tokens = tokenize(src)
    decls, program = Parser(tokens).parse_program()
    syscalls = {}
    for d in decls:
        if d.name in syscalls:
            raise CompileError(f"syscall '{d.name}' declared more than once")
        syscalls[d.name] = (d.id, len(d.params))
    return Codegen(syscalls).gen_program(program)


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
}
_OPERAND_LEN = {
    OP_PUSH_I32: 4, OP_LOAD: 1, OP_STORE: 1, OP_JMP: 2, OP_JZ: 2, OP_JNZ: 2, OP_CALL: 2,
}


def disassemble(code: bytes) -> str:
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
        elif op in (OP_LOAD, OP_STORE):
            lines.append(f"{pc:4d}: {name} slot={code[pc + 1]}")
        elif op in (OP_JMP, OP_JZ, OP_JNZ):
            target = int.from_bytes(code[pc + 1:pc + 3], "little")
            lines.append(f"{pc:4d}: {name} -> {target}")
        elif op == OP_CALL:
            lines.append(f"{pc:4d}: {name} id={code[pc + 1]} argc={code[pc + 2]}")
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
    args = ap.parse_args(argv)

    src = sys.stdin.read() if args.source == "-" else open(args.source).read()

    try:
        code = compile_source(src)
    except CompileError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    if args.disasm:
        print(disassemble(code))
        return 0

    if args.format == "bin":
        out_path = args.output or (args.source + ".bin" if args.source != "-" else "a.out.bin")
        with open(out_path, "wb") as f:
            f.write(code)
    elif args.format == "hex":
        text = code.hex()
        if args.output:
            open(args.output, "w").write(text + "\n")
        else:
            print(text)
    else:  # carray
        body = ", ".join(f"0x{b:02X}" for b in code)
        text = (f"const uint8_t {args.carray_name}[] = {{ {body} }};\n"
                f"const uint16_t {args.carray_name}_len = {len(code)};\n")
        if args.output:
            open(args.output, "w").write(text)
        else:
            print(text, end="")

    return 0


if __name__ == "__main__":
    sys.exit(main())
