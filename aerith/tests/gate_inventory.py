"""The gate's own refusal conditions, read out of its source by AST.

Why this exists. REQ-LC-023 declares a list of conditions that must each carry a failing test
row and a mutant, and for three review rounds the list itself was prose: it claimed a set, the
code held a different set, and the difference was found by a reviewer rather than by a check.
The worst case was a condition that is not a field of the record at all, the vendor key, which
the readiness surfaces could not see while the prose said the class was complete.

So the inventory is extracted, never typed. Every construct that can refuse is recognised by an
explicit pattern, and anything this module does not recognise raises `Unrecognised`. That is the
whole design: an extractor that silently skips a shape it was not taught would hand a machine
stamp to the same defect. It is deliberately strict about code shape, which is a cost paid by
the gate's authors on purpose.

What it does not see, stated so it is not assumed: an exception other than GateError that
escapes a scoped function uncaught (an OSError from reading a file, for example) is not a
condition here; callers decide what it means. Calls from a scoped function into any other
package function are listed, and each must be declared by whoever maintains the inventory.

Stdlib only. Reads source text; imports nothing from the package.
"""
from __future__ import annotations

import ast
import builtins
from dataclasses import dataclass


class Unrecognised(Exception):
    """A construct inside the scope that no pattern here consumes."""


@dataclass(frozen=True)
class Atom:
    """One condition, or one declared non-condition, inside a scoped function.

    `kind` is `condition` (a refusal fires when this fails), `expression` (a scope guard, a
    selection, a delegation to another scoped function, or a relay of a decision already
    inventoried), or `implicit` (a try block whose handler turns an exception into a refusal).
    """

    id: str
    module: str
    function: str
    kind: str
    text: str
    message: str


def record_row_values(name: str, values: dict) -> None:
    """Write what each row of a closed-list test actually produced, when asked to.

    The mutation runner sets `PROJECT_CREATOR_ROW_ACTUALS` to a directory and compares these
    values between the control run and each mutant. A row label alone cannot tell a condition
    that stopped refusing from a process that fell over, and a mutant killed the second way
    proves nothing about the condition it is named for. Unset, this writes nothing.
    """
    import json
    import os

    import re

    directory = os.environ.get("PROJECT_CREATOR_ROW_ACTUALS")
    if not directory:
        return
    # The name decides a filename, so it is a name and nothing else: no separator, no parent, no
    # drive, and the file must not already exist (R25-SEC-07).
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", name):
        raise ValueError("a row-value sink name is a plain identifier")
    target = os.path.join(directory, name + ".json")
    if os.path.dirname(os.path.abspath(target)) != os.path.abspath(directory):
        raise ValueError("a row-value sink writes inside the directory it was given")
    with open(target, "x", encoding="utf-8") as stream:
        json.dump({f"{name}:{label}": value for label, value in values.items()}, stream, indent=1, sort_keys=True)


def _is_gate_raise(node: ast.AST) -> bool:
    return (isinstance(node, ast.Raise) and isinstance(node.exc, ast.Call)
            and isinstance(node.exc.func, ast.Name) and node.exc.func.id == "GateError")


def _message(node: ast.Raise) -> str:
    """The refusal text, as far as it is visible at the raise."""
    argument = node.exc.args[0] if isinstance(node.exc, ast.Call) and node.exc.args else None
    if isinstance(argument, ast.Constant) and isinstance(argument.value, str):
        return argument.value
    if isinstance(argument, ast.JoinedStr):
        return "".join(value.value if isinstance(value, ast.Constant) else "{" + ast.unparse(value.value) + "}"
                       for value in argument.values)
    return "" if argument is None else "via " + ast.unparse(argument)


def _iterable_element(node: ast.AST):
    """The element of `all(<generator>)` or `any(<generator>)`, which is the real condition."""
    if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
            and node.func.id in {"all", "any"} and len(node.args) == 1 and not node.keywords
            and isinstance(node.args[0], (ast.GeneratorExp, ast.ListComp, ast.SetComp))):
        return node.args[0].elt
    return None


def _flatten(node: ast.expr) -> list[ast.expr]:
    """Split a test into the conditions a reader would count separately."""
    if isinstance(node, ast.BoolOp):
        return [atom for value in node.values for atom in _flatten(value)]
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
        if isinstance(node.operand, ast.BoolOp) or _iterable_element(node.operand) is not None:
            return _flatten(node.operand)
        return [node]
    element = _iterable_element(node)
    return [node] if element is None else _flatten(element)


