"""
Mask parser for structured meter data import.

Mask tokens:
  DD, MM, YYYY      – date (day, month, year)
  HH, mm, ss        – time (hour, minute, second)
  E...E             – main meter reading value (numeric)
  A...A, B...B, ... – custom numeric field values (any uppercase except D/M/Y/H and text letters)
  T S R N X P Q    – free text capture (string, not numeric)
                      In the middle of a mask: captures until next literal delimiter
                      At the end of a mask:    captures rest of line (optional)

Literal characters (., :, ;, /, space, etc.) must appear as-is in the data.

Examples:
  mask → "DD.MM.YYYY;HH:mm;AAA,AAAA;EEEEE,EEEE;SSSSSSS;RRRRRR;"
  data → "22.07.2025;02:15;0,0544;7,4771;Gemessen;ET;"
  → S="Gemessen", R="ET"

  mask → "DD.MM.YYYY HH:mm EEEEE,EEE - TTTTTTT"
  data → "02.01.2026 11:52 5835,679 - Keller"
  → T="Keller" (optional)
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
# Uppercase letters reserved for date/time tokens
_DT_UPPER = frozenset('DYMH')
# Uppercase letters that capture free text (not numeric)
_TEXT_UPPER = frozenset('TSRNXPQ')


def _tokenize(mask):
    """
    Yield (token_type, name_or_char, metadata) for each segment of the mask.
      ('dt',      'year'|'month'|..., seq_str)
      ('value',   letter,             has_decimal_sep: bool)
      ('text',    letter,             None)
      ('literal', char,               None)
    """
    i, n = 0, len(mask)
    while i < n:
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
        if c.isupper() and c not in _DT_UPPER:
            letter = c
            j = i
            while j < n and mask[j] == letter:
                j += 1

            if letter in _TEXT_UPPER:
                yield 'text', letter, None
                i = j
            else:
                # Numeric: may contain internal decimal separator
                has_decimal = False
                while j < n:
                    if mask[j] == letter:
                        j += 1
                    elif not mask[j].isalpha():
                        k = j
                        while k < n and not mask[k].isalpha():
                            k += 1
                        if k < n and mask[k] == letter:
                            has_decimal = True
                            j = k
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
    """Return ordered list of unique value/text letters found in the mask."""
    seen, result = set(), []
    for ttype, val, _ in _tokenize(mask):
        if ttype in ('value', 'text') and val not in seen:
            seen.add(val)
            result.append(val)
    return result


def mask_to_regex(mask):
    """
    Convert a mask string to (compiled_pattern, groups_list).

    Text tokens in the middle use a negated-char-class based on the next literal
    so they stop at the right delimiter (e.g. [^;]* before a semicolon).
    The trailing text token + its preceding literals are wrapped in (?:...)?
    making them optional.
    """
    tokens = list(_tokenize(mask))
    dt_regex = {name: rx for _, name, rx in _DT_TOKENS}

    # Detect optional trailing suffix: last text token + any literals before it
    suffix_start = None
    for i in range(len(tokens) - 1, -1, -1):
        ttype = tokens[i][0]
        if ttype == 'text':
            suffix_start = i
            j = i - 1
            while j >= 0 and tokens[j][0] == 'literal':
                suffix_start = j
                j -= 1
            break
        elif ttype not in ('literal',):
            break

    def text_pattern(idx):
        """Regex for a text token: negated-class if next token is a literal, else .*"""
        for j in range(idx + 1, len(tokens)):
            ntype, nval, _ = tokens[j]
            if ntype == 'literal':
                return f'([^{re.escape(nval)}]*)'
            if ntype in ('dt', 'value', 'text'):
                break
        return r'(.*)'

    def token_to_pattern(idx, ttype, val, extra):
        if ttype == 'dt':
            return dt_regex[val]
        if ttype == 'value':
            return r'(\d+(?:[,\.]\d+)*)' if extra else r'(\d+(?:[,\.]\d+)?)'
        if ttype == 'text':
            return text_pattern(idx)
        return re.escape(val)  # literal

    pattern = ''
    suffix  = ''
    groups  = []

    for idx, (ttype, val, extra) in enumerate(tokens):
        part = token_to_pattern(idx, ttype, val, extra)
        if ttype in ('dt', 'value', 'text'):
            groups.append(val)
        if suffix_start is not None and idx >= suffix_start:
            suffix += part
        else:
            pattern += part

    if suffix:
        pattern += f'(?:{suffix})?'

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
        return float(s.replace('.', '').replace(',', '.'))
    return float(s)


def parse_line(line, mask):
    """
    Parse one data line using the mask.
    Returns (result_dict, error_str).
    result_dict contains 'read_at' (datetime) + one key per value/text letter.
    Text letters are kept as strings; value letters are converted to float.
    """
    try:
        regex, groups = mask_to_regex(mask)
    except Exception as e:
        return None, f'Ungültige Maske: {e}'

    m = regex.match(line.strip())
    if not m:
        return None, 'Zeile stimmt nicht mit der Maske überein'

    raw = dict(zip(groups, m.groups()))
    text_letters = {val for ttype, val, _ in _tokenize(mask) if ttype == 'text'}

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

    for key, val in raw.items():
        if key in ('year', 'month', 'day', 'hour', 'minute', 'second'):
            continue
        if val is None:
            continue
        if key in text_letters:
            result[key] = val.strip()
        else:
            try:
                result[key] = normalize_number(val)
            except ValueError:
                return None, f'Ungültiger Zahlenwert für {key!r}: {val!r}'

    return result, None
