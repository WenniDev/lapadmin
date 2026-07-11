from typing import TYPE_CHECKING

from . import Column, Id, Table, column, relation

if TYPE_CHECKING:
    from . import Payment, Visit


class Visitor(Table, Id):
    first_name: Column[str] = column(nullable=True)
    last_name: Column[str] = column(nullable=True)
    nick: Column[str] = column(nullable=True)
    email: Column[str] = column(nullable=True)

    visits: Column[list["Visit"]] = relation(
        "Visit", back_populates="visitor", cascade="all, delete-orphan"
    )
    payments: Column[list["Payment"]] = relation(
        "Payment", back_populates="visitor"
    )

    @property
    def full_name(self):
        if not self.first_name:
            return self.last_name
        if not self.last_name:
            return self.first_name
        return f"{self.first_name} {self.last_name}"

    @property
    def input(self):
        return f"{self} (#{self.id})"

    @property
    def is_incomplete(self):
        return not bool(self.first_name or self.last_name or self.email)

    def __gt__(self, other):
        def key(visitor):
            return (visitor.nick or visitor.full_name or visitor.email).lower()

        return key(self) > key(other)

    def __str__(self):
        name = self.full_name or self.email.split("@")[0]
        if self.nick:
            if not name:
                return self.nick
            name += f' "{self.nick}"'
        return name or "Empty"


def get_input_list():
    from app import app

    with app.session() as s:
        return [
            v.input
            for v in sorted(s.query(Visitor), key=lambda x: str(x).lower())
        ]
