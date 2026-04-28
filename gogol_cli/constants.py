"""Application-wide constants."""

# --- Date / time formats ---------------------------------------------------------

DATE_FORMAT = "%Y-%m-%d"  # ISO date: CLI input parsing and DB range queries
EVENT_DATE_DISPLAY_FORMAT = "%d.%m.%Y"  # Russian display format stored in event properties
DATETIME_FORMAT = "%Y-%m-%d %H:%M:%S"

# --- Shared defaults -------------------------------------------------------------

DEFAULT_USER_ID = 1

# --- Pin iblock ------------------------------------------------------------------

PIN_IBLOCK_ID = 38
PIN_IBLOCK_SECTION_ID = None

PIN_DEFAULT_SORT = 50

PIN_LINK_PROPERTY_ID = 150
PIN_BUTTON_TEXT_PROPERTY_ID = 149
PIN_NAME_PROPERTY_ID = 148

# --- Event iblock ----------------------------------------------------------------

EVENT_IBLOCK_ID = 6
EVENT_IBLOCK_SECTION_ID = 7
EVENT_IBLOCK_TYPE_ID = "recital"
EVENT_IBLOCK_CODE = "recital"
EVENT_IBLOCK_EXTERNAL_ID = "6"

EVENT_DEFAULT_SORT = 500

EVENT_TIME_PROPERTY_ID = 14
EVENT_DATE_PROPERTY_ID = 15
EVENT_PRICE_PROPERTY_ID = 129

# --- Chronograph iblock ----------------------------------------------------------

CHRONOGRAPH_IBLOCK_ID = 8

CHRONOGRAPH_DEFAULT_SORT = 500
CHRONOGRAPH_YEAR_PROPERTY_ID = 23
CHRONOGRAPH_YEAR_OFFSET = 5  # years between source and destination sections

# --- Exhibition iblock -----------------------------------------------------------

EXHIBITION_IBLOCK_ID = 14
EXHIBITION_DEFAULT_SORT = 500
EXHIBITION_SECTION_PROPERTY_ID = 47
EXHIBITION_DATE_PROPERTY_ID = 211

# --- Book iblock -----------------------------------------------------------------

BOOK_IBLOCK_ID = 9
BOOK_DEFAULT_SORT = 500

BOOK_FULL_BIB_PROPERTY_ID = 30
BOOK_AUTHOR_PROPERTY_ID = 31
BOOK_CITY_PROPERTY_ID = 57
BOOK_PUBLISHER_PROPERTY_ID = 58
BOOK_YEAR_PROPERTY_ID = 59

# --- Virtual exhibition iblock ---------------------------------------------------

VIRTUAL_EXHIBITION_IBLOCK_ID = 5
VIRTUAL_EXHIBITION_IBLOCK_SECTION_ID = 283
VIRTUAL_EXHIBITION_DEFAULT_SORT = 500

# Properties on the exhibition element
VIRTUAL_EXHIBITION_PROP_TYPE_ID = 9  # iblock type (enum list)
VIRTUAL_EXHIBITION_PROP_TYPE_VALUE = 61  # value_enum for prop 9
VIRTUAL_EXHIBITION_PROP_ACTIVE_FROM_ID = 54
VIRTUAL_EXHIBITION_PROP_ACTIVE_TO_ID = 55
VIRTUAL_EXHIBITION_PROP_SORT_ID = 66
VIRTUAL_EXHIBITION_PROP_SUBTITLE_ID = 196  # short sub-header text

# Per-item property IDs (linked by scp_XXXX description key)
VIRTUAL_EXHIBITION_PROP_ITEM_NAME_ID = 197
VIRTUAL_EXHIBITION_PROP_ITEM_BIB_ID = 198
VIRTUAL_EXHIBITION_PROP_ITEM_DESC_ID = 199
VIRTUAL_EXHIBITION_PROP_ITEM_IMAGE_ID = 200
VIRTUAL_EXHIBITION_PROP_ITEM_LINK_ID = 205  # PHP-serialised linking array

# Category / section link property
VIRTUAL_EXHIBITION_PROP_CATEGORY_ID = 213
VIRTUAL_EXHIBITION_PROP_CATEGORY_VALUE = 120  # value_enum for prop 213

# Image resizing
VIRTUAL_EXHIBITION_MAX_IMAGE_DIM = 1280

# --- Calendar --------------------------------------------------------------------

DECEMBER = 12

MONTH_NAMES: dict[int, str] = {
    1: "Январь",
    2: "Февраль",
    3: "Март",
    4: "Апрель",
    5: "Май",
    6: "Июнь",
    7: "Июль",
    8: "Август",
    9: "Сентябрь",
    10: "Октябрь",
    11: "Ноябрь",
    12: "Декабрь",
}
