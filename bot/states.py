from aiogram.fsm.state import State, StatesGroup


class AdminInput(StatesGroup):
    waiting = State()
