"""Guard package imports and dependency direction without loading external clients."""

import ast
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


def backend_imports(path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("backend"):
            yield node.module, node.lineno
        elif isinstance(node, ast.Import):
            for name in node.names:
                if name.name.startswith("backend."):
                    yield name.name, node.lineno


class BackendArchitectureTests(unittest.TestCase):
    def test_backend_imports_resolve_in_application_migrations_and_scripts(self):
        paths = [path for directory in ("backend", "migrations", "tests")
                 for path in (ROOT / directory).rglob("*.py")]
        paths.append(ROOT / "test_langsmith_eval.py")
        for path in paths:
            for module, line in backend_imports(path):
                target = ROOT.joinpath(*module.split("."))
                with self.subTest(file=str(path.relative_to(ROOT)), line=line, module=module):
                    self.assertTrue(target.with_suffix(".py").is_file() or (target / "__init__.py").is_file())

    def test_lower_layers_do_not_import_transport_or_business_layers(self):
        allowed = {
            "core": ("backend.core",),
            "documents": ("backend.core", "backend.documents"),
            "infrastructure": ("backend.core", "backend.documents", "backend.infrastructure"),
            "services": ("backend.core", "backend.documents", "backend.infrastructure", "backend.services", "backend.rag"),
        }
        for layer, prefixes in allowed.items():
            for path in (ROOT / "backend" / layer).rglob("*.py"):
                for module, line in backend_imports(path):
                    with self.subTest(file=str(path.relative_to(ROOT)), line=line, module=module):
                        self.assertTrue(any(module == prefix or module.startswith(prefix + ".") for prefix in prefixes))


if __name__ == "__main__":
    unittest.main()
