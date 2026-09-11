"""Local iPhone control; importing the package does not start/register a server."""


def main() -> None:
    from .server import main as run
    run()


__all__ = ['main']
