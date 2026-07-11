import unidecode
from pydantic import BaseModel, ConfigDict, Field

from app import app, gsheet


@app.cli.group("import")
def import_():
    pass


class SheetVisitor(BaseModel):
    last_name: str | int | float | None = Field(validation_alias="Nom")
    email: str | int | float | None = Field(validation_alias="Email")
    first_name: str | int | float | None = Field(validation_alias="Prénom")
    nick: str | int | float | None = Field(validation_alias="Pseudo")

    @property
    def full_name(self):
        if not self.first_name:
            return self.last_name
        if not self.last_name:
            return self.first_name
        return f"{self.first_name} {self.last_name}"

    def model_post_init(self, _context: object) -> None:
        def clean_from_multiple_spaces(s: str) -> str | None:
            if not s:
                return None
            return " ".join(s.split())

        # Convert to string if needed
        if self.email is not None:
            self.email = str(self.email).lower().strip() or None
        if self.first_name is not None:
            self.first_name = str(self.first_name).title()
        if self.last_name is not None:
            self.last_name = str(self.last_name).title()

        if self.nick is not None:
            self.nick = str(self.nick).strip(' "')

        if self.nick and self.nick[0] == "+":
            words = self.nick.split()
            if len(words) > 1 and words[1] == "de":
                print('Removing nick "+X de <name>"')
                self.nick = None

        if self.nick and self.nick.find("(") != -1:
            if self.nick[-1] == ")":
                nick = unidecode.unidecode(self.nick)
                if nick.endswith("(a completer)") or nick.endswith("(a remplir)"):
                    print("Removing suffix", nick[nick.find("(") :])
                    self.nick = self.nick[: self.nick.find("(")].strip()

        if self.nick and self.nick == self.full_name:
            print("Removing nick equal to full name", self.nick)
            self.nick = None

        for attr in ["first_name", "last_name", "nick"]:
            if not getattr(self, attr):
                continue
            setattr(self, attr, clean_from_multiple_spaces(getattr(self, attr)))

    @property
    def is_empty(self):
        return not bool(self.first_name or self.last_name or self.email or self.nick)


@import_.command()
def visitors():
    if not gsheet.is_ready:
        print("Google Sheets module is not ready")
        return

    from app.db import Visitor

    created = []
    sheet = gsheet.gc.open("Visiteurs")
    worksheet = sheet.get_worksheet(0)
    print("Got sheet", worksheet)
    expected_headers: list[str] = [
        str(field.validation_alias)
        for field in SheetVisitor.model_fields.values()
        if field.validation_alias is not None
    ]
    for row in worksheet.get_all_records(expected_headers=expected_headers):
        sheet_visitor = SheetVisitor(**row)
        if sheet_visitor.is_empty:
            print("Skipping empty row", row)
            continue
        with app.session() as s:
            if sheet_visitor.email:
                db_visitor = (
                    s.query(Visitor).filter_by(email=sheet_visitor.email).first()
                )
            elif sheet_visitor.first_name and sheet_visitor.last_name:
                db_visitor = (
                    s.query(Visitor)
                    .filter_by(
                        first_name=sheet_visitor.first_name,
                        last_name=sheet_visitor.last_name,
                    )
                    .first()
                )
            else:
                db_visitor = s.query(Visitor).filter_by(nick=sheet_visitor.nick).first()
            if db_visitor:
                print("Skipping existing user", db_visitor)
                continue
            print("Creating user", sheet_visitor)
            db_visitor = Visitor(
                first_name=sheet_visitor.first_name,
                last_name=sheet_visitor.last_name,
                email=sheet_visitor.email,
                nick=sheet_visitor.nick,
            )
            s.add(db_visitor)
            s.commit()
            print("Created user", db_visitor)
            created.append(db_visitor)

    print("Created", len(created), "visitors")


