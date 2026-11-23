from datetime import datetime

from gogol_cli.events.docx_parser import (
    _extract_date_times,
    _parse_event_block,
    split_event_blocks,
)

LAST_DATE_IDX = 2
FIRST_EVENT_DATES = 3

MULTI = [
    "<b>Концерт один</b>",
    "5 декабря в 15:30 и 18:30",
    "6 декабря в 15:30",
    "Описание первого концерта, достаточно длинное, чтобы не быть датой.",
    "<b>Стоимость билета: от 650 руб.</b>",
    "https://example.com/one",
    "<b>Концерт два</b>",
    "<b>7</b><b> </b><b>декабря</b><b> в 17:00</b>",
    "Описание второго концерта.",
    "Стоимость билета: 550 руб.",
    "https://example.com/two",
]


def test_split_event_blocks_finds_each_event() -> None:
    blocks = split_event_blocks(MULTI)
    assert [b[0] for b in blocks] == ["<b>Концерт один</b>", "<b>Концерт два</b>"]
    assert sum(len(b) for b in blocks) == len(MULTI)


def test_single_event_is_one_block() -> None:
    single = ["Название", "27 мая в 19:00", "Описание"]
    assert split_event_blocks(single) == [single]


def test_extract_date_times_collects_all_date_lines() -> None:
    date_times, last_idx = _extract_date_times(MULTI[:4])
    assert [(d.month, d.day, d.hour, d.minute) for d in date_times] == [
        (12, 5, 15, 30),
        (12, 5, 18, 30),
        (12, 6, 15, 30),
    ]
    assert last_idx == LAST_DATE_IDX


def test_parse_event_block_fields() -> None:
    first, second = (_parse_event_block(b, []) for b in split_event_blocks(MULTI))
    assert (first.name, first.price, first.purchase_link) == (
        "Концерт один",
        "650",
        "https://example.com/one",
    )
    assert len(first.date_times) == FIRST_EVENT_DATES
    assert (second.name, second.price, second.purchase_link) == (
        "Концерт два",
        "550",
        "https://example.com/two",
    )
    assert [d.replace(year=2000) for d in second.date_times] == [datetime(2000, 12, 7, 17, 0)]
    assert "Описание второго" in second.description
    assert "Описание первого" not in second.description
