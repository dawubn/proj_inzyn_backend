"""Message sent from the contact form.

The text ends up in an email read by the team, so every field is cleaned and limited:
single-line fields cannot carry line breaks into email headers, invisible characters that
could hide or reorder text are removed and the amount of text and links is capped.
"""

import re
import unicodedata

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

NAME_MAX_LENGTH = 100
SUBJECT_MAX_LENGTH = 150
MESSAGE_MIN_LENGTH = 10
MESSAGE_MAX_LENGTH = 5000
MAX_LINKS = 3

_LINK = re.compile(r"https?://|www\.", re.IGNORECASE)
_MANY_BLANK_LINES = re.compile(r"\n{3,}")
_SPACES = re.compile(r"[ \t]+")


# Control characters, invisible formatting (right-to-left override, zero width space)
# and Unicode line separators
_HIDDEN_CATEGORIES = ("Cc", "Cf", "Zl", "Zp")


def _without_hidden_characters(value: str) -> str:
    """Line breaks and tabs stay; other control and invisible characters are removed."""
    value = value.replace("\r\n", "\n").replace("\r", "\n")
    return "".join(
        char
        for char in value
        if char in "\n\t" or unicodedata.category(char) not in _HIDDEN_CATEGORIES
    )


def _single_line(value: str) -> str:
    # A line break here could add a header to the email
    if "\n" in value or "\r" in value:
        raise ValueError("must be a single line")
    return _SPACES.sub(" ", _without_hidden_characters(value)).strip()


class ContactRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: EmailStr = Field(max_length=254)
    name: str = Field(default="", max_length=NAME_MAX_LENGTH)
    subject: str = Field(default="", max_length=SUBJECT_MAX_LENGTH)
    message: str = Field(max_length=MESSAGE_MAX_LENGTH)
    # Hidden in the form; people leave it empty, bots that fill every field do not
    website: str = Field(default="", max_length=200)

    @field_validator("name", "subject")
    @classmethod
    def _clean_single_line(cls, value: str) -> str:
        return _single_line(value)

    @field_validator("message")
    @classmethod
    def _clean_message(cls, value: str) -> str:
        text = _without_hidden_characters(value)
        text = "\n".join(line.rstrip() for line in text.split("\n"))
        text = _MANY_BLANK_LINES.sub("\n\n", text).strip()
        if len(text) < MESSAGE_MIN_LENGTH:
            raise ValueError(f"must be at least {MESSAGE_MIN_LENGTH} characters long")
        if len(_LINK.findall(text)) > MAX_LINKS:
            raise ValueError(f"may contain at most {MAX_LINKS} links")
        return text

    @property
    def is_bot(self) -> bool:
        return bool(self.website.strip())
