"""User ORM model for authentication and identity."""

import enum

from sqlalchemy import Boolean, String
from sqlalchemy import Enum as SQLEnum
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import BaseModel


class Persona(str, enum.Enum):
    """Who the user is, which decides the add-ons shown by default.

    Visibility only: every endpoint accepts every persona.
    """

    STUDENT = "student"
    RESEARCHER = "researcher"
    BUILDER = "builder"


#: One native Postgres type shared by `users.persona` and
#: `projects.persona_override`, so the two can never drift apart.
PERSONA_SQL_ENUM = SQLEnum(
    Persona,
    name="persona_enum",
    native_enum=True,
    values_callable=lambda enum_cls: [member.value for member in enum_cls],
)


class User(BaseModel):
    """A registered platform user."""

    __tablename__ = "users"

    email: Mapped[str] = mapped_column(
        String(255), unique=True, index=True, nullable=False
    )
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    full_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    persona: Mapped[Persona] = mapped_column(
        PERSONA_SQL_ENUM,
        nullable=False,
        default=Persona.RESEARCHER,
        server_default=Persona.RESEARCHER.value,
    )
