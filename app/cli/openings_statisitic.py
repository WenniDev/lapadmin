import datetime as _dt
import re

import click
from pydantic import BaseModel, Field

from app import app, gsheet
from app.db import Opening, Visit, Visitor


@app.cli.group("stats")
def stats_():
    pass


class SheetOpening(BaseModel):
    pseudo: str | None = Field(validation_alias="Pseudo")
    arrival: str | None = Field(validation_alias="Arrivée")
    departure: str | None = Field(validation_alias="Sortie")
    payement: str | None = Field(validation_alias="CB / Liquide ")
    waiting: str | None = Field(validation_alias="Réservation")
    notes: str | None = Field(validation_alias="Notes")
    extra: str | None = Field(validation_alias="Supplément")


def is_opening(sheet_name: str) -> bool:
    return re.match(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", sheet_name) is not None


def parse_time(date: _dt.date, timestr: str | None) -> _dt.datetime | None:
    if timestr is None:
        return None
    ts = str(timestr).strip()
    if not ts:
        return None
    for fmt in ("%H:%M:%S", "%H:%M"):
        try:
            t = _dt.datetime.strptime(ts, fmt).time()
            return _dt.datetime.combine(date, t)
        except ValueError:
            continue
    try:
        return _dt.datetime.fromisoformat(ts)
    except Exception:
        return None


def get_openings(sheet_name: str) -> Opening | None:
    if not gsheet.is_ready:
        print("Google Sheets module is not ready")
        return None

    openings = []
    sheet = gsheet.gc.open(sheet_name)
    worksheet_list = sheet.worksheets()
    for ws in worksheet_list:
        if not is_opening(ws.title):
            continue

        print("Processing opening sheet", ws.title)
        expected_headers = [
            field.validation_alias for field in SheetOpening.model_fields.values()
        ]

        opening_date = _dt.datetime.strptime(ws.title, "%Y-%m-%d").date()

        start_dt = _dt.datetime.combine(opening_date, _dt.time(16, 0))
        db_opening = Opening(
            start=start_dt,
            end=start_dt + _dt.timedelta(hours=6),
            scope="PUBLIC",
        )

        with app.session() as s:
            for row in ws.get_all_records(head=2, expected_headers=expected_headers):
                sheet_opening = SheetOpening(**row)
                pseudo = (sheet_opening.pseudo or "").strip()
                if not pseudo:
                    continue
                if pseudo == "LIMITE LEGALE D'ACCUEIL":
                    continue

                db_visitor = s.query(Visitor).filter_by(nick=pseudo).first()

                if not db_visitor:
                    print("Skipping unknown visitor with nick", pseudo)
                    continue

                entry_dt = parse_time(opening_date, sheet_opening.arrival)
                exit_dt = parse_time(opening_date, sheet_opening.departure)

                visit = Visit(
                    visitor=db_visitor, opening=db_opening, entry=entry_dt, exit=exit_dt
                )
                db_opening.visits.append(visit)
        openings.append(db_opening)

    return openings


@stats_.command()
@click.argument("nicknames", nargs=-1, required=False)
def visits(nicknames: tuple[str]) -> None:
    """Display all visits for specified users across all openings."""
    if not nicknames:
        nicknames = ("koukidemami", "Hinivir")

    with app.session() as s:
        visitors = s.query(Visitor).filter(Visitor.nick.in_(nicknames)).all()

        if not visitors:
            click.echo(f"No visitors found with nicknames: {', '.join(nicknames)}")
            return

        click.echo(f"Found {len(visitors)} visitor(s):\n")

        for visitor in visitors:
            click.echo(f"=== {visitor.nick or visitor.full_name} ===")

            visits_list = (
                s.query(Visit)
                .join(Opening)
                .filter(Visit.visitor_id == visitor.id)
                .order_by(Opening.start)
                .all()
            )

            if not visits_list:
                click.echo("  No visits recorded\n")
                continue

            click.echo(f"  Total visits: {len(visits_list)}")

            # Calculate total time
            total_hours = 0.0
            visits_with_duration = 0

            for visit in visits_list:
                opening = visit.opening
                entry_time = visit.entry.strftime("%H:%M") if visit.entry else "N/A"
                exit_time = visit.exit.strftime("%H:%M") if visit.exit else "N/A"

                # Calculate duration
                if visit.entry and visit.exit:
                    delta = visit.exit - visit.entry
                    hours = delta.total_seconds() / 3600.0
                    if hours < 0:
                        hours += 24.0
                    duration_str = f"{hours:.2f}h"
                    total_hours += hours
                    visits_with_duration += 1
                elif visit.entry:
                    duration_str = "ongoing"
                else:
                    duration_str = "N/A"

                click.echo(
                    f"  {opening.start.date()} ({opening.start.strftime('%a')}): "
                    f"{entry_time} → {exit_time} ({duration_str})"
                )

            click.echo(f"\n  Total time: {total_hours:.2f}h")

            click.echo()


@stats_.command()
@click.option("--limit", "-n", default=10, help="Number of top visitors to show")
def top(limit: int) -> None:
    """Display the top visitors by total time spent (default) or visit count."""
    with app.session() as s:
        visitors = s.query(Visitor).all()

        visitor_stats = []

        for visitor in visitors:
            visits_list = (
                s.query(Visit)
                .join(Opening)
                .filter(Visit.visitor_id == visitor.id)
                .order_by(Opening.start)
                .all()
            )

            if not visits_list:
                continue

            total_hours = 0.0
            for visit in visits_list:
                if visit.entry and visit.exit:
                    delta = visit.exit - visit.entry
                    hours = delta.total_seconds() / 3600.0
                    if hours < 0:
                        hours += 24.0
                    total_hours += hours

            visitor_stats.append(
                {
                    "visitor": visitor,
                    "visit_count": len(visits_list),
                    "total_hours": total_hours,
                    "mean": total_hours / len(visits_list) if visits_list else 0.0,
                }
            )

        if not visitor_stats:
            click.echo("No visitor data found")
            return

        visitor_stats.sort(key=lambda x: x["total_hours"], reverse=True)

        click.echo(f"{'Rank':<6} {'Nickname':<25} {'Visits':<10} {'Total Time':<15}")
        click.echo("=" * 70)

        for rank, stats in enumerate(visitor_stats[:limit], 1):
            visitor = stats["visitor"]
            nick = visitor.nick or visitor.full_name or "Unknown"
            visit_count = stats["visit_count"]
            total_hours = stats["total_hours"]
            mean = stats["mean"]

            click.echo(
                f"{rank:<6} {nick:<25} {visit_count:<10} {total_hours:.2f}h (mean: {mean:.2f}h)"
            )


@stats_.command()
@click.argument("year", type=int)
@click.argument("month", type=int)
@click.option("--limit", "-n", default=10, help="Number of top visitors to show")
def month(year: int, month: int, limit: int) -> None:
    """Display visitor statistics for a specific month.

    Example: flask stats month 2024 12
    """
    if month < 1 or month > 12:
        click.echo("Error: Month must be between 1 and 12")
        return

    # Calculate the start and end of the month
    month_start = _dt.datetime(year, month, 1)
    if month == 12:
        month_end = _dt.datetime(year + 1, 1, 1)
    else:
        month_end = _dt.datetime(year, month + 1, 1)

    with app.session() as s:
        # Get all openings in the specified month
        openings_in_month = (
            s.query(Opening)
            .filter(Opening.start >= month_start, Opening.start < month_end)
            .all()
        )

        if not openings_in_month:
            click.echo(
                f"No openings found for {_dt.date(year, month, 1).strftime('%B %Y')}"
            )
            return

        click.echo(
            f"=== Statistics for {_dt.date(year, month, 1).strftime('%B %Y')} ==="
        )
        click.echo(f"Total openings: {len(openings_in_month)}\n")

        # Collect visitor statistics for the month
        visitor_stats = {}

        for opening in openings_in_month:
            for visit in opening.visits:
                visitor = visit.visitor
                visitor_id = visitor.id

                if visitor_id not in visitor_stats:
                    visitor_stats[visitor_id] = {
                        "visitor": visitor,
                        "visit_count": 0,
                        "total_hours": 0.0,
                    }

                visitor_stats[visitor_id]["visit_count"] += 1

                if visit.entry and visit.exit:
                    delta = visit.exit - visit.entry
                    hours = delta.total_seconds() / 3600.0
                    if hours < 0:
                        hours += 24.0
                    visitor_stats[visitor_id]["total_hours"] += hours

        if not visitor_stats:
            click.echo("No visitor data found for this month")
            return

        # Sort by total hours
        sorted_stats = sorted(
            visitor_stats.values(), key=lambda x: x["total_hours"], reverse=True
        )

        click.echo(f"{'Rank':<6} {'Nickname':<25} {'Visits':<10} {'Total Time':<15}")
        click.echo("=" * 70)

        for rank, stats in enumerate(sorted_stats[:limit], 1):
            visitor = stats["visitor"]
            nick = visitor.nick or visitor.full_name or "Unknown"
            visit_count = stats["visit_count"]
            total_hours = stats["total_hours"]

            click.echo(f"{rank:<6} {nick:<25} {visit_count:<10} {total_hours:.2f}h")

        # Display total statistics
        total_visits = sum(s["visit_count"] for s in visitor_stats.values())
        total_hours = sum(s["total_hours"] for s in visitor_stats.values())
        unique_visitors = len(visitor_stats)

        click.echo("\n" + "=" * 70)
        click.echo(f"Unique visitors: {unique_visitors}")
        click.echo(f"Total visits: {total_visits}")
        click.echo(f"Total hours: {total_hours:.2f}h")


@stats_.command()
@click.option(
    "--min-visits",
    "-m",
    default=1,
    help="Minimum number of visits to include a visitor",
)
@click.option(
    "--sort-by",
    "-s",
    type=click.Choice(["avg", "total", "visits"]),
    default="avg",
    help="Sort by: avg time, total time, or visit count",
)
def average(min_visits: int, sort_by: str) -> None:
    """Display average visit duration statistics for all visitors.

    Shows total visits, total time, and average time per visit for each visitor.
    """
    with app.session() as s:
        visitors = s.query(Visitor).all()

        visitor_stats = []

        for visitor in visitors:
            visits_list = (
                s.query(Visit)
                .join(Opening)
                .filter(Visit.visitor_id == visitor.id)
                .order_by(Opening.start)
                .all()
            )

            if not visits_list or len(visits_list) < min_visits:
                continue

            total_hours = 0.0
            valid_visits = 0

            for visit in visits_list:
                if visit.entry and visit.exit:
                    delta = visit.exit - visit.entry
                    hours = delta.total_seconds() / 3600.0
                    if hours < 0:
                        hours += 24.0
                    total_hours += hours
                    valid_visits += 1

            if valid_visits == 0:
                continue

            average_hours = total_hours / valid_visits

            visitor_stats.append(
                {
                    "visitor": visitor,
                    "visit_count": len(visits_list),
                    "valid_visits": valid_visits,
                    "total_hours": total_hours,
                    "average_hours": average_hours,
                }
            )

        if not visitor_stats:
            click.echo("No visitor data found")
            return

        # Sort based on user preference
        if sort_by == "avg":
            visitor_stats.sort(key=lambda x: x["average_hours"], reverse=True)
            sort_label = "Average Time"
        elif sort_by == "total":
            visitor_stats.sort(key=lambda x: x["total_hours"], reverse=True)
            sort_label = "Total Time"
        else:  # visits
            visitor_stats.sort(key=lambda x: x["visit_count"], reverse=True)
            sort_label = "Visit Count"

        click.echo("=== Average Visit Duration Statistics ===")
        click.echo(f"Sorted by: {sort_label}")
        if min_visits > 1:
            click.echo(f"Minimum visits filter: {min_visits}")
        click.echo()

        click.echo(
            f"{'Rank':<6} {'Nickname':<25} {'Visits':<10} {'Total Time':<15} {'Avg Time':<15}"
        )
        click.echo("=" * 85)

        for rank, stats in enumerate(visitor_stats, 1):
            visitor = stats["visitor"]
            nick = visitor.nick or visitor.full_name or "Unknown"
            visit_count = stats["visit_count"]
            total_hours = stats["total_hours"]
            average_hours = stats["average_hours"]

            click.echo(
                f"{rank:<6} {nick:<25} {visit_count:<10} {total_hours:<14.2f}h {average_hours:<14.2f}h"
            )

        # Global statistics
        total_visitors = len(visitor_stats)
        total_visits_all = sum(s["visit_count"] for s in visitor_stats)
        total_hours_all = sum(s["total_hours"] for s in visitor_stats)
        total_valid_visits = sum(s["valid_visits"] for s in visitor_stats)

        if total_valid_visits > 0:
            global_average = total_hours_all / total_valid_visits
        else:
            global_average = 0.0

        click.echo("\n" + "=" * 85)
        click.echo(f"Total visitors: {total_visitors}")
        click.echo(
            f"Total visits: {total_visits_all} (with duration data: {total_valid_visits})"
        )
        click.echo(f"Total hours: {total_hours_all:.2f}h")
        click.echo(f"Global average per visit: {global_average:.2f}h")
