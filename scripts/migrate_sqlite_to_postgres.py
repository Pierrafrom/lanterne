r"""Idempotent, checked migration of Lanterne's data from SQLite to PostgreSQL.

See ``docs/decisions/0014-postgres-migration.md`` for the full rationale.
Reads exclusively from a **backup** SQLite file passed on the command line
— never the live database — and writes into the PostgreSQL database
identified by ``DATABASE_URL`` (same convention as the application),
inserting rows in foreign-key-safe order and preserving every primary key
so ``dedup_key`` and cross-table references stay intact. Postgres
sequences are reset to continue past the highest migrated id once done.

Safe to re-run: a row whose primary key already exists in the target is
skipped, never duplicated or overwritten.

Usage::

    # Dry run — read-only, writes nothing, reports what a real run would do:
    uv run python scripts/migrate_sqlite_to_postgres.py \\
        backups/lanterne-pre-pg-migration-*.db --check

    # Real run:
    uv run python scripts/migrate_sqlite_to_postgres.py \\
        backups/lanterne-pre-pg-migration-*.db

Run ``--check`` again after a real run to verify the target now matches
the source exactly (zero missing/extra/mismatched rows).
"""

import argparse
import asyncio
import hashlib
import json
import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from lanterne.config import Settings
from lanterne.core.models import (
    EventSighting,
    EventType,
    Film,
    FilmRating,
    RatingSource,
    ScreeningEvent,
    Source,
    Subscriber,
    Venue,
    VenueKind,
)
from sqlalchemy import Table, text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine


def _parse_utc(value: str) -> datetime:
    """Parse a SQLite-stored timestamp string as a UTC-aware datetime.

    The application only ever writes UTC-normalized values (see
    ``UtcDateTime`` in ``core/models.py``), so a naive stored value is
    always already UTC — this just attaches the timezone back.
    """
    return datetime.fromisoformat(value).replace(tzinfo=UTC)


def _parse_json(value: str | None) -> list[str] | None:
    """Parse a SQLite JSON-as-text column, including the literal ``"null"``.

    SQLAlchemy's ``JSON`` type serializes a Python ``None`` as the JSON
    text ``"null"`` (four characters), not a SQL ``NULL``, unless
    configured otherwise — ``json.loads`` handles both a real ``NULL``
    (already ``None`` here) and the literal ``"null"`` string correctly.
    """
    return None if value is None else json.loads(value)


@dataclass(frozen=True)
class _TableSpec:
    """How one table is read from SQLite and written to PostgreSQL."""

    name: str
    sqlalchemy_table: Table
    id_column: str
    transform: Any  # Callable[[sqlite3.Row], dict[str, Any]]


def _film_row(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "title_key": row["title_key"],
        "title": row["title"],
        "tmdb_id": row["tmdb_id"],
        "imdb_id": row["imdb_id"],
        "original_title": row["original_title"],
        "director": row["director"],
        "release_year": row["release_year"],
        "runtime_minutes": row["runtime_minutes"],
        "genres": _parse_json(row["genres"]),
        "overview": row["overview"],
        "poster_url": row["poster_url"],
        "backdrop_url": row["backdrop_url"],
        "vote_average": row["vote_average"],
    }


def _venue_row(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "slug": row["slug"],
        "name": row["name"],
        "kind": VenueKind[row["kind"]],
        "address": row["address"],
        "website": row["website"],
        "accepted_passes": _parse_json(row["accepted_passes"]),
        "seat_count": row["seat_count"],
        "screen_width_m": row["screen_width_m"],
        "screen_height_m": row["screen_height_m"],
        "paris_cine_info_tid": row["paris_cine_info_tid"],
    }


def _screeningevent_row(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "dedup_key": row["dedup_key"],
        "film_id": row["film_id"],
        "venue_id": row["venue_id"],
        "event_type": EventType[row["event_type"]]
        if row["event_type"] is not None
        else None,
        "is_special": bool(row["is_special"]),
        "specialness_reasons": _parse_json(row["specialness_reasons"]),
        "specialness_confidence": row["specialness_confidence"],
        "starts_at": _parse_utc(row["starts_at"]),
        "has_team_present": bool(row["has_team_present"]),
        "description": row["description"],
        "cycle_name": row["cycle_name"],
        "booking_url": row["booking_url"],
    }


