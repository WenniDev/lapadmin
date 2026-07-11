import datetime
import enum
from enum import StrEnum
from typing import TYPE_CHECKING

from . import Column, Id, Table, column, relation

if TYPE_CHECKING:
    from . import Visit


class Opening(Table, Id):
    class Scope(StrEnum):
        PUBLIC = enum.auto()
        PRIVATE = enum.auto()

        def __str__(self):
            return self.name.upper()

    start: Column[datetime.datetime]
    end: Column[datetime.datetime]
    scope: Column[Scope] = column(default=Scope.PUBLIC)
    auto_start_streams: Column[bool] = column(default=False)
    streams_started_at: Column[datetime.datetime | None] = column(
        nullable=True, default=None
    )

    def __str__(self):
        hour_start = self.start.strftime("%H:%M")
        hour_end = self.end.strftime("%H:%M")
        return f"#{self.id} {self.month}-{self.day} {hour_start} - {hour_end}"

    @property
    def month(self):
        return self.start.strftime("%Y-%m")

    @property
    def day(self):
        return self.start.strftime("%d")

    visits: Column[list["Visit"]] = relation("Visit", back_populates="opening")
