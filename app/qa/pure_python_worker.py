"""Bounded AST interpreter for pure-function tests. Never imports or execs generated code.

Run by the trusted parent with -I -S. Unsupported language features are NOT_RUN,
not evidence that the generated program is wrong or that its whole GUI works.
"""
import ast
import collections
import json
import math
import operator
import re
import sys


class UnsupportedProgram(Exception):
    pass


class LimitExceeded(UnsupportedProgram):
    pass


class ReturnValue(Exception):
    def __init__(self, value):
        self.value = value


class LoopBreak(Exception):
    pass


class LoopContinue(Exception):
    pass


class Module:
    def __init__(self, members):
        self.members = members


class Function:
    def __init__(self, node, closure):
        if getattr(node,'decorator_list',[]):
            raise UnsupportedProgram('decorators are unsupported')
        self.node, self.closure = node, closure
        try:
            self.defaults=[ast.literal_eval(item) for item in node.args.defaults]
        except (ValueError,TypeError):
            raise UnsupportedProgram('non-literal default arguments are unsupported')


class Scope(dict):
    def __init__(self, parent=None):
        super().__init__()
        self.parent = parent


class Interpreter:
    MAX_ITEMS = 4096

    def __init__(self, source):
        if len(source) > 200_000:
            raise LimitExceeded('source too large')
        self.tree = ast.parse(source)
        self.steps = 0
        self.depth = 0
        self.resolving = set()
        self.global_scope = Scope()
        self.nodes = {}
        self.dirty_globals = set()
        self.allowed_calls = set()
        self.binary = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
                       ast.Div: operator.truediv, ast.FloorDiv: operator.floordiv,
                       ast.Mod: operator.mod, ast.Pow: operator.pow}
        self.builtins = {name: value for name, value in {
            'int': int, 'float': float, 'str': str, 'bool': bool, 'len': len,
            'abs': abs, 'round': round, 'min': min, 'max': max, 'sum': sum,
            'list': list, 'tuple': tuple, 'dict': dict, 'set': set,
            'range': self.safe_range, 'enumerate': enumerate, 'zip': zip, 'type': self.safe_type,
            'isinstance': isinstance, 'reversed': reversed, 'sorted': sorted,
            'ValueError': ValueError, 'TypeError': TypeError, 'SyntaxError': SyntaxError,
            'ZeroDivisionError': ZeroDivisionError, 'ArithmeticError': ArithmeticError,
            'Exception': Exception, 'IndexError': IndexError, 'KeyError': KeyError,
            'True': True, 'False': False, 'None': None,
        }.items()}
        self.modules = {
            'ast': Module({name: getattr(ast, name) for name in (
                'parse', 'walk', 'iter_child_nodes', 'Expression', 'BinOp', 'UnaryOp',
                'Constant', 'Num', 'Add', 'Sub', 'Mult', 'Div', 'FloorDiv', 'Mod',
                'Pow', 'USub', 'UAdd', 'Load', 'Name', 'Call', 'AST')}),
            'operator': Module({name: getattr(operator, name) for name in (
                'add', 'sub', 'mul', 'truediv', 'floordiv', 'mod', 'pow', 'neg', 'pos')}),
            'math': Module({name: getattr(math, name) for name in (
                'sqrt', 'floor', 'ceil', 'isfinite', 'fabs', 'pi', 'e')}),
            're': Module({name: getattr(re, name) for name in (
                'findall', 'finditer', 'fullmatch', 'match', 'search', 'sub', 'split', 'compile')}),
            'collections': Module({'deque': collections.deque}),
        }
        self.allowed_calls.update(value for value in self.builtins.values() if callable(value))
        for module in self.modules.values():
            self.allowed_calls.update(value for value in module.members.values() if callable(value))
        for node in self.tree.body:
            if isinstance(node, ast.FunctionDef):
                self.global_scope.pop(node.name,None)
                self.nodes[node.name] = node
            elif isinstance(node, (ast.Assign, ast.AnnAssign)):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                for target in targets:
                    if isinstance(target, ast.Name):
                        self.global_scope.pop(target.id,None)
                        self.nodes[target.id] = node.value
                        if len(targets)>1:self.dirty_globals.add(target.id)
            elif isinstance(node,ast.AugAssign) and isinstance(node.target,ast.Name):
                self.dirty_globals.add(node.target.id)
            elif isinstance(node,ast.Expr) and isinstance(node.value,ast.Call):
                function=node.value.func
                if isinstance(function,ast.Attribute) and isinstance(function.value,ast.Name):
                    self.dirty_globals.add(function.value.id)
                else:
                    raise UnsupportedProgram('module initialization calls are outside the pure-function scope')
            elif isinstance(node,(ast.If,ast.For,ast.While,ast.Try,ast.With)):
                main_guard=(isinstance(node,ast.If) and isinstance(node.test,ast.Compare)
                    and isinstance(node.test.left,ast.Name) and node.test.left.id=='__name__'
                    and len(node.test.ops)==1 and isinstance(node.test.ops[0],ast.Eq)
                    and isinstance(node.test.comparators[0],ast.Constant)
                    and node.test.comparators[0].value=='__main__' and not node.orelse)
                if not main_guard:
                    raise UnsupportedProgram('module control flow is outside the pure-function scope')
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name in self.modules:
                        self.nodes.pop(alias.asname or alias.name,None)
                        self.global_scope[alias.asname or alias.name] = self.modules[alias.name]
            elif isinstance(node, ast.ImportFrom) and node.module in self.modules and not node.level:
                for alias in node.names:
                    if alias.name in self.modules[node.module].members:
                        self.nodes.pop(alias.asname or alias.name,None)
                        self.global_scope[alias.asname or alias.name] = self.modules[node.module].members[alias.name]

    def tick(self):
        self.steps += 1
        if self.steps > 30_000:
            raise LimitExceeded('AST step limit exceeded')

    def safe_range(self, *args):
        value = range(*args)
        if len(value) > self.MAX_ITEMS:
            raise LimitExceeded('range too large')
        return value

    def safe_type(self, value):
        return type(value)

    def bounded(self, value):
        if isinstance(value, (str, list, tuple, dict, set, collections.deque)) and len(value) > self.MAX_ITEMS:
            raise LimitExceeded('value too large')
        if isinstance(value, int) and value.bit_length() > 4096:
            raise LimitExceeded('integer too large')
        return value

    def name(self, name, scope):
        if name.startswith('__'):
            raise UnsupportedProgram('dunder access is unsupported')
        current = scope
        while current is not None:
            if current is self.global_scope and name in self.dirty_globals:
                raise UnsupportedProgram('mutated module dependency: '+name)
            if name in current:
                return current[name]
            current = current.parent
        if name in self.dirty_globals:
            raise UnsupportedProgram('mutated module dependency: '+name)
        if name in self.nodes:
            if name in self.resolving:
                raise UnsupportedProgram('cyclic global initialization')
            node = self.nodes[name]
            if isinstance(node, ast.FunctionDef):
                if node.decorator_list:
                    raise UnsupportedProgram('decorators are unsupported')
                value = Function(node, self.global_scope)
            else:
                self.resolving.add(name)
                try:
                    value = self.expr(node, self.global_scope)
                finally:
                    self.resolving.remove(name)
            self.global_scope[name] = value
            return value
        if name in self.builtins:
            return self.builtins[name]
        raise UnsupportedProgram('unavailable name: ' + name)

    def attribute(self, value, attr):
        if attr=='__name__' and isinstance(value,type) and value in self.allowed_calls:
            return value.__name__
        if attr.startswith('_'):
            raise UnsupportedProgram('private attribute access is unsupported')
        if isinstance(value, Module):
            if attr not in value.members:
                raise UnsupportedProgram('unavailable module member: ' + attr)
            return value.members[attr]
        if isinstance(value, ast.AST) and (attr in value._fields or attr in {'n', 's', 'lineno', 'col_offset'}):
            return getattr(value, attr)
        methods = {
            str: {'strip','lstrip','rstrip','replace','split','join','isdigit','isdecimal','isspace','startswith','endswith','lower','upper','find','count'},
            list: {'append','pop','insert','remove','clear','copy','reverse','index','count'},
            dict: {'get','keys','values','items','pop','copy'},
            tuple: {'index','count'},
            set: {'add','remove','discard','pop'},
            collections.deque: {'append','appendleft','pop','popleft','clear'},
            re.Pattern: {'match','fullmatch','search','findall','finditer','split','sub'},
            re.Match: {'group','groups','start','end','span'},
        }
        for kind, allowed in methods.items():
            if isinstance(value, kind) and attr in allowed:
                method = getattr(value, attr)
                self.allowed_calls.add(method)
                return method
        raise UnsupportedProgram('unsupported attribute: ' + attr)

    def operation(self, op, left, right):
        if type(op) not in self.binary:
            raise UnsupportedProgram('unsupported operator')
        if isinstance(op, ast.Pow) and (not isinstance(right, (int,float)) or abs(right) > 100):
            raise LimitExceeded('exponent too large')
        if isinstance(op, ast.Mult):
            for sequence, count in ((left,right),(right,left)):
                if isinstance(sequence,(str,list,tuple)) and isinstance(count,int) and len(sequence)*max(count,0)>self.MAX_ITEMS:
                    raise LimitExceeded('sequence too large')
        return self.bounded(self.binary[type(op)](left,right))

    def call(self, function, args, kwargs):
        self.tick()
        if isinstance(function, Function):
            node = function.node
            if node.args.vararg or node.args.kwarg or node.args.kwonlyargs:
                raise UnsupportedProgram('variadic function signature is unsupported')
            parameters = node.args.posonlyargs + node.args.args
            if len(args) > len(parameters):
                raise TypeError('too many arguments')
            scope = Scope(function.closure)
            for parameter, value in zip(parameters, args):
                scope[parameter.arg] = value
            for key, value in kwargs.items():
                if key not in {p.arg for p in parameters} or key in scope:
                    raise TypeError('invalid argument')
                scope[key] = value
            defaults = dict(zip([p.arg for p in parameters[-len(function.defaults):]], function.defaults)) if function.defaults else {}
            for parameter in parameters:
                if parameter.arg not in scope:
                    if parameter.arg not in defaults:
                        raise TypeError('missing argument')
                    scope[parameter.arg] = defaults[parameter.arg]
            self.depth += 1
            if self.depth > 80:
                raise LimitExceeded('call depth exceeded')
            try:
                self.block(node.body,scope)
            except ReturnValue as result:
                return result.value
            finally:
                self.depth -= 1
            return None
        try:
            allowed = function in self.allowed_calls
        except TypeError:
            allowed = False
        if not allowed:
            raise UnsupportedProgram('call target is not allowed')
        # Route operator functions through the same resource checks as binary expressions.
        for kind, operation in self.binary.items():
            if function is operation and len(args)==2 and not kwargs:
                return self.operation(kind(),*args)
        return self.bounded(function(*args,**kwargs))

    def expr(self, node, scope):
        self.tick()
        if isinstance(node, ast.Constant):
            return self.bounded(node.value)
        if isinstance(node,ast.JoinedStr):
            return self.bounded(''.join(self.expr(item,scope) for item in node.values))
        if isinstance(node,ast.FormattedValue):
            if node.format_spec is not None:
                raise UnsupportedProgram('formatted width/specification is unsupported')
            value=self.expr(node.value,scope)
            if not isinstance(value,(str,int,float,bool,type(None),Exception,type)):
                raise UnsupportedProgram('formatting this value is unsupported')
            convert={-1:str,115:str,114:repr,97:ascii}.get(node.conversion)
            if convert is None:raise UnsupportedProgram('unsupported string conversion')
            return self.bounded(convert(value))
        if isinstance(node, ast.Name):
            return self.name(node.id,scope)
        if isinstance(node, ast.Attribute):
            return self.attribute(self.expr(node.value,scope),node.attr)
        if isinstance(node, ast.Subscript):
            return self.expr(node.value,scope)[self.expr(node.slice,scope)]
        if isinstance(node, ast.Slice):
            return slice(*(self.expr(n,scope) if n else None for n in (node.lower,node.upper,node.step)))
        if isinstance(node, (ast.List,ast.Tuple,ast.Set)):
            values=[self.expr(n,scope) for n in node.elts]
            return self.bounded({ast.List:list,ast.Tuple:tuple,ast.Set:set}[type(node)](values))
        if isinstance(node, ast.Dict):
            if any(key is None for key in node.keys):
                raise UnsupportedProgram('dictionary unpacking is unsupported')
            return self.bounded({self.expr(k,scope):self.expr(v,scope) for k,v in zip(node.keys,node.values)})
        if isinstance(node, ast.BinOp):
            return self.operation(node.op,self.expr(node.left,scope),self.expr(node.right,scope))
        if isinstance(node, ast.UnaryOp):
            value=self.expr(node.operand,scope)
            if isinstance(node.op,ast.USub): return -value
            if isinstance(node.op,ast.UAdd): return +value
            if isinstance(node.op,ast.Not): return not value
        if isinstance(node, ast.BoolOp):
            value=None
            for item in node.values:
                value=self.expr(item,scope)
                if isinstance(node.op,ast.And) and not value: break
                if isinstance(node.op,ast.Or) and value: break
            return value
        if isinstance(node, ast.Compare):
            comparisons={ast.Eq:operator.eq,ast.NotEq:operator.ne,ast.Lt:operator.lt,ast.LtE:operator.le,
                         ast.Gt:operator.gt,ast.GtE:operator.ge,ast.Is:operator.is_,ast.IsNot:operator.is_not,
                         ast.In:lambda a,b:a in b,ast.NotIn:lambda a,b:a not in b}
            left=self.expr(node.left,scope)
            for op,expression in zip(node.ops,node.comparators):
                right=self.expr(expression,scope)
                if not comparisons[type(op)](left,right): return False
                left=right
            return True
        if isinstance(node, ast.IfExp):
            return self.expr(node.body if self.expr(node.test,scope) else node.orelse,scope)
        if isinstance(node, ast.Call):
            if any(isinstance(arg,ast.Starred) for arg in node.args) or any(k.arg is None for k in node.keywords):
                raise UnsupportedProgram('call unpacking is unsupported')
            return self.call(self.expr(node.func,scope),[self.expr(arg,scope) for arg in node.args],
                             {k.arg:self.expr(k.value,scope) for k in node.keywords})
        if isinstance(node, ast.Lambda):
            synthetic=ast.FunctionDef(name='lambda',args=node.args,body=[ast.Return(value=node.body)],decorator_list=[])
            return Function(synthetic,scope)
        if isinstance(node,(ast.ListComp,ast.SetComp,ast.DictComp,ast.GeneratorExp)):
            values=[]
            def visit(index, local):
                self.tick()
                if index==len(node.generators):
                    values.append((self.expr(node.key,local),self.expr(node.value,local)) if isinstance(node,ast.DictComp) else self.expr(node.elt,local))
                    self.bounded(values)
                    return
                generator=node.generators[index]
                if generator.is_async: raise UnsupportedProgram('async is unsupported')
                for value in self.expr(generator.iter,local):
                    self.tick();child=Scope(local);self.assign(generator.target,value,child)
                    if all(self.expr(test,child) for test in generator.ifs):visit(index+1,child)
            visit(0,Scope(scope))
            return dict(values) if isinstance(node,ast.DictComp) else set(values) if isinstance(node,ast.SetComp) else iter(values) if isinstance(node,ast.GeneratorExp) else values
        raise UnsupportedProgram('unsupported expression: '+type(node).__name__)

    def assign(self, target, value, scope):
        if isinstance(target,ast.Name):
            if target.id.startswith('__'):raise UnsupportedProgram('private name')
            scope[target.id]=self.bounded(value)
        elif isinstance(target,(ast.Tuple,ast.List)):
            if len(target.elts)!=len(value):raise ValueError('unpacking length mismatch')
            for item,part in zip(target.elts,value):self.assign(item,part,scope)
        elif isinstance(target,ast.Subscript):
            obj=self.expr(target.value,scope);obj[self.expr(target.slice,scope)]=self.bounded(value);self.bounded(obj)
        else:raise UnsupportedProgram('unsupported assignment')

    def block(self, statements, scope):
        for node in statements:
            self.tick()
            if isinstance(node,ast.Return):raise ReturnValue(self.expr(node.value,scope) if node.value else None)
            elif isinstance(node,ast.FunctionDef):scope[node.name]=Function(node,scope)
            elif isinstance(node,ast.Import):
                for alias in node.names:
                    if alias.name not in self.modules:raise UnsupportedProgram('unavailable import: '+alias.name)
                    scope[alias.asname or alias.name]=self.modules[alias.name]
            elif isinstance(node,ast.ImportFrom):
                if node.level or node.module not in self.modules:raise UnsupportedProgram('unavailable import')
                for alias in node.names:
                    if alias.name not in self.modules[node.module].members:raise UnsupportedProgram('unavailable import member')
                    scope[alias.asname or alias.name]=self.modules[node.module].members[alias.name]
            elif isinstance(node,(ast.Assign,ast.AnnAssign)):
                if node.value is not None:
                    value=self.expr(node.value,scope)
                    for target in node.targets if isinstance(node,ast.Assign) else [node.target]:self.assign(target,value,scope)
            elif isinstance(node,ast.AugAssign):self.assign(node.target,self.operation(node.op,self.expr(node.target,scope),self.expr(node.value,scope)),scope)
            elif isinstance(node,ast.Expr):self.expr(node.value,scope)
            elif isinstance(node,ast.If):self.block(node.body if self.expr(node.test,scope) else node.orelse,scope)
            elif isinstance(node,(ast.For,ast.While)):
                broke=False
                def values():
                    if isinstance(node,ast.For):yield from self.expr(node.iter,scope)
                    else:
                        while self.expr(node.test,scope):self.tick();yield None
                for value in values():
                    self.tick()
                    if isinstance(node,ast.For):self.assign(node.target,value,scope)
                    try:self.block(node.body,scope)
                    except LoopContinue:continue
                    except LoopBreak:broke=True;break
                if not broke:self.block(node.orelse,scope)
            elif isinstance(node,ast.Break):raise LoopBreak()
            elif isinstance(node,ast.Continue):raise LoopContinue()
            elif isinstance(node,ast.Pass):pass
            elif isinstance(node,ast.Raise):
                error=self.expr(node.exc,scope) if node.exc else None
                if isinstance(error,type) and issubclass(error,Exception):error=error()
                if not isinstance(error,Exception):raise UnsupportedProgram('unsupported raise')
                raise error
            elif isinstance(node,ast.Try):
                unavailable=False
                try:
                    try:self.block(node.body,scope)
                    except UnsupportedProgram:
                        unavailable=True
                        raise
                    except (ReturnValue,LoopBreak,LoopContinue):raise
                    except Exception as error:
                        handled=False
                        for handler in node.handlers:
                            kind=self.expr(handler.type,scope) if handler.type else Exception
                            if isinstance(error,kind):
                                if handler.name:scope[handler.name]=error
                                self.block(handler.body,scope);handled=True;break
                        if not handled:raise
                    else:self.block(node.orelse,scope)
                finally:
                    if not unavailable:self.block(node.finalbody,scope)
            else:raise UnsupportedProgram('unsupported statement: '+type(node).__name__)


