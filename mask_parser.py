"""
Mask parser for structured meter data import.

Mask tokens:
  DD, MM, YYYY   – date (day, month, year)
  HH, mm, ss     – time (hour, minute, second)
  E...E          – main meter reading value (any sequence of E's + decimal separator)
  A...A, B...B   – custom field values (any uppercase letter except D/M/Y/H)

Literal characters (., :, /, space, etc.) must appear as-is in the data.

Example:
  mask  → "DD.MM.YYYY HH:mm EEEE,EEE AAA,AA/BBB,BB/CCC,CC"
  data  → "02.01.2026 11:52 5835,679 37,1/42,1/42,1"
"""
import re
from datetime import datetime

# Date/time token definitions (longest first to prevent partial matches)
_DT_TOKENS = [
    ('YYYY', 'year',   r'(\d{4})'),
    ('DD',   'day',    r'(\d{2})'),
    ('MM',   'month',  r'(\d{2})'),
    ('HH',   'hour',   r'(\d{2})'),
    ('mm',   'minute', r'(\d{2})'),
    ('ss',   'second', r'(\d{2})'),
]
# Uppercase letters that belong to date/time tokens – cannot be value letters
_DT_UPPER = frozenset('DYMH')


def _tokenize(mask):
    """
    Yield (token_type, name_or_char, metadata) for each segment of the mask.
      ('dt',      'year'|'month'|..., seq_str)
      ('value',   letter,             has_decimal_sep: bool)
      ('literal', char,               None)
    """
    i, n = 0, len(mask)
    while i < n:
        # Try date/time tokens first (longest first)
        matched = False
        for seq, name, _ in _DT_TOKENS:
            end = i + len(seq)
            if mask[i:end] == seq:
                matched = True
                yield 'dt', name, seq
                i = end
                break
        if matched:
            continue

        c = mask[i]
        # Value letter: uppercase, not reserved for date/time
        if c.isupper() and c not in _DT_UPPER:
            letter = c
            j = i
            has_decimal = False
            # Consume the full block: same letter, possibly with internal separators
            while j < n:
                if mask[j] == letter:
                    j += 1
                elif not mask[j].isalpha():
                    # Peek ahead: if same letter follows, this is a decimal separator
                    k = j
                    while k < n and not mask[k].isalpha():
                        k += 1
                    if k < n and mask[k] == letter:
                        has_decimal = True
                        j = k  # jump to next letter group (separator included in regex)
                    else:
                        break
                else:
                    break
            yield 'value', letter, has_decimal
            i = j
        else:
            yield 'literal', c, None
            i += 1


def extract_value_letters(mask):
    """Return ordered list of unique value letters found in the mask."""
    seen, result = set(), []
    for ttype, val, _ in _tokenize(mask):
        if ttype == 'value' and val not in seen:
            seen.add(val)
            result.append(val)
    return result


def mask_to_regex(mask):
    """
    Convert a mask string to (compiled_pattern, groups_list).
    groups_list is in capture-group order: e.g. ['day','month','year','hour','minute','E','A','B','C']
    """
    pattern = ''
    groups = []
    dt_regex = {name: rx for _, name, rx in _DT_TOKENS}

    for ttype, val, extra in _tokenize(mask):
        if ttype == 'dt':
            pattern += dt_regex[val]
            groups.append(val)
        elif ttype == 'value':
            if extra:  # has decimal separator in block
                pattern += r'(\d+(?:[,\.]\d+)+)'
            else:
                pattern += r'(\d+(?:[,\.]\d+)?)'
            groups.append(val)
        else:  # literal
            pattern += re.escape(val)

    return re.compile(r'^\s*' + pattern + r'\s*$'), groups


def normalize_number(s):
    """
    Convert locale-formatted number strings to float.
      '5835,679'  → 5835.679
      '1.234,56'  → 1234.56
      '5835.679'  → 5835.679
    """
    s = str(s).strip()
    if ',' in s and '.' not in s:
        return float(s.replace(',', '.'))
    if '.' in s and ',' in s:
        # Thousands dot + decimal comma (e.g. German format)
        return float(s.replace('.', '').replace(',', '.'))
    return float(s)


def parse_line(line, mask):
    """
    Parse one data line using the mask.
    Returns (result_dict, error_str).
    On success: result_dict contains 'read_at' (datetime) + one key per value letter.
    On failure: result_dict is None, error_str describes the problem.
    """
    try:
        regex, groups = mask_to_regex(mask)
    except Exception as e:
        return None, f'Ungültige Maske: {e}'

    m = regex.match(line.strip())
    if not m:
        return None, 'Zeile stimmt nicht mit der Maske überein'

    raw = dict(zip(groups, m.groups()))

    # Build datetime
    try:
        dt = datetime(
            year   = int(raw.get('year',   2000)),
            month  = int(raw.get('month',  1)),
            day    = int(raw.get('day',    1)),
            hour   = int(raw.get('hour',   0)),
            minute = int(raw.get('minute', 0)),
            second = int(raw.get('second', 0)),
        )
    except ValueError as e:
        return None, f'Ungültiges Datum/Zeit: {e}'

    result = {'read_at': dt}

    # Parse numeric values
    for key, val in raw.items():
        if key in ('year', 'month', 'day', 'hour', 'minute', 'second'):
            continue
        try:
            result[key] = normalize_number(val)
        except ValueError:
            return None, f'Ungültiger Zahlenwert für {key!r}: {val!r}'

    return result, None
