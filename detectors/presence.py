"""Confirm successful responses only for attendance currently active in this tab."""
import re
from . import values

class PresenceDetector:
    def __init__(self, rules):
        self.rules = rules
        self.reset()

    def reset(self):
        self.active = set()
        self.correct = set()
        self.responses_seen = set()

    def matches(self, url):
        return bool(self.rules) and any(re.search(self.rules[k], url) for k in ('active_url_pattern', 'response_url_pattern'))

    def feed(self, url, body):
        if not self.rules:
            return
        r = self.rules
        if re.search(r['active_url_pattern'], url):
            self.active = {str(identity) for item in values(body, r['active_items_path'])
                           if any(v in r['active_values'] for v in values(item, r['active_state_path']))
                           for identity in values(item, r['active_id_path'])}
        match = re.search(r['response_url_pattern'], url)
        if match:
            identity = match.group('id')
            self.responses_seen.add(identity)
            # Require JSON boolean true, not a nonempty string or numeric value.
            if any(v is True for v in values(body, r['confirmed_path'])):
                self.correct.add(identity)
            else:
                self.correct.discard(identity)

    def confirmed(self):
        return self.active & self.correct
