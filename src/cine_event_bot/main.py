"""Admin CLI entry point for cine-event-bot.

All long-running async tasks (bot polling, weekly digest cron) are launched
from here via ``asyncio.run()`` so the CLI itself stays synchronous while
the internals remain fully async.
"""

import typer

app = typer.Typer(help="cine-event-bot admin CLI")


def get_greeting() -> str:
    """Return the bot startup greeting.

    Returns:
        A short status message confirming the bot package is importable.
    """
    return "cine-event-bot is ready — tracking special screenings in Paris/IDF."


@app.command()
def greet() -> None:
    """Print the startup greeting (smoke-test that the install works)."""
    typer.echo(get_greeting())


if __name__ == "__main__":  # pragma: no cover
    app()
