from datetime import datetime

import flask
from flask_wtf import FlaskForm
from wtforms import BooleanField, SelectField, StringField
from wtforms.validators import DataRequired

from app import app, private
from app.db import Opening, visitor


def get_translated_scopes() -> dict[str, str]:
    TR = {
        Opening.Scope.PUBLIC: "Publique",
        Opening.Scope.PRIVATE: "Privée",
    }
    return {scope.name: TR[scope] for scope in Opening.Scope}


class CreateOpeningForm(FlaskForm):
    start_date = StringField(validators=[DataRequired()], label="Date de début")
    start_time = StringField(
        validators=[DataRequired()], label="Heure de début"
    )
    end_date = StringField(validators=[DataRequired()], label="Date de fin")
    end_time = StringField(validators=[DataRequired()], label="Heure de fin")
    scope = SelectField(
        validators=[DataRequired()],
        label="Type d'ouverture",
        choices=[
            (scope, label) for scope, label in get_translated_scopes().items()
        ],
    )
    auto_start_streams = BooleanField(
        label="Lancer les streams automatiquement"
    )


@private.route("/calendar/<month>/<day>/")
def calendar_day(month, day):
    date = f"{month}-{day}"
    form = CreateOpeningForm()
    form.start_date.data = date
    if form.validate_on_submit():
        datetime_format = "%Y-%m-%d %H:%M"
        start_date = datetime.strptime(
            f"{form.start_date.data} {form.start_time.data}", datetime_format
        )
        end_date = datetime.strptime(
            f"{form.end_date.data} {form.end_time.data}", datetime_format
        )
        with app.session() as s:
            opening = Opening(
                start=start_date,
                end=end_date,
                scope=form.scope.data,
                auto_start_streams=form.auto_start_streams.data,
            )
            s.add(opening)
            s.commit()
            flask.flash(f"L'ouverture du {start_date} a été crée.")

    with app.session() as s:
        openings = s.query(Opening).filter(
            ((Opening.start >= date) & (Opening.start <= f"{date} 23:59:59"))
            | ((Opening.end >= date) & (Opening.end <= f"{date} 23:59:59")),
        )

    def get_day_info(current: datetime, compare: datetime) -> str:
        diff = (compare.date() - current.date()).days
        return f"+{diff}" if diff > 0 else f"{diff}" if diff < 0 else ""

    date_time = datetime.fromisoformat(f"{date}T00:00:00")
    openings = [
        (
            opening,
            get_day_info(date_time, opening.start),
            get_day_info(date_time, opening.end),
        )
        for opening in openings
    ]

    form.end_date.data = form.end_date.data or date

    return app.render(
        "day",
        form=form,
        openings=openings,
        date=date,
        visitors=visitor.get_input_list(),
    )
