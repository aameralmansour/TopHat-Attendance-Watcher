from abc import ABC, abstractmethod
from email.message import EmailMessage
import asyncio
import logging
import os
import smtplib
import ssl
import requests

log = logging.getLogger(__name__)

def env(key):
    value = os.environ.get(key)
    if not value:
        raise ValueError(f'Missing environment variable: {key}')
    return value

class Notifier(ABC):
    @abstractmethod
    def send(self, message): ...

class TwilioNotifier(Notifier):
    def __init__(self):
        self.sid, self.token = env('TWILIO_ACCOUNT_SID'), env('TWILIO_AUTH_TOKEN')
        self.sender, self.recipient = env('TWILIO_FROM'), env('TWILIO_TO')
    def send(self, message):
        r = requests.post(f'https://api.twilio.com/2010-04-01/Accounts/{self.sid}/Messages.json', auth=(self.sid, self.token), data={'From': self.sender, 'To': self.recipient, 'Body': message}, timeout=20)
        r.raise_for_status()

class NtfyNotifier(Notifier):
    def __init__(self):
        self.topic = env('NTFY_TOPIC')
        self.server = os.getenv('NTFY_SERVER', 'https://ntfy.sh').rstrip('/')
        if not self.server.startswith('https://'):
            raise ValueError('NTFY_SERVER must use HTTPS')
    def send(self, message):
        headers = {'Title': 'Top Hat attendance'}
        if os.getenv('NTFY_TOKEN'):
            headers['Authorization'] = 'Bearer ' + os.environ['NTFY_TOKEN']
        r = requests.post(f'{self.server}/{self.topic}', data=message.encode('utf-8'), headers=headers, timeout=20)
        r.raise_for_status()

class PushoverNotifier(Notifier):
    def __init__(self):
        self.token, self.user = env('PUSHOVER_TOKEN'), env('PUSHOVER_USER')
    def send(self, message):
        r = requests.post('https://api.pushover.net/1/messages.json', data={'token': self.token, 'user': self.user, 'message': message}, timeout=20)
        r.raise_for_status()
        if r.json().get('status') != 1:
            raise RuntimeError('Pushover rejected notification')

class SMTPNotifier(Notifier):
    def __init__(self):
        self.host, self.sender, self.to = env('SMTP_HOST'), env('SMTP_FROM'), env('SMTP_TO')
    def send(self, message):
        mail = EmailMessage()
        mail['From'], mail['To'], mail['Subject'] = self.sender, self.to, 'Top Hat attendance'
        mail.set_content(message)
        mode = os.getenv('SMTP_SECURITY', 'starttls')
        if mode not in ('ssl', 'starttls'):
            raise ValueError('SMTP_SECURITY must be ssl or starttls')
        cls = smtplib.SMTP_SSL if mode == 'ssl' else smtplib.SMTP
        kwargs = {'context': ssl.create_default_context()} if mode == 'ssl' else {}
        with cls(self.host, int(os.getenv('SMTP_PORT', '465' if mode == 'ssl' else '587')), timeout=20, **kwargs) as smtp:
            if mode == 'starttls':
                smtp.starttls(context=ssl.create_default_context())
            if os.getenv('SMTP_USER'):
                smtp.login(env('SMTP_USER'), env('SMTP_PASSWORD'))
            smtp.send_message(mail)

def create():
    classes = {'twilio': TwilioNotifier, 'ntfy': NtfyNotifier, 'pushover': PushoverNotifier, 'smtp': SMTPNotifier}
    backend = os.getenv('NOTIFIER', 'ntfy')
    if backend not in classes:
        raise ValueError('NOTIFIER must be twilio, ntfy, pushover, or smtp')
    return classes[backend]()

async def deliver(notifier, message):
    for attempt in range(3):
        try:
            await asyncio.to_thread(notifier.send, message)
            return True
        except Exception as exc:
            # Do not log provider URLs or exception strings: they may contain credentials.
            log.error('notification_send_failed', extra={'attempt': attempt + 1, 'error_type': type(exc).__name__})
            if attempt < 2:
                await asyncio.sleep(2 ** (attempt + 1))
    return False
