"""Formatting helpers for text injected into Slides.

Extracted from `slides_client.py` (split by operation family) — content
unchanged, and still re-exported by `slides_client.py`, which remains the
connector's import path.
"""


def parse_bold_markdown(text):
    """Parse **bold** segments → (clean_text, [(start, end), ...]).

    Useful when injecting markdown-flavoured text into Slides placeholders :
    the Slides API does not understand markdown, so we extract the ranges and
    apply `updateTextStyle bold:True` to them.
    """
    clean, bolds = [], []
    i, pos = 0, 0
    while i < len(text):
        if text[i:i + 2] == '**':
            j = text.find('**', i + 2)
            if j == -1:
                clean.append(text[i:])
                pos += len(text) - i
                break
            seg = text[i + 2:j]
            start = pos
            clean.append(seg)
            pos += len(seg)
            bolds.append((start, pos))
            i = j + 2
        else:
            clean.append(text[i])
            pos += 1
            i += 1
    return ''.join(clean), bolds


def _hex_to_rgb(hex_color):
    """
    Convert hex color to Google Slides RGB format (0.0-1.0)

    Args:
        hex_color: Hex color string like '#RRGGBB' or 'RRGGBB'

    Returns:
        dict: {'red': float, 'green': float, 'blue': float}
    """
    hex_color = hex_color.lstrip('#')
    r = int(hex_color[0:2], 16) / 255.0
    g = int(hex_color[2:4], 16) / 255.0
    b = int(hex_color[4:6], 16) / 255.0
    return {'red': r, 'green': g, 'blue': b}
