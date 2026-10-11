import pytest

from magscan.metadata import MagazineInfo, published_date, render, render_relative_path, template_fields


def values(**kw):
    return template_fields(MagazineInfo(**kw), "orig")


def test_optional_sections_drop_when_empty():
    t = "{magazine} - {year}-{month:02}[ - Issue {issue}]"
    assert render_relative_path(t, values(magazine="Wired", year=2024, month=3)) == "Wired - 2024-03"
    assert render_relative_path(t, values(magazine="Wired", year=2024, month=3, issue="7")) == "Wired - 2024-03 - Issue 7"


def test_required_field_missing_renders_empty():
    assert render("{magazine} {month_name} {year}", values(magazine="Wired", year=2024)) == "Wired  2024"


def test_values_cannot_create_folders_but_template_can():
    t = "{magazine}/{year}/{magazine} {date}"
    assert render_relative_path(t, values(magazine="AC/DC Fan: Mag?", year=2023, month=11)) == \
        "AC-DC Fan Mag/2023/AC-DC Fan Mag 2023-11"


def test_literal_brackets_and_unknown_field():
    assert render("[[{year}]]", values(year=2020)) == "[2020]"
    with pytest.raises(KeyError):
        render("{nope}", values())


def test_published_date():
    assert published_date(MagazineInfo(year=2024, month=2, day=31)) == "2024-02-29"
    assert published_date(MagazineInfo(year=2024, season="Autumn")) == "2024-09-01"
    assert published_date(MagazineInfo()) is None
