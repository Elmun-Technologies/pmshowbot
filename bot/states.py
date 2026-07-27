"""FSM states for the registration form."""
from aiogram.fsm.state import State, StatesGroup


class Registration(StatesGroup):
    language = State()
    country = State()
    country_other = State()
    plate = State()
    direction = State()
    photos = State()
    phone = State()