def _preceding(statements, line: int):
    """Every statement that runs before `line`, descending into the block that contains it.

    Round 25 filtered the function's TOP-LEVEL statements by end line, which silently discarded
    the whole `with` block that holds the launch, and with it a refusal inside that block
    (R25-SEC-01, R25-SPEC-06). A statement enclosing the launch is opened rather than dropped.
    """
    kept = []
    for statement in statements:
        if statement.lineno > line:
            break
        if (statement.end_lineno or statement.lineno) < line:
            kept.append(statement)
            continue
        for field in ("body", "orelse", "finalbody"):
            inner = getattr(statement, field, None)
            if isinstance(inner, list) and inner and isinstance(inner[0], ast.stmt):
                kept.extend(_preceding(inner, line))
        break
    return kept


class _Walker:
    """Consume every statement of one function, or refuse."""

    def __init__(self, module: str, function: str, callees: set[str]):
        self.module, self.function, self.callees = module, function, callees
        self.atoms: list[Atom] = []
        self.claimed: set[int] = set()
        self.calls: set[str] = set()
        # Names assigned a decision (a conjunction, or a scoped helper's answer). A test that is
        # only such a name relays a decision already inventoried; it is not a second condition.
        self.decisions: set[str] = set()

    def _relay(self, node: ast.expr) -> bool:
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
            node = node.operand
        return isinstance(node, ast.Name) and node.id in self.decisions

    def _record(self, text: str, kind: str, message: str) -> None:
        self.atoms.append(Atom(id=f"{self.function} | {message} | {text}", module=self.module,
                               function=self.function, kind=kind, text=text, message=message))

    def _conditions(self, test: ast.expr, message: str, kind: str = "condition") -> None:
        for inner in ast.walk(test):
            self.claimed.add(id(inner))
        for atom in _flatten(test):
            self._record(ast.unparse(atom), "expression" if self._relay(atom) else kind, message)

    def body(self, statements, message: str) -> None:
        for statement in statements:
            self.statement(statement, message)

    def statement(self, node: ast.stmt, message: str) -> None:
        if isinstance(node, ast.If):
            self._if(node, message)
        elif isinstance(node, ast.Try):
            self._try(node, message)
        elif isinstance(node, (ast.For, ast.With)):
            self.body(node.body, message)
            self.body(getattr(node, "orelse", []), message)
        elif isinstance(node, ast.Assign):
            self._assign(node)
        elif _is_gate_raise(node):
            raise Unrecognised(f"{self.function}: unconditional refusal {ast.unparse(node)[:70]}")
        elif isinstance(node, ast.Raise):
            if node.exc is not None and not message:
                raise Unrecognised(f"{self.function}: {ast.unparse(node)[:70]} outside a refusing handler")
        elif isinstance(node, ast.Return):
            if isinstance(node.value, ast.BoolOp):
                self._conditions(node.value, f"via {self.function}")
        elif not isinstance(node, (ast.Expr, ast.AugAssign, ast.Import, ast.ImportFrom, ast.Pass)):
            raise Unrecognised(f"{self.function}: statement {type(node).__name__}")

    def _direct_exit(self, statements):
        for statement in statements:
            if isinstance(statement, (ast.Raise, ast.Return)):
                return statement
        return None

    def _if(self, node: ast.If, message: str) -> None:
        exit_ = self._direct_exit(node.body)
        if exit_ is not None:
            if _is_gate_raise(exit_):
                refusal = _message(exit_)
            elif isinstance(exit_, ast.Raise):
                if not message:
                    raise Unrecognised(f"{self.function}: {ast.unparse(exit_)} outside a refusing handler")
                refusal = message
            elif isinstance(exit_.value, ast.Constant) and isinstance(exit_.value.value, str):
                refusal = exit_.value.value
            elif isinstance(exit_.value, ast.Constant) and exit_.value.value in (None, False):
                refusal = message or f"via {self.function}"
            else:
                # `if <admission conditions>: return <the admitted value>`, whose failure falls
                # through to a None return that a caller turns into a refusal.
                refusal = f"via {self.function}"
            self._conditions(node.test, refusal)
        else:
            # Every other `if` is a guard. It cannot refuse by itself, and some of them decide
            # nothing a refusal reads, but all of them are RECORDED, because round 25 shipped a
            # version that silently dropped the ones whose bodies neither exit nor assign a
            # decision — and one of those decided whether the launch-time hash lock covers the
            # executable at all (R25-SPEC-02). A guard is named by what it guards, because two
            # guards on one test are otherwise indistinguishable.
            self._conditions(node.test, "guards " + ast.unparse(node.body[0]).splitlines()[0][:80],
                             kind="expression")
        self.body([s for s in node.body if s is not exit_], message)
        self.body(node.orelse, message)

    def _try(self, node: ast.Try, message: str) -> None:
        converts = None  # the refusal an exception inside this block is turned into, if any
        for handler in node.handlers:
            exit_ = self._direct_exit(handler.body)
            assigned = [ast.unparse(target) for statement in handler.body if isinstance(statement, ast.Assign)
                        for target in statement.targets]
            if _is_gate_raise(exit_):
                converts = _message(exit_)
            elif exit_ is None and assigned:
                converts = "via " + assigned[0]  # e.g. `valid = False`
            elif not (isinstance(exit_, ast.Raise) and exit_.exc is None):
                raise Unrecognised(f"{self.function}: handler {ast.unparse(handler)[:70]}")
            # A handler that only cleans up and re-raises converts nothing and records nothing.
            self.body([s for s in handler.body if s is not exit_], message)
        if converts is not None:
            first = ast.unparse(node.body[0]).splitlines()[0] if node.body else "pass"
            self._record("try " + first, "implicit", converts)
        self.body(node.body, converts if converts is not None else message)
        self.body(node.orelse, message)
        self.body(node.finalbody, message)

    def _assign(self, node: ast.Assign) -> None:
        target = ast.unparse(node.targets[0])
        if isinstance(node.value, ast.BoolOp):
            self._conditions(node.value, f"via {target}")
            self.decisions.add(target)
        elif (isinstance(node.value, ast.Call) and isinstance(node.value.func, ast.Name)
              and node.value.func.id in self.scoped):
            # Delegation to a scoped helper, whose own conditions are inventoried there.
            self._conditions(node.value, f"via {target}", kind="expression")
            self.decisions.add(target)
        for inner in ast.walk(node.value):
            if isinstance(inner, ast.IfExp) and id(inner) not in self.claimed:
                self.claimed.add(id(inner))
                self._conditions(inner.test, f"selects {target}", kind="expression")
            if isinstance(inner, ast.comprehension):
                for test in inner.ifs:
                    if id(test) not in self.claimed:
                        self._conditions(test, f"selects {target}", kind="expression")

    def resolve_relays(self) -> None:
        """Name a conjunct by the refusal it causes, not by the local that carries it.

        `valid = (A and B and ...)` followed by `if not valid: raise GateError(M)` used to leave
        every conjunct labelled `via valid`, which no row could be checked against (R25-SEC-06).
        The label becomes M, so a row that claims to cover a conjunct can be compared with what
        that row asserts.
        """
        refusals = {}
        for atom in self.atoms:
            name = atom.text[4:] if atom.text.startswith("not ") else atom.text
            if (name in self.decisions and atom.message
                    and not atom.message.startswith(("via ", "guards ", "selects "))):
                refusals[name] = atom.message
        if not refusals:
            return
        self.atoms = [atom if atom.message[4:] not in refusals or not atom.message.startswith("via ")
                      else Atom(id=f"{atom.function} | {refusals[atom.message[4:]]} | {atom.text}",
                                module=atom.module, function=atom.function, kind=atom.kind,
                                text=atom.text, message=refusals[atom.message[4:]])
                      for atom in self.atoms]

    def sweep(self, function: ast.FunctionDef) -> None:
        """Refuse on any conditional construct no pattern consumed."""
        for node in ast.walk(function):
            if isinstance(node, (ast.IfExp, ast.BoolOp)) and id(node) not in self.claimed:
                raise Unrecognised(f"{self.function}: unclaimed {type(node).__name__} "
                                   f"{ast.unparse(node)[:70]}")
            if isinstance(node, ast.comprehension):
                for test in node.ifs:
                    if id(test) not in self.claimed:
                        raise Unrecognised(f"{self.function}: comprehension filter {ast.unparse(test)[:70]}")
            if isinstance(node, (ast.Match, ast.Assert, ast.While, ast.Lambda)):
                raise Unrecognised(f"{self.function}: {type(node).__name__} is not a known shape")
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in self.callees
                    and node.func.id != "GateError"):
                self.calls.add(node.func.id)


