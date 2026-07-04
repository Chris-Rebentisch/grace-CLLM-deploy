"""D25 boundary guard: ExtractionLLMClient must not import get_provider (D444.5)."""
import ast


def test_instructor_client_does_not_import_get_provider():
    with open("src/extraction/instructor_client.py") as f:
        tree = ast.parse(f.read())
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.module and "llm_provider" in node.module:
                names = [alias.name for alias in node.names]
                assert "get_provider" not in names, (
                    "D25 boundary violated: instructor_client.py imports get_provider"
                )
