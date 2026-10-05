"""Rules operate on intercepted data only; no private API requests."""
from abc import ABC, abstractmethod
import re
import yaml

def values(obj, path):
    nodes = [obj]
    for part in path.split('.') if path else []:
        out = []
        for node in nodes:
            if part == '*':
                out.extend(node.values() if isinstance(node, dict) else node if isinstance(node, list) else [])
            elif isinstance(node, dict) and part in node:
                out.append(node[part])
        nodes = out
    return nodes

def load_rules(path='detection.yaml'):
    with open(path) as f:
        rules = yaml.safe_load(f)
    if not isinstance(rules, dict) or rules.get('strategy') not in ('network', 'dom', 'both'):
        raise ValueError('strategy must be network, dom, or both')
    if type(rules.get('settle_ms', 3000)) is not int or not 0 <= rules.get('settle_ms', 3000) <= 20000:
        raise ValueError('settle_ms must be an integer from 0 to 20000')
    if rules.get('configured') is not True:
        raise ValueError('Detection rules are examples. Run discovery and set configured: true')
    for rule in rules.get('network', []):
        re.compile(rule['url_pattern'])
        for key in ('items_path', 'state_path'):
            if not isinstance(rule[key], str):
                raise ValueError(f'{key} must be a string')
        if not isinstance(rule['open_values'], list) or not rule['open_values']:
            raise ValueError('open_values must be a nonempty list')
    for rule in rules.get('dom', []):
        if not rule.get('selector'):
            raise ValueError('DOM rule requires a selector')
        re.compile(rule.get('text_pattern', ''))
    for pattern in rules.get('session', {}).get('login_url_patterns', []):
        re.compile(pattern)
    if rules['strategy'] in ('network', 'both') and not rules.get('network'):
        raise ValueError('Network strategy requires rules')
    if rules['strategy'] in ('dom', 'both') and not rules.get('dom'):
        raise ValueError('DOM strategy requires rules')
    if 'presence' in rules:
        presence = rules['presence']
        for key in ('active_url_pattern', 'response_url_pattern'):
            re.compile(presence[key])
        if 'id' not in re.compile(presence['response_url_pattern']).groupindex:
            raise ValueError('presence response_url_pattern requires named group id')
        for key in ('active_items_path', 'active_id_path', 'active_state_path', 'confirmed_path'):
            if not isinstance(presence[key], str):
                raise ValueError(f'presence {key} must be a string')
        if not isinstance(presence['active_values'], list) or not presence['active_values']:
            raise ValueError('presence active_values must be a nonempty list')
    return rules

class Detector(ABC):
    @abstractmethod
    async def detect(self, page): ...

class NetworkDetector(Detector):
    def __init__(self, rules):
        self.rules = rules
        self.items = set()
        self.matched = False

    def reset(self):
        self.items.clear()
        self.matched = False

    def feed(self, url, body):
        for rule in self.rules:
            if not re.search(rule['url_pattern'], url):
                continue
            records = values(body, rule['items_path'])
            if rule['items_path'].endswith('.*') and values(body, rule['items_path'][:-2]) == [[]]:
                self.matched = True
            for item in records:
                ids = values(item, rule.get('id_path', 'id'))
                identity = str(ids[0]) if ids and ids[0] is not None else None
                states = values(item, rule['state_path'])
                if states:
                    self.matched = True
                if any(v in rule['open_values'] for v in states):
                    self.items.add(identity)
                elif states:
                    self.items.discard(identity)

    async def detect(self, page):
        return set(self.items)

class DomDetector(Detector):
    def __init__(self, rules):
        self.rules = rules

    async def detect(self, page):
        found = set()
        for rule in self.rules:
            locator = page.locator(rule['selector'])
            for i in range(await locator.count()):
                element = locator.nth(i)
                if not await element.is_visible():
                    continue
                if rule.get('text_pattern') and not re.search(rule['text_pattern'], await element.inner_text(), re.I):
                    continue
                item = await element.get_attribute(rule['id_attribute']) if rule.get('id_attribute') else None
                found.add(item)
        return found
