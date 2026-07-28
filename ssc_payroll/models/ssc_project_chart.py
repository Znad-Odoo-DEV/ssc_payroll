# -*- coding: utf-8 -*-
"""Horizontal bar chart rendered for a set of project distribution lines.

Odoo cannot embed a graph view inside a form, so the distribution is drawn as
a self-contained HTML field instead. Kept in one place because the payslip and
the salary batch show the same picture at different scales.
"""
from markupsafe import Markup, escape

# Distinct enough to stay readable next to each other, and readable on both
# the light and the dark backgrounds Odoo uses for form sheets.
PALETTE = [
    '#714B67', '#017E84', '#F06050', '#6EC1A7', '#F4A261',
    '#5B8FF9', '#B37FEB', '#E8684A', '#5AD8A6', '#945FB9',
]

_STYLE_WRAP = (
    "display:flex;flex-direction:column;gap:.65rem;"
    "padding:1rem 0 .25rem 0;max-width:900px;"
)
_STYLE_ROW = "display:flex;align-items:center;gap:.75rem;font-size:.875rem;"
_STYLE_LABEL = (
    "flex:0 0 30%;text-align:right;overflow:hidden;text-overflow:ellipsis;"
    "white-space:nowrap;font-weight:500;"
)
_STYLE_TRACK = (
    "flex:1 1 auto;background:rgba(128,128,128,.15);border-radius:.25rem;"
    "height:1.35rem;position:relative;overflow:hidden;"
)
_STYLE_VALUE = "flex:0 0 26%;text-align:left;white-space:nowrap;font-variant-numeric:tabular-nums;"


def render_project_chart(lines, label_of, amount_of, share_of, currency):
    """Return a bar chart for ``lines``, widest first.

    ``label_of`` / ``amount_of`` / ``share_of`` read one line each, so the
    caller decides what a "line" is and what it is measured by.
    """
    rows = sorted(
        ((label_of(l), amount_of(l), share_of(l)) for l in lines),
        key=lambda r: r[1], reverse=True,
    )
    rows = [r for r in rows if r[1]]
    if not rows:
        return False

    widest = max(r[1] for r in rows)
    symbol = escape(currency.symbol or '') if currency else ''
    parts = [Markup('<div style="%s">') % Markup(_STYLE_WRAP)]

    for index, (label, amount, share) in enumerate(rows):
        # Scale to the widest bar, not to 100%, so small shares stay visible.
        width = 100.0 * amount / widest if widest else 0.0
        colour = PALETTE[index % len(PALETTE)]
        parts.append(Markup(
            '<div style="{row}">'
            '<div style="{label_style}" title="{label}">{label}</div>'
            '<div style="{track}">'
            '<div style="width:{width:.2f}%;height:100%;background:{colour};'
            'border-radius:.25rem;"></div>'
            '</div>'
            '<div style="{value}"><b>{share:.1f}%</b> &nbsp;{symbol}{amount:,.2f}</div>'
            '</div>'
        ).format(
            row=Markup(_STYLE_ROW), label_style=Markup(_STYLE_LABEL),
            track=Markup(_STYLE_TRACK), value=Markup(_STYLE_VALUE),
            label=label, width=width, colour=colour,
            share=share, symbol=symbol, amount=amount,
        ))

    total = sum(r[1] for r in rows)
    parts.append(Markup(
        '<div style="{row}border-top:1px solid rgba(128,128,128,.3);'
        'margin-top:.35rem;padding-top:.5rem;">'
        '<div style="{label_style}">Total</div>'
        '<div style="{track}background:none;"></div>'
        '<div style="{value}"><b>100.0%</b> &nbsp;{symbol}{total:,.2f}</div>'
        '</div>'
    ).format(
        row=Markup(_STYLE_ROW), label_style=Markup(_STYLE_LABEL),
        track=Markup(_STYLE_TRACK), value=Markup(_STYLE_VALUE),
        symbol=symbol, total=total,
    ))
    parts.append(Markup('</div>'))
    return Markup('').join(parts)
