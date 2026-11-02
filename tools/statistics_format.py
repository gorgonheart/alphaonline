"""Statistics presentation only; never round values before persistence."""
from datetime import datetime
from decimal import Decimal
from math import isfinite, floor, log10


def format_number(value, *, signed=False, significant=3):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return '未知'
    if not isfinite(number):
        return '未知'
    if number == 0:
        return '0'
    rounded = Decimal(format(number, f'.{significant}g'))
    text = format(rounded.quantize(Decimal(1).scaleb(rounded.adjusted()-significant+1)), 'f')
    return ('+' if signed and number > 0 else '') + text


def format_amount(value, *, signed=False):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return '未知'
    if not isfinite(number):
        return '未知'
    text = format(number, '+.0f' if signed else '.0f')
    return '0' if text in ('-0', '+0') else text


def _price_number(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if isfinite(number) else None


def _price_decimals(number):
    return 2 if not number or abs(number) >= 1 else 3-floor(log10(abs(number)))


def _price_text(number, decimals):
    if number is None:
        return '未知'
    if number == 0:
        return '0'
    if decimals > 12:
        # Bound table width for exceptionally tiny prices; never turn them into zero.
        significant = max(3, decimals+floor(log10(abs(number))))
        return format(number, f'.{significant}e')
    return format(number, f'.{decimals}f')


def format_price(value):
    number = _price_number(value)
    return _price_text(number, _price_decimals(number))


def _price_pair(left, right):
    numbers = (_price_number(left), _price_number(right))
    decimals = max(_price_decimals(n) for n in numbers)
    result = tuple(_price_text(n, decimals) for n in numbers)
    if all(n is not None for n in numbers) and numbers[0] != numbers[1]:
        # At most two refinements; do not chase arbitrary floating point noise.
        for _ in range(2):
            if result[0] != result[1]:
                break
            decimals += 1
            result = tuple(_price_text(n, decimals) for n in numbers)
    return result


def format_percent(ratio):
    try:
        text = format_number(float(ratio)*100)
    except (TypeError, ValueError):
        return '未知'
    return text if text == '未知' else text+'%'


def format_time(value):
    try:
        if isinstance(value, str):
            value = datetime.fromisoformat(value)
        return value.strftime('%m-%d %H:%M:%S')
    except (AttributeError, TypeError, ValueError):
        return '未知'


def format_weights(row, names):
    return '\n'.join(f'{names.get(str(key), str(key))}: {format_percent(value)}'
                     for key, value in row.items())


def format_frame(frame):
    display = frame.copy()
    amounts = {'pos_u', 'market_value', 'pnl_u',
               'historical_pos_u', 'current_pos_u', 'position_pnl', 'total_pnl', 'trade_pnl'}
    for column in display:
        if column in amounts:
            display[column] = display[column].map(format_amount)
        elif getattr(display[column].dtype, 'kind', '') == 'M' or column in {'time', 'timestamp', 'observed_at', 'candle_begin_time'}:
            display[column] = display[column].map(format_time)
    for pair in (('avg_price', 'cur_price'), ('historical_price', 'current_price')):
        columns = [column for column in pair if column in frame]
        if len(columns) == 2:
            # Work on arrays rather than constructing a pandas Series for every row.
            values = [_price_pair(left, right) for left, right in zip(frame[columns[0]].array, frame[columns[1]].array)]
            display[columns[0]] = [value[0] for value in values]
            display[columns[1]] = [value[1] for value in values]
        elif columns:
            display[columns[0]] = frame[columns[0]].map(format_price)
    return display