def _eventsighting_row(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "event_id": row["event_id"],
        "source": Source[row["source"]],
        "source_url": row["source_url"],
        "scraped_at": _parse_utc(row["scraped_at"]),
    }


def _filmrating_row(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "film_id": row["film_id"],
        "source": RatingSource[row["source"]],
        "rating": row["rating"],
        "url": row["url"],
        "fetched_at": _parse_utc(row["fetched_at"]),
    }


def _subscriber_row(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "chat_id": row["chat_id"],
        "subscribed_at": _parse_utc(row["subscribed_at"]),
        "is_active": bool(row["is_active"]),
    }


# Foreign-key-safe order: parents before children.
_TABLE_SPECS: Sequence[_TableSpec] = (
    _TableSpec("venue", Venue.__table__, "id", _venue_row),
    _TableSpec("film", Film.__table__, "id", _film_row),
    _TableSpec("screeningevent", ScreeningEvent.__table__, "id", _screeningevent_row),
    _TableSpec("eventsighting", EventSighting.__table__, "id", _eventsighting_row),
    _TableSpec("filmrating", FilmRating.__table__, "id", _filmrating_row),
    _TableSpec("subscriber", Subscriber.__table__, "chat_id", _subscriber_row),
)


def _row_hash(values: dict[str, Any]) -> str:
    """Deterministic content hash of a transformed row, keyed by nothing.

    Used to compare a source-derived row against what actually ended up in
    Postgres — independent of column order and of either engine's own
    string formatting, since both sides are hashed via the same Python
    values.
    """
    canonical = json.dumps(values, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass
class _TableCheckResult:
    """Outcome of comparing one table between the SQLite source and Postgres."""

    table: str
    source_count: int
    target_count: int
    missing_in_target: list[int] = field(default_factory=list)
    extra_in_target: list[int] = field(default_factory=list)
    content_mismatches: list[int] = field(default_factory=list)

    @property
    def is_clean(self) -> bool:
        """Whether the target fully and correctly matches the source."""
        return not (
            self.missing_in_target or self.extra_in_target or self.content_mismatches
        )


async def _load_source_rows(
    sqlite_path: Path, spec: _TableSpec
) -> dict[int, dict[str, Any]]:
    """Read and transform every row of one table from the SQLite backup."""
    connection = sqlite3.connect(sqlite_path)
    connection.row_factory = sqlite3.Row
    try:
        rows = connection.execute(f'SELECT * FROM "{spec.name}"').fetchall()  # noqa: S608 — spec.name is one of six hardcoded literals above, never user input
    finally:
        connection.close()
    return {row[spec.id_column]: spec.transform(row) for row in rows}


async def _load_target_rows(
    engine: AsyncEngine, spec: _TableSpec
) -> dict[int, dict[str, Any]]:
    """Read every row currently in the Postgres target table."""
    async with engine.connect() as connection:
        result = await connection.execute(spec.sqlalchemy_table.select())
        rows = result.mappings().all()
    return {row[spec.id_column]: dict(row) for row in rows}


def _compare(
    spec: _TableSpec,
    source_rows: dict[int, dict[str, Any]],
    target_rows: dict[int, dict[str, Any]],
) -> _TableCheckResult:
    """Diff source and target rows for one table by id, then by content."""
    source_ids = set(source_rows)
    target_ids = set(target_rows)
    mismatches = [
        row_id
        for row_id in source_ids & target_ids
        if _row_hash(source_rows[row_id]) != _row_hash(target_rows[row_id])
    ]
    return _TableCheckResult(
        table=spec.name,
        source_count=len(source_rows),
        target_count=len(target_rows),
        missing_in_target=sorted(source_ids - target_ids),
        extra_in_target=sorted(target_ids - source_ids),
        content_mismatches=sorted(mismatches),
    )


async def check(sqlite_path: Path, database_url: str) -> list[_TableCheckResult]:
    """Compare every table between the SQLite backup and the Postgres target.

    Read-only on both sides — writes nothing. Safe to run before a real
    migration (as a preview of what is pending) or after one (as
    verification).
    """
    engine = create_async_engine(database_url)
    try:
        results = []
        for spec in _TABLE_SPECS:
            source_rows = await _load_source_rows(sqlite_path, spec)
            target_rows = await _load_target_rows(engine, spec)
            results.append(_compare(spec, source_rows, target_rows))
        return results
    finally:
        await engine.dispose()


async def migrate(sqlite_path: Path, database_url: str) -> dict[str, int]:
    """Insert every missing row from the SQLite backup into Postgres.

    Idempotent: a row already present in the target (by primary key) is
    skipped, never duplicated or overwritten. Resets each table's sequence
    to continue past the highest migrated id once every table is done.

    Returns:
        Number of rows actually inserted, per table.
    """
    engine = create_async_engine(database_url)
    inserted_counts: dict[str, int] = {}
    try:
        for spec in _TABLE_SPECS:
            source_rows = await _load_source_rows(sqlite_path, spec)
            target_rows = await _load_target_rows(engine, spec)
            to_insert = [
                values
                for row_id, values in source_rows.items()
                if row_id not in target_rows
            ]
            if to_insert:
                async with engine.begin() as connection:
                    await connection.execute(spec.sqlalchemy_table.insert(), to_insert)
            inserted_counts[spec.name] = len(to_insert)

        async with engine.begin() as connection:
            for spec in _TABLE_SPECS:
                if spec.id_column != "id":
                    continue  # only integer-PK sequences need resetting
                await connection.execute(
                    text(
                        f"SELECT setval("  # noqa: S608 — spec.name is one of six hardcoded literals above, never user input
                        f"pg_get_serial_sequence('{spec.name}', 'id'), "
                        f"COALESCE((SELECT max(id) FROM {spec.name}), 1))"
                    )
                )
        return inserted_counts
    finally:
        await engine.dispose()


def _print_check_report(results: list[_TableCheckResult]) -> bool:
    """Print a human-readable check report; return whether everything is clean."""
    all_clean = True
    for result in results:
        status = "OK" if result.is_clean else "MISMATCH"
        print(
            f"{result.table:16s} source={result.source_count:6d} "
            f"target={result.target_count:6d}  [{status}]"
        )
        if result.missing_in_target:
            all_clean = False
            print(
                f"  missing in target ({len(result.missing_in_target)}): "
                f"{result.missing_in_target[:10]}"
                f"{'...' if len(result.missing_in_target) > 10 else ''}"
            )
        if result.extra_in_target:
            all_clean = False
            print(
                f"  extra in target ({len(result.extra_in_target)}): "
                f"{result.extra_in_target[:10]}"
                f"{'...' if len(result.extra_in_target) > 10 else ''}"
            )
        if result.content_mismatches:
            all_clean = False
            print(
                f"  content mismatches ({len(result.content_mismatches)}): "
                f"{result.content_mismatches[:10]}"
                f"{'...' if len(result.content_mismatches) > 10 else ''}"
            )
    return all_clean


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "sqlite_path", type=Path, help="Path to a SQLite backup file (read-only)."
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Read-only: report the diff between source and target, write nothing.",
    )
    return parser.parse_args()


async def _main() -> None:
    args = _parse_args()
    if not args.sqlite_path.is_file():
        raise SystemExit(f"SQLite backup not found: {args.sqlite_path}")

    database_url = Settings().database_url

    if args.check:
        results = await check(args.sqlite_path, database_url)
        clean = _print_check_report(results)
        raise SystemExit(0 if clean else 1)

    inserted = await migrate(args.sqlite_path, database_url)
    for table, count in inserted.items():
        print(f"{table}: inserted {count} row(s)")
    print("\nRun with --check to verify the target now matches the source.")


if __name__ == "__main__":
    asyncio.run(_main())
