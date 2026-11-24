"""Conservative table candidates. No inferred period-to-column alignment."""
import re
from bs4 import BeautifulSoup
from decimal import Decimal, InvalidOperation


def normalize(raw: str) -> str:
    text = raw.strip().replace(',', '').replace('$', '').replace('%', '')
    if text.startswith('(') and text.endswith(')'):
        text = '-' + text[1:-1]
    try:
        value = Decimal(text)
    except InvalidOperation as exc:
        raise ValueError('Enter a finite number, optionally with commas, $ or %.') from exc
    if not value.is_finite():
        raise ValueError('Value must be finite.')
    return str(value)


def change(previous, current, unit, prior_unit):
    if unit != prior_unit:
        return {'status': 'not_comparable'}
    p, c = Decimal(normalize(previous)), Decimal(normalize(current))
    if unit == 'percent':
        return {'status': 'ok', 'value': str(c-p), 'kind': 'percentage_points'}
    if p == 0:
        return {'status': 'zero_denominator'}
    return {'status': 'ok', 'value': str((c-p)/abs(p)*100), 'kind': 'percent_change'}


def table_candidates(pages):
    """Expose numeric table cells with their headers; always require review.

    This starter deliberately doesn't infer fiscal dates, metric definitions,
    actual vs guidance, or chart ordering. It is not general financial extraction.
    """
    results = []
    for page in pages:
        # The connected parser returns HTML tables by default. Convert only
        # rectangular, non-spanning tables; complex geometry needs another stage.
        soup = BeautifulSoup(page['markdown'], 'html.parser')
        for table in soup.find_all('table'):
            rows = table.find_all('tr')
            if table.find(attrs={'rowspan': True}) or table.find(attrs={'colspan': True}):
                table.replace_with('\n[Complex table requires manual review]\n')
                continue
            lines = []
            for row in rows:
                cells = [cell.get_text(' ', strip=True).replace('|', '/') for cell in row.find_all(['td', 'th'])]
                lines.append('| ' + ' | '.join(cells) + ' |')
            table.replace_with('\n' + '\n'.join(lines) + '\n')
        header = None
        for line in soup.get_text().splitlines():
            if '|' not in line:
                header = None
                continue
            cells = [x.strip() for x in line.strip().strip('|').split('|')]
            if all(re.fullmatch(r'[:\-\s]*', x) for x in cells):
                continue
            if header is None:
                header = cells
                continue
            label = cells[0]
            if not label or not re.search('[A-Za-z]', label):
                continue
            for i, cell in enumerate(cells[1:], 1):
                if not re.fullmatch(r'\(?-?\$?\s*\d[\d,]*(?:\.\d+)?\s*%?\)?', cell):
                    continue
                results.append({
                    'metric': label, 'value': normalize(cell), 'raw_value': cell,
                    'period': header[i] if i < len(header) else 'Unresolved',
                    'unit': 'percent' if '%' in cell else 'unresolved',
                    'page': page['page'], 'evidence': line,
                    'note': 'Verify period, scale, definition and actual/guidance before acceptance.',
                    'status': 'pending',
                })
    return results
