"""Validated, hot-reloaded schedules; all comparisons use UTC instants."""
from dataclasses import dataclass
from datetime import datetime, date, time, timedelta, timezone
from urllib.parse import urlparse
from zoneinfo import ZoneInfo
import re
import yaml

UTC = timezone.utc
DAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']

def clock(value):
    if not isinstance(value, str) or not re.fullmatch(r'\d{2}:\d{2}', value):
        raise ValueError('Times must be quoted HH:MM strings')
    return time.fromisoformat(value)

def dates(values):
    if not isinstance(values, list):
        raise ValueError('skip_dates must be a list')
    return {date.fromisoformat(str(v)) for v in values}

def url(value):
    p = urlparse(value)
    if p.scheme != 'https' or not p.hostname or p.username or p.password:
        raise ValueError('Course/attendance URLs must be HTTPS without credentials')
    return value

def load(path='config.yaml'):
    with open(path) as f:
        c = yaml.safe_load(f)
    if not isinstance(c, dict):
        raise ValueError('Config must be a mapping')
    c.setdefault('timezone', 'America/Detroit')
    ZoneInfo(c['timezone'])
    c.setdefault('poll_interval_seconds', 60)
    if type(c['poll_interval_seconds']) is not int or c['poll_interval_seconds'] < 30:
        raise ValueError('poll_interval_seconds must be an integer >= 30')
    c.setdefault('notify_errors', True)
    if type(c['notify_errors']) is not bool:
        raise ValueError('notify_errors must be boolean')
    dates(c.setdefault('skip_dates', []))
    if not isinstance(c.get('courses'), list):
        raise ValueError('courses must be a list')
    names = set()
    for course in c['courses']:
        name = course['name']
        if not isinstance(name, str) or not name.strip() or name in names:
            raise ValueError('Course names must be nonempty and unique')
        names.add(name)
        url(course['course_url'])
        course.setdefault('attendance_url', course['course_url'])
        url(course['attendance_url'])
        if type(course.setdefault('enabled', True)) is not bool:
            raise ValueError('enabled must be boolean')
        dates(course.setdefault('skip_dates', []))
        if not isinstance(course.get('windows'), list):
            raise ValueError('windows must be a list')
        for w in course['windows']:
            if not isinstance(w.get('days'), list) or not w['days'] or any(d not in DAYS for d in w['days']):
                raise ValueError('days must contain Mon..Sun')
            if clock(w['start']) == clock(w['end']):
                raise ValueError('Equal start/end is ambiguous; split full-day windows')
    return c

def boundary(day, wall, zone, end=False):
    """Ambiguous: earliest start/latest end. Nonexistent: shift forward by DST gap."""
    naive = datetime.combine(day, wall)
    candidates = [naive.replace(tzinfo=zone, fold=f).astimezone(UTC) for f in (0, 1)]
    valid = [v for v in candidates if v.astimezone(zone).replace(tzinfo=None) == naive]
    return (max(valid) if end else min(valid)) if valid else max(candidates)

@dataclass(frozen=True)
class Window:
    start: datetime
    end: datetime
    key: str

def windows(config, course, now, days=7):
    zone = ZoneInfo(config['timezone'])
    today = now.astimezone(zone).date()
    skipped = dates(config['skip_dates']) | dates(course['skip_dates'])
    if not course['enabled']:
        return []
    result = []
    for offset in range(-1, days + 1):
        day = today + timedelta(days=offset)
        if day in skipped:
            continue
        for w in course['windows']:
            if DAYS[day.weekday()] not in w['days']:
                continue
            a, b = clock(w['start']), clock(w['end'])
            stopday = day + timedelta(days=b < a)
            start, end = boundary(day, a, zone), boundary(stopday, b, zone, True)
            # A skipped calendar day also cancels the portion of an overnight window on that day.
            if stopday in skipped:
                end = boundary(stopday, time(), zone)
            if end > start:
                result.append(Window(start, end, f'{day}:{w["start"]}-{w["end"]}'))
    return sorted(set(result), key=lambda w: w.start)

def active(config, course, now):
    instant = now.astimezone(UTC)
    return [w for w in windows(config, course, now, 0) if w.start <= instant < w.end]
