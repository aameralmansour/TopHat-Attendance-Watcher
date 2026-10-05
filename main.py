#!/usr/bin/env python3
import argparse
import asyncio
from datetime import datetime, timedelta, timezone
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import sys
from zoneinfo import ZoneInfo
from filelock import FileLock, Timeout
from dotenv import load_dotenv
import config
from auth import login
from auto_login import AutoLogin, credentials
from detectors import load_rules
from notifiers import create, deliver
from state import State
import watcher

class JsonFormatter(logging.Formatter):
    def format(self, record):
        data = {'timestamp': datetime.now(timezone.utc).isoformat(), 'level': record.levelname, 'event': record.getMessage()}
        for key in ('course', 'result', 'error_type', 'attempt'):
            if hasattr(record, key):
                data[key] = getattr(record, key)
        return json.dumps(data, ensure_ascii=False)

def logging_setup():
    Path('logs').mkdir(exist_ok=True, mode=0o700)
    handler = RotatingFileHandler('logs/watcher.jsonl', maxBytes=2_000_000, backupCount=5, encoding='utf-8')
    handler.setFormatter(JsonFormatter())
    logging.basicConfig(level=logging.INFO, handlers=[handler])

def parse_args():
    parser = argparse.ArgumentParser(description='Notification-only Top Hat attendance watcher')
    parser.add_argument('command', nargs='?', choices=['run'])
    actions = parser.add_mutually_exclusive_group()
    actions.add_argument('--login', action='store_true')
    actions.add_argument('--discover', metavar='COURSE')
    actions.add_argument('--check', metavar='COURSE')
    actions.add_argument('--test-notify', action='store_true')
    actions.add_argument('--list', action='store_true')
    actions.add_argument('--validate', action='store_true')
    parser.add_argument('--config', default='config.yaml')
    parser.add_argument('--detection', default='detection.yaml')
    parser.add_argument('--profile', default='profile')
    parser.add_argument('--state', default='state.sqlite3')
    parser.add_argument('--headed', action='store_true')
    args = parser.parse_args()
    selected = any([args.login, args.discover, args.check, args.test_notify, args.list, args.validate])
    if bool(args.command) == selected:
        parser.error('Choose exactly one command: run or one of the --actions')
    return args

async def execute(args):
    if args.test_notify:
        if not await deliver(create(), 'Top Hat watcher test notification'):
            raise RuntimeError('Test notification failed; see logs')
        print('Test notification accepted by provider.')
        return
    cfg = config.load(args.config)
    if args.validate:
        load_rules(args.detection)
        print('Configuration and detection rules are valid.')
        return
    if args.list:
        now = datetime.now(timezone.utc)
        end = now + timedelta(days=7)
        zone = ZoneInfo(cfg['timezone'])
        rows = [(w.start, c['name'], w) for c in cfg['courses'] for w in config.windows(cfg, c, now) if w.end > now and w.start < end]
        for _, name, w in sorted(rows):
            print(f'{name}: {w.start.astimezone(zone).isoformat()} → {w.end.astimezone(zone).isoformat()}')
        return
    if args.login:
        if not cfg['courses']:
            raise ValueError('Add a course URL before login')
        await login(args.profile, cfg['courses'][0]['course_url'])
        return
    if args.discover or args.check:
        name = args.discover or args.check
        course = next((c for c in cfg['courses'] if c['name'].casefold() == name.casefold()), None)
        if course is None:
            raise ValueError(f'Unknown course: {name}')
        if args.discover:
            await watcher.discover(args, course)
        else:
            await watcher.check(args, course, load_rules(args.detection))
        return
    load_rules(args.detection)
    notifier = create()
    login_credentials = credentials()
    state = State(args.state)
    try:
        relogin = AutoLogin(state, *login_credentials, school=os.getenv('TOPHAT_SCHOOL', 'Michigan State University')) if login_credentials else None
        await watcher.run(args, state, notifier, relogin)
    finally:
        state.close()

def main():
    os.umask(0o077)
    load_dotenv()
    logging_setup()
    args = parse_args()
    # Prevent duplicate sends and Chromium profile corruption across CLI/service instances.
    locks = [FileLock(str(Path(args.profile).resolve()) + '.watcher.lock'), FileLock(str(Path(args.state).resolve()) + '.watcher.lock')]
    try:
        if args.command or args.login or args.discover or args.check:
            with locks[0].acquire(timeout=0), locks[1].acquire(timeout=0):
                asyncio.run(execute(args))
        else:
            asyncio.run(execute(args))
    except Timeout:
        print('Watcher/profile is already in use. Stop the service before login, discovery, or check.', file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 0
    except Exception as exc:
        logging.getLogger(__name__).error('fatal', extra={'error_type': type(exc).__name__})
        # Validation messages contain no notifier credentials.
        print(str(exc) if isinstance(exc, (ValueError, FileNotFoundError)) else f'{type(exc).__name__}: command failed; see logs.', file=sys.stderr)
        return 1
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