def values_equal(actual,expected,depth=0):
    """Compare JSON-like values without bool/int coercion inside containers."""
    if depth>128 or type(actual) is not type(expected):return False
    if isinstance(expected,dict):
        return (actual.keys()==expected.keys()
                and all(type(key) is str for key in actual)
                and all(values_equal(actual[key],value,depth+1) for key,value in expected.items()))
    if isinstance(expected,(list,tuple)):
        return len(actual)==len(expected) and all(values_equal(a,b,depth+1) for a,b in zip(actual,expected))
    return actual==expected


def run_case(source, case):
    try:
        engine=Interpreter(source)
        if not isinstance(engine.nodes.get(case['function']),ast.FunctionDef):
            return {'status':'FAIL','reason':'requested module-level function is missing: '+case['function']}
    except LimitExceeded as exc:
        return {'status':'FAIL','reason':str(exc)}
    except UnsupportedProgram as exc:
        return {'status':'NOT_RUN','reason':str(exc)}
    except (SyntaxError,ValueError) as exc:
        return {'status':'FAIL','reason':'invalid source: '+str(exc)[:300]}
    try:
        args=case.get('args',[])
        if len(json.dumps(args))>4096:raise LimitExceeded('input too large')
        value=engine.call(engine.name(case['function'],engine.global_scope),args,{})
        if case.get('error'):
            passed=not case.get('error_type') and isinstance(value,str) and bool(re.match(r'^(?:error|invalid|오류|잘못)(?:\b|[:\s])',value.strip(),re.I))
        else:
            expected=case['expected']
            passed=(not isinstance(value,bool) and isinstance(value,(int,float)) and not isinstance(expected,bool)
                    and isinstance(expected,(int,float)) and math.isclose(value,expected,rel_tol=1e-9,abs_tol=1e-9)) or values_equal(value,expected)
        return {'status':'PASS' if passed else 'FAIL','actual':repr(value)[:300]}
    except LimitExceeded as exc:
        return {'status':'FAIL','reason':str(exc)}
    except UnsupportedProgram as exc:
        return {'status':'NOT_RUN','reason':str(exc)}
    except (ReturnValue,LoopBreak,LoopContinue):
        return {'status':'NOT_RUN','reason':'invalid control flow'}
    except Exception as exc:
        passed=bool(case.get('error')) and (type(exc).__name__==case['error_type'] if case.get('error_type')
            else isinstance(exc,(ValueError,TypeError,ArithmeticError,SyntaxError)))
        return {'status':'PASS' if passed else 'FAIL','exception':type(exc).__name__,'reason':str(exc)[:300]}


def main():
    try:
        request=json.loads(sys.stdin.read(250_000))
        results=[dict(case=case,**run_case(request['source'],case)) for case in request['cases'][:32]]
        sys.stdout.write(json.dumps({'cases':results},ensure_ascii=True))
    except Exception as exc:
        sys.stdout.write(json.dumps({'error':type(exc).__name__+': '+str(exc)[:300]}))


if __name__=='__main__':
    main()
