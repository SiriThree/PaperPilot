"""Compare frozen signatures without depending on Python's typing display syntax."""

import ast
import inspect


def _typing_name(node: ast.AST, name: str) -> bool:
    return (isinstance(node, ast.Name) and node.id == name) or (
        isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "typing"
        and node.attr == name
    )


def _union(members: list[ast.expr]) -> ast.expr:
    unique = {}
    for member in members:
        parts = (
            member.slice.elts
            if isinstance(member, ast.Subscript) and _typing_name(member.value, "Union")
            else [member]
        )
        for part in parts:
            unique[ast.dump(part)] = part
    values = [unique[key] for key in sorted(unique)]
    if len(values) == 1:
        return values[0]
    return ast.Subscript(
        value=ast.Name(id="Union", ctx=ast.Load()),
        slice=ast.Tuple(elts=values, ctx=ast.Load()),
        ctx=ast.Load(),
    )


class _TypeSyntax(ast.NodeTransformer):
    def visit_Subscript(self, node):
        self.generic_visit(node)
        if _typing_name(node.value, "Optional"):
            return _union([node.slice, ast.Constant(value=None)])
        if _typing_name(node.value, "Union"):
            members = node.slice.elts if isinstance(node.slice, ast.Tuple) else [node.slice]
            return _union(members)
        return node

    def visit_BinOp(self, node):
        self.generic_visit(node)
        return _union([node.left, node.right]) if isinstance(node.op, ast.BitOr) else node


def _signature_key(signature: str) -> str:
    # Parse only: contract annotations/defaults must never be evaluated or executed.
    tree = ast.parse(f"def contract{signature}:\n    pass")
    normalizer = _TypeSyntax()
    for node in ast.walk(tree):
        if isinstance(node, ast.arg) and node.annotation is not None:
            node.annotation = normalizer.visit(node.annotation)
        elif isinstance(node, ast.FunctionDef) and node.returns is not None:
            node.returns = normalizer.visit(node.returns)
    return ast.dump(tree, include_attributes=False)


def assert_signature_matches(expected: str, actual: inspect.Signature | str) -> None:
    assert _signature_key(expected) == _signature_key(str(actual)), (
        f"Contract signature changed:\nexpected: {expected}\nactual:   {actual}"
    )