def _package_callees(tree: ast.Module) -> set[str]:
    """Every function this module defines, or imports from the package itself."""
    names = {node.name for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}
    for node in tree.body:
        if isinstance(node, ast.ImportFrom) and node.level:
            names.update(alias.asname or alias.name for alias in node.names)
    return names


def _function(tree: ast.Module, name: str):
    found = [node for node in ast.walk(tree)
             if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name]
    if len(found) != 1:
        raise Unrecognised(f"{len(found)} functions named {name}")
    return found[0]


def extract(source: str, module: str, functions: list[str]):
    """Every condition inside `functions`, and the other package functions each one calls.

    Raises `Unrecognised` for any construct no pattern consumes, for a scoped function that
    yields no condition at all, and for two conditions that would share one id.
    """
    tree = ast.parse(source)
    callees = _package_callees(tree)
    atoms, calls = [], {}
    for name in functions:
        function = _function(tree, name)
        walker = _Walker(module, name, callees)
        walker.scoped = set(functions)
        walker.body(function.body, "")
        walker.resolve_relays()
        walker.sweep(function)
        if not any(atom.kind == "condition" for atom in walker.atoms):
            raise Unrecognised(f"{name}: no condition extracted")
        atoms.extend(walker.atoms)
        calls[name] = sorted(walker.calls - set(functions))
    ids = [atom.id for atom in atoms]
    duplicated = sorted({one for one in ids if ids.count(one) > 1})
    if duplicated:
        raise Unrecognised(f"two conditions share one id: {duplicated}")
    return atoms, calls


