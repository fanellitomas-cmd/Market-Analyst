class SourceError(Exception):
    """La fuente externa falló o devolvió algo que no se pudo interpretar."""

    def __init__(self, source: str, message: str) -> None:
        super().__init__(f"{source}: {message}")
        self.source = source
        self.message = message


class NotFoundError(SourceError):
    """La fuente respondió bien, pero el símbolo o la serie pedida no existe."""
