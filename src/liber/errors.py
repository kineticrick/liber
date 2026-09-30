class LiberError(Exception):
    """An expected failure. The CLI prints the message and exits 1."""


class VaultNotFoundError(LiberError):
    """No usable vault is configured."""


class VaultExistsError(LiberError):
    """`liber init` target already has content."""