def _local_sources(function) -> dict[str, str]:
    """Each local name assigned exactly once, mapped to the source of what it holds."""
    seen: dict[str, list[str]] = {}
    for node in ast.walk(function):
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            seen.setdefault(node.targets[0].id, []).append(ast.unparse(node.value))
    return {name: values[0] for name, values in seen.items() if len(values) == 1}


def launch_preconditions(source: str, cls: str = "CLIProvider", method: str = "invoke",
                         launch: str = "execute"):
    """What `invoke` refuses on before it launches anything, each with what it reads.

    Returns (atom, resolved_text, reads_only_the_record) for every condition before the first
    call to `launch`. A condition whose inputs trace back only to `self` (the record and its
    vendor key) must also be a condition of the gate, or a readiness surface can report ready a
    record every launch refuses. That was R24-SPEC-01, and this is the check that catches it.
    Locals are traced to their single assignment; a local assigned twice is untraceable and
    counts as reading more than the record.
    """
    tree = ast.parse(source)
    owner = [node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == cls]
    if len(owner) != 1:
        raise Unrecognised(f"no single class {cls}")
    function = _function(owner[0], method)
    first_launch = min((node.lineno for node in ast.walk(function) if isinstance(node, ast.Call)
                        and isinstance(node.func, ast.Name) and node.func.id == launch),
                       default=None)
    if first_launch is None:
        raise Unrecognised(f"{method} never calls {launch}")
    walker = _Walker("providers", method, _package_callees(tree))
    walker.scoped = set()
    walker.body(_preceding(function.body, first_launch), "")
    locals_ = _local_sources(function)
    parameters = {argument.arg for argument in function.args.args}
    module_names = ({node.name for node in tree.body if isinstance(node, (ast.FunctionDef, ast.ClassDef))}
                    | {target.id for node in tree.body if isinstance(node, ast.Assign)
                       for target in node.targets if isinstance(target, ast.Name)}
                    | _package_callees(tree))

    def roots(text: str, depth: int = 0) -> set[str]:
        found = set()
        bound = {n.id for n in ast.walk(ast.parse(text, mode="eval"))
                 if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store)}
        for node in ast.walk(ast.parse(text, mode="eval")):
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load) and node.id not in bound:
                if node.id in parameters:
                    found.add(node.id)
                elif node.id in locals_ and depth < 8:
                    found |= roots(locals_[node.id], depth + 1)
                elif node.id in locals_ or (node.id not in module_names and not hasattr(builtins, node.id)):
                    found.add("<untraceable:" + node.id + ">")
        return found

    results = []
    for atom in walker.atoms:
        if atom.kind != "condition" and not atom.text.isidentifier():
            continue
        resolved = locals_.get(atom.text, atom.text) if atom.text.isidentifier() else atom.text
        resolved = resolved.replace("self.config", "config").replace("self.name", "name")
        results.append((atom, resolved, roots(atom.text) <= {"self"}))
    return results


def row_labels(source: str, function: str, variable: str) -> list[str]:
    """The labels of a closed-list table: the first element of each tuple assigned to `variable`."""
    target = _function(ast.parse(source), function)
    for node in ast.walk(target):
        if (isinstance(node, ast.Assign) and any(isinstance(one, ast.Name) and one.id == variable
                                                 for one in node.targets)
                and isinstance(node.value, ast.Tuple)):
            labels = []
            for element in node.value.elts:
                if not (isinstance(element, ast.Tuple) and isinstance(element.elts[0], ast.Constant)):
                    raise Unrecognised(f"{function}: row {ast.unparse(element)[:60]} has no literal label")
                labels.append(element.elts[0].value)
            return labels
    raise Unrecognised(f"{function}: no table assigned to {variable}")


def gate_texts(atoms, source: str, function: str = "validate_capability") -> set[str]:
    """Every condition text of the gate, with single-assignment locals resolved the same way."""
    locals_ = _local_sources(_function(ast.parse(source), function))
    return {locals_.get(atom.text, atom.text) if atom.text.isidentifier() else atom.text for atom in atoms}
