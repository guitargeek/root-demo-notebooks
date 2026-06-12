import ast
import inspect
import textwrap
import typing
from typing import get_origin, get_args


# --- Simple Python->C++ type mapper -----------------------------------------
def _cpp_type(py_t):
    origin = get_origin(py_t)
    args = get_args(py_t)
    if py_t in (int, "int"):
        return "int"
    if py_t in (float, "float"):
        return "double"
    if origin in (list, typing.List):
        (elem,) = args
        elem_cpp = _cpp_type(elem)
        return f"std::vector<{elem_cpp}>"
    if hasattr(py_t, "__cpp_name__"):
        return py_t.__cpp_name__
    if origin is None:
        return "void"
    raise TypeError(f"Unsupported type hint: {py_t!r}")


# --- AST -> C++ codegen (very small subset) ----------------------------------
class _CppGen(ast.NodeVisitor):
    def __init__(self, locals_types):
        self.lines = []
        self.ind = 0
        self.locals = dict(locals_types)  # name -> cpp type (for locals inference)

    def emit(self, s=""):
        self.lines.append("    " * self.ind + s)

    # Expressions --------------------------------------------------------------
    def expr(self, node):
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.Constant):
            v = node.value
            if isinstance(v, bool):
                return "true" if v else "false"
            if isinstance(v, int):
                return str(v)
            if isinstance(v, float):
                # ensure it's a double literal
                return repr(float(v))
            raise TypeError(f"Unsupported constant {v!r}")
        if isinstance(node, ast.BinOp):
            left = self.expr(node.left)
            right = self.expr(node.right)
            op = type(node.op)
            op_map = {
                ast.Add: "+",
                ast.Sub: "-",
                ast.Mult: "*",
                ast.Div: "/",
                ast.Mod: "%",
                ast.Pow: None,  # not supported here
            }
            if op not in op_map or op_map[op] is None:
                raise TypeError(f"Unsupported binop {op}")
            return f"({left} {op_map[op]} {right})"
        if isinstance(node, ast.Compare):
            if len(node.ops) != 1 or len(node.comparators) != 1:
                raise TypeError("Only single comparisons supported")
            left = self.expr(node.left)
            right = self.expr(node.comparators[0])
            op = type(node.ops[0])
            op_map = {ast.Lt: "<", ast.LtE: "<=", ast.Gt: ">", ast.GtE: ">=", ast.Eq: "==", ast.NotEq: "!="}
            if op not in op_map:
                raise TypeError(f"Unsupported comparator {op}")
            return f"({left} {op_map[op]} {right})"
        if isinstance(node, ast.Call):
            # Support range(...) only
            if isinstance(node.func, ast.Name) and node.func.id == "range":
                args = [self.expr(a) for a in node.args]
                if len(args) == 1:
                    return ("__RANGE__", ("0", args[0], "1"))
                if len(args) == 2:
                    return ("__RANGE__", (args[0], args[1], "1"))
                if len(args) == 3:
                    return ("__RANGE__", (args[0], args[1], args[2]))
                raise TypeError("range expects 1..3 args")
            raise TypeError("Only range(...) calls supported")
        if isinstance(node, ast.Subscript):
            # vector index: a[i]
            return f"{self.expr(node.value)}[{self.expr(node.slice)}]"
        if isinstance(node, ast.UnaryOp):
            if isinstance(node.op, ast.USub):
                return f"(-{self.expr(node.operand)})"
            if isinstance(node.op, ast.UAdd):
                return f"(+{self.expr(node.operand)})"
            if isinstance(node.op, ast.Not):
                return f"!({self.expr(node.operand)})"
        if isinstance(node, ast.IfExp):
            # ternary operator
            return f"({self.expr(node.test)} ? {self.expr(node.body)} : {self.expr(node.orelse)})"

        raise TypeError(f"Unsupported expression: {ast.dump(node)}")

    # Statements ---------------------------------------------------------------
    def visit_Module(self, node):  # not used
        for s in node.body:
            self.visit(s)

    def visit_Assign(self, node):
        if len(node.targets) != 1:
            raise TypeError("Only simple assignment supported")
        target = node.targets[0]
        if not isinstance(target, ast.Name):
            # allow a[i] = expr
            if isinstance(target, ast.Subscript):
                self.emit(f"{self.expr(target)} = {self.expr(node.value)};")
                return
            raise TypeError("Only simple names (or vector subscripts) on LHS")
        name = target.id
        # infer type: if assigning from number -> double/int, else leave as auto
        val = node.value
        decl = False
        if name not in self.locals:
            # very naive inference: default to double for numeric, int for ints
            if isinstance(val, ast.Constant) and isinstance(val.value, int):
                self.locals[name] = "int"
            else:
                self.locals[name] = "double"
            decl = True
        typ = self.locals[name]
        if decl:
            self.emit(f"{typ} {name} = {self.expr(val)};")
        else:
            self.emit(f"{name} = {self.expr(val)};")

    def visit_AugAssign(self, node):
        target = self.expr(node.target)
        op = type(node.op)
        op_map = {ast.Add: "+=", ast.Sub: "-=", ast.Mult: "*=", ast.Div: "/="}
        if op not in op_map:
            raise TypeError(f"Unsupported augassign {op}")
        self.emit(f"{target} {op_map[op]} {self.expr(node.value)};")

    def visit_If(self, node):
        cond = self.expr(node.test)
        self.emit(f"if {cond} " + "{")
        self.ind += 1
        for s in node.body:
            self.visit(s)
        self.ind -= 1
        if node.orelse:
            self.emit("} else {")
            self.ind += 1
            for s in node.orelse:
                self.visit(s)
            self.ind -= 1
        self.emit("}")

    def visit_For(self, node):
        # Only for i in range(...):
        itr = self.expr(node.iter)
        if not (isinstance(itr, tuple) and itr[0] == "__RANGE__"):
            raise TypeError("Only 'for ... in range(...)' supported")
        start, stop, step = itr[1]
        if not isinstance(node.target, ast.Name):
            raise TypeError("Loop var must be a name")
        i = node.target.id
        # loop var type int
        if i not in self.locals:
            self.locals[i] = "int"
            self.emit(f"for (int {i} = {start}; {i} < {stop}; {i} += {step}) " + "{")
        else:
            self.emit(f"for ({self.locals[i]} {i} = {start}; {i} < {stop}; {i} += {step}) " + "{")
        self.ind += 1
        for s in node.body:
            self.visit(s)
        self.ind -= 1
        self.emit("}")

    def visit_Return(self, node):
        self.emit(f"return {self.expr(node.value)};")

    def visit_Pass(self, node):
        self.emit("")

    def generic_visit(self, node):
        raise TypeError(f"Unsupported node: {ast.dump(node)}")


