from pathlib import Path


def write(path: Path, text: str) -> Path:
    """Create parent dirs and write UTF-8 text."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path