class SheetOpening(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    pseudo: str | int | float | None = Field(default=None, validation_alias="Pseudo")
    arrival: str | int | float | None = Field(default=None, validation_alias="Arrivée")
    departure: str | int | float | None = Field(default=None, validation_alias="Sortie")
    payement: str | int | float | None = Field(
        default=None, validation_alias="CB / Liquide "
    )
    waiting: str | int | float | None = Field(
        default=None, validation_alias="Fil d'attente"
    )
    notes: str | int | float | None = Field(default=None, validation_alias="Notes")
    extra: str | int | float | None = Field(default=None, validation_alias="Supplément")


@import_.command()
def opening():
    if not gsheet.is_ready:
        print("Google Sheets module is not ready")
        return

    import datetime as _dt
    import re

    from app.db import Opening, Visit, Visitor

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

    def process_spreadsheet(sheet_name: str):
        """Process a single spreadsheet and import all its opening worksheets."""
        print(f"\n=== Processing spreadsheet: {sheet_name} ===")
        try:
            sheet = gsheet.gc.open(sheet_name)
        except Exception as e:
            print(f"Error opening spreadsheet {sheet_name}: {e}")
            return

        worksheet_list = sheet.worksheets()
        for ws in worksheet_list:
            if not is_opening(ws.title):
                print("Skipping non-opening sheet", ws.title)
                continue

            print("Processing opening sheet", ws.title)
            # Don't include "Supplément" as it doesn't exist in older sheets
            # When it's missing, the value defaults to None (implying 0)
            expected_headers = [
                "Pseudo",
                "Arrivée",
                "Sortie",
                "CB / Liquide ",
                "Notes",
            ]

            opening_date = _dt.datetime.strptime(ws.title, "%Y-%m-%d").date()

            with app.session() as s:
                start_dt = _dt.datetime.combine(opening_date, _dt.time(16, 0))

                db_opening = s.query(Opening).filter_by(start=start_dt).first()
                if db_opening:
                    print("Opening already exists", db_opening)
                else:
                    db_opening = Opening(
                        start=start_dt,
                        end=start_dt + _dt.timedelta(hours=6),
                        scope="PUBLIC",
                    )
                    s.add(db_opening)
                    s.commit()
                    print("Created opening", db_opening)

                for row in ws.get_all_records(
                    head=2, expected_headers=expected_headers
                ):
                    sheet_opening = SheetOpening(**row)
                    pseudo = str(sheet_opening.pseudo or "").strip()
                    if not pseudo:
                        continue

                    db_visitor = s.query(Visitor).filter_by(nick=pseudo).first()

                    if not db_visitor:
                        print("Skipping unknown visitor with nick", pseudo)
                        continue

                    existing_visit = (
                        s.query(Visit)
                        .filter_by(visitor_id=db_visitor.id, opening_id=db_opening.id)
                        .first()
                    )

                    if existing_visit:
                        print(
                            f"Skipping existing visit for {pseudo} at opening {db_opening}"
                        )
                        continue

                    entry_dt = parse_time(
                        opening_date,
                        str(sheet_opening.arrival)
                        if sheet_opening.arrival is not None
                        else None,
                    )
                    exit_dt = parse_time(
                        opening_date,
                        str(sheet_opening.departure)
                        if sheet_opening.departure is not None
                        else None,
                    )

                    visit = Visit(
                        visitor=db_visitor,
                        opening=db_opening,
                        entry=entry_dt,
                        exit=exit_dt,
                    )
                    s.add(visit)

                s.commit()

    known_spreadsheets = [
        "2024-06",
        "2024-07",
        "2024-08",
        "2024-09",
        "2024-10",
        "2024-11",
        "2024-12",
        "2025-01",
        "2025-02",
        "2025-03",
        "2025-04",
        "2025-05",
        "2025-06",
        "2025-07",
        "2025-08",
        "Entrées Octobre 2025",
        "Entrées Septembre 2025",
    ]
    for sheet_name in known_spreadsheets:
        try:
            process_spreadsheet(sheet_name)
        except Exception as e:
            print(f"Error processing {sheet_name}: {e}")