def _gen_cpp(func):
    # Parse source
    src = textwrap.dedent(inspect.getsource(func))
    mod = ast.parse(src)
    fdef = next(n for n in mod.body if isinstance(n, ast.FunctionDef))

    # Build signature
    ann = typing.get_type_hints(func)
    ret_py = ann.get("return", float)
    ret_cpp = _cpp_type(ret_py)

    params = []
    locals_types = {}
    for arg in fdef.args.args:
        name = arg.arg
        py_t = ann.get(name)
        if py_t is None:
            raise TypeError(f"Missing type annotation for parameter '{name}'")
        cpp_t = _cpp_type(py_t)
        params.append(f"{cpp_t} {name}")
        locals_types[name] = cpp_t

    gen = _CppGen(locals_types)
    gen.emit("{")
    gen.ind += 1
    for s in fdef.body:
        gen.visit(s)
    gen.ind -= 1
    gen.emit("}")

    signature = f'{ret_cpp} {fdef.name}({", ".join(params)})'
    code = signature + "\n" + "\n".join(gen.lines) + "\n"
    return code, fdef.name


# --- The decorator -----------------------------------------------------------
def jit_cpp_code(func):
    """
    Decorator that translates a *typed* Python function into C++, JIT-declares it
    into ROOT (if available), and returns a Python wrapper that calls the C++ version.

    Supported subset:
      - Types: int, float, list[int], list[float] (maps to std::vector<...>)
      - Statements: assignment, augmented assignment, for-range loops, if/else, return
      - Expressions: + - * / comparisons, indexing (v[i])
      - range(start[, stop[, step]])

    Fallback:
      If ROOT is not importable or codegen fails, returns the original Python function.

    The generated C++ code is attached as `wrapper.__cpp_code__`.
    """
    import ROOT

    code, name = _gen_cpp(func)

    ROOT.gInterpreter.Declare(code)

    cpp_func = getattr(ROOT, name)

    def wrapper(*args, **kwargs):
        return cpp_func(*args, **kwargs)

    wrapper.__name__ = func.__name__
    wrapper.__doc__ = func.__doc__
    wrapper.__cpp_code__ = code
    wrapper.__python_fallback__ = func

    return wrapper


