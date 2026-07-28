# -*- coding: utf-8 -*-
"""Shared helpers for the SSC Payroll module."""

MONTH_ABBR = {
    1: 'JAN', 2: 'FEB', 3: 'MAR', 4: 'APR', 5: 'MAY', 6: 'JUN',
    7: 'JUL', 8: 'AUG', 9: 'SEP', 10: 'OCT', 11: 'NOV', 12: 'DEC',
}

# ---------------------------------------------------------------------------
# TEMPORARY Studio attendance bridge
# ---------------------------------------------------------------------------
# Until the biometric ``ssc_attendance`` module becomes the attendance source,
# labour attendance is read from the Studio model ``x_attendance_per_emplo``,
# whose daily lines live in one monthly field per (year, month). The field
# names are inconsistent, so they are mapped explicitly here. Add new years as
# needed; unknown (year, month) simply yields no data.
STUDIO_MONTH_FIELDS = {
    2025: {
        1: 'x_studio_january_2025_attendance_sheet',
        2: 'x_studio_february_2025_attendance_sheet',
        3: 'x_studio_march_2025_attendance_sheet',
        4: 'x_studio_apr_2025', 5: 'x_studio_may_2025', 6: 'x_studio_jun_2025',
        7: 'x_studio_jul_2025', 8: 'x_studio_aug_2025', 9: 'x_studio_sep_2025',
        10: 'x_studio_oct_2025', 11: 'x_studio_nov_2025', 12: 'x_studio_dec_2025',
    },
    2026: {
        1: 'x_studio_january_2026', 2: 'x_studio_february_2026',
        3: 'x_studio_march_2026', 4: 'x_studio_april_2026',
        5: 'x_studio_may_2026', 6: 'x_studio_june_2026',
        7: 'x_studio_july_2026', 8: 'x_studio_august_2026',
        9: 'x_studio_september_2026', 10: 'x_studio_october_2026',
        11: 'x_studio_november_2026', 12: 'x_studio_dec_2026',
    },
}

# Stored value of the "Approved" status in x_sick_leave_reports.
SICK_LEAVE_APPROVED = 'Approved'


def ordinal(day):
    """Return the English ordinal suffix for a day number (1 -> st, 2 -> nd)."""
    if 10 <= day % 100 <= 20:
        return 'th'
    return {1: 'st', 2: 'nd', 3: 'rd'}.get(day % 10, 'th')


def studio_get(record, field_name, default=False):
    """Safely read a Studio field that may not exist on this database."""
    if record and field_name in record._fields:
        return record[field_name]
    return default
