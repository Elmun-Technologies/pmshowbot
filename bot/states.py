"""FSM states for the registration form (tenant-aware, 2-level directions)."""
from aiogram.fsm.state import State, StatesGroup


class Registration(StatesGroup):
    language = State()
    country = State()
    country_other = State()
    plate = State()
    direction = State()
    sub_direction = State()  # podnapravleniye selection when parent has children
    photos = State()
    mods = State()
    phone = State()