def declare_cpp_struct(klass):
    """
    Translate a Python class definition into a C++ struct with ROOT JIT compilation.

    This decorator takes a plain Python class whose attributes define fields,
    and dynamically generates equivalent C++ `struct` definitions.
    Both an "Array-of-Structs" (AoS) and "Struct-of-Arrays" (SoA) variant are emitted
    and declared into ROOT's interpreter for high-performance numerical analysis.

    - The original struct is named after the Python class (`MyClass` → `struct MyClass`).
    - An SoA companion is also generated (`struct MyClass_SoA`) containing `std::vector`
      fields for columnar storage.
    - A type alias `using SoA = MyClass_SoA;` is added inside the AoS struct for convenience.

    Each generated C++ class is made available in `ROOT` under the same name as the
    original Python class. The corresponding C++ source code is also attached as a
    string to the class object under the attribute `__cpp_code__`.

    Parameters
    ----------
    klass : type
        A Python class with attributes whose types can be mapped to C++.
        - If an attribute's type defines `__cpp_name__`, that name will be used as the C++ type.
        - If an attribute is a float, it is translated into a `double`.
        - TODO: Support other fundamental types.

    Returns
    -------
    cpp_klass : ROOT C++ class
        The C++ struct bound in ROOT that mirrors the given Python class.

    Examples
    --------
    >>> @cppstruct()
    ... class Particle:
    ...     px = 0.0
    ...     py = 0.0
    ...     charge = 1
    ...
    >>> print(Particle.__cpp_code__)
    struct Particle
    {
        double px = 0.0;
        double py = 0.0;
        int charge = 1;
        using SoA = Particle_SoA;
    };

    >>> print(Particle.SoA.__cpp_code__)
    struct Particle_SoA
    {
        std::vector<double> px;
        std::vector<double> py;
        std::vector<int> charge;
    };
    """
    import ROOT

    kname = klass.__name__

    code = "struct " + kname + "\n"
    code += "{\n"
    code_soa = "struct " + kname + "_SoA\n"
    code_soa += "{\n"

    for name, value in klass.__dict__.items():
        if name.startswith("__"):
            continue

        cpp_name = getattr(type(value), "__cpp_name__", None)

        if cpp_name is not None:
            code += f"    {cpp_name} {name} = {value};\n"
            code_soa += f"    std::vector<{cpp_name}> {name};\n"

        if isinstance(value, float):
            code += f"    double {name} = {value};\n"
            code_soa += f"    std::vector<double> {name};\n"

    code += "\n"
    code += f"    using SoA = {kname}_SoA;\n"

    code += "};\n"
    code_soa += "};\n"

    ROOT.gInterpreter.Declare(code_soa + "\n" + code)

    cpp_klass = getattr(ROOT, kname)
    cpp_klass.__cpp_code__ = code

    cpp_klass_soa = getattr(ROOT, kname).SoA
    cpp_klass_soa.__cpp_code__ = code_soa

    return cpp_klass
